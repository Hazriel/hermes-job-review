import importlib
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def reporting():
    spec = importlib.util.find_spec('reporting')
    assert spec is not None, 'reporting module is not implemented'
    return importlib.import_module('reporting')


class ReportingTests(unittest.TestCase):
    def test_weekly_report_has_denominators_top_ten_and_coverage_caveat(self):
        module = reporting()
        self.assertTrue(hasattr(module, 'render_weekly'), 'render_weekly is not implemented')
        stats = dict(total=4, top_skills=[{'name': f'Skill{i:02}', 'count': 2} for i in range(12)],
                     contract_counts=[{'name': 'permanent', 'count': 3}, {'name': 'freelance', 'count': 2}],
                     remote_counts=[{'name': 'full', 'count': 3}, {'name': 'hybrid', 'count': 1}],
                     daily_counts=[{'date': '2026-10-05', 'count': 4}], source_counts=[{'name': 'board-a', 'count': 4}])
        start, end = date(2026, 10, 5), date(2026, 10, 12)
        text = module.render_weekly(stats, start, end)
        for expected in ['2026-10-05', '2026-10-11', '4 matching observed offers', 'Skill00: 2/4 offers (50.0%)',
                         'Skill09', 'permanent: 3', 'full: 3', 'board-a: 4', 'No baseline', 'first week', 'not market-wide']:
            self.assertIn(expected, text)
        self.assertNotIn('Skill10', text)
        compared = module.render_weekly(stats, start, end, previous={'total': 6})
        self.assertIn('-2 offers (-33.3%)', compared)
        zero = module.render_weekly(dict(total=0, top_skills=[]), start, end, previous={'total': 0})
        self.assertIn('0 matching observed offers', zero)
        self.assertNotIn('nan', zero)
        self.assertIn('percentage unavailable', zero)

    def test_digest_is_utf16_safe_and_numbers_each_unique_id_once(self):
        module = reporting()
        self.assertTrue(hasattr(module, 'render_digest'), 'render_digest is not implemented')
        now = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
        jobs = [dict(id=i, title='😀' * 1000, employer='Company', location='Paris',
                     url=f'https://example.test/job/{i}', remote_policy='full') for i in range(1, 31)]
        chunks = module.render_digest(jobs + [jobs[0]], now)
        self.assertGreater(len(chunks), 1)
        self.assertIn('Total: 30 matching offers', chunks[0])
        self.assertIn('2026-10-09', chunks[0])
        joined = '\n'.join(chunks)
        for index in range(1, 31):
            self.assertEqual(joined.count(f'{index}. [ID {index}]'), 1)
            self.assertEqual(joined.count(f'https://example.test/job/{index}\n'), 1 if index != 30 else 0)
        for job in jobs:
            self.assertEqual(sum(job['url'] in chunk.splitlines() for chunk in chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 3500)
            self.assertLessEqual(len(chunk.encode('utf-16-le')) // 2, 3500)
        self.assertIn('Total: 0 matching offers', module.render_digest([], now)[0])
        with self.assertRaises(ValueError):
            module.render_digest([dict(id=99, title='Huge URL', url='https://example.test/' + 'a' * 4000)], now)

    def test_offer_formats_saved_and_raw_records_without_full_description(self):
        module = reporting()
        self.assertTrue(hasattr(module, 'render_offer'), 'render_offer is not implemented')
        job = dict(id=7, title='Platform Engineer', employer='Example SA', location='Lyon', salary='65k EUR', daily_rate='600 EUR/day',
                   published_date='2026-10-05', contracts_raw=['CDI'], url='https://example.test/job/7', description='DO NOT INCLUDE',
                   analysis={'remote_policy': 'hybrid', 'role_category': 'platform', 'skills': [{'name': 'Kubernetes', 'evidence': 'K8s'}]})
        text = module.render_offer(job)
        for expected in ['Platform Engineer', 'Example SA', 'Lyon', '65k EUR', '600 EUR/day', 'Hybrid', 'platform', 'CDI', 'Kubernetes', '2026-10-05', job['url']]:
            self.assertIn(expected, text)
        self.assertNotIn('DO NOT INCLUDE', text)
        minimal = module.render_offer({'title': 'No metadata', 'url': 'https://example.test/empty'})
        self.assertNotIn('None', minimal)
        self.assertIn('Unknown', minimal)

    def test_previous_week_uses_paris_monday_and_handles_dst(self):
        module = reporting()
        self.assertEqual(module.previous_week(datetime(2026, 10, 11, 22, 30, tzinfo=timezone.utc)),
                         (date(2026, 10, 5), date(2026, 10, 12)))
        self.assertEqual(module.previous_week(datetime(2026, 3, 30, 0, 30, tzinfo=ZoneInfo('Europe/Paris'))),
                         (date(2026, 3, 23), date(2026, 3, 30)))
        self.assertEqual(module.previous_week(datetime(2026, 10, 26, 0, 30, tzinfo=ZoneInfo('Europe/Paris'))),
                         (date(2026, 10, 19), date(2026, 10, 26)))
        with self.assertRaises(ValueError):
            module.previous_week(datetime(2026, 10, 9))


if __name__ == '__main__':
    unittest.main()
