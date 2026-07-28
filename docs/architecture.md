# DashKoda architecture

## Agreed direction

DashKoda is a Django modular monolith. Its planned production runtime is Docker
on Unraid, backed by PostgreSQL and eventually published at
`dash.orgusaar.ee`. PR-02 provides a local, production-shaped runtime foundation;
it does not configure or deploy to those production systems.

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
bundle in PR-02.

## Operational health

- `GET /health/live/` is public and database-independent.
- `GET /health/ready/` is public and performs a minimal `SELECT 1`.
- readiness returns only a minimal status and never exposes database or exception details.
- the database container uses `pg_isready`.
- the web container healthcheck uses `/health/ready/`.

## Module boundaries

The future monolith will separate shared infrastructure from business modules:

- `core` owns application-wide primitives and operational endpoints.
- identity and access will own the planned PIN/session workflow.
- membership will own member-domain data and membership views.
- ingestion will own source registration, artifacts, and import runs.
- audit will own immutable records of significant actions.
- dashboard modules will read prepared application data and present focused views.

Exact future app names and models are introduced only by their implementing pull
requests. PR-02 does not create placeholder business apps or models.

## Implemented through PR-02

- minimal Django project and `core` app
- split base, local, production, and test settings
- PostgreSQL-only runtime and test database configuration through environment variables
- Django built-in auth, content type, session, message, admin, and static foundations
- public liveness and readiness endpoints
- multi-stage, non-root Gunicorn image
- two-service Compose runtime with private PostgreSQL and a persistent named volume
- WhiteNoise and `STATIC_ROOT`
- PostgreSQL-backed tests and GitHub Actions CI

## Not implemented yet

There is no production deployment, Unraid override, Cloudflare or DNS
configuration, backup/restore automation, PIN access, viewer session workflow,
membership or ingestion domain model, audit event model, CSV import, dashboard,
frontend template, HTMX, Alpine.js, Tailwind, ECharts, logo, CVI asset, demo
data, or real data.

The next planned stage is PR-03 `viewer-access-security`.
