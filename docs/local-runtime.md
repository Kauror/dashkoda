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

## Deployment boundary

Unraid, Cloudflare, DNS, and `dash.orgusaar.ee` are not configured yet. These
commands must not be run against existing server services.
