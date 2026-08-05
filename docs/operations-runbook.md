# Operations runbook

What exists, what has actually been exercised, and what to do when something
breaks. Every procedure below that is marked **verified** was run against the
pilot host on 2026-08-05 and the real numbers are recorded with it. Everything
else is marked as untested, because a procedure nobody has run is a hypothesis.

This does not make the operations milestone complete. See "Still missing".

---

## The database backup

A nightly `pg_dump` runs on the Unraid host at **02:30 UTC**, installed from the
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

| | restored (02:30 UTC) | production at the time |
| --- | --- | --- |
| public tables | 39 | 39 |
| event snapshots / current | 1 / 1 | 3 / 1 |
| declared vs actual events | 1186 = 1186 | 1188 = 1188 |
| legal snapshots / current | 5 / 1 | 6 / 1 |
| declared vs actual records | 607 = 607 | 606 = 606 |

The differences are correct, not drift: the backup is a point-in-time capture
from 02:30 UTC, and both feeds imported after it. Two events were added during
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
