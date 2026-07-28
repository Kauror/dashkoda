# DashKoda architecture

## Agreed direction

DashKoda is a Django modular monolith. Its planned production runtime is Docker
on Unraid, backed by PostgreSQL and eventually published at
`dash.orgusaar.ee`. PR-03 adds the local viewer-access boundary; it does not
configure or deploy to those production systems.

The planned presentation layer remains server-rendered Django templates with
HTMX and Alpine.js under a strict Content Security Policy. Tailwind CSS will
provide styling and ECharts will render charts where useful. The default web
typography will use
`system-ui, -apple-system, "Segoe UI", Arial, sans-serif`.

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

WhiteNoise serves collected static files through Gunicorn. There is no frontend
bundle in PR-03.

## Viewer access boundary

The `access` app owns the shared viewer PIN, session marker, rate-limit bucket,
login/logout views, security middleware, and placeholder authenticated home
page. The PIN is never stored in plaintext: runtime configuration supplies a
Django password hash and `check_password` verifies submitted values.

Viewer authentication is a database-backed Django session lasting seven days.
The session key rotates on successful login and stores the configured PIN
version. Changing that positive version invalidates existing sessions. Logout
is a CSRF-protected POST that flushes the session.

Routes are protected by default. Only `/sisene/`, both health routes,
`/robots.txt`, and required static files are public. `/admin/` first requires
viewer access and then uses the normal Django admin authentication flow.
Redirect destinations are restricted to internal URLs.

The current server-rendered and future non-HTMX requests receive a standard
redirect to `/sisene/` when viewer access is missing. When HTMX is introduced,
the access middleware must preserve this boundary and may add an `HX-Redirect`
response for HTMX requests; HTMX routes must never be added to the public
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
- membership will own member-domain data and membership views.
- ingestion will own source registration, artifacts, and import runs.
- audit will own immutable records of significant actions.
- dashboard modules will read prepared application data and present focused views.

Exact future app names and models are introduced only by their implementing pull
requests. PR-03 does not create placeholder business apps or models.

## Implemented through PR-03

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

## Not implemented yet

There is no production deployment, Unraid override, Cloudflare or DNS
configuration, backup/restore automation, membership or ingestion domain model,
audit event model, CSV import, dashboard shell, HTMX, Alpine.js, Tailwind,
ECharts, logo, CVI asset, demo data, or real data.

The next planned stage is PR-04 `dashboard-shell`.
