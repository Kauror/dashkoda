# DashKoda

DashKoda is a planned internal management dashboard for the Estonian Chamber of
Commerce and Industry. It is intended for Chamber staff who need one consistent,
auditable view of operational and membership information.

The project is at the sources-and-audit stage. PR-01 established the Django
core, PR-02 added the local runtime and PostgreSQL, PR-03 protects application
routes with a shared PIN session and database-backed rate limiting, PR-04 added
the responsive dashboard shell and its design system, and PR-05 adds the
source, private artifact, import-registry and audit foundation.

**There is no business data yet.** Every section of the dashboard renders an
explicit empty state, because no data source is connected. Nothing on the page
is a real or placeholder metric. PR-05 adds the models that later imports will
use, but no importer exists and nothing is scheduled.

A development/pilot deployment runs at `https://dash.orgusaar.ee`. That is
ahead of the planned operations milestone, which is **not** complete: there is
no backup automation, no tested restore, no rollback tooling and no separate
staging environment. See [docs/deployment-status.md](docs/deployment-status.md).

## Requirements

- Docker with Compose v2 for the supported local runtime
- Python 3.14 and [`uv`](https://docs.astral.sh/uv/) for host-side static checks
- Node 22 and npm for the frontend build
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
npm ci
npm run build
uv run ruff format --check .
uv run ruff check .
uv run python manage.py makemigrations --check
uv run python manage.py check
uv run python manage.py collectstatic --noinput
```

`npm run build` must precede `collectstatic`: the templates reference the
compiled bundle in `static/build/`, which is generated and not committed.

Run the PostgreSQL-backed suite inside the development container:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec web uv run pytest
```

Run the browser smoke suite against a running application:

```powershell
npx playwright install --with-deps chromium
npm run e2e
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — module boundaries and runtime
- [docs/data-model.md](docs/data-model.md) — sources, artifacts, imports, audit
- [docs/design-system.md](docs/design-system.md) — tokens, components, breakpoints
- [docs/frontend.md](docs/frontend.md) — build, assets, logo provenance, Playwright
- [docs/security.md](docs/security.md) — viewer access boundary and browser policy
- [docs/local-runtime.md](docs/local-runtime.md) — Compose runtime operations
- [docs/deployment-status.md](docs/deployment-status.md) — what runs, what does not

## Current boundaries

This repository owns the application only. It does not own or configure Unraid,
Cloudflare, DNS or the tunnel, and `cloudflared` is managed separately from the
DashKoda Compose stack. No pull request alters the server or any existing
container. See [docs/security.md](docs/security.md) for the implemented access
boundary and [docs/deployment-status.md](docs/deployment-status.md) for the
current deployment reality.

Do not commit secrets, `.env` files, production data, or real member data. The
repository may contain only intentionally synthetic test values.
