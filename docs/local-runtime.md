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

After signing in, the root route renders the dashboard shell. Every section is
an explicit empty state: no data source is connected yet, so there is nothing
truthful to display.

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

`sync_oigusloome` is the optional Graph route and additionally needs the five
Microsoft Graph variables. Neither command is required for local UI work — the
application starts and every page renders with none of these variables set. See
[legal-work-feed.md](legal-work-feed.md).

Never copy a real workbook into the repository, and never commit the sharing URL.
The file lives outside the working tree; a private artifact produced by the
manual import or the Graph route is stored under `SOURCE_ARTIFACT_ROOT` like
every other original.

## Deployment boundary

A development/pilot deployment of the application exists at
`https://dash.orgusaar.ee`. This repository still does not own or configure
Unraid, Cloudflare, DNS or the tunnel, and `cloudflared` is managed separately
from the DashKoda Compose stack. The commands in this file are for a local
runtime and must not be run against the server. See
[deployment-status.md](deployment-status.md).
