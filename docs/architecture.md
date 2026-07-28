# DashKoda architecture

## Agreed direction

DashKoda will be a Django modular monolith. Its planned production runtime is a
Docker deployment on Unraid, backed by PostgreSQL and published through the
existing infrastructure at `dash.orgusaar.ee`. Those runtime components are not
implemented in PR-01.

The planned presentation layer is server-rendered Django templates with HTMX and
Alpine.js under a strict Content Security Policy. Tailwind CSS will provide the
styling foundation and ECharts will render interactive charts where charts are
useful. The default web typography will use the lightweight system stack
`system-ui, -apple-system, "Segoe UI", Arial, sans-serif`; this is the approved
practical replacement for relying on FF DIN Pro being installed on every device.

## Module boundaries

The future monolith will separate shared infrastructure from business modules:

- `core` owns application-wide primitives and operational endpoints.
- identity and access will own the planned PIN/session workflow.
- membership will own member-domain data and membership views.
- ingestion will own source registration, artifacts, and import runs.
- audit will own immutable records of significant actions.
- dashboard modules will read prepared application data and present focused views.

Exact future app names and models will be introduced only in the pull request
that implements them. PR-01 intentionally does not create placeholder apps.

## Implemented in PR-01

- a minimal Django project and one `core` app
- separate base, local, production, and test settings
- Estonian locale and the `Europe/Tallinn` application timezone
- a public, database-independent `GET /health/live/` endpoint
- a dummy, nonpersistent Django database backend pending PostgreSQL in PR-02
- Ruff, pytest, and pytest-django development tooling
- automated liveness, URL, and settings tests

## Not implemented yet

PR-01 does not include Docker or Compose, PostgreSQL, Redis, Celery, production
deployment, Cloudflare or DNS changes, backups, authentication or the planned PIN
flow, sessions, admin workflows, membership data, ingestion models, audit events,
CSV imports, dashboards, templates, HTMX, Alpine.js, CSP integration, Tailwind,
ECharts, logos, CVI assets, demo data, or real data.

PR-02 is planned to introduce the Docker/Unraid-compatible runtime, PostgreSQL,
and CI foundations without claiming that a production deployment already exists.
