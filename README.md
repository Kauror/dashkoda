# DashKoda

DashKoda is a planned internal management dashboard for the Estonian Chamber of
Commerce and Industry. It is intended for Chamber staff who need one consistent,
auditable view of operational and membership information.

The project is at the viewer-access stage. PR-01 established the Django core,
PR-02 added the local runtime and PostgreSQL, and PR-03 protects application
routes with a shared PIN session and database-backed rate limiting. The
authenticated home page is intentionally only a placeholder; no product
dashboard or business data exists yet.

## Requirements

- Docker with Compose v2 for the supported local runtime
- Python 3.14 and [`uv`](https://docs.astral.sh/uv/) for host-side static checks
- all commands run from the repository root

## Start the local runtime

Create a local environment file and replace every example secret:

```powershell
Copy-Item .env.example .env
```

Generate the local PIN hash through hidden terminal input. The command accepts
no PIN argument and prints only the Django password hash:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml run --rm web python manage.py generate_viewer_pin_hash
```

Put the output in `VIEWER_PIN_HASH`, choose a positive
`VIEWER_PIN_VERSION`, and set a separate long random
`VIEWER_RATE_LIMIT_SECRET`. Keep `TRUST_CLOUDFLARE_IP_HEADER=false` locally.
Incrementing the version invalidates all existing viewer sessions.

Validate, build, and start the two-service runtime:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml config
docker compose -f compose.yaml -f compose.dev.yaml build
docker compose -f compose.yaml -f compose.dev.yaml up -d
```

The development override publishes only the web application on
`127.0.0.1:${DASHKODA_PORT:-8000}` through the frontend network. PostgreSQL
remains only on the internal backend network and has no host port.

Apply the built-in Django migrations and collect static files:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py migrate
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py collectstatic --noinput
```

Check the endpoints:

```text
http://127.0.0.1:8000/sisene/
http://127.0.0.1:8000/health/live/
http://127.0.0.1:8000/health/ready/
http://127.0.0.1:8000/robots.txt
```

Liveness is independent of PostgreSQL. Readiness performs only a minimal
database query and returns a detail-free `503` response when the database is
unavailable. All routes except the exact public allowlist and required static
files redirect unauthenticated viewers to `/sisene/`; `/admin/` is not exempt.

See [docs/local-runtime.md](docs/local-runtime.md) for tests, shutdown, and
intentional local-data removal.

## Host-side quality checks

Set the `POSTGRES_*` variables to a reachable PostgreSQL test database before
running database-backed commands directly on the host. The supported Compose
runtime keeps PostgreSQL private, so its complete test suite is normally run in
the development web container.

```powershell
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run python manage.py makemigrations --check
uv run python manage.py check
```

Run the PostgreSQL-backed suite inside the development container:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec web uv run pytest
```

## Current boundaries

Unraid, Cloudflare, DNS, and `dash.orgusaar.ee` are not configured by this
repository stage. PR-03 performs no deployment and does not alter any server or
existing container. See [docs/security.md](docs/security.md) for the implemented
access boundary and its operational settings.

Do not commit secrets, `.env` files, production data, or real member data. The
repository may contain only intentionally synthetic test values.
