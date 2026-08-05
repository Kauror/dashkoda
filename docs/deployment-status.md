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
pilot host — a dump at 02:30 UTC that the script verifies before keeping and
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

**The schedule is installed** on the pilot host, at 07:00 `Europe/Tallinn` as the
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
at 07:05 `Europe/Tallinn` — five minutes after the legal-work job, so the two
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

One host schedule, at 07:00 `Europe/Tallinn` after the 06:30 workbook
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

Two settings are declared and **optional**: `GA4_PROPERTY_ID` and
`GA4_CREDENTIALS_FILE`. Both may stay unset indefinitely — the application
starts, every page renders and nothing contacts Google without them. The
scheduled `sync_ga4` command can collect one completed day of website traffic
once the deployment supplies the property ID and a mounted **read-only**
service-account key (`ops/unraid/sync_ga4.sh.example` is the schedule
template). Live acceptance against the real property has not been performed,
and the website slot claims a connection only after an observation has actually
been published.

The first real figures are typed by an authorised staff user after deployment at
`/admin/data-entry/visibility/new/`. **No production figure is committed to this
repository**, and none was entered during development.

**No server, Cloudflare, DNS, tunnel or schedule change.**

## What PR-05 changed here

Nothing operationally. PR-05 adds the source, artifact, import-registry and
audit models and a private storage location for original files. It adds no
importer, no real data, no scheduled job and no server-side change. Its only
deployment-adjacent requirement is a persistent private directory for source
artifacts; Compose provides one through the `source_artifacts` named volume, and
`SOURCE_ARTIFACT_ROOT` must be set in any non-Compose deployment.
