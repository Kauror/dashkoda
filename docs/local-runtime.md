# Local Docker runtime

## Prepare the environment

Copy the example and replace every placeholder with a local-only value:

```powershell
Copy-Item .env.example .env
```

Never commit `.env`. The checked-in example contains no real credentials.

Generate a local viewer PIN hash using hidden input:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml run --rm web python manage.py generate_viewer_pin_hash
```

Copy only its hash output to `VIEWER_PIN_HASH`. Also replace
`VIEWER_RATE_LIMIT_SECRET` with an independent long random value. Use a positive
integer for `VIEWER_PIN_VERSION`; increasing it invalidates existing sessions.
Keep `TRUST_CLOUDFLARE_IP_HEADER=false` because the local runtime is not behind a
trusted Cloudflare proxy.

## Build the frontend

The image build compiles the frontend in its own pinned Node stage, so nothing
extra is needed for Compose. For host-side checks such as `collectstatic`, build
it first:

```powershell
npm ci
npm run build
```

See [frontend.md](frontend.md) for the build, the asset strategy and the
browser smoke tests.

## Validate and start

```powershell
docker compose -f compose.yaml -f compose.dev.yaml config
docker compose -f compose.yaml -f compose.dev.yaml build
docker compose -f compose.yaml -f compose.dev.yaml up -d
```

The development override runs the production settings module by default. It is
the only place where `DJANGO_SETTINGS_MODULE` can be overridden, and it exists so
the browser smoke suite can drive the application over plain HTTP on loopback:

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings.local"
docker compose -f compose.yaml -f compose.dev.yaml up -d --wait
```

Never point the production Compose file at anything but
`config.settings.production`.

The runtime contains only `web` and `db`. The web service joins `frontend` and
the internal `backend` network, with its host port bound to `127.0.0.1`. The
database joins only `backend` and its port is not published.

## Initialize and verify

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py migrate
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py collectstatic --noinput
docker compose -f compose.yaml -f compose.dev.yaml exec web uv run pytest
```

Check the public login, health, and crawler-control endpoints:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/sisene/ -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/health/live/ -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/health/ready/ -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/robots.txt -UseBasicParsing
```

Both return `200` while PostgreSQL is healthy. Liveness remains available if
PostgreSQL becomes unavailable; readiness returns a minimal `503` response.

The root route, `/admin/` and `/dashboard/varskus/` redirect to `/sisene/` until
the viewer PIN is accepted. Django admin then presents its own standard login.
Logout is available only as a CSRF-protected `POST /logi-valja/`.

After signing in, the root route renders the board overview. On a fresh database
every figure on it is an explicit empty state, because nothing has been
collected yet and there is nothing truthful to display. Run `sync_koda_public`
and one of the legal-work synchronisation commands to populate it. The
communication-channel figures are typed in rather than collected — see below —
and the parts with no source at all, press coverage and the newsletter itself,
stay marked `Ühendamata` whatever you run.

## Rate-limit maintenance

The default purge removes inactive viewer rate-limit buckets older than 30
days, while preserving a currently locked bucket:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py purge_viewer_rate_limits
```

## Stop without deleting data

```powershell
docker compose -f compose.yaml -f compose.dev.yaml down
```

The `postgres_data` named volume remains and is reused on the next `up`.

## Intentionally delete the local database

The following command permanently deletes the local Compose database volume.
Use it only when a clean local database is explicitly wanted:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml down --volumes
```

This is a local development reset, not a backup or restore procedure.

## Private source artifacts

Original source files are written to `SOURCE_ARTIFACT_ROOT`, never to
PostgreSQL and never anywhere a web server serves. Compose mounts the named
`source_artifacts` volume at `/srv/dashkoda/source-artifacts`; host-side
commands fall back to the git-ignored `.private-media/source-artifacts/`.

Production requires the setting explicitly and has no fallback, so a
misconfigured deployment fails at startup rather than quietly writing originals
into a container layer that the next deploy discards.

The volume survives `down` and is deleted by `down --volumes`, exactly like the
database volume. Treat it as data: it holds the only copies of registered
originals, and no backup automation exists yet.

## Legal-work feed

Import a canonical workbook locally without touching OneDrive:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec -T web python manage.py import_oigusloome --file /path/to/dashkoda_oigusloome.xlsx --dry-run
docker compose -f compose.yaml -f compose.dev.yaml exec -T web python manage.py import_oigusloome --file /path/to/dashkoda_oigusloome.xlsx
```

The imported data appears at `http://127.0.0.1:8000/oigusloome/`.

Synchronize from the public sharing link instead, which needs no Microsoft
credentials at all. Put the URL in the untracked local `.env` as
`OIGUSLOOME_PUBLIC_URL` — never on the command line, and never in a tracked
file:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec -T web python manage.py sync_oigusloome_public --dry-run --json
docker compose -f compose.yaml -f compose.dev.yaml exec -T web python manage.py sync_oigusloome_public --json
```

The dry run validates and publishes nothing. A second live run of unchanged
bytes reports `unchanged` rather than duplicating anything. This route stores no
workbook file: it downloads into a temporary directory that is removed in every
outcome, and the artifact carries only the checksum, size and MIME type.

Neither command is required for local UI work — the application starts and every
page renders with no legal-work variable set. See
[legal-work-feed.md](legal-work-feed.md).

Never copy a real workbook into the repository, and never commit the sharing URL.
The file lives outside the working tree; a private artifact produced by the
manual import is stored under `SOURCE_ARTIFACT_ROOT` like every other original.

## Public Koda.ee feeds

Collect the public member count, news feed and events calendar. No credential is
needed — all three endpoints are anonymous and read-only:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec -T web python manage.py sync_koda_public --source all --dry-run --json
docker compose -f compose.yaml -f compose.dev.yaml exec -T web python manage.py sync_koda_public --source all --json
```

The results appear at `http://127.0.0.1:8000/liikmeskond/`, `/uudised/` and
`/sundmused/`, and on the overview. Each source runs independently, so exit code
`2` means at least one source failed while another published. See
[koda-public-feeds.md](koda-public-feeds.md).

## Internal membership history

A one-time import of the approved package. Nothing is collected remotely and
there is no schedule; this is run once per environment by hand.

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec -T web python manage.py import_membership_history --package /run/imports/dashkoda-membership-history-import-package.zip --dry-run --json
docker compose -f compose.yaml -f compose.dev.yaml exec -T web python manage.py import_membership_history --package /run/imports/dashkoda-membership-history-import-package.zip --json
```

The dry run writes nothing. Running the identical package again reports
`unchanged` and writes nothing, so a repeat is safe.

The package is **never committed**. Keep it outside the working tree and remove
the local copy when you are done; the registered artifact keeps the checksum, not
the file. To start over locally, reset the database as described above — a
published observation cannot be edited or deleted, which is the point.

Future reports are entered through the staff form at
`http://127.0.0.1:8000/admin/membership/internal-report/new/`, which needs a
Django superuser as well as the viewer PIN. See
[internal-membership-history.md](internal-membership-history.md).

## Communication-channel figures

Nothing is collected. The newsletter and social audience sizes are typed in by a
staff user, so there is no command to run and no credential to configure:

```text
http://127.0.0.1:8000/admin/data-entry/
http://127.0.0.1:8000/admin/data-entry/visibility/new/
```

Both need a Django superuser as well as the viewer PIN. The results appear at
`http://127.0.0.1:8000/nahtavus/` and in the overview's channel band.

Use obviously synthetic values locally. **Never enter real Chamber follower
counts into a development database**, and never commit one.

## Filling a development database with synthetic content

Empty states are what a fresh database shows, and for a long time they were the
only thing the browser suite ever saw — which is how a 152-pixel horizontal
overflow shipped while every viewport assertion passed. One command publishes
content shaped to catch that:

```bash
docker compose exec -T web python manage.py seed_e2e_data
```

It publishes through the ordinary domain services — a real workbook through the
real legal-work parser, the public feeds through their own synchronisation, the
board-report history and the visibility figures through their manual publication
services — so the result is a state the application could actually have reached.

Every value is invented and obviously synthetic, every value is a fixed
constant, and re-running publishes nothing new. The command **refuses to run
under `config.settings.production`**: it is permitted only under
`config.settings.local` and `config.settings.test`.

To see the pages with that content in a browser, run the two browser stages the
way CI does — the empty stage first, then the seed, then the seeded stage:

```bash
npm run e2e
docker compose exec -T web python manage.py seed_e2e_data
DASHKODA_E2E_SEEDED=1 npm run e2e
```

The two stages are mutually exclusive by design: the empty-state suite asserts
that no digit reaches the shell, which is exactly what the seeded suite needs.
`DASHKODA_E2E_SEEDED=1` selects `e2e/seeded/` and its own report directory.

Google Analytics needs no local configuration. `GA4_PROPERTY_ID` and
`GA4_CREDENTIALS_FILE` may stay unset: the application starts, every page
renders and the whole test suite passes with neither, and only the scheduled
`sync_ga4` command ever requires them. See
[visibility-manual-entry.md](visibility-manual-entry.md).

## Deployment boundary

A development/pilot deployment of the application exists at
`https://dash.orgusaar.ee`. This repository still does not own or configure
Unraid, Cloudflare, DNS or the tunnel, and `cloudflared` is managed separately
from the DashKoda Compose stack. The commands in this file are for a local
runtime and must not be run against the server. See
[deployment-status.md](deployment-status.md).
