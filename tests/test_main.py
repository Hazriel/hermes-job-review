import unittest, tempfile, sys, json, os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from store import Store
try:
    import main
except ImportError:
    main=None
SCRATCH=os.environ.get('TMPDIR','/tmp')
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'jobs.sqlite3')
        self.addCleanup(self.store.close)
        self.now=datetime(2026,10,9,18,tzinfo=ZoneInfo('Europe/Paris'))
        self.offer=dict(source='free-work',source_id='1',url='https://www.free-work.com/fr/tech-it/job-mission/test/test',title='Remote analyst',published_at='2026-10-09T10:00:00+02:00',published_date='2026-10-09',remote_hint='full',remote_raw='full',contracts_raw=['permanent'],skills_raw=['Python'],employer='Example',location='France',description='Full remote analyst using Python.')
        self.analysis=dict(remote_policy='full',remote_evidence='Full remote',role_category='other',role_evidence='',contract_types=['permanent'],skills=[{'name':'Python','evidence':'Python'}],summary='An analyst role',seniority=None,usage={'prompt_tokens':100,'completion_tokens':20})
    def test_matching_offer_is_saved_and_second_run_is_silent_before_details(self):
        self.assertIsNotNone(main,'collector must exist')
        calls=[]
        def iterator(today,lookback_days):
            self.assertEqual(lookback_days,2)
            return iter([self.offer])
        def fetcher(o): calls.append(o['source_id']);return o
        def analyzer(o):return self.analysis
        output,counts=main.collect(self.store,self.now,iterator=iterator,fetcher=fetcher,analyzer=analyzer,batch_id='execution-1',workers=1)
        self.assertIn(self.offer['url'],output)
        self.assertEqual(self.store.stats()['matched'],1)
        self.assertEqual(counts['notified'],1)
        output,counts=main.collect(self.store,self.now,iterator=iterator,fetcher=fetcher,analyzer=analyzer,batch_id='execution-2',workers=1)
        self.assertEqual(output,'')
        self.assertEqual(calls,['1'])
        self.assertEqual(counts['already_seen'],1)

    def test_strict_onsite_is_discarded_before_detail_or_llm(self):
        offer={**self.offer,'remote_hint':'onsite','remote_raw':'none'}
        def must_not_run(o):self.fail('Onsite offer fetched or analyzed')
        output,counts=main.collect(self.store,self.now,iterator=lambda *a,**kw:iter([offer]),fetcher=must_not_run,analyzer=must_not_run,batch_id='onsite',workers=1)
        self.assertEqual(output,'')
        self.assertTrue(self.store.has_seen('free-work','1'))
        self.assertEqual(counts['rejected'],1)

    def test_failed_extraction_is_logged_and_retryable_without_losing_good_offers(self):
        bad={**self.offer,'source_id':'2','url':self.offer['url']+'-2'}
        events=[]
        def analyzer(o):
            if o['source_id']=='2':raise ValueError('Invalid provider extraction')
            return self.analysis
        output,counts=main.collect(self.store,self.now,iterator=lambda *a,**kw:iter([bad,self.offer]),fetcher=lambda o:o,analyzer=analyzer,batch_id='error-batch',workers=1,log=lambda event,**kw:events.append((event,kw)))
        self.assertFalse(self.store.has_seen('free-work','2'))
        self.assertTrue(self.store.has_seen('free-work','1'))
        self.assertEqual(counts['errors'],1)
        self.assertIn(self.offer['url'],output)
        self.assertTrue(any(e[0]=='offer_error' for e in events))

    def test_detail_analysis_uses_bounded_parallel_workers(self):
        import threading
        barrier=threading.Barrier(2,timeout=1)
        second={**self.offer,'source_id':'2','url':self.offer['url']+'-2'}
        def analyzer(o):barrier.wait();return self.analysis
        output,counts=main.collect(self.store,self.now,iterator=lambda *a,**kw:iter([self.offer,second]),fetcher=lambda o:o,analyzer=analyzer,batch_id='parallel',workers=2)
        self.assertEqual(counts['analyzed'],2)
        self.assertEqual(counts['errors'],0)

    def test_silent_run_has_persistent_local_success_log(self):
        self.assertTrue(hasattr(main,'run_job'),'run wrapper must exist')
        root=Path(self.tmp.name)/'local-log'
        output=main.run_job(root,'collect',self.now,batch_id='empty',iterator=lambda *a,**kw:iter([]),workers=1)
        self.assertEqual(output,'')
        rows=[json.loads(s) for s in (root/'logs/runs.jsonl').read_text().splitlines()]
        self.assertEqual(rows[-1]['event'],'run_finished')
        self.assertEqual(rows[-1]['status'],'ok')
        db=Store(root/'jobs.sqlite3')
        self.addCleanup(db.close)
        self.assertEqual(db.stats()['last_run']['status'],'ok')

    def test_weekly_report_is_paris_monday_nine_and_emits_once(self):
        self.assertTrue(hasattr(main,'weekly'),'weekly handler must exist')
        root=Path(self.tmp.name)/'weekly'
        before=datetime(2026,10,12,8,tzinfo=ZoneInfo('Europe/Paris'))
        due=datetime(2026,10,12,9,tzinfo=ZoneInfo('Europe/Paris'))
        self.assertEqual(main.run_job(root,'weekly',before,batch_id='week-before'),'')
        report=main.run_job(root,'weekly',due,batch_id='week-first')
        self.assertIn('2026-10-05 to 2026-10-11',report)
        self.assertIn('0 matching observed offers',report)
        self.assertEqual(main.run_job(root,'weekly',due.replace(hour=10),batch_id='week-second'),'')
        self.assertTrue((root/'backups/2026-10-05.sqlite3').exists())

    def test_reserved_notifications_reconcile_only_from_exact_execution(self):
        self.assertTrue(hasattr(main,'reconcile'),'reconciliation must exist')
        job_id=self.store.save_match(self.offer,self.analysis)
        self.store.reserve_notifications([job_id],'exact-execution')
        calls=[]
        def lookup(execution_id):
            calls.append(execution_id)
            return {'id':execution_id,'status':'completed','delivery_outcome':'delivered'}
        main.reconcile(self.store,lookup=lookup)
        self.assertEqual(calls,['exact-execution'])
        self.assertEqual(self.store.stats()['notifications'],{'sent':1})

    def test_cli_status_is_queryable_without_network(self):
        import subprocess
        done=subprocess.run([sys.executable,str(Path(main.__file__)),'status','--root',self.tmp.name],capture_output=True,text=True,timeout=10)
        self.assertEqual(done.returncode,0,done.stderr)
        value=json.loads(done.stdout)
        self.assertEqual(value['matched'],0)
        self.assertEqual(value['timezone'],'Europe/Paris')

    def test_quota_failure_stops_further_model_calls_in_that_run(self):
        offers=[{**self.offer,'source_id':str(i),'url':self.offer['url']+str(i)} for i in range(4)]
        calls=[]
        def analyzer(o):calls.append(o['source_id']);raise RuntimeError('429 quota exhausted')
        output,counts=main.collect(self.store,self.now,iterator=lambda *a,**kw:iter(offers),fetcher=lambda o:o,analyzer=analyzer,batch_id='quota',workers=1)
        self.assertEqual(len(calls),1)
        self.assertEqual(counts['errors'],4)
        self.assertEqual(output,'')
        self.assertEqual(self.store.stats()['seen'],0)

if __name__=='__main__':unittest.main()
