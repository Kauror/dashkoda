# DashKoda architecture

## Agreed direction

DashKoda is a Django modular monolith. It runs as a development/pilot
deployment on Docker on Unraid, backed by PostgreSQL and reachable at
`https://dash.orgusaar.ee` through an existing Cloudflare Tunnel.

That deployment happened ahead of the planned operations milestone and is a
sequencing deviation, not a finished operations stage. This repository does not
own or configure Cloudflare, DNS, the tunnel or the Unraid host; `cloudflared`
is managed separately from the DashKoda Compose application, and no tunnel
token, route definition or production environment value belongs in Git. See
[deployment-status.md](deployment-status.md) for what is and is not done.

The presentation layer is server-rendered Django templates with HTMX and the
Alpine.js CSP build under a strict Content Security Policy. Tailwind CSS 4
provides styling and ECharts is bundled for charts, though no chart is rendered
yet. Web typography uses
`system-ui, -apple-system, "Segoe UI", Arial, sans-serif`.

See [design-system.md](design-system.md) for the visual language,
[frontend.md](frontend.md) for the build, asset strategy and logo provenance,
and [data-model.md](data-model.md) for the source, import and audit foundation.

## Runtime topology

The Compose runtime contains exactly two services:

- `web` runs Gunicorn and Django as the non-root `dashkoda` user.
- `db` runs PostgreSQL 18.4 with a persistent named volume.

PostgreSQL uses only the internal `backend` network. The web service joins that
database network and a separate `frontend` network. PostgreSQL has no published
host port; the development override publishes only the web service on the
loopback interface. The production image contains locked runtime dependencies
and collected static files, but not uv, Ruff, pytest, build caches, Node.js,
Redis, or Celery.

WhiteNoise serves collected static files through Gunicorn. The frontend bundle
is produced by a pinned Node build stage in the Dockerfile and copied into the
Python builder before `collectstatic`; Node, npm and `node_modules` never reach
either runtime image.

## Viewer access boundary

The `access` app owns the shared viewer PIN, session marker, rate-limit bucket,
login/logout views and security middleware. The PIN is never stored in
plaintext: runtime configuration supplies a Django password hash and
`check_password` verifies submitted values.

Viewer authentication is a database-backed Django session lasting seven days.
The session key rotates on successful login and stores the configured PIN
version. Changing that positive version invalidates existing sessions. Logout
is a CSRF-protected POST that flushes the session.

Routes are protected by default. Only `/sisene/`, both health routes,
`/robots.txt`, and required static files are public. `/admin/` first requires
viewer access and then uses the normal Django admin authentication flow.
Redirect destinations are restricted to internal URLs.

Ordinary requests receive a standard redirect to `/sisene/` when viewer access
is missing. A request carrying `HX-Request: true` instead receives `204` with an
`HX-Redirect` header pointing at the same login URL, so htmx makes the browser
navigate rather than swapping the login page into a fragment target. The route
policy is identical in both cases and HTMX routes are never added to the public
allowlist.

Failed PIN attempts are serialized in PostgreSQL per HMAC-pseudonymized client
address. Five failures within 15 minutes lock that client for 15 minutes.
Successful authentication removes the bucket. Raw client addresses are not
stored. `REMOTE_ADDR` is authoritative by default; `CF-Connecting-IP` is used
only when the explicit Cloudflare trust setting is enabled.

## Operational health

- `GET /health/live/` is public and database-independent.
- `GET /health/ready/` is public and performs a minimal `SELECT 1`.
- readiness returns only a minimal status and never exposes database or exception details.
- the database container uses `pg_isready`.
- the web container healthcheck uses `/health/ready/`.

## Module boundaries

The future monolith will separate shared infrastructure from business modules:

- `core` owns application-wide primitives and operational endpoints.
- `access` owns the implemented PIN/session and viewer rate-limit workflow.
- `dashboard` owns the overview route, the shell layout, navigation
  presentation, the shared visual components and the neutral HTMX fragment. It
  owns no business data, no source or audit model, no authentication and no
  import pipeline.
- `sources` owns source registration, private original files, the import-run
  registry and private artifact access. It owns no domain data and performs no
  parsing.
- `audit` owns append-only records of significant actions and depends on no
  domain module.
- membership will own member-domain data and membership views.
- dashboard modules will read prepared application data and present focused views.

Exact future app names and models are introduced only by their implementing pull
requests. Neither PR-03 nor PR-04 creates placeholder business apps or models:
the six planned modules appear in the navigation as inert entries marked
`Lisamisel` and have no routes.

## Implemented through PR-04

- minimal Django project and `core` app
- split base, local, production, and test settings
- PostgreSQL-only runtime and test database configuration through environment variables
- Django built-in auth, content type, session, message, admin, and static foundations
- public liveness and readiness endpoints
- multi-stage, non-root Gunicorn image
- two-service Compose runtime with private PostgreSQL and a persistent named volume
- WhiteNoise and `STATIC_ROOT`
- PostgreSQL-backed tests and GitHub Actions CI
- shared PIN access using a Django password hash and versioned database sessions
- PostgreSQL-backed, concurrency-safe per-client login throttling
- default-deny route middleware, protected admin, POST-only logout, and safe redirects
- strict CSP, anti-indexing, browser policy, and protected-response cache headers
- hidden-input PIN hash generation and stale rate-limit purge commands
- deterministic, lockfile-pinned frontend build with a Node-only Docker stage
- dark Chamber-aligned design system and shared base layout
- `dashboard` app, overview route, responsive shell and reusable components
- restyled viewer login page
- one neutral HTMX fragment and its `HX-Redirect` session handling
- locally bundled ECharts bootstrap that no page renders yet
- Playwright browser smoke suite across four viewports in CI
- `sources` app: `DataSource`, private `SourceArtifact`, `ImportRun` registry
- private artifact storage outside every served path, with a staff-only,
  permission-guarded download
- `audit` app: append-only `AuditEvent` with redaction and a database trigger
- explicit service layer for checksums, import keys and import state transitions

## Not implemented yet

There is no Unraid override, Cloudflare or DNS configuration, backup or restore
automation, rollback tooling, staging environment, membership domain model, CSV
importer, chart, demo data, or real data in this repository. The dashboard shows
structure only: every section is an explicit empty state because no data source
is connected.

PR-05 adds the registry that later imports will use, but **no importer runs and
nothing is scheduled**. Creating an `ImportRun` records an attempt; it never
writes a domain record.

An application deployment exists at `dash.orgusaar.ee`, but the operations
milestone it belongs to is not complete. See
[deployment-status.md](deployment-status.md).

The next planned stage is PR-06 `membership-domain`.
