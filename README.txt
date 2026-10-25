FREE-WORK JOB REVIEW

Project: /home/hazriel/.hermes/job-review
Storage: jobs.sqlite3 (SQLite WAL)
Local event log: logs/runs.jsonl
Hourly cron ID: 684f023bd641
Weekly-report cron ID: 186362a1c3fb
Telegram target: Gabriel Bertini, chat 8939372278

BEHAVIOUR

Collect offers published today and yesterday in Europe/Paris, not a rolling UTC 48-hour interval. Reject strictly onsite offers before fetching details or matching roles. Include fully remote offers regardless of role. Include hybrid offers only for full-stack, DevOps, cloud, platform, AI, frontend or backend roles. Unspecified remote policy is excluded conservatively. There are no contract or seniority restrictions.

Free-Work returns structured Nuxt data. Listings may contain promoted/out-of-order dates even with sort=date. The adapter exhausts the source's seven-day freshness result set, then retains only the two requested Paris publication dates. This avoids inferring a false cutoff; only eligible unseen offers receive detail requests and LLM analysis. A complete listing scan can take several minutes.

New non-onsite offer details include the full description, candidate profile and work environment. GPT-6 Luna uses the existing openai-codex subscription, low reasoning, bounded output and four parallel workers. It has no tools and cannot act on offer instructions. Filter decisions and technical skills require literal supporting source quotations; invalid extraction is retried once with the same model. No automatic switch to an expensive model. Authentication/quota errors trip a per-run circuit breaker. Usage tokens and per-offer failures are logged locally. Failed offers remain retryable while in the date window.

Jobs and rejected source identities are permanent. Only matching jobs retain full descriptions and LLM provenance. Source+source_id identities allow later job-board adapters without ID collisions. Contract types are stored in job_contracts with original codes and normalized categories. Technical skill aliases are canonicalized and counted once per job.

Hourly collector uses Hermes script-only scheduling: its own Python code makes the required GPT Luna calls; no main-agent LLM is invoked. Empty stdout sends no message. Scheduler failures are local-only. Both configured scripts run from the active Hermes scripts directory and use the installation's dependency environment via launch.py.

NOTIFICATION SAFETY

A UNIQUE per-job notification reservation is committed before stdout is emitted. The gateway then delivers stdout to Telegram. Reservations are reconciled against the exact Hermes cron execution ID by status and subsequent collections. Confirmed delivered executions become sent. Failed/unknown delivery becomes uncertain and is NOT automatically replayed, prioritizing no duplicate alerts. An interrupted reserved send is also withheld. Review uncertain rows locally before choosing any manual resend.

WEEKLY REPORT

Every Monday at 09:00 Europe/Paris, report the previous Monday-Sunday publication cohort: unique matching offers, top ten technical skills and counts/percentages, contract categories, remote policy, source and publication-day counts. An hourly silent checker makes this DST-safe without changing global Hermes timezone settings; on Monday it can catch up after 09:00 if another collection held the lock. A week is reserved once before gateway delivery. Early weeks are labeled partial; comparisons are only enabled when observation began before the baseline week. SQLite backups are created under backups/YYYY-MM-DD.sqlite3 and retained permanently.

COMMANDS

Status and notification reconciliation (no network job discovery):
  python /home/hazriel/.hermes/job-review/launch.py /home/hazriel/.hermes/job-review/main.py status

Local weekly report preview (does not reserve or send):
  python /home/hazriel/.hermes/job-review/launch.py /home/hazriel/.hermes/job-review/main.py preview-weekly

Run collector through gateway delivery:
  hermes cron run 684f023bd641

Collect locally without emitting/reserving messages (still spends model quota on unseen offers):
  python /home/hazriel/.hermes/job-review/launch.py /home/hazriel/.hermes/job-review/main.py collect --stage

Inspect/pause/resume:
  hermes cron list
  hermes cron pause 684f023bd641
  hermes cron resume 684f023bd641
  hermes cron pause 186362a1c3fb
  hermes cron resume 186362a1c3fb

Run tests:
  cd /home/hazriel/.hermes/job-review
  python -m unittest discover -s tests -v

QUERYABLE TABLES

jobs: publication dates, role, remote policy, company, location, compensation, full description, source identities, raw and analyzed JSON.
job_contracts: job_id, raw, normalized; join with jobs.id.
job_skills: job_id, canonical name, evidence_json.
seen: source, source_id, decision, reason, evaluated_at (early discard index).
notifications: job_id, batch_id (exact cron execution ID), status, receipts_json, error.
runs: kind, start/finish, status, counts_json, error; available even for silent runs.
metadata: reserved weekly cohorts.

SQL examples:
  SELECT j.title,j.url,c.raw,c.normalized FROM jobs j JOIN job_contracts c ON c.job_id=j.id WHERE c.normalized='freelance';
  SELECT s.name,COUNT(DISTINCT j.id) FROM jobs j JOIN job_skills s ON s.job_id=j.id WHERE j.published_date>='2026-10-05' AND j.published_date<'2026-10-12' GROUP BY s.name ORDER BY COUNT(DISTINCT j.id) DESC,s.name LIMIT 10;
  SELECT * FROM runs ORDER BY id DESC LIMIT 10;
  SELECT job_id,batch_id,status,error FROM notifications WHERE status IN ('reserved','uncertain','failed');

LIMITATIONS

The database describes your matching observed segment, not the whole tech market. Source downtime or model quota exhaustion lasting beyond the two-day window can still lose offers. Removed detail pages are logged as errors, not invented. Source pagination is live and offers inserted after the first page are picked up next run. LLM extraction is grounded but not guaranteed infallible. Telegram cannot provide perfect exactly-once acknowledgement after an uncertain network outcome; this installation deliberately fails closed rather than resending automatically.

The gateway must run for execution/delivery. Its service was running during setup and systemd linger was enabled; autostart was disabled in the existing configuration and has not been changed by this project.
