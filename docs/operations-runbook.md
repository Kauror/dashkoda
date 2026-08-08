# Operations runbook

What exists, what has actually been exercised, and what to do when something
breaks. Every procedure below that is marked **verified** was run against the
pilot host on 2026-08-05 and the real numbers are recorded with it. Everything
else is marked as untested, because a procedure nobody has run is a hypothesis.

This does not make the operations milestone complete. See "Still missing".

---

## The database backup

A nightly `pg_dump` runs on the Unraid host at **00:30 UTC**, installed from the
flash drive so it survives a reboot. The wrapper is
`/mnt/user/appdata/dashkoda/backup_db.sh` and it is deliberately careful:

1. dumps to `nightly-<timestamp>.sql.gz.partial`, never straight to the final name;
2. tests the archive with `gzip -t` and discards it on failure;
3. greps the last lines for `PostgreSQL database dump complete` and discards an
   archive without that marker;
4. only then renames `.partial` to its final name;
5. prunes `nightly-*.sql.gz` older than 30 days and **nothing else** — manual
   `pre-*.sql.gz` dumps are left alone;
6. appends one line per run to `logs/backup_db.log`.

Archives live in `/mnt/user/appdata/dashkoda/backups/`. Format is plain SQL,
gzipped — restore with `psql`, not `pg_restore`.

**Verified 2026-08-05.** The first scheduled run produced
`nightly-20260805-023002.sql.gz`, 330 710 bytes compressed / 2 315 712
uncompressed, and the log recorded `exit=0 … 3 nightly archives retained`.

## The morning feed chain

<!-- SCHEDULE:BEGIN -->
<!-- Generated from ops/unraid/generate_examples.py. Do not edit by hand:
     tests/core/test_ops_wrappers.py fails if this drifts from the wrappers. -->

The whole chain runs **05:30–06:50 Europe/Tallinn**, so every figure on the
dashboard is fresh before anyone looks at it at 07:00.

The pilot host cannot express Tallinn time — `/etc/localtime` is absent and both
the clock and `crond` run on UTC — so each job is installed as a **pair** of UTC
entries and the wrapper's own hour guard runs only the occurrence that is the
intended Tallinn hour. The skipped occurrence is a no-op. `Europe/Tallinn` is
missing from the trimmed zoneinfo, so the guards read `Europe/Athens`, which has
identical EET/EEST offsets.

| Tallinn | UTC (summer, EEST) | UTC (winter, EET) | Guard | Job |
| --- | --- | --- | --- | --- |
| **05:30** | `30 2 * * *` | `30 3 * * *` | `05` | `sync_oigusloome_public` |
| **05:35** | `35 2 * * *` | `35 3 * * *` | `05` | `sync_event_programme` |
| **05:40** | `40 2 * * *` | `40 3 * * *` | `05` | `sync_koda_public` |
| **05:45** | `45 2 * * *` | `45 3 * * *` | `05` | `sync_legal_current_topics` |
| **05:50** | `50 2 * * *` | `50 3 * * *` | `05` | `match_legal_current_topics` |
| **06:00** | `0 3 * * *` | `0 4 * * *` | `06` | `sync_legal_archived_topics` |
| **06:15** | `15 3 * * *` | `15 4 * * *` | `06` | `match_legal_archived_topics` |
| **06:20** | `20 3 * * *` | `20 4 * * *` | `06` | `sync_legal_opinion_documents` |
| **06:25** | `25 3 * * *` | `25 4 * * *` | `06` | `sync_public_opinions` |
| **06:30** | `30 3 * * *` | `30 4 * * *` | `06` | `match_legal_opinion_documents` |
| **06:40** | `40 3 * * *` | `40 4 * * *` | `06` | `discover_koda_event_pages` |
| **06:50** | `50 3 * * *` | `50 4 * * *` | `06` | `match_public_event_links` |

Two UTC minutes carry two jobs each. That is not a clash: in any given season
exactly one of the pair passes its hour guard and the other exits immediately.

`sync_public_opinions` is **installed and running** as of 2026-08-08, after
its one-time `--full` historical walk collected 126 pages and 115 public
opinion PDFs. Its scheduled runs are incremental: the listing edge plus a
short refresh overlap, which measured 5 listing pages, 14 detail refreshes and
no downloads once the corpus was complete. The `--full` walk is a one-time
step and is never what the schedule runs — incremental mode refuses to run at
all until a full walk has succeeded.

The two event-link jobs, by contrast, are documented above but **not yet
installed**: `/etc/cron.d/root` holds 23 dashkoda lines (eleven guarded jobs
as a UTC pair each, plus the backup), and `discover_koda_event_pages` and
`match_public_event_links` are not among them.

Order is a dependency order, not a preference. The workbook is first because
every matcher scores against whichever legal snapshot is current when it runs;
each collector precedes the matcher that reads it; and the archive collection
gets fifteen minutes rather than five because a full walk is 143 pages.

The two event-link jobs close the chain. `discover_koda_event_pages` is a
different job from `sync_koda_public --source events`: that one publishes the
upcoming calendar, while this accumulates the addresses of pages for events
that have already happened, so the programme's 2018 rows can be linked. Its
ordinary run reads only unknown and stale pages — a handful of requests. The
**initial backfill is 1,510 pages and is run by hand once**, with
`--full --max-detail-pages N`; it is deliberately larger than any scheduled run
is allowed to be.

The nightly database backup sits outside this chain at **00:30 UTC**, anchored
to UTC rather than to Tallinn — see "The database backup" above.

Run any job by hand with `DASHKODA_FORCE=1` to bypass its hour guard.
<!-- SCHEDULE:END -->

### Checking that last night's backup happened

```bash
ls -l /mnt/user/appdata/dashkoda/backups/nightly-*.sql.gz | tail -3
tail -3 /mnt/user/appdata/dashkoda/logs/backup_db.log
```

A missing archive for last night is a real fault. Investigate before trusting
anything else here.

---

## Restore drill — verified

This restores a backup into a **throwaway database**, proving the archive is
usable without touching what the dashboard is serving. Run it after any change
to the backup script, and periodically regardless.

**Measured 2026-08-05: 103 seconds**, `ON_ERROR_STOP=1`, zero errors on stderr.

```bash
cd /mnt/user/appdata/dashkoda/repo
C="docker compose -f compose.yaml -f compose.unraid.yaml"
A=/mnt/user/appdata/dashkoda/backups/nightly-20260805-023002.sql.gz
S=dashkoda_restore_drill
```

Verify the archive before doing anything with it:

```bash
gzip -t "$A" && echo ok
zcat "$A" | tail -6 | grep -q "PostgreSQL database dump complete" && echo ok
```

Create the scratch database, restore into it, and time it:

```bash
$C exec -T db psql -U dashkoda -d postgres -c "CREATE DATABASE $S;"
zcat "$A" | $C exec -T db psql -U dashkoda -d "$S" -v ON_ERROR_STOP=1 -q
```

Check the restored database is complete and self-consistent — every declared
count must equal what the tables actually hold, and exactly one snapshot per feed
may be current:

```sql
SELECT count(*) FROM information_schema.tables WHERE table_schema='public';
SELECT count(*) FROM event_programme_eventprogrammesnapshot WHERE is_current;
SELECT canonical_event_count FROM event_programme_eventprogrammesnapshot WHERE is_current;
SELECT count(*) FROM event_programme_eventprogrammeitem i
  JOIN event_programme_eventprogrammesnapshot s ON s.id=i.snapshot_id AND s.is_current;
SELECT count(*) FROM legal_work_legalworksnapshot WHERE is_current;
SELECT total_record_count FROM legal_work_legalworksnapshot WHERE is_current;
SELECT count(*) FROM legal_work_legalworkitem i
  JOIN legal_work_legalworksnapshot s ON s.id=i.snapshot_id AND s.is_current;
```

Then remove the scratch database — it is the only thing this drill deletes:

```bash
$C exec -T db psql -U dashkoda -d postgres -c "DROP DATABASE IF EXISTS $S;"
```

### What the 2026-08-05 drill found

| | restored (nightly) | production at the time |
| --- | --- | --- |
| public tables | 39 | 39 |
| event snapshots / current | 1 / 1 | 3 / 1 |
| declared vs actual events | 1186 = 1186 | 1188 = 1188 |
| legal snapshots / current | 5 / 1 | 6 / 1 |
| declared vs actual records | 607 = 607 | 606 = 606 |

The differences are correct, not drift: the backup is a point-in-time capture
from that night's dump, and both feeds imported after it. Two events were added during
the day and one legal record left the set — ordinary movement, and a useful
reminder that the restored figures are *supposed* to differ from live ones.

The drill deleted only `dashkoda_restore_drill`. The `dashkoda` database was
never opened for write, no container was recreated, and all 8 Docker volumes
were present before and after.

---

## Restoring over production — **not tested**

Nobody has restored a backup over the live database, so what follows is a plan,
not a procedure, and it should be rehearsed on a scratch database first.

The shape it would take: stop `web` so nothing writes, rename the live database
aside rather than dropping it, restore the archive into a fresh database under
the production name, start `web`, and only remove the renamed original after the
dashboard has been checked. Keeping the original under another name is the
important part — it makes the restore reversible.

**Do not improvise this during an incident.** Rehearse it, measure it, then
replace this section with what actually happened.

---

## What the logs contain

Every collector logs what it did at `INFO`, under a `dashkoda.*` logger name, to
**stderr** — which the container runtime and each cron wrapper's own log file
already capture. There is nothing extra to configure or collect.

Until `config/settings/base.py` gained an explicit `LOGGING` block, none of it
arrived anywhere: with no such setting Django configures handlers for `django`
and `django.server` only, so a record under `dashkoda.*` had no handler and was
discarded. The scheduled jobs still wrote their JSON summary line, which is why
nothing looked broken — what was missing was everything a collector noticed on
the way to that line.

What you can expect to see:

- which host was read, and how many pages or bytes it took;
- why a run decided the content was unchanged;
- the counts a publication produced.

What is deliberately **not** in a log line, and is tested for in
`tests/core/test_logging.py`:

- a sharing URL, a property ID or a credential path — all bearer-style secrets;
- any part of a response body, a workbook cell, an opinion PDF or its filename;
- an access token.

Third-party loggers stay at `WARNING`. `urllib3` narrates every connection at
`INFO` and `google.auth` narrates credential handling, neither of which belongs
in a cron log.

### Reading them

```bash
# One job's own file, newest last.
tail -n 40 /mnt/user/appdata/dashkoda/logs/sync_oigusloome_public.log

# The container's stream, which carries the INFO records above.
docker compose -f compose.yaml -f compose.unraid.yaml logs --tail 200 web
```

## Session and rate-limit housekeeping

Two maintenance commands exist. **Neither is scheduled**, and neither needs to
be at the current scale.

`purge_viewer_rate_limits` deletes inactive viewer rate-limit buckets older than
30 days. It is scoped to that one table, it preserves a bucket that is currently
locked out, and it is idempotent — a second run matches nothing new. It prints
the number of rows deleted and nothing else.

```bash
docker compose -f compose.yaml -f compose.unraid.yaml exec -T web   python manage.py purge_viewer_rate_limits
```

`clearsessions` is Django's own, and removes expired session rows.

```bash
docker compose -f compose.yaml -f compose.unraid.yaml exec -T web   python manage.py clearsessions
```

**Recommendation, not an installation.** If either is ever scheduled, the free
slots are before 05:30 or after 06:30 Tallinn, outside the feed chain — the same
constraint the GA4 wrapper example describes. Neither has an hour guard, so
either would need one adding, or a UTC-anchored slot like the backup's.

## Still missing

- **Rollback tooling.** There is no command that returns the application to a
  previous release. A rollback today means a manual `git checkout` of the older
  SHA, a rebuild and a container recreation, with no rehearsed sequence.
- **A restore-over-production rehearsal**, as above.
- **Alerting.** The event flow emails on failure; the legal flow has no
  notification of any kind, so a silent legal breakage reaches nobody. Both feeds
  disclose failure in the interface, but only if somebody looks.

Because these are outstanding, the operations milestone is **not complete**, and
`AGENTS.md` still forbids describing it as such.

---

## Deploying a new release

Verified 2026-08-05 when production moved from `7338760` to `62a6223`.

```bash
cd /mnt/user/appdata/dashkoda/repo
git pull --ff-only
BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) GIT_COMMIT=$(git rev-parse --short HEAD) \
  docker compose -f compose.yaml -f compose.unraid.yaml build web
docker compose -f compose.yaml -f compose.unraid.yaml up -d --force-recreate web
docker compose -f compose.yaml -f compose.unraid.yaml exec -T web python manage.py migrate --noinput
```

Both stamps matter: without them the image carries no identity and you cannot
tell from the container what is running. Confirm afterwards:

```bash
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' \
  "$(docker compose -f compose.yaml -f compose.unraid.yaml ps -q web)" | grep DASHKODA_
```

Only `web` is ever recreated. The `db` container and every volume stay as they
are.

---

## Checking the feeds by hand

```bash
cd /mnt/user/appdata/dashkoda/repo
C="docker compose -f compose.yaml -f compose.unraid.yaml"
$C exec -T web python manage.py sync_oigusloome_public --dry-run --json
$C exec -T web python manage.py sync_event_programme --dry-run --json
```

A dry run downloads and validates without publishing, and it performs the same
collapse check the real import does — so it tells you whether the real import
would be accepted rather than passing and failing later.

Never pass a sharing URL on the command line. Neither command accepts one: they
read it from the environment precisely so it cannot enter shell history or a
process listing.
