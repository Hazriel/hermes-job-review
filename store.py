"""Durable source-scoped storage and conservative deterministic matching."""

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path


def _record(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return vars(value).copy()


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS seen (
                source TEXT NOT NULL, source_id TEXT NOT NULL,
                url TEXT NOT NULL, title TEXT, published_at TEXT, published_date TEXT,
                decision TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', evaluated_at TEXT NOT NULL,
                PRIMARY KEY(source, source_id)
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
                url TEXT NOT NULL, title TEXT, published_at TEXT, published_date TEXT,
                employer TEXT, location TEXT, description TEXT, salary TEXT, daily_rate TEXT,
                remote_hint TEXT, remote_policy TEXT, role_category TEXT, seniority TEXT,
                summary TEXT, raw_offer_json TEXT NOT NULL, analysis_json TEXT NOT NULL,
                saved_at TEXT NOT NULL, UNIQUE(source, source_id), UNIQUE(source, url)
            );
            CREATE INDEX IF NOT EXISTS jobs_publication_date ON jobs(published_date);
            CREATE TABLE IF NOT EXISTS job_contracts (
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                raw TEXT NOT NULL, normalized TEXT, PRIMARY KEY(job_id,raw)
            );
            CREATE INDEX IF NOT EXISTS contracts_normalized ON job_contracts(normalized);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL, counts_json TEXT NOT NULL DEFAULT '{}', error TEXT
            );
            CREATE TABLE IF NOT EXISTS notifications (
                job_id INTEGER PRIMARY KEY REFERENCES jobs(id), batch_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('reserved','sent','uncertain','failed')),
                reserved_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                receipts_json TEXT, error TEXT
            );
            CREATE INDEX IF NOT EXISTS notifications_batch ON notifications(batch_id);
            CREATE TABLE IF NOT EXISTS job_skills (
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                name TEXT NOT NULL, evidence_json TEXT NOT NULL,
                PRIMARY KEY(job_id,name)
            );
        ''')

    def save_match(self, offer, analysis):
        offer = _record(offer)
        if not matches(analysis, offer.get('remote_hint')):
            raise ValueError('Only deterministic matches may be permanently saved')
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            existing = self.conn.execute('''SELECT id FROM jobs WHERE source=?
                AND (source_id=? OR url=?) ORDER BY (source_id=?) DESC LIMIT 1''',
                (offer['source'], str(offer['source_id']), offer['url'], str(offer['source_id']))).fetchone()
            if existing:
                self._mark_seen(offer, 'matched', '')
                return existing['id']
            cursor = self.conn.execute('''INSERT INTO jobs
                (source,source_id,url,title,published_at,published_date,employer,location,
                 description,salary,daily_rate,remote_hint,remote_policy,role_category,seniority,
                 summary,raw_offer_json,analysis_json,saved_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (offer['source'], str(offer['source_id']), offer['url'], offer.get('title'),
                 str(offer.get('published_at') or '') or None,
                 str(offer.get('published_date') or '') or None,
                 analysis.get('employer') or offer.get('employer'), analysis.get('location') or offer.get('location'),
                 offer.get('description'), analysis.get('salary') or offer.get('salary'),
                 analysis.get('daily_rate') or offer.get('daily_rate'), offer.get('remote_hint'),
                 analysis.get('remote_policy'), analysis.get('role_category'), analysis.get('seniority'),
                 analysis.get('summary'), _json(offer), _json(analysis), _now()))
            job_id = cursor.lastrowid
            normalized_seen = set()
            raw_contracts = list(dict.fromkeys(offer.get('contracts_raw') or []))
            for raw in raw_contracts:
                normalized = CONTRACT_ALIASES.get(raw.strip().casefold())
                self.conn.execute('INSERT INTO job_contracts VALUES(?,?,?)', (job_id, raw, normalized))
                if normalized:
                    normalized_seen.add(normalized)
            raw_codes = {raw.strip().casefold() for raw in raw_contracts}
            for code in dict.fromkeys(analysis.get('contract_types') or []):
                normalized = CONTRACT_ALIASES.get(code.strip().casefold(), code)
                if normalized not in normalized_seen and code.strip().casefold() not in raw_codes:
                    self.conn.execute('INSERT INTO job_contracts VALUES(?,?,?)',
                                      (job_id, '[analysis] ' + normalized, normalized))
                    normalized_seen.add(normalized)
            skills = {}
            for skill in analysis.get('skills') or []:
                raw_name = skill['name'].strip()
                name = SKILL_ALIASES.get(raw_name.casefold(), raw_name)
                if name:
                    skills.setdefault(name, []).append(skill.get('evidence', ''))
            for name, evidence in skills.items():
                self.conn.execute('INSERT INTO job_skills VALUES(?,?,?)', (job_id, name, _json(evidence)))
            self._mark_seen(offer, 'matched', '')
            return job_id

    def get_meta(self, key):
        row = self.conn.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        return row['value'] if row else None

    def set_meta(self, key, value):
        with self.conn:
            self.conn.execute('''INSERT INTO metadata(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value''', (key, value))

    def start_run(self, kind, now_iso):
        with self.conn:
            cursor = self.conn.execute("INSERT INTO runs(kind,started_at,status) VALUES(?,?,'running')", (kind, now_iso))
            return cursor.lastrowid

    def finish_run(self, run_id, status, counts, error=None):
        with self.conn:
            self.conn.execute('UPDATE runs SET status=?,counts_json=?,error=?,finished_at=? WHERE id=?',
                              (status, _json(counts), error, _now(), run_id))

    def stats(self):
        result = {table: self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ['seen', 'jobs', 'runs']}
        result['matched'] = result.pop('jobs')
        result['rejected'] = self.conn.execute("SELECT COUNT(*) FROM seen WHERE decision='rejected'").fetchone()[0]
        result['pending'] = self.conn.execute('''SELECT COUNT(*) FROM jobs WHERE NOT EXISTS
            (SELECT 1 FROM notifications WHERE notifications.job_id=jobs.id)''').fetchone()[0]
        result['notifications'] = {row['status']: row['n'] for row in self.conn.execute(
            'SELECT status,COUNT(*) AS n FROM notifications GROUP BY status')}
        row = self.conn.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
        result['last_run'] = dict(row) if row else None
        if row:
            result['last_run']['counts'] = json.loads(result['last_run'].pop('counts_json'))
        return result

    def weekly_stats(self, start, end):
        """Publication-date cohort [start,end); a skill counts once per job."""
        if start >= end:
            raise ValueError('The publication window must have start < end')
        params = (start.isoformat(), end.isoformat())
        where = 'jobs.published_date>=? AND jobs.published_date<?'
        total = self.conn.execute(f'SELECT COUNT(*) FROM jobs WHERE {where}', params).fetchone()[0]

        def grouped(expression, table='jobs', key='name'):
            rows = self.conn.execute(f'''SELECT {expression} AS value, COUNT(DISTINCT jobs.id) AS count
                FROM {table} WHERE {where} GROUP BY value ORDER BY count DESC,value ASC''', params)
            return [{key: row['value'], 'count': row['count']} for row in rows]

        daily = grouped('jobs.published_date', key='date')
        daily.sort(key=lambda item: item['date'])
        return {
            'total': total,
            'top_skills': grouped('job_skills.name', 'jobs JOIN job_skills ON jobs.id=job_skills.job_id')[:10],
            'contract_counts': grouped("COALESCE(job_contracts.normalized, 'raw: ' || job_contracts.raw)",
                                       'jobs JOIN job_contracts ON jobs.id=job_contracts.job_id'),
            'remote_counts': grouped('jobs.remote_policy'),
            'daily_counts': daily,
            'source_counts': grouped('jobs.source'),
        }

    def get_pending(self):
        """Fail closed: every existing notification state excludes a job."""
        rows = self.conn.execute('''SELECT jobs.* FROM jobs
            WHERE NOT EXISTS (SELECT 1 FROM notifications WHERE notifications.job_id=jobs.id)
            ORDER BY jobs.id''').fetchall()
        result = []
        for row in rows:
            item = json.loads(row['raw_offer_json'])
            item.update(dict(row))
            item['analysis'] = json.loads(row['analysis_json'])
            item['skills'] = [dict(name=skill['name'], evidence=json.loads(skill['evidence_json']))
                              for skill in self.conn.execute('SELECT * FROM job_skills WHERE job_id=? ORDER BY name', (row['id'],))]
            item['contract_types'] = [contract[0] for contract in self.conn.execute('''SELECT DISTINCT normalized
                FROM job_contracts WHERE job_id=? AND normalized IS NOT NULL ORDER BY normalized''', (row['id'],))]
            result.append(item)
        return result

    def reserve_notifications(self, job_ids, batch_id):
        """Reserve a whole batch before delivery; overlap rolls back every row."""
        ids = list(dict.fromkeys(job_ids))
        if not ids or not batch_id:
            return False
        try:
            with self.conn:
                self.conn.execute('BEGIN IMMEDIATE')
                if self.conn.execute('SELECT 1 FROM notifications WHERE batch_id=? LIMIT 1', (batch_id,)).fetchone():
                    return False
                now = _now()
                self.conn.executemany('''INSERT INTO notifications
                    (job_id,batch_id,status,reserved_at,updated_at) VALUES(?,?,'reserved',?,?)''',
                    [(job_id, batch_id, now, now) for job_id in ids])
            return True
        except sqlite3.IntegrityError:
            return False

    def list_reserved_batches(self):
        """Execution IDs awaiting authoritative delivery reconciliation."""
        return [row[0] for row in self.conn.execute(
            "SELECT DISTINCT batch_id FROM notifications WHERE status='reserved' ORDER BY batch_id")]

    def mark_notifications(self, batch_id, status, receipts=None, error=None):
        if status not in {'sent', 'uncertain', 'failed'}:
            raise ValueError('Invalid notification status')
        with self.conn:
            self.conn.execute('''UPDATE notifications SET status=?,receipts_json=?,error=?,updated_at=?
                WHERE batch_id=? AND status='reserved' ''',
                (status, _json(receipts) if receipts is not None else None, error, _now(), batch_id))

    def close(self):
        self.conn.close()

    def has_seen(self, source, source_id):
        row = self.conn.execute('SELECT decision FROM seen WHERE source=? AND source_id=?',
                                (source, str(source_id))).fetchone()
        return bool(row and row['decision'] in {'rejected', 'matched', 'evaluated'})

    def _mark_seen(self, offer, decision, reason):
        self.conn.execute('''INSERT INTO seen
            (source,source_id,url,title,published_at,published_date,decision,reason,evaluated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source,source_id) DO NOTHING''',
            (offer['source'], str(offer['source_id']), offer['url'], offer.get('title'),
             offer.get('published_at'), str(offer.get('published_date') or '') or None,
             decision, reason, _now()))

    def mark_seen(self, offer, decision, reason=''):
        # Errors are deliberately absent from the early terminal index.
        if decision in {'error', 'failed', 'retryable'}:
            return
        if decision not in {'rejected', 'matched', 'evaluated'}:
            raise ValueError('Unknown terminal decision')
        with self.conn:
            self._mark_seen(_record(offer), decision, reason)


CONTRACT_ALIASES = {
    'cdi': 'permanent', 'permanent': 'permanent', 'cdd': 'fixed_term', 'fixed term': 'fixed_term',
    'fixed_term': 'fixed_term', 'freelance': 'freelance', 'indépendant': 'freelance',
    'contractor': 'freelance', 'intérim': 'temporary', 'temporary': 'temporary',
    'stage': 'internship', 'internship': 'internship', 'alternance': 'apprenticeship',
    'apprenticeship': 'apprenticeship', 'portage salarial': 'umbrella', 'umbrella': 'umbrella',
}
SKILL_ALIASES = {
    'js': 'JavaScript', 'javascript': 'JavaScript', 'react': 'React', 'react.js': 'React',
    'reactjs': 'React', 'aws': 'AWS', 'amazon web services': 'AWS',
    'k8s': 'Kubernetes', 'kubernetes': 'Kubernetes',
    'ts': 'TypeScript', 'typescript': 'TypeScript', 'nodejs': 'Node.js', 'node.js': 'Node.js',
    'postgres': 'PostgreSQL', 'postgresql': 'PostgreSQL', 'gcp': 'GCP', 'google cloud platform': 'GCP',
    'python': 'Python', 'docker': 'Docker', 'vue.js': 'Vue', 'vue': 'Vue',
    'ms azure': 'Azure', 'microsoft azure': 'Azure', 'azure': 'Azure',
}

HYBRID_ROLES = frozenset({'full_stack', 'devops', 'cloud', 'platform', 'ai', 'front_end', 'back_end'})


def matches(analysis, remote_hint=None):
    """Reject explicit onsite metadata before considering analyzed policy."""
    if remote_hint == 'onsite':
        return False
    policy = analysis.get('remote_policy')
    return policy == 'full' or (policy == 'hybrid' and analysis.get('role_category') in HYBRID_ROLES)
