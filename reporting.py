"""Plain-text digests and publication-week reports (Europe/Paris)."""

import json
from datetime import timedelta
from zoneinfo import ZoneInfo

PARIS = ZoneInfo('Europe/Paris')


def _text(value, limit=160, default='Unknown'):
    text = ' '.join(str(value).split()) if value is not None else ''
    text = text or default
    return text if len(text) <= limit else text[:limit - 1] + '…'


def render_offer(job):
    """Concise plain text; provenance/full descriptions stay in storage."""
    analysis = job.get('analysis') or json.loads(job.get('analysis_json') or '{}')
    def field(name):
        return job.get(name) or analysis.get(name)
    policy = field('remote_policy') or 'unknown'
    remote = {'full': 'Fully remote', 'hybrid': 'Hybrid', 'onsite': 'Onsite', 'unknown': 'Unknown remote policy'}.get(policy, 'Unknown remote policy')
    contracts = job.get('contracts_raw') or job.get('contract_types') or analysis.get('contract_types') or []
    skills = job.get('skills') or analysis.get('skills') or []
    skill_names = list(dict.fromkeys(skill['name'] if isinstance(skill, dict) else str(skill) for skill in skills))
    compensation = []
    for label, key in [('Salary', 'salary'), ('Daily rate', 'daily_rate')]:
        if field(key) is not None:
            compensation.append(f'{label}: {_text(field(key), 100)}')
    return '\n'.join([
        _text(job.get('title')),
        f"Company: {_text(field('employer'), 100)} | Location: {_text(field('location'), 100)}",
        f"{remote} | Role: {_text(field('role_category'), 50)} | Contract: {_text(', '.join(contracts), 160)}",
        ' | '.join(compensation) or 'Compensation: not stated',
        'Skills: ' + _text(', '.join(skill_names[:10]), 300, 'not stated'),
        'Posted: ' + _text(job.get('published_date') or job.get('published_at'), 40, 'not stated'),
        job.get('url') or 'URL: not provided',
    ])


def _units(text):
    return len(text.encode('utf-16-le')) // 2


def render_digest(jobs, now):
    """Return <=3500 UTF-16-unit chunks, splitting only between offers.

    Input duplicate database IDs are suppressed. Pathological direct URLs are
    never truncated: an oversize offer raises before any delivery is attempted.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    unique = {}
    for job in jobs:
        if 'id' not in job:
            raise ValueError('Digest offers require their database id')
        unique.setdefault(job['id'], job)
    header = f"Job digest — {now.astimezone(PARIS).date().isoformat()} (Paris)\nTotal: {len(unique)} matching offers"
    chunks = []
    current = header
    for number, job in enumerate(unique.values(), 1):
        block = f"{number}. [ID {job['id']}]\n{render_offer(job)}"
        if _units(block) > 3500:
            raise ValueError(f"Offer {job['id']} exceeds the safe message size")
        candidate = current + '\n\n' + block if current else block
        if _units(candidate) > 3500:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def render_weekly(stats, start, end, previous=None):
    """Describe observed matching offers, never infer market-wide demand."""
    if start >= end:
        raise ValueError('The publication window must have start < end')
    total = stats['total']
    lines = [f"Weekly jobs — {start.isoformat()} to {(end - timedelta(days=1)).isoformat()} (Paris)",
             f'{total} matching observed offers (publication dates).']
    if previous is None:
        lines.append('No baseline: first week or no comparable previous observation; coverage may be partial.')
    else:
        baseline = previous['total']
        delta = total - baseline
        change = f'{delta:+d} offers'
        change += f' ({delta / baseline * 100:+.1f}%)' if baseline else ' (percentage unavailable: zero baseline)'
        lines.append('Change versus previous observed week: ' + change + '.')
    lines.append('Top skills: distinct offers mentioning each skill / all matching observed offers.')
    top = sorted(stats.get('top_skills', []), key=lambda skill: (-skill['count'], skill['name']))[:10]
    for skill in top:
        count = skill['count']
        percent = count / total * 100 if total else 0
        lines.append(f"{_text(skill['name'], 80)}: {count}/{total} offers ({percent:.1f}%)")
    if not top:
        lines.append('No skill observations in this window.')
    for title, key, label in [('Contracts', 'contract_counts', 'name'), ('Remote policy', 'remote_counts', 'name'),
                              ('Sources', 'source_counts', 'name'), ('Publication days', 'daily_counts', 'date')]:
        values = stats.get(key) or []
        if values:
            entries = [f"{_text(item[label], 80)}: {item['count']}" for item in values[:10]]
            if len(values) > 10:
                entries.append(f'{len(values) - 10} additional categories')
            lines.append(title + ': ' + '; '.join(entries))
    lines.append('Coverage: collected matching offers only; not market-wide. Collection gaps, missing publication dates, and first-week startup can undercount. Contract categories may overlap.')
    return '\n'.join(lines)


def previous_week(now):
    """Return last completed Paris calendar week, end Monday exclusive."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    today = now.astimezone(PARIS).date()
    end = today - timedelta(days=today.weekday())
    return end - timedelta(days=7), end
