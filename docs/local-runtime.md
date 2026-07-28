# Local Docker runtime

## Prepare the environment

Copy the example and replace every placeholder with a local-only value:

```powershell
Copy-Item .env.example .env
```

Never commit `.env`. The checked-in example contains no real credentials.

## Validate and start

```powershell
docker compose -f compose.yaml -f compose.dev.yaml config
docker compose -f compose.yaml -f compose.dev.yaml build
docker compose -f compose.yaml -f compose.dev.yaml up -d
```

The runtime contains only `web` and `db`. The web service joins `frontend` and
the internal `backend` network, with its host port bound to `127.0.0.1`. The
database joins only `backend` and its port is not published.

## Initialize and verify

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py migrate
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py collectstatic --noinput
docker compose -f compose.yaml -f compose.dev.yaml exec web uv run pytest
```

Check both public health endpoints:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health/live/ -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/health/ready/ -UseBasicParsing
```

Both return `200` while PostgreSQL is healthy. Liveness remains available if
PostgreSQL becomes unavailable; readiness returns a minimal `503` response.

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
