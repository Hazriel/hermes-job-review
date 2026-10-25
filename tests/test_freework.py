"""Adapter regression tests. freework_live_* are saved public HTTP 200 pages.
All other payloads constructed by these tests are explicitly synthetic fixtures.
"""
import importlib
import json
import math
import unittest
from datetime import date, datetime
from unittest.mock import patch
from urllib.error import HTTPError, URLError


def synthetic_job(identity=1, timestamp='2026-10-09T12:00:00+02:00', **fields):
    """Small synthetic fixture, never represented as live job data."""
    return dict(id=identity, title='Synthetic job', publishedAt=timestamp,
                description='<p>Work &amp; learn</p>', cardProps={'to': '/fr/tech-it/job-mission/dev/synthetic-' + str(identity)}, **fields)


def synthetic_nuxt(data, next_url=None):
    """Encode a tiny synthetic Nuxt devalue graph, with actual integer refs."""
    values = [['ShallowReactive', 1], {'data': 2}, ['ShallowReactive', 3], None]

    def encode(value):
        index = len(values)
        values.append(None)
        if isinstance(value, dict):
            values[index] = {key: encode(item) for key, item in value.items()}
        elif isinstance(value, list):
            values[index] = [encode(item) for item in value]
        else:
            values[index] = value
        return index

    values[3] = {key: encode(value) for key, value in data.items()}
    link = '<link rel="next" href="' + next_url + '">' if next_url else ''
    return link + '<script id="__NUXT_DATA__" type="application/json">' + json.dumps(values).replace('<', '\\u003c') + '</script>'


def synthetic_listing(jobs, page=1, total=None, next_url=None):
    return synthetic_nuxt({'jobs-search-/fr/tech-it/jobs': {
        'jobs': jobs, 'page': page, 'totalItems': len(jobs) if total is None else total,
        'filters': {'sort': 'date', 'freshness': 'less_than_7_days'},
    }}, next_url)

from pathlib import Path

try:
    freework = importlib.import_module('freework')
except ModuleNotFoundError:
    freework = None

FIXTURES = Path(__file__).parent / 'fixtures'


class ListingTests(unittest.TestCase):
    def test_live_listing_extracts_only_jobs_with_canonical_metadata(self):
        self.assertIsNotNone(freework, 'Free-Work adapter must exist')
        page = freework.parse_listing((FIXTURES / 'freework_live_listings.html').read_text())
        self.assertEqual(page['total_items'], 9089)
        self.assertEqual(page['page'], 1)
        self.assertEqual(page['next_url'], 'https://www.free-work.com/fr/tech-it/jobs?page=2')
        self.assertEqual(len(page['offers']), 16)
        job = page['offers'][0]
        self.assertEqual(job['source'], 'free-work')
        self.assertEqual(job['source_id'], '680586')
        self.assertEqual(job['title'], 'Project Manager OT - Énergie (H/F)')
        self.assertEqual(job['url'], 'https://www.free-work.com/fr/tech-it/job-mission/community-manager-social-media-manager/project-manager-ot-energie-h-f')
        self.assertEqual(job['published_at'], '2026-10-09T17:06:19+02:00')
        self.assertEqual(job['published_date'], '2026-10-09')
        self.assertEqual(job['remote_raw'], 'partial')
        self.assertEqual(job['remote_hint'], 'hybrid')
        self.assertEqual(job['contracts_raw'], ['contractor', 'permanent'])
        self.assertEqual(job['skills_raw'], ['Gestion de projet', 'SCADA'])
        self.assertEqual(job['employer'], 'Linkup Partner')
        self.assertEqual(job['location'], 'Île-de-France, France')
        self.assertEqual(job['salary'], '71k-85k\xa0€')
        self.assertEqual(job['daily_rate'], '530-630\xa0€')
        self.assertIn('Diplôme d’ingénieur', job['listing_description'])
        self.assertNotIn('<p>', job['listing_description'])
        self.assertEqual(job['raw_listing']['id'], 680586)

    def test_live_second_page_maps_remote_metadata_not_description_guesses(self):
        page = freework.parse_listing((FIXTURES / 'freework_live_page2.html').read_text(), freework.BASE_URL + '?page=2')
        jobs = {offer['source_id']: offer for offer in page['offers']}
        self.assertEqual(page['page'], 2)
        self.assertEqual(page['next_url'], freework.BASE_URL + '?page=3')
        self.assertEqual(jobs['678128']['remote_hint'], 'full')
        self.assertEqual(jobs['678128']['remote_raw'], 'full')
        self.assertEqual(jobs['680472']['remote_hint'], 'onsite')
        self.assertEqual(jobs['680472']['remote_raw'], 'none')
        self.assertEqual(jobs['680562']['remote_hint'], 'unknown')
        self.assertEqual(jobs['680562']['remote_raw'], '')

    def test_description_plain_text_excludes_executable_markup(self):
        raw = synthetic_job()
        raw['description'] = '<p>Safe&nbsp; words &lt;T&gt;</p><script>BAD SCRIPT</script><style>BAD CSS</style><!--BAD COMMENT--><ul><li>One</li><li>Two</li></ul>'
        offer = freework.parse_listing(synthetic_listing([raw]))['offers'][0]
        self.assertEqual(offer['listing_description'], 'Safe words <T>\nOne\nTwo')

    def test_malformed_metadata_types_are_not_coerced_into_valid_offers(self):
        for fields in ({'title': None}, {'title': '  '}, {'contracts': 'contractor'},
                       {'contracts': [1]}, {'skills': ['Python']}, {'remoteMode': []},
                       {'annualSalary': 55000}, {'dailySalary': False}, {'company': 'Employer'},
                       {'location': {'label': 12}}, {'publishedAt': None}, {'description': 12}):
            raw = synthetic_job()
            raw.update(fields)
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    freework.parse_listing(synthetic_listing([raw]))
        raw = synthetic_job(remoteMode='new-unknown-mode', contracts=['literal value'])
        offer = freework.parse_listing(synthetic_listing([raw]))['offers'][0]
        self.assertEqual(offer['remote_raw'], 'new-unknown-mode')
        self.assertEqual(offer['remote_hint'], 'unknown')
        self.assertEqual(offer['contracts_raw'], ['literal value'])

    def test_nullable_optional_metadata_stays_explicitly_unknown(self):
        raw = synthetic_job(remoteMode=None, contracts=None, skills=None, company=None, location=None,
                            annualSalary=None, dailySalary=None)
        offer = freework.parse_listing(synthetic_listing([raw]))['offers'][0]
        self.assertEqual(offer['remote_hint'], 'unknown')
        self.assertEqual(offer['remote_raw'], '')
        self.assertEqual(offer['contracts_raw'], [])
        self.assertEqual(offer['skills_raw'], [])
        self.assertEqual(offer['employer'], '')
        self.assertEqual(offer['location'], '')
        self.assertIsNone(offer['salary'])
        self.assertIsNone(offer['daily_rate'])
        self.assertEqual(offer['raw_listing'], raw)

    def test_source_must_acknowledge_requested_freshness_prefilter(self):
        url = freework.BASE_URL + '?sort=date&freshness=less_than_7_days'
        for filters in ({'sort': 'date'}, {'sort': 'date', 'freshness': 'less_than_24_hours'}):
            with self.subTest(filters=filters):
                html = synthetic_nuxt({'jobs-search-/fr/tech-it/jobs': {'jobs': [], 'page': 1, 'totalItems': 0, 'filters': filters}})
                with self.assertRaisesRegex(ValueError, 'freshness'):
                    freework.parse_listing(html, url)

    def test_pagination_must_advance_one_page_without_changing_search_filters(self):
        base = freework.BASE_URL + '?sort=date&freshness=less_than_7_days'
        for next_url in (base, base + '&page=3', freework.BASE_URL + '?page=2', base + '&page=2&remote=full'):
            with self.subTest(next_url=next_url):
                with self.assertRaisesRegex(ValueError, 'pagination|page|order'):
                    freework.parse_listing(synthetic_listing([synthetic_job()], total=2, next_url=next_url), base)
        with self.assertRaisesRegex(ValueError, 'page'):
            freework.parse_listing(synthetic_listing([synthetic_job()], page=1), base + '&page=2')

    def test_invalid_listing_schema_raises_not_empty_success(self):
        invalid = [
            '<html>Blocked</html>', '<script id="__NUXT_DATA__">[]</script>',
            '<script id="__NUXT_DATA__">{}</script>', synthetic_nuxt({'unrelated': {}}),
        ]
        for changes in ({'jobs': None}, {'page': 0}, {'page': '1'}, {'totalItems': -1}, {'totalItems': '12'}):
            listing = {'jobs': [], 'page': 1, 'totalItems': 0, 'filters': {}}
            listing.update(changes)
            invalid.append(synthetic_nuxt({'jobs-search-/fr/tech-it/jobs': listing}))
        for html in invalid:
            with self.subTest(html=html[:90]):
                with self.assertRaises(ValueError):
                    freework.parse_listing(html)
        for identity in (None, True, 'bad-id', -1):
            with self.subTest(identity=identity):
                with self.assertRaises(ValueError):
                    freework.parse_listing(synthetic_listing([synthetic_job(identity)]))

    def test_listing_links_cannot_escape_freework_job_routes(self):
        bad_urls = ['https://evil.example/job', '//evil.example/job',
                    'https://www.free-work.com.evil.example/fr/tech-it/jobs',
                    'https://user@www.free-work.com/fr/tech-it/job-mission/dev/id',
                    'http://www.free-work.com/fr/tech-it/job-mission/dev/id',
                    'https://www.free-work.com/fr/tech-it/blog/article',
                    'https://www.free-work.com:8443/fr/tech-it/job-mission/dev/id']
        for bad in bad_urls:
            with self.subTest(link=bad):
                job = synthetic_job()
                job['cardProps']['to'] = bad
                with self.assertRaisesRegex(ValueError, 'URL'):
                    freework.parse_listing(synthetic_listing([job]))
                with self.assertRaisesRegex(ValueError, 'URL'):
                    freework.parse_listing(synthetic_listing([synthetic_job()], next_url=bad))
        with self.assertRaisesRegex(ValueError, 'URL'):
            freework.parse_listing(synthetic_listing([synthetic_job()]), 'https://evil.example/fr/tech-it/jobs')

    def test_publication_dates_require_offset_and_use_paris_calendar(self):
        with self.assertRaisesRegex(ValueError, 'timezone'):
            freework.parse_listing(synthetic_listing([synthetic_job(timestamp='2026-10-09T12:00:00')]))
        cases = {
            '2026-10-08T22:30:00Z': '2026-10-09',
            '2026-01-08T23:30:00+00:00': '2026-01-09',
            '2026-10-09T00:30:00+05:00': '2026-10-08',
            '2026-03-29T01:30:00Z': '2026-03-29',
        }
        for timestamp, expected in cases.items():
            with self.subTest(timestamp=timestamp):
                job = freework.parse_listing(synthetic_listing([synthetic_job(timestamp=timestamp)]))['offers'][0]
                self.assertEqual(job['published_date'], expected)
                self.assertEqual(job['published_at'], timestamp)

    def test_invalid_devalue_references_and_cycles_raise_structural_errors(self):
        html = synthetic_listing([synthetic_job()])
        values = json.loads(html[html.index('>') + 1:html.index('</script>')])
        index = next(i for i, value in enumerate(values) if isinstance(value, dict) and 'publishedAt' in value)
        for ref in (-7, True, 99999):
            graph = json.loads(json.dumps(values))
            graph[index]['publishedAt'] = ref
            with self.subTest(ref=ref):
                with self.assertRaisesRegex(ValueError, 'Nuxt|reference'):
                    freework.parse_listing('<script id="__NUXT_DATA__">' + json.dumps(graph) + '</script>')
        graph = json.loads(json.dumps(values))
        graph[index]['loop'] = index
        with self.assertRaisesRegex(ValueError, 'cycle|Cyclic'):
            freework.parse_listing('<script id="__NUXT_DATA__">' + json.dumps(graph) + '</script>')
        values[0][0] = 'UnsupportedRootWrapper'
        with self.assertRaisesRegex(ValueError, 'Nuxt|wrapper'):
            freework.parse_listing('<script id="__NUXT_DATA__">' + json.dumps(values) + '</script>')

    def test_devalue_special_references_do_not_index_from_end(self):
        html = synthetic_listing([synthetic_job(sentinels=[])])
        start = html.index('>') + 1
        values = json.loads(html[start:html.index('</script>')])
        node = next(v for v in values if isinstance(v, dict) and 'sentinels' in v)
        values[node['sentinels']] = [-1, -2, -3, -4, -5, -6]
        html = '<script id="__NUXT_DATA__">' + json.dumps(values) + '</script>'
        result = freework.parse_listing(html)['offers'][0]['raw_listing']['sentinels']
        self.assertIsNone(result[0])
        self.assertIsNone(result[1])
        self.assertTrue(math.isnan(result[2]))
        self.assertEqual(result[3:5], [math.inf, -math.inf])
        self.assertEqual(math.copysign(1, result[5]), -1)


class FetchTests(unittest.TestCase):
    def test_malformed_url_tokens_are_rejected_not_repaired_before_lookup(self):
        for token in (None, '', ' ' + freework.BASE_URL, freework.BASE_URL + '\n'):
            with self.subTest(token=token):
                with patch('freework._opener.open') as opener:
                    with self.assertRaisesRegex(ValueError, 'URL'):
                        freework.fetch_html(token)
                    opener.assert_not_called()

    def test_fetch_timeout_must_be_positive_and_finite(self):
        with patch('freework._opener.open') as opener:
            for timeout in (None, 0, -1, True, float('nan'), float('inf')):
                with self.subTest(timeout=timeout):
                    with self.assertRaises(ValueError):
                        freework.fetch_html(freework.BASE_URL, timeout=timeout)
            opener.assert_not_called()

    def test_retry_after_dates_are_honored_and_long_waits_are_not_shortened(self):
        from email.message import Message
        from email.utils import formatdate
        from io import BytesIO
        failure = HTTPError(freework.BASE_URL, 429, 'Rate limited', {'Retry-After': formatdate(1700000010, usegmt=True)}, None)
        self.addCleanup(failure.close)
        response = BytesIO(b'ok')
        response.headers = Message()
        with patch('freework._opener.open', side_effect=[failure, response]), patch('freework.time.time', return_value=1700000000), patch('freework.time.sleep') as sleep:
            self.assertEqual(freework.fetch_html(freework.BASE_URL), 'ok')
        self.assertGreaterEqual(sleep.call_args.args[0], 10)
        failure = HTTPError(freework.BASE_URL, 429, 'Rate limited', {'Retry-After': '120'}, None)
        self.addCleanup(failure.close)
        with patch('freework._opener.open', side_effect=failure) as opener, patch('freework.time.sleep') as sleep:
            with self.assertRaises(HTTPError):
                freework.fetch_html(freework.BASE_URL)
        self.assertEqual(opener.call_count, 1)
        sleep.assert_not_called()

    def test_http_failures_close_bodies_and_preserve_original_exceptions(self):
        from io import BytesIO
        for status, attempts in ((404, 1), (503, 3)):
            with self.subTest(status=status):
                body = BytesIO(b'error body')
                failure = HTTPError(freework.BASE_URL, status, 'Failure', {}, body)
                self.addCleanup(failure.close)
                with patch('freework._opener.open', side_effect=failure) as opener, patch('freework.time.sleep'):
                    with self.assertRaises(HTTPError) as caught:
                        freework.fetch_html(freework.BASE_URL)
                self.assertIs(caught.exception, failure)
                self.assertEqual(opener.call_count, attempts)
                self.assertTrue(body.closed)

    def test_fetch_rejects_foreign_origin_before_transport_or_redirect(self):
        with patch('freework._opener.open') as opener:
            with self.assertRaisesRegex(ValueError, 'URL'):
                freework.fetch_html('https://evil.example/fr/tech-it/jobs')
            self.assertEqual(opener.call_count, 0)
        self.assertTrue(hasattr(freework, '_FreeWorkRedirectHandler'), 'safe redirect policy missing')
        req = freework.request.Request(freework.BASE_URL)
        handler = freework._FreeWorkRedirectHandler()
        with self.assertRaisesRegex(ValueError, 'URL'):
            handler.redirect_request(req, None, 302, 'Found', {}, 'https://evil.example/job')
        redirected = handler.redirect_request(req, None, 302, 'Found', {}, freework.BASE_URL + '?page=2')
        self.assertEqual(redirected.full_url, freework.BASE_URL + '?page=2')

    def test_transient_get_failures_are_retried_with_bounded_backoff(self):
        self.assertTrue(hasattr(freework, 'time'), 'bounded retry/backoff missing')
        from email.message import Message
        from io import BytesIO
        for error in (HTTPError(freework.BASE_URL, 503, 'Unavailable', {}, None),
                      HTTPError(freework.BASE_URL, 429, 'Rate limited', {'Retry-After': '4'}, None),
                      URLError('connection lost'), TimeoutError('timeout')):
            with self.subTest(error=type(error).__name__, code=getattr(error, 'code', None)):
                response = BytesIO(b'<html>ok</html>')
                response.headers = Message()
                with patch('freework._opener.open', side_effect=[error, error, response]) as opener, patch('freework.time.sleep') as sleep:
                    self.assertEqual(freework.fetch_html(freework.BASE_URL), '<html>ok</html>')
                self.assertEqual(opener.call_count, 3)
                self.assertEqual(sleep.call_count, 2)
                self.assertTrue(all(1 <= call.args[0] <= 60 for call in sleep.call_args_list))
                if getattr(error, 'code', None) == 429:
                    self.assertTrue(all(call.args[0] >= 4 for call in sleep.call_args_list))

    def test_fetch_html_uses_public_get_timeout_and_decodes_charset(self):
        self.assertTrue(hasattr(freework, 'fetch_html'), 'public HTML fetcher missing')
        from email.message import Message
        from io import BytesIO
        response = BytesIO('Offre française'.encode('utf-8'))
        response.headers = Message()
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        with patch('freework._opener.open', return_value=response) as opener:
            html = freework.fetch_html(freework.BASE_URL, timeout=17)
        self.assertEqual(html, 'Offre française')
        args, kwargs = opener.call_args
        self.assertEqual(args[0].get_method(), 'GET')
        self.assertEqual(args[0].full_url, freework.BASE_URL)
        self.assertTrue(args[0].get_header('User-agent'))
        self.assertEqual(kwargs['timeout'], 17)


class DetailTests(unittest.TestCase):
    def test_removed_or_incomplete_detail_raises_recoverable_error(self):
        offer = freework.parse_listing(synthetic_listing([synthetic_job()]))['offers'][0]
        job = synthetic_job()
        job['description'] = ''
        with self.assertRaisesRegex(ValueError, 'description|detail'):
            freework.fetch_offer(offer, fetcher=lambda url: synthetic_nuxt({'job-posting-test': {'job': job}}))
        with self.assertRaises(ValueError):
            freework.fetch_offer(offer, fetcher=lambda url: '<html>Offer removed</html>')
        failure = HTTPError(offer['url'], 404, 'Removed', {}, None)
        self.addCleanup(failure.close)

        def removed(url):
            raise failure

        with self.assertRaises(HTTPError) as caught:
            freework.fetch_offer(offer, fetcher=removed)
        self.assertIs(caught.exception, failure)

    def test_injected_detail_fetcher_never_receives_an_offsite_url(self):
        offer = freework.parse_listing(synthetic_listing([synthetic_job()]))['offers'][0]
        offer['url'] = 'https://evil.example/fr/tech-it/job-mission/dev/synthetic-1'
        called = []

        def fetch(url):
            called.append(url)
            return synthetic_nuxt({'job-posting-test': {'job': synthetic_job()}})

        with self.assertRaisesRegex(ValueError, 'URL'):
            freework.fetch_offer(offer, fetcher=fetch)
        self.assertEqual(called, [])

    def test_detail_must_identify_exactly_the_requested_live_job(self):
        offer = freework.parse_listing(synthetic_listing([synthetic_job()]))['offers'][0]
        payloads = [
            synthetic_nuxt({'job-posting-test': {'job': synthetic_job(2)}}),
            synthetic_nuxt({'job-posting-one': {'job': synthetic_job()}, 'job-posting-two': {'job': synthetic_job()}}),
            synthetic_nuxt({'unrelated': {}}),
            synthetic_nuxt({'job-posting-test': {'job': synthetic_job(published=False)}}),
        ]
        for html in payloads:
            with self.subTest(html=html[:70]):
                with self.assertRaisesRegex(ValueError, 'identity|detail|published'):
                    freework.fetch_offer(offer, fetcher=lambda url: html)
        refreshed = synthetic_job(timestamp='2026-10-10T12:00:00+02:00', remoteMode='full', contracts=['contractor'],
                                  skills=[{'name': 'Python'}], candidateProfile='<p>Full candidate requirements</p>')
        detail = freework.fetch_offer(offer, fetcher=lambda url: synthetic_nuxt({'job-posting-test': {'job': refreshed}}))
        self.assertEqual(detail['published_at'], offer['published_at'])
        self.assertEqual(detail['published_date'], offer['published_date'])
        self.assertEqual(detail['remote_hint'], 'full')
        self.assertEqual(detail['skills_raw'], ['Python'])
        self.assertEqual(detail['contracts_raw'], ['contractor'])
        self.assertIn('Full candidate requirements', detail['description'])

    def test_live_detail_contains_full_profile_and_environment_without_sidebar(self):
        self.assertTrue(hasattr(freework, 'fetch_offer'), 'detail enrichment missing')
        offer = freework.parse_listing((FIXTURES / 'freework_live_listings.html').read_text())['offers'][0]
        before = json.loads(json.dumps(offer))
        calls = []

        def fetch(url):
            calls.append(url)
            return (FIXTURES / 'freework_live_detail.html').read_text()

        detail = freework.fetch_offer(offer, fetcher=fetch)
        self.assertEqual(calls, [offer['url']])
        self.assertEqual(offer, before)
        for key in ('source', 'source_id', 'url', 'published_at', 'published_date', 'raw_listing'):
            self.assertEqual(detail[key], before[key])
        self.assertIn('Profil recherché', detail['description'])
        self.assertIn('5 ans d’expérience', detail['description'])
        self.assertIn('Connaissances techniques : électricité, énergies renouvelables (ENR), thermique', detail['description'])
        self.assertNotIn('<p>', detail['description'])
        self.assertNotIn('Coup de gueule', detail['description'])
        self.assertEqual(detail['raw_detail']['id'], 680586)
        self.assertEqual(detail['remote_hint'], 'hybrid')
        self.assertEqual(detail['contracts_raw'], ['contractor', 'permanent'])
        self.assertEqual(detail['employer'], 'Linkup Partner')
        self.assertEqual(detail['skills_raw'], ['Gestion de projet', 'SCADA'])


class RecentTests(unittest.TestCase):
    def setUp(self):
        clock = patch('freework.datetime', wraps=datetime)
        clock.start().now.return_value = datetime(2026, 10, 9, 12, tzinfo=freework.PARIS)
        self.addCleanup(clock.stop)

    def test_invalid_calendar_window_is_rejected_before_fetching(self):
        called = []

        def fetch(url):
            called.append(url)
            return synthetic_listing([])

        for lookback in (0, -1, True, 1.5, None):
            with self.subTest(lookback=lookback):
                with self.assertRaises(ValueError):
                    list(freework.iter_recent_offers(date(2026, 10, 9), lookback_days=lookback, fetcher=fetch))
        for today in ('2026-10-09', None, datetime(2026, 10, 9)):
            with self.subTest(today=today):
                with self.assertRaises(ValueError):
                    list(freework.iter_recent_offers(today, fetcher=fetch))
        self.assertEqual(called, [])

    def test_overlapping_offset_pages_dedupe_without_false_exhaustion_error(self):
        base = freework.BASE_URL + '?sort=date&freshness=less_than_7_days'
        job = synthetic_job()
        pages = {
            base: synthetic_listing([job], total=2, next_url=base + '&page=2'),
            base + '&page=2': synthetic_listing([job], page=2, total=2),
        }
        results = list(freework.iter_recent_offers(date(2026, 10, 9), fetcher=pages.__getitem__))
        self.assertEqual([offer['source_id'] for offer in results], ['1'])

    def test_unproven_exhaustion_raises_instead_of_silent_partial_results(self):
        for html in (synthetic_listing([], total=2), synthetic_listing([synthetic_job()], total=2)):
            with self.subTest(html=html[:50]):
                with self.assertRaisesRegex(ValueError, 'empty|pagination|exhaust'):
                    list(freework.iter_recent_offers(date(2026, 10, 9), fetcher=lambda url: html))
        self.assertEqual(list(freework.iter_recent_offers(date(2026, 10, 9), fetcher=lambda url: synthetic_listing([]))), [])
        final = synthetic_listing([synthetic_job()], total=1)
        self.assertEqual(len(list(freework.iter_recent_offers(date(2026, 10, 9), fetcher=lambda url: final))), 1)

    def test_ignored_date_sort_is_not_an_older_boundary(self):
        for filters in ({}, {'sort': 'relevance'}):
            html = synthetic_nuxt({'jobs-search-/fr/tech-it/jobs': {
                'jobs': [synthetic_job(timestamp='2026-10-07T12:00:00+02:00')],
                'page': 1, 'totalItems': 1, 'filters': filters,
            }})
            with self.subTest(filters=filters):
                with self.assertRaisesRegex(ValueError, 'order'):
                    list(freework.iter_recent_offers(date(2026, 10, 9), fetcher=lambda url: html))

    def test_iterator_requests_explicit_date_sort(self):
        visited = []

        def fetch(url):
            visited.append(url)
            return synthetic_listing([])

        self.assertEqual(list(freework.iter_recent_offers(date(2026, 10, 9), fetcher=fetch)), [])
        self.assertEqual(visited, [freework.BASE_URL + '?sort=date&freshness=less_than_7_days'])
        visited.clear()
        self.assertEqual(list(freework.iter_recent_offers(date(2026, 9, 9), fetcher=fetch)), [])
        self.assertEqual(visited, [freework.BASE_URL + '?sort=date'])
        visited.clear()
        self.assertEqual(list(freework.iter_recent_offers(date(2026, 10, 9), lookback_days=30, fetcher=fetch)), [])
        self.assertEqual(visited, [freework.BASE_URL + '?sort=date'])

    def test_promoted_out_of_order_jobs_do_not_hide_recent_offers(self):
        base = freework.BASE_URL + '?sort=date&freshness=less_than_7_days'
        old = synthetic_job(1, '2026-10-07T12:00:00+02:00')
        recent = synthetic_job(2, '2026-10-09T12:00:00+02:00')
        for pages, expected in (
            ({base: synthetic_listing([old, recent], total=2)}, ['2']),
            ({base: synthetic_listing([synthetic_job(1, '2026-10-09T10:00:00+02:00'), recent])}, ['2', '1']),
            ({base: synthetic_listing([old], total=2, next_url=base + '&page=2'),
              base + '&page=2': synthetic_listing([recent], page=2, total=2)}, ['2']),
        ):
            with self.subTest(pages=len(pages)):
                results = list(freework.iter_recent_offers(date(2026, 10, 9), fetcher=pages.__getitem__))
                self.assertEqual([offer['source_id'] for offer in results], expected)

    def test_two_paris_calendar_days_traverse_all_pages_and_dedupe(self):
        self.assertTrue(hasattr(freework, 'iter_recent_offers'), 'recent page traversal missing')
        base = freework.BASE_URL + '?sort=date&freshness=less_than_7_days'
        overlap = synthetic_job(2, '2026-10-08T22:30:00Z')  # October 9 in Paris
        pages = {
            base: synthetic_listing([synthetic_job(9, '2026-10-10T12:00:00+02:00'),
                                     synthetic_job(1, '2026-10-09T12:00:00+02:00'), overlap], total=6, next_url=base + '&page=2'),
            base + '&page=2': synthetic_listing([overlap, synthetic_job(3, '2026-10-08T12:00:00+02:00')], page=2, total=6, next_url=base + '&page=3'),
            base + '&page=3': synthetic_listing([synthetic_job(4, '2026-10-07T12:00:00+02:00')], page=3, total=6, next_url=base + '&page=4'),
            base + '&page=4': synthetic_listing([synthetic_job(5, '2026-10-06T12:00:00+02:00')], page=4, total=6),
        }
        visited = []

        def fetch(url):
            visited.append(url)
            return pages[url]

        results = list(freework.iter_recent_offers(date(2026, 10, 9), fetcher=fetch))
        self.assertEqual([offer['source_id'] for offer in results], ['1', '2', '3'])
        self.assertEqual([offer['published_date'] for offer in results], ['2026-10-09', '2026-10-09', '2026-10-08'])
        self.assertEqual(visited, [base, base + '&page=2', base + '&page=3', base + '&page=4'])


if __name__ == '__main__':
    unittest.main()
