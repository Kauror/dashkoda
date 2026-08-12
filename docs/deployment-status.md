# Deployment status

This file exists because the rest of the documentation used to describe
`dash.orgusaar.ee` as merely planned, and that is no longer true. It records
what actually runs, what this repository owns, and what is still outstanding.

## What is running

DashKoda runs as a **development/pilot deployment**:

- reachable at `https://dash.orgusaar.ee`;
- Docker on Unraid, two application containers (`web` and `db`);
- PostgreSQL data persists on the host, outside the container;
- an existing Cloudflare Tunnel fronts it;
- the deployed build carries the legal-work feed, the public Koda.ee feeds and
  the canonical Excel event programme, so it now shows **real Chamber data**
  rather than only empty states.

## What this repository owns

Only the application: Django code, the Compose application stack, the image, CI
and the documentation here.

It does **not** own, configure or change:

- Cloudflare, its tunnel, its routes or its access rules;
- DNS;
- the Unraid host, its shares or its mounts;
- `cloudflared`, which is managed separately from the DashKoda Compose stack.

No tunnel token, Cloudflare credential, production `.env`, PIN, PIN hash or
other production value is ever committed. The checked-in `.env.example`
contains placeholders only.

## What is not done

The earlier deployment is a **sequencing deviation**. It does not mean the
planned operations milestone (PR-09) is complete. Still outstanding:

- a rehearsed restore **over the production database**;
- rollback tooling;
- Unraid deployment configuration held in this repository;
- failure alerting for the legal feed.

Two of these have since moved. A nightly database backup is installed on the
pilot host — a dump at 00:30 UTC that the script verifies before keeping and
that prunes only its own nightly archives — and on 2026-08-05 an archive was
restored into a throwaway database and checked: 39 tables, one current snapshot
per feed, and every declared count equal to what the tables actually held. It
took 103 seconds. [operations-runbook.md](operations-runbook.md) records the
procedure and the numbers.

That proves the archives are usable. It does not prove a recovery: nobody has
restored over the live database, and there is still no rehearsed way back to a
previous release. A backup you can read is not yet a recovery you can perform.

Until the rest exists, the deployment should be treated as a pilot rather than
as a hardened production service.

## Known risk

**The deployed environment is currently also the development/pilot
environment.** There is no separate staging. A change reaches the same place
people are looking at. This used to be accepted on the grounds that the
dashboard held no business data — **that ground is gone**. The legal-work feed,
the public Koda.ee feeds and the event programme are connected and carry real
Chamber information, which is the point at which this document said the
arrangement stops being acceptable. A separate staging environment, and
Cloudflare Access in front of the tunnel, are now overdue rather than
anticipated.

## How the host expresses Tallinn time

Every schedule below is described in `Europe/Tallinn`, but the pilot host cannot
express it: its `/etc/localtime` is absent and both the clock and `crond` run on
UTC. Each job is therefore installed as a **pair** of UTC entries, and the
script's own time guard runs only the occurrence that is the intended Tallinn
time:

- summer (EEST, UTC+3): the `04:xx` UTC entry runs, the `05:xx` one skips;
- winter (EET, UTC+2): the `05:xx` UTC entry runs, the `04:xx` one skips.

The skipped occurrence is a no-op. A literal `07:00` entry would fire at 10:00
Tallinn, which is why the templates in `ops/unraid/` are not installed verbatim.

The pairs are held on the Unraid flash drive so they survive a reboot, and they
are applied into the system crontab rather than into a user crontab. This
repository ships the script templates only; it installs no schedule.

## What the legal-work feed changes here

It is the first module carrying **real Chamber information**, which changes the
risk picture even though it changes nothing operationally in this repository.

Still true: this repository makes no server, Cloudflare, DNS or tunnel change
and performs no deployment, and the 07:00 job exists here only as a script
template. An administrator has since installed that template on the pilot host.

Newly required for a deployment that actually syncs, via the **MVP public-link
route**:

- one environment variable, `OIGUSLOOME_PUBLIC_URL`, held only in the server's
  own environment file and treated as a credential;
- a host schedule on `Europe/Tallinn`, created by an administrator.

That is the whole list. **No Entra application, Microsoft credential, rclone,
Power Automate, webhook or upload endpoint is required**, and no new volume is
needed because this route keeps no permanent copy of the workbook.

The Microsoft Graph route was retired, so its five variables, its Entra
application and its tenant admin consent are no longer needed by anything.

**The schedule is installed** on the pilot host, at 05:30 `Europe/Tallinn` as the
UTC pair described above.

- Public link: the download, URL handling, XLSX validation and temporary-file
  cleanup were verified against the live link across more than one published
  revision of the workbook, and **the end-to-end import has since completed
  there** — the job's log records both `imported` and `unchanged` runs. It had
  needed two things outside this repository: a PostgreSQL instance to publish
  into, and a workbook published with its `tbl_oigusloome` Excel Table intact,
  because uploading through Excel Online strips it. The log also carries earlier
  failed runs from before those were satisfied. See
  [legal-work-feed.md](legal-work-feed.md).

The exact post-deployment commands are in
[legal-work-feed.md](legal-work-feed.md).

## What the public Koda.ee feeds change here

Three further sources — the public member directory, the news RSS feed and the
events calendar — need **no credential at all**. They are anonymous, read-only
public endpoints, so a deployment that syncs them requires only a host schedule
at 05:40 `Europe/Tallinn` — five minutes after the legal-work job, so the two
never contend and each keeps its own readable log — created by an administrator
from
[`ops/unraid/sync_koda_public.sh.example`](../ops/unraid/sync_koda_public.sh.example).
On the pilot host that is the UTC pair described above:

```text
5 4 * * *
5 5 * * *
```

No new environment variable, no new volume and no new container. Nothing is
retained on disk: the collectors keep no raw response and their artifacts are
metadata only.

**This schedule is installed too**, and the collectors are running: the job's log
records `imported` and `unchanged` runs. See
[koda-public-feeds.md](koda-public-feeds.md).

The events calendar is now a **supplementary** source within this schedule. The
dashboard's event figures and its event history come from the canonical Excel
programme instead, so this job no longer feeds the Sündmused page or the
overview's event cell.

## What the event programme changes here

The Excel event programme is the source of truth for the Sündmused page, the
overview's event figures and the shell's event domain. It needs one environment
variable, `EVENT_PROGRAMME_PUBLIC_URL`, holding the view-only sharing link — a
bearer-style secret that stays in the server environment and reaches no log, audit
summary, command output or database row. No new volume and no new container.

One host schedule, at 05:30 `Europe/Tallinn` after the 06:30 workbook
publication — on the pilot host the UTC pair described above — from
[`ops/unraid/sync_event_programme.sh.example`](../ops/unraid/sync_event_programme.sh.example).

**Both the variable and the schedule are installed**, and the feed has been
accepted in production: the full acceptance sequence — dry run, first import,
unchanged re-run, count verification against `DASH_CONTROL`, page checks, and
only then the schedule — was completed in the order set out in
[event-programme-feed.md](event-programme-feed.md). The variable holds the
sharing link in the server's environment file alone; it is in no log, no command
output and no database row, and it is not in this repository.

This is the change that retired the known risk above from a future condition to
a present one.

## What the internal membership history changes here

**No schedule and no new configuration.** This dataset has no remote source, so
there is nothing to poll, no credential to add, no volume to mount and no cron
entry to create. Future board reports are typed by a staff user in the admin.

It does add one **one-time operator task**, not yet performed. The approved
package is copied to a temporary path on the server, imported, and the copy can
then be removed — the registered artifact carries the content identity and the
application never needs the file again.

```bash
docker compose exec -T web python manage.py import_membership_history --package /run/imports/dashkoda-membership-history-import-package.zip --dry-run --json
```

```bash
docker compose exec -T web python manage.py import_membership_history --package /run/imports/dashkoda-membership-history-import-package.zip --json
```

```bash
docker compose exec -T web python manage.py import_membership_history --package /run/imports/dashkoda-membership-history-import-package.zip --json
```

The dry run validates and publishes nothing. The second call imports. The third
must report `unchanged`; if it does not, stop and investigate rather than
re-running.

The package is not committed to this repository and must not be. It is
transferred to the server by an administrator and deleted afterwards. See
[internal-membership-history.md](internal-membership-history.md).

## What the manual visibility metrics change here

**No schedule, no credential and no new configuration.** The newsletter and
social audience figures have no remote source, so there is nothing to poll, no
key to add, no volume to mount and no cron entry to create.

One migration:

```bash
docker compose exec web python manage.py migrate visibility
```

It creates three tables and touches no existing one. The five manual data sources
register themselves on first use, so nothing has to be seeded.

### Google Analytics: connected, collecting, not yet backfilled

GA4 was connected on **2026-08-09**. The section below used to say it was
deliberately disabled; that stopped being true, and a deployment document that
describes the opposite of the deployment is worse than one that says nothing.

What is configured on the host:

| | |
| --- | --- |
| `GA4_PROPERTY_ID` | set, from the environment |
| `GA4_CREDENTIALS_FILE` | set, key mounted read-only |
| Service account | created, `analytics.readonly` only |
| Wrapper on the host | `/mnt/user/appdata/dashkoda/sync_ga4.sh` |
| Cron entry | UTC pair `15 2` / `15 3`, hour guard `05` — 05:15 Tallinn |
| Live acceptance | performed against the real property |

The schedule is generated from `ops/unraid/generate_examples.py` like every other
job, and `tests/core/test_ops_wrappers.py` fails if the wrapper, the cron pair
and the runbook drift apart.

What the scheduled job does is **reconcile the last eight completed days**, not
fetch yesterday: GA4 revises recent days for several days after they end. See
[website-analytics.md](website-analytics.md) for the model, the metric semantics
and the operator commands.

**The historical backfill has not been run.** The property carries data from
2023-06-16 (about 1 151 days), and importing it is a one-time manual command,
deliberately not something a schedule does:

```bash
docker compose -f compose.yaml -f compose.unraid.yaml exec web \
  python manage.py sync_ga4 --start-date 2023-06-16 --end-date <yesterday> --json
```

Run it in pieces, checking `ga4_status` between them. It is resumable by
re-running: a day already published produces nothing.

GA4 is **not** counted in the dashboard's global freshness row. That denominator
is the four wired modules. It was excluded while GA4 was disabled, so that a
source nobody had connected could not report the deployment as permanently one
short of healthy; now that it is connected, including it is a reasonable change
and a separate one.

The first real figures are typed by an authorised staff user after deployment at
`/admin/data-entry/visibility/new/`. **No production figure is committed to this
repository**, and none was entered during development.

**No server, Cloudflare, DNS, tunnel or schedule change.**

### Smaily: connected and collecting

The newsletter audiences were connected on **2026-08-10**. Two scheduled jobs
run: `sync_smaily` reads each list's current size, and `sync_smaily_campaigns`
catalogues completed sends with their aggregate statistics.

What is configured on the host:

| | |
| --- | --- |
| `SMAILY_SUBDOMAIN`, `SMAILY_API_USERNAME`, `SMAILY_API_PASSWORD` | set, from the environment |
| Cron entries | `sync_smaily` 05:20 Tallinn, `sync_smaily_campaigns` 05:25 |
| Live acceptance | performed against the real account |

Two things are worth knowing before touching this job.

**The three variables must be named under `environment:` in `compose.yaml`, not
only set in the environment file.** Compose reads `.env` for interpolation, so a
variable never referenced there does not reach the container and both commands
report every setting missing however correct the file is. That cost one
deployment round trip.

**There is no backfill for list sizes and there cannot be one.** Smaily reports
what a list holds now, so the history begins on the day collection started and a
missed day is unrecoverable — which is why `SmailyAudienceSnapshot` is never
pruned. The campaign catalogue is different and *can* be rebuilt from the API.

The credential deserves care beyond the usual: Smaily's API users have no
permission model, so the account that reads a list can also send campaigns and
delete subscribers. That the integration cannot write is a property of our code
rather than of the credential — see `apps/visibility/smaily.py`, which is the
only module that issues a request, whose method is a literal `GET` and whose
endpoint is a lookup into a fixed set. **No production figure, segment name or
credential is committed to this repository.**

**No server, Cloudflare, DNS or tunnel change; the two cron entries were
installed by an administrator.**

## What the current-topic matching changes here

**No environment variable, no volume, no container and no server-side change.**
The pipeline needs no credential — the Koda.ee listing is an anonymous,
read-only public endpoint on the existing host allowlist — and it retains no
file: the artifact is metadata only.

What it does change is the interface. A `matched` decision makes a legal topic a
link to its Koda.ee consultation page on `/oigusloome/` and on the overview card;
everything else stays plain text. The address is resolved at read time from
PostgreSQL, so **no page render contacts Koda.ee**.

The intended times are 05:45 `Europe/Tallinn` for `sync_legal_current_topics`
and 05:50 for `match_legal_current_topics`, both after the 05:30 workbook job and
the other public feeds. They are **two separate jobs**, each with its own
wrapper, flock file and log, from
[`ops/unraid/sync_legal_current_topics.sh.example`](../ops/unraid/sync_legal_current_topics.sh.example)
and
[`ops/unraid/match_legal_current_topics.sh.example`](../ops/unraid/match_legal_current_topics.sh.example).
On the pilot host each is installed as the UTC pair described above.

A failure of either command cannot affect the legal workbook synchronisation,
the current legal snapshot or the three existing public feeds: separate source,
separate advisory lock, separate transaction. When either fails, the previous
catalogue and match snapshots stay in storage, `/oigusloome/` keeps rendering,
and topics whose links no longer satisfy the current-snapshot rules simply return
to plain text.

Until both commands have been run on the deployment there is no catalogue and no
match snapshot, so every topic renders as plain text exactly as before. The
global freshness denominator stays four; the catalogue is not a fifth source.
See [legal-current-topic-matching.md](legal-current-topic-matching.md) for the
acceptance steps.

## What the archive fallback changes here

**No environment variable, no volume, no container and no server-side change**,
and no credential: the archive is another anonymous public endpoint on the
existing Koda.ee host allowlist, retaining no file.

Two additional jobs, intended at 06:00 and 06:15 `Europe/Tallinn`, from
[`ops/unraid/sync_legal_archived_topics.sh.example`](../ops/unraid/sync_legal_archived_topics.sh.example)
and
[`ops/unraid/match_legal_archived_topics.sh.example`](../ops/unraid/match_legal_archived_topics.sh.example).
On the pilot host each is installed as the UTC pair described above.

**The initial backfill is run by hand before the schedules are enabled.** The
archive holds about eleven hundred entries across a decade; the first pass reads
its whole 143-page index plus a year of detail pages, over several bounded runs
using `--full --max-detail-pages N`, until the output reports
`"backfill_complete": true`. `--full` never goes into the daily job.

An archive failure cannot affect the legal workbook sync, the current-listing
catalogue or matcher, the event programme or the public feeds: separate source,
separate advisory lock, separate transaction. The global freshness denominator
stays four — the archive is not a fifth source.

## What the opinion document catalogue changes here

The first feed that needs **server-side storage**, and the first whose source is
private rather than a public endpoint. Two host directories and two bind mounts:

| | Host path | Container path | Mode |
|---|---|---|---|
| Source inbox | `/mnt/user/appdata/dashkoda/opinions/source` | `/data/opinions/source` | **read-only** |
| Managed store | `/mnt/user/appdata/dashkoda/opinions/store` | `/data/opinions/store` | read-write |

Both are bind mounts in `compose.unraid.yaml`, so **`compose.unraid.yaml` must be
backed up before it is edited** and both Compose files must be used for every
command, as for every other operation on this host. Adding them recreates `web`
only; the database container and every named volume are untouched.

No environment variable is required — both roots default correctly — and no
credential exists, because there is nothing remote to authenticate to.

**The managed store is not covered by the PostgreSQL backup and cannot be
reconstructed from it**: the database holds text and metadata, not bytes. The
store must be backed up alongside the database dump, and both archives in
`opinions/bootstrap-archive/` retained as the evidence the catalogue was derived
from.

The initial import is run by hand over several bounded runs using
`--max-documents N` until the output stops reporting `"result": "partial"`. A
partial build publishes nothing, so the pilot can be interrupted at any point
without leaving a half-catalogue current.

`sync_legal_opinion_documents` **is scheduled**, at 06:20 `Europe/Tallinn` as
the usual UTC pair (`20 3` / `20 4` with an `06` hour guard). It became worth
running daily once the source stopped being a static archive: since the
2025+2026 activation the source root holds loose PDFs in year folders, so a new
document appears simply by being placed there.

`verify_legal_opinion_store` remains unscheduled and is run by hand.

**The cloud-to-server step is not yet automated.** OneDrive is where staff
maintain the documents, but nothing copies them to this host automatically. A
new PDF must be placed in `opinions/source/onedrive/<year>/` over the existing
administrative SSH path. Authenticated OneDrive mirroring is a later piece of
work; until it exists, do not describe this feed as end-to-end automatic.

A catalogue failure cannot affect any other feed: separate source, separate
advisory lock, separate transaction, and a failure leaves the previous catalogue
current. The global freshness denominator stays four — opinion documents are not
a fifth dashboard source, and documents arrive irregularly, so a quiet week is an
ordinary week rather than a stale feed.

Phase 1 serves nothing. No viewer route, no resource page and no PDF endpoint
exist yet, and no legal topic links to a document.

## What opinion matching changes here

**No new environment variable, no new mount, no new volume and no credential.**
Matching reads PostgreSQL and the managed store that the catalogue phase already
mounts, and writes only its own tables.

Two new authenticated routes exist — the resource page and the protected
document endpoint — and both sit behind the ordinary viewer gate. The public
allowlist is unchanged: `/sisene/`, `/health/live/`, `/health/ready/` and
`/robots.txt`. The managed store gains no static, media or public route; the
document view is the only way a private PDF reaches a browser.

Migration `0006` is additive. It adds one field to the blob table, backfills an
opaque identifier for every existing row in batches, then tightens it to unique
— safe on a table that already holds documents, which the pilot's does.

`match_legal_opinion_documents` **is scheduled**, at 06:30 `Europe/Tallinn` as
the usual UTC pair (`30 3` / `30 4` with an `06` hour guard), installed after
production acceptance showed zero false primary links. It runs ten minutes after
the catalogue job, so a document that arrived overnight is catalogued before the
matcher considers it.

A matching failure cannot affect any other feed, and leaves the previous match
snapshot current. Global freshness stays four.

## What PR-05 changed here

Nothing operationally. PR-05 adds the source, artifact, import-registry and
audit models and a private storage location for original files. It adds no
importer, no real data, no scheduled job and no server-side change. Its only
deployment-adjacent requirement is a persistent private directory for source
artifacts; Compose provides one through the `source_artifacts` named volume, and
`SOURCE_ARTIFACT_ROOT` must be set in any non-Compose deployment.
