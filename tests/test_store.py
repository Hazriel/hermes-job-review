import importlib
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def storage():
    spec = importlib.util.find_spec('store')
    assert spec is not None, 'store module is not implemented'
    return importlib.import_module('store')


class MatchingTests(unittest.TestCase):
    def test_remote_policy_and_allowed_hybrid_roles(self):
        module = storage()
        for role in ['full_stack', 'devops', 'cloud', 'platform', 'ai', 'front_end', 'back_end', 'other']:
            with self.subTest(role=role):
                self.assertTrue(module.matches({'remote_policy': 'full', 'role_category': role}))
                self.assertEqual(module.matches({'remote_policy': 'hybrid', 'role_category': role}), role != 'other')
        for policy in ['onsite', 'unknown', None, 'garbage']:
            self.assertFalse(module.matches({'remote_policy': policy, 'role_category': 'ai'}))
        self.assertFalse(module.matches({'remote_policy': 'full', 'role_category': 'ai'}, remote_hint='onsite'))


def offer(source='board-a', source_id='42', url=None, **overrides):
    value = dict(source=source, source_id=source_id, url=url or f'https://example.test/{source}/{source_id}',
                 title='Engineer', published_at='2026-10-05T09:00:00+02:00', published_date='2026-10-05',
                 employer='Company', location='Paris', contracts_raw=['CDI'], description='PRIVATE FULL DESCRIPTION',
                 salary='60k EUR', daily_rate=None, remote_hint='full', raw_detail={'text': 'original'})
    value.update(overrides)
    return value


def analysis(**overrides):
    value = dict(remote_policy='full', role_category='other', contract_types=['permanent'], seniority=None,
                 skills=[], remote_evidence='remote', role_evidence='Engineer', summary='Useful role')
    value.update(overrides)
    return value


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/home/hazriel/.hermes/cache/scratch')
        self.path = Path(self.temp.name) / 'jobs.sqlite3'
        module = storage()
        self.assertTrue(hasattr(module, 'Store'), 'Store is not implemented')
        self.store = module.Store(self.path)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)

    def test_common_skill_aliases_have_one_canonical_row_per_job(self):
        names = ['TS', 'TypeScript', 'NodeJS', 'Node.js', 'Postgres', 'PostgreSQL', 'GCP', 'Google Cloud Platform',
                 'Python', 'python', 'Docker', 'docker', 'Vue.js', 'Vue', 'MS Azure', 'Microsoft Azure']
        job_id = self.store.save_match(offer(), analysis(skills=[{'name': name, 'evidence': name} for name in names]))
        rows = self.store.conn.execute('SELECT name FROM job_skills WHERE job_id=? ORDER BY name', (job_id,)).fetchall()
        self.assertEqual([row[0] for row in rows], ['Azure', 'Docker', 'GCP', 'Node.js', 'PostgreSQL', 'Python', 'TypeScript', 'Vue'])

    def test_analysis_contract_codes_are_normalized_without_double_counting(self):
        job_id = self.store.save_match(offer(contracts_raw=['CDI', 'exotic']), analysis(contract_types=['cdi', 'permanent', 'exotic', 'CDD', 'fixed_term']))
        rows = self.store.conn.execute('SELECT raw,normalized FROM job_contracts WHERE job_id=? ORDER BY raw', (job_id,)).fetchall()
        self.assertEqual([tuple(row) for row in rows], [('CDI', 'permanent'), ('[analysis] fixed_term', 'fixed_term'), ('exotic', None)])
        stats = self.store.weekly_stats(date(2026, 10, 5), date(2026, 10, 12))
        self.assertEqual(stats['contract_counts'], [{'name': 'fixed_term', 'count': 1}, {'name': 'permanent', 'count': 1}, {'name': 'raw: exotic', 'count': 1}])

    def test_terminal_notification_results_cannot_be_overwritten(self):
        for status in ['sent', 'uncertain', 'failed']:
            with self.subTest(status=status):
                job_id = self.store.save_match(offer(source_id=status), analysis())
                self.store.reserve_notifications([job_id], status)
                self.store.mark_notifications(status, status, receipts={'original': True}, error='original')
                self.store.mark_notifications(status, 'uncertain' if status == 'sent' else 'sent', error='later')
                row = self.store.conn.execute('SELECT status,receipts_json,error FROM notifications WHERE job_id=?', (job_id,)).fetchone()
                self.assertEqual(row['status'], status)
                self.assertEqual(row['error'], 'original')
                self.assertIn('original', row['receipts_json'])

    def test_reserved_batches_are_queryable_until_reconciled(self):
        self.assertTrue(hasattr(self.store, 'list_reserved_batches'), 'list_reserved_batches is not implemented')
        self.assertEqual(self.store.list_reserved_batches(), [])
        one = self.store.save_match(offer(), analysis())
        two = self.store.save_match(offer(source_id='43'), analysis())
        self.assertTrue(self.store.reserve_notifications([one, two], 'cron-execution-1'))
        self.assertEqual(self.store.list_reserved_batches(), ['cron-execution-1'])
        self.store.close()
        self.store = storage().Store(self.path)
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.list_reserved_batches(), ['cron-execution-1'])
        self.store.mark_notifications('cron-execution-1', 'sent', receipts={'execution_id': 'cron-execution-1'})
        self.assertEqual(self.store.list_reserved_batches(), [])
        self.assertEqual(self.store.get_pending(), [])

    def test_run_logging_metadata_and_status_survive_reopen(self):
        self.assertTrue(hasattr(self.store, 'start_run'), 'run API is not implemented')
        self.assertIsNone(self.store.get_meta('weekly:2026-10-05'))
        self.store.set_meta('weekly:2026-10-05', 'reserved')
        self.store.set_meta('weekly:2026-10-05', 'sent')
        run = self.store.start_run('review', '2026-10-09T12:00:00+00:00')
        self.store.finish_run(run, 'completed', {'found': 4, 'matched': 1})
        bad = self.store.start_run('review', '2026-10-09T13:00:00+00:00')
        self.store.finish_run(bad, 'failed', {'found': 0}, error='unavailable')
        self.store.mark_seen(offer(source_id='reject'), 'rejected', 'onsite')
        self.store.save_match(offer(), analysis())
        stats = self.store.stats()
        self.assertEqual({key: stats[key] for key in ['seen', 'matched', 'pending', 'runs', 'rejected']},
                         {'seen': 2, 'matched': 1, 'pending': 1, 'runs': 2, 'rejected': 1})
        self.assertEqual(stats['last_run']['status'], 'failed')
        self.assertEqual(stats['last_run']['counts'], {'found': 0})
        self.assertEqual(stats['last_run']['error'], 'unavailable')
        self.assertIsNotNone(stats['last_run']['finished_at'])
        self.store.close()
        self.store = storage().Store(self.path)
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.get_meta('weekly:2026-10-05'), 'sent')
        self.assertEqual(self.store.stats()['runs'], 2)

    def test_weekly_counts_use_publication_window_distinct_canonical_skills(self):
        self.assertTrue(hasattr(self.store, 'weekly_stats'), 'weekly_stats is not implemented')
        skills = [{'name': name, 'evidence': name} for name in ['React.js', 'React', 'AWS', 'Amazon Web Services', 'K8s', 'Kubernetes']]
        self.store.save_match(offer(), analysis(skills=skills))
        self.store.save_match(offer(source='board-b', source_id='1', published_date='2026-10-11', contracts_raw=['CDD', 'exotic']),
                              analysis(remote_policy='hybrid', role_category='cloud', contract_types=['fixed_term'],
                                       skills=[{'name': 'React', 'evidence': 'React'}]))
        for identity, publication in [('before', '2026-10-04'), ('after', '2026-10-12'), ('missing', None)]:
            self.store.save_match(offer(source_id=identity, published_date=publication), analysis())
        stats = self.store.weekly_stats(date(2026, 10, 5), date(2026, 10, 12))
        self.assertEqual(stats['total'], 2)
        self.assertEqual(stats['top_skills'], [{'name': 'React', 'count': 2}, {'name': 'AWS', 'count': 1}, {'name': 'Kubernetes', 'count': 1}])
        self.assertEqual(stats['contract_counts'], [{'name': 'fixed_term', 'count': 1}, {'name': 'permanent', 'count': 1}, {'name': 'raw: exotic', 'count': 1}])
        self.assertEqual(stats['remote_counts'], [{'name': 'full', 'count': 1}, {'name': 'hybrid', 'count': 1}])
        self.assertEqual(stats['daily_counts'], [{'date': '2026-10-05', 'count': 1}, {'date': '2026-10-11', 'count': 1}])
        self.assertEqual(stats['source_counts'], [{'name': 'board-a', 'count': 1}, {'name': 'board-b', 'count': 1}])
        self.assertEqual(self.store.weekly_stats(date(2020, 1, 1), date(2020, 1, 8))['total'], 0)
        with self.assertRaises(ValueError):
            self.store.weekly_stats(date(2026, 10, 12), date(2026, 10, 5))

    def test_notifications_are_atomically_reserved_and_fail_closed(self):
        self.assertTrue(hasattr(self.store, 'get_pending'), 'notification API is not implemented')
        one = self.store.save_match(offer(), analysis())
        two = self.store.save_match(offer(source_id='43'), analysis())
        pending = self.store.get_pending()
        self.assertEqual([x['id'] for x in pending], [one, two])
        self.assertEqual(pending[0]['analysis']['remote_policy'], 'full')
        self.assertEqual(pending[0]['contracts_raw'], ['CDI'])
        self.assertTrue(self.store.reserve_notifications([one], 'batch-1'))
        peer = storage().Store(self.path)
        self.addCleanup(peer.close)
        self.assertFalse(peer.reserve_notifications([two, one], 'batch-2'))
        self.assertEqual([x['id'] for x in peer.get_pending()], [two])
        self.store.mark_notifications('batch-1', 'uncertain', error='network timeout')
        self.assertFalse(peer.reserve_notifications([one], 'batch-3'))
        self.assertTrue(peer.reserve_notifications([two], 'batch-4'))
        peer.mark_notifications('batch-4', 'sent', receipts={'message_ids': [111]})
        self.assertEqual(self.store.get_pending(), [])
        rows = self.store.conn.execute('SELECT job_id,status,receipts_json,error FROM notifications ORDER BY job_id').fetchall()
        self.assertEqual(rows[0]['status'], 'uncertain')
        self.assertEqual(rows[0]['error'], 'network timeout')
        self.assertIn('111', rows[1]['receipts_json'])
        three = self.store.save_match(offer(source_id='44'), analysis())
        self.assertTrue(self.store.reserve_notifications([three], 'batch-5'))
        self.store.mark_notifications('batch-5', 'failed', error='definite failure')
        self.assertEqual(self.store.get_pending(), [])
        self.assertFalse(self.store.reserve_notifications([9999], 'missing'))
        with self.assertRaises(ValueError):
            self.store.mark_notifications('batch-5', 'invalid')

    def test_identity_is_immutable_idempotent_and_source_scoped(self):
        first = self.store.save_match(offer(), analysis())
        self.assertEqual(self.store.save_match(offer(title='Changed', url='https://example.test/new'), analysis()), first)
        self.assertEqual(self.store.conn.execute('SELECT title,url FROM jobs WHERE id=?', (first,)).fetchone()[:],
                         ('Engineer', offer()['url']))
        alias = offer(source_id='alias', url=offer()['url'])
        self.assertEqual(self.store.save_match(alias, analysis()), first)
        self.assertTrue(self.store.has_seen('board-a', 'alias'))
        second = self.store.save_match(offer(source='board-b', url=offer()['url']), analysis())
        self.assertNotEqual(first, second)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 2)

    def test_save_match_keeps_provenance_and_relational_contracts(self):
        self.assertTrue(hasattr(self.store, 'save_match'), 'save_match is not implemented')
        data = analysis(skills=[{'name': 'JS', 'evidence': 'JS required'}, {'name': 'JavaScript', 'evidence': 'JavaScript'}],
                        contract_types=['permanent', 'freelance'], provider='local', model='test')
        job_id = self.store.save_match(offer(contracts_raw=['CDI', 'Freelance']), data)
        self.assertTrue(self.store.has_seen('board-a', '42'))
        row = self.store.conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        self.assertEqual(row['description'], 'PRIVATE FULL DESCRIPTION')
        import json
        self.assertEqual(json.loads(row['analysis_json']), data)
        self.assertEqual(json.loads(row['raw_offer_json'])['raw_detail'], {'text': 'original'})
        contracts = self.store.conn.execute('SELECT raw, normalized FROM job_contracts WHERE job_id=? ORDER BY raw', (job_id,)).fetchall()
        self.assertEqual([tuple(x) for x in contracts], [('CDI', 'permanent'), ('Freelance', 'freelance')])
        skills = self.store.conn.execute('SELECT name FROM job_skills WHERE job_id=?', (job_id,)).fetchall()
        self.assertEqual([x[0] for x in skills], ['JavaScript'])

    def test_rejections_are_small_durable_and_source_scoped(self):
        self.assertEqual(self.store.conn.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(self.store.conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertFalse(self.store.has_seen('board-a', '42'))
        self.store.mark_seen(offer(), 'rejected', 'onsite')
        self.assertTrue(self.store.has_seen('board-a', '42'))
        self.assertFalse(self.store.has_seen('board-b', '42'))
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
        row = dict(self.store.conn.execute('SELECT * FROM seen').fetchone())
        self.assertNotIn('description', row)
        self.assertNotIn('raw_detail', row)
        self.store.close()
        self.store = storage().Store(self.path)
        self.addCleanup(self.store.close)
        self.assertTrue(self.store.has_seen('board-a', '42'))
        self.store.mark_seen(offer(source_id='retry'), 'error', 'transient')
        self.assertFalse(self.store.has_seen('board-a', 'retry'))


if __name__ == '__main__':
    unittest.main()
