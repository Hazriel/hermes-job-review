"""Hourly collection and weekly analytics; stdout is gateway delivery only."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor,as_completed
import freework
from llm import analyze_offer
from store import Store,matches
from reporting import render_digest,render_weekly,previous_week
PARIS=ZoneInfo('Europe/Paris')

def collect(store,now,iterator=None,fetcher=None,analyzer=None,batch_id=None,workers=4,log=None,emit=True):
    iterator=iterator or freework.iter_recent_offers
    fetcher=fetcher or freework.fetch_offer
    analyzer=analyzer or analyze_offer
    counts=dict(observed=0,already_seen=0,rejected=0,analyzed=0,matched=0,errors=0,notified=0,prompt_tokens=0,completion_tokens=0)
    candidates=[]
    for offer in iterator(now.astimezone(PARIS).date(),lookback_days=2):
        counts['observed']+=1
        if store.has_seen(offer['source'],offer['source_id']):
            counts['already_seen']+=1
            continue
        if offer.get('remote_hint')=='onsite':
            store.mark_seen(offer,'rejected','Strictly onsite: rejected before detail fetch and role matching')
            counts['rejected']+=1
            continue
        candidates.append(offer)
    if log:log('listing_complete',candidates=len(candidates),counts=dict(counts))
    from threading import Event
    provider_blocked=Event()
    def extract(offer):
        if provider_blocked.is_set():return offer,None,None,'Model quota/authentication blocked earlier in this run; retry later'
        try:
            detail=fetcher(offer)
            return offer,detail,analyzer(detail),None
        except Exception as exc:
            error=str(exc)
            if any(marker in error.casefold() for marker in ['quota','429','rate limit','401','authentication','unauthorized']):
                provider_blocked.set()
            return offer,None,None,error
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for offer,detail,analysis,error in pool.map(extract,candidates):
            if error is not None:
                counts['errors']+=1
                if log:log('offer_error',source=offer['source'],source_id=offer['source_id'],error=error)
                continue
            counts['analyzed']+=1
            for key in ('prompt_tokens','completion_tokens'):
                counts[key]+=int(analysis.get('usage',{}).get(key,0) or 0)
            if matches(analysis,detail.get('remote_hint')):
                store.save_match(detail,analysis)
                counts['matched']+=1
                decision='matched'
            else:
                store.mark_seen(offer,'rejected','Remote policy or role does not match')
                counts['rejected']+=1
                decision='rejected'
            if log:log('offer_evaluated',source=offer['source'],source_id=offer['source_id'],decision=decision,model=analysis.get('model'),usage=analysis.get('usage'))
    pending=store.get_pending()
    if not emit or not pending:return '',counts
    output='\n\n'.join(render_digest(pending,now))
    if not batch_id:raise RuntimeError('Delivery requires a cron execution identity')
    if not store.reserve_notifications([o['id'] for o in pending],batch_id):
        raise RuntimeError('Could not atomically reserve digest')
    counts['notified']=len(pending)
    return output,counts

def reconcile(store,lookup=None):
    import json
    if lookup is None:
        try:
            from cron.executions import get_execution
            lookup=get_execution
        except ImportError:
            return 0
    changed=0
    def disposition(record):
        if record and record.get('status')=='completed' and record.get('delivery_outcome')=='delivered':return 'sent'
        if record and record.get('status') in {'failed','unknown','completed'}:return 'uncertain'
        return None
    for batch_id in store.list_reserved_batches():
        record=lookup(batch_id)
        status=disposition(record)
        if status:
            store.mark_notifications(batch_id,status,receipts=record,error=record.get('error'))
            changed+=1
    for row in store.conn.execute("SELECT key,value FROM metadata WHERE key LIKE 'weekly:%'").fetchall():
        value=json.loads(row['value'])
        if value.get('status')!='reserved':continue
        record=lookup(value['batch_id'])
        status=disposition(record)
        if status:
            value.update(status=status,receipt=record)
            store.set_meta(row['key'],json.dumps(value))
            changed+=1
    return changed

def weekly(store,now,root,batch_id=None,force=False):
    import json,sqlite3
    from pathlib import Path
    local=now.astimezone(PARIS)
    if not force and (local.weekday()!=0 or local.hour<9):
        return '',{'skipped':'not Paris Monday at or after 09:00'}
    start,end=previous_week(now)
    key='weekly:'+start.isoformat()
    if store.get_meta(key):return '',{'skipped':'already reserved or delivered'}
    stats=store.weekly_stats(start,end)
    baseline=store.weekly_stats(start-timedelta(days=7),start)
    earliest=store.conn.execute('SELECT MIN(started_at) FROM runs WHERE kind="collect"').fetchone()[0]
    comparable=bool(earliest and datetime.fromisoformat(earliest).astimezone(PARIS).date()<=start-timedelta(days=7))
    report=render_weekly(stats,start,end,previous=baseline if comparable else None)
    backupdir=Path(root)/'backups'
    backupdir.mkdir(exist_ok=True)
    with sqlite3.connect(backupdir/(start.isoformat()+'.sqlite3')) as backup:
        store.conn.backup(backup)
    if not batch_id:raise RuntimeError('Weekly delivery requires a cron execution identity')
    store.set_meta(key,json.dumps({'batch_id':batch_id,'status':'reserved','total':stats['total']}))
    return report,{'total':stats['total'],'week':start.isoformat()}

def run_job(root,kind,now=None,batch_id=None,**kwargs):
    from pathlib import Path
    import json
    root=Path(root)
    now=now or datetime.now(PARIS)
    logfile=root/'logs/runs.jsonl'
    logfile.parent.mkdir(parents=True,exist_ok=True)
    def log(event,**fields):
        with logfile.open('a',encoding='utf-8') as f:
            f.write(json.dumps(dict(timestamp=datetime.now(PARIS).isoformat(),event=event,kind=kind,**fields),ensure_ascii=False)+'\n')
    store=Store(root/'jobs.sqlite3')
    run_id=store.start_run(kind,now.isoformat())
    log('run_started',run_id=run_id,batch_id=batch_id)
    try:
        if kind=='weekly':
            output,counts=weekly(store,now,root,batch_id=batch_id,**kwargs)
        elif kind=='collect':
            output,counts=collect(store,now,batch_id=batch_id,log=log,**kwargs)
        else:
            raise ValueError('Unknown job kind')
        status='partial' if counts.get('errors') else 'ok'
        store.finish_run(run_id,status,counts)
        log('run_finished',run_id=run_id,status=status,counts=counts,output_chars=len(output))
        return output
    except Exception as exc:
        store.finish_run(run_id,'failed',{},str(exc))
        log('run_finished',run_id=run_id,status='failed',error=str(exc))
        raise
    finally:
        store.close()

def cli():
    import argparse,json,fcntl,sys
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['collect','weekly','status','preview-weekly'])
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--stage',action='store_true',help='Collect without reserving or emitting alerts')
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    if not 1<=args.workers<=8:parser.error('workers must be between 1 and 8')
    root=args.root
    root.mkdir(parents=True,exist_ok=True)
    if args.command in {'status','preview-weekly'}:
        store=Store(root/'jobs.sqlite3')
        try:
            reconcile(store)
            if args.command=='status':
                info=store.stats()
                info.update(timezone='Europe/Paris',lookback_days=2,model='gpt-6-luna',database=str(root/'jobs.sqlite3'),log=str(root/'logs/runs.jsonl'))
                print(json.dumps(info,ensure_ascii=False,indent=2))
            else:
                start,end=previous_week(datetime.now(PARIS))
                print(render_weekly(store.weekly_stats(start,end),start,end))
        finally:store.close()
        return 0
    batch_id=None
    if not args.stage:
        config=json.loads((root/'config.json').read_text())
        from cron.executions import live_inflight_execution
        record=live_inflight_execution(config[args.command+'_job_id'])
        if not record:raise RuntimeError('Use hermes cron run for delivery; direct calls require --stage')
        batch_id=record['id']
    with (root/'collector.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            logfile=root/'logs/runs.jsonl'
            logfile.parent.mkdir(exist_ok=True)
            with logfile.open('a') as f:f.write(json.dumps({'timestamp':datetime.now(PARIS).isoformat(),'event':'run_skipped','kind':args.command,'reason':'another collection/report is running'})+'\n')
            return 0
        store=Store(root/'jobs.sqlite3')
        try:reconcile(store)
        finally:store.close()
        kwargs={'workers':args.workers,'emit':not args.stage} if args.command=='collect' else {}
        output=run_job(root,args.command,batch_id=batch_id,**kwargs)
        if output:print(output)
    return 0

if __name__=='__main__':
    try:raise SystemExit(cli())
    except Exception as exc:
        import sys
        print(f'Job review failed: {type(exc).__name__}: {exc}',file=sys.stderr)
        raise SystemExit(1)
