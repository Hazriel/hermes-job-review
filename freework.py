"""Public, stdlib-only Free-Work job source adapter.

Parse Nuxt's structured job payload rather than unrelated navigation or cards.
"""
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, parse_qs
from urllib import request
from zoneinfo import ZoneInfo


class _FreeWorkRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = _url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = request.build_opener(_FreeWorkRedirectHandler())


def fetch_html(url, timeout=30):
    """Fetch public HTML using a bounded, unauthenticated GET request."""
    if type(timeout) not in (int, float) or not 0 < timeout < float('inf'):
        raise ValueError('Expected a positive finite GET timeout')
    url = _url(url)
    req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; FreeWorkReview/1.0)'})
    for attempt in range(3):
        try:
            with _opener.open(req, timeout=timeout) as response:
                encoding = response.headers.get_content_charset() or 'utf-8'
                return response.read().decode(encoding)
        except (HTTPError, URLError, TimeoutError) as exc:
            if isinstance(exc, HTTPError):
                exc.close()
            if isinstance(exc, HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == 2:
                raise
            delay = 2 ** attempt
            if isinstance(exc, HTTPError):
                retry_after = (exc.headers or {}).get('Retry-After', '')
                retry_delay = 0
                try:
                    retry_delay = float(retry_after) if retry_after.isdigit() else max(
                        0, parsedate_to_datetime(retry_after).timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
                if retry_delay > 60:
                    raise  # Let the caller retry later; never violate Retry-After.
                delay = max(delay, retry_delay)
            time.sleep(delay)


BASE_URL = 'https://www.free-work.com/fr/tech-it/jobs'
PARIS = ZoneInfo('Europe/Paris')


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.payload = []
        self.in_payload = False
        self.next_href = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('id') == '__NUXT_DATA__':
            self.in_payload = True
        if tag == 'link' and 'next' in attrs.get('rel', '').split():
            self.next_href = attrs.get('href')

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_payload = False

    def handle_data(self, data):
        if self.in_payload:
            self.payload.append(data)


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ('p', 'div', 'li', 'br', 'ul', 'ol', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.hidden = max(0, self.hidden - 1)
            return
        if not self.hidden and tag in ('p', 'div', 'li', 'ul', 'ol', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(html):
    parser = _TextParser()
    parser.feed(html or '')
    return '\n'.join(line for line in (re.sub(r'\s+', ' ', line).strip()
                                      for line in ''.join(parser.parts).splitlines()) if line)


def _payload(html):
    parser = _PageParser()
    parser.feed(html)
    values = json.loads(''.join(parser.payload))
    if (not isinstance(values, list) or len(values) < 2
            or not isinstance(values[0], list) or len(values[0]) != 2
            or values[0][0] not in ('ShallowReactive', 'Reactive', 'Ref', 'ShallowRef')
            or type(values[0][1]) is not int or not 0 <= values[0][1] < len(values)
            or not isinstance(values[values[0][1]], dict)
            or 'data' not in values[values[0][1]]):
        raise ValueError('Invalid Free-Work Nuxt root payload')
    memo = {}
    active = set()

    def resolve(index):
        if type(index) is not int or index < -6 or index >= len(values):
            raise ValueError('Invalid Nuxt devalue reference')
        if index < 0:
            specials = {-1: None, -2: None, -3: float('nan'), -4: float('inf'),
                        -5: -float('inf'), -6: -0.0}
            return specials[index]
        if index in active:
            raise ValueError('Cyclic Nuxt job payload cannot be persisted')
        if index in memo:
            return memo[index]
        active.add(index)
        value = values[index]
        if isinstance(value, dict):
            result = {}
            memo[index] = result
            result.update({key: resolve(ref) for key, ref in value.items()})
        elif isinstance(value, list):
            if value and isinstance(value[0], str):
                if value[0] not in ('ShallowReactive', 'Reactive', 'Ref', 'ShallowRef'):
                    raise ValueError('Unsupported Nuxt wrapper: ' + value[0])
                result = resolve(value[1])
            else:
                result = []
                memo[index] = result
                result.extend(resolve(ref) for ref in value)
        else:
            result = value
        active.remove(index)
        memo[index] = result
        return result

    # The root object is wrapped at index zero; resolve only data, not UI state.
    root = values[values[0][1]]
    return resolve(root['data']), parser.next_href


def _url(value, base=BASE_URL, kind=None):
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ValueError('Malformed Free-Work URL token')
    url = urljoin(base, value)
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in ('www.free-work.com', 'free-work.com')
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise ValueError('Unsafe Free-Work URL: ' + url)
    if kind == 'listing' and parts.path != '/fr/tech-it/jobs':
        raise ValueError('Unexpected Free-Work listing URL: ' + url)
    if kind == 'offer' and not re.fullmatch(r'/fr/tech-it/job-mission/[^/]+/[^/]+', parts.path):
        raise ValueError('Unexpected Free-Work offer URL: ' + url)
    return url


def _timestamp(value):
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError('Free-Work publication timestamp must include timezone offset')
    return timestamp


def _offer(job, page_url):
    if not isinstance(job, dict) or type(job.get('id')) is not int or job['id'] <= 0:
        raise ValueError('Invalid Free-Work job identity')
    if not isinstance(job.get('title'), str) or not job['title'].strip():
        raise ValueError('Invalid Free-Work job title')
    if not isinstance(job.get('publishedAt'), str):
        raise ValueError('Invalid Free-Work publication timestamp')
    for field in ('description', 'remoteMode', 'annualSalary', 'dailySalary'):
        if job.get(field) is not None and not isinstance(job[field], str):
            raise ValueError('Invalid Free-Work job field: ' + field)
    contracts = job.get('contracts')
    if contracts is not None and (not isinstance(contracts, list) or
                                  any(not isinstance(item, str) for item in contracts)):
        raise ValueError('Invalid Free-Work contracts metadata')
    skills = job.get('skills')
    if skills is not None and (not isinstance(skills, list) or
                               any(not isinstance(item, dict) or not isinstance(item.get('name'), str)
                                   for item in skills)):
        raise ValueError('Invalid Free-Work skills metadata')
    for field, label in (('company', 'name'), ('location', 'label')):
        value = job.get(field)
        if value is not None and (not isinstance(value, dict) or
                                  (value.get(label) is not None and not isinstance(value[label], str))):
            raise ValueError('Invalid Free-Work job field: ' + field)
    timestamp = job['publishedAt']
    return {
        'source': 'free-work',
        'source_id': str(job['id']),
        'url': _url(job['cardProps']['to'], page_url, kind='offer'),
        'title': job['title'].strip(),
        'published_at': timestamp,
        'published_date': _timestamp(timestamp).astimezone(PARIS).date().isoformat(),
        'remote_hint': {'partial': 'hybrid', 'full': 'full', 'none': 'onsite'}.get(job.get('remoteMode'), 'unknown'),
        'remote_raw': job.get('remoteMode') or '',
        'contracts_raw': job.get('contracts') or [],
        'skills_raw': [skill['name'] for skill in (job.get('skills') or [])],
        'employer': (job.get('company') or {}).get('name') or '',
        'location': (job.get('location') or {}).get('label') or '',
        'salary': job.get('annualSalary'),
        'daily_rate': job.get('dailySalary'),
        'listing_description': _plain(job.get('description', '')),
        'raw_listing': job,
    }


def iter_recent_offers(today: date, lookback_days=2, fetcher=fetch_html):
    """Yield deduplicated, newest-first offers within Paris calendar days.

    Live date-sort pages contain promoted/out-of-order publication timestamps.
    Therefore exhaust the search, never infer a boundary from an old card, and
    sort the complete matching window before yielding anything to the caller.
    """
    if type(today) is not date or type(lookback_days) is not int or lookback_days < 1:
        raise ValueError('Expected a Paris calendar date and positive integer lookback_days')
    lower = today - timedelta(days=lookback_days - 1)
    seen = set()
    observed_count = 0
    matching = []
    url = BASE_URL + '?sort=date'
    # A seven-day source filter safely contains up to six Paris calendar days,
    # including DST transitions. Historical/wider windows remain unfiltered.
    if today == datetime.now(PARIS).date() and lookback_days <= 6:
        url += '&freshness=less_than_7_days'
    while url:
        page = parse_listing(fetcher(url), url)
        observed_count += len(page['offers'])
        if not page['offers'] and page['total_items']:
            raise ValueError('Unexpected empty Free-Work page; cannot confirm exhaustion')
        for offer in page['offers']:
            identity = offer['source_id']
            if identity in seen:
                continue  # Offset pages can overlap as new jobs are published.
            seen.add(identity)
            published = date.fromisoformat(offer['published_date'])
            if lower <= published <= today:
                matching.append(offer)
        if not page['next_url'] and observed_count < page['total_items']:
            raise ValueError('Free-Work pagination exhausted before the advertised total')
        url = page['next_url']
    yield from sorted(matching, key=lambda offer: _timestamp(offer['published_at']), reverse=True)


def fetch_offer(offer, fetcher=fetch_html):
    """Fetch the full job description, candidate profile and work environment."""
    url = _url(offer['url'], kind='offer')
    data, _ = _payload(fetcher(url))
    if not isinstance(data, dict):
        raise ValueError('Invalid Free-Work detail payload')
    candidates = [value.get('job') for key, value in data.items()
                  if key.startswith('job-posting-') and isinstance(value, dict)]
    candidates = [job for job in candidates if isinstance(job, dict)
                  and type(job.get('id')) is int and str(job['id']) == offer['source_id']]
    if len(candidates) != 1:
        raise ValueError('Free-Work detail identity does not uniquely match requested offer')
    job = candidates[0]
    if job.get('published') is False or job.get('status', 'published') != 'published':
        raise ValueError('Free-Work detail is no longer published')
    if not _plain(job.get('description', '')):
        raise ValueError('Free-Work detail full description is absent')
    combined = dict(offer['raw_listing'])
    combined.update(job)
    enriched = dict(offer)
    enriched.update(_offer(combined, offer['url']))
    for key in ('source', 'source_id', 'url', 'published_at', 'published_date', 'raw_listing', 'listing_description'):
        enriched[key] = offer[key]
    sections = [_plain(job.get('description', ''))]
    for field, heading in (('candidateProfile', 'Profil recherché'),
                           ('companyDescription', 'Environnement de travail')):
        text = _plain(job.get(field, ''))
        if text:
            sections.append(heading + '\n' + text)
    enriched['description'] = '\n\n'.join(section for section in sections if section)
    enriched['raw_detail'] = job
    return enriched


def parse_listing(html, page_url=BASE_URL):
    """Return structured offers and the site's explicit next-page link."""
    page_url = _url(page_url, kind='listing')
    data, next_href = _payload(html)
    listing = data.get('jobs-search-/fr/tech-it/jobs') if isinstance(data, dict) else None
    if (not isinstance(listing, dict) or not isinstance(listing.get('jobs'), list)
            or type(listing.get('page')) is not int or listing['page'] < 1
            or type(listing.get('totalItems')) is not int or listing['totalItems'] < 0):
        raise ValueError('Invalid Free-Work listing schema')
    query = parse_qs(urlsplit(page_url).query, keep_blank_values=True)
    if query.get('page', ['1']) != [str(listing['page'])]:
        raise ValueError('Free-Work returned a different page than requested')
    if query.get('sort') == ['date']:
        if listing.get('filters', {}).get('sort') != 'date':
            raise ValueError('Free-Work date order was not acknowledged by source metadata')
    if query.get('freshness'):
        if query['freshness'] != [listing.get('filters', {}).get('freshness')]:
            raise ValueError('Free-Work freshness filter was not acknowledged by source metadata')
    next_url = _url(next_href, page_url, kind='listing') if next_href else None
    if next_url:
        next_query = parse_qs(urlsplit(next_url).query, keep_blank_values=True)
        if next_query.pop('page', None) != [str(listing['page'] + 1)]:
            raise ValueError('Free-Work pagination must advance exactly one page')
        current_filters = {key: value for key, value in query.items() if key != 'page'}
        if next_query != current_filters:
            raise ValueError('Free-Work pagination changed search filters or date order')
    return {
        'offers': [_offer(job, page_url) for job in listing['jobs']],
        'total_items': listing['totalItems'],
        'page': listing['page'],
        'next_url': next_url,
    }
