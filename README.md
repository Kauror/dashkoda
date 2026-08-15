# DashKoda

DashKoda is a planned internal management dashboard for the Estonian Chamber of
Commerce and Industry. It is intended for Chamber staff who need one consistent,
auditable view of operational and membership information.

PR-01 established the Django core, PR-02 added the local runtime and
PostgreSQL, PR-03 protects application routes with a shared PIN session and
database-backed rate limiting, PR-04 added the responsive dashboard shell and
its design system, and PR-05 added the source, private artifact,
import-registry and audit foundation.

The legal-work feed is the first module carrying **real business data**: it
imports a prepared Excel workbook from OneDrive and renders it at
`/oigusloome/`.

Three public Koda.ee sources follow: the size of the public member directory,
the news RSS feed and the events calendar, at `/liikmeskond/` and `/uudised/`.
All three are anonymous, read-only and collected once each morning; no raw
response is retained. See
[docs/koda-public-feeds.md](docs/koda-public-feeds.md).

`/sundmused/` is the **Chamber's own event programme**, imported from a prepared
Excel export: the whole available event history with real dates, tags, types,
delivery modes and the public links the workbook validated. It is the source of
truth for every event figure on the dashboard. The public events calendar keeps
collecting alongside it as a separate, supplementary feed for publicly announced
upcoming events, and never overrides it. See
[docs/event-programme-feed.md](docs/event-programme-feed.md).

The four **social** follower counts — Facebook, LinkedIn, Instagram and YouTube
— are **entered by hand** by a staff user and shown in the overview's channel
band. (`/nahtavus/` itself is now a redirect: the website surface became
**Koduleht** at `/koduleht/`.) Nothing collects them: there is no social integration,
no credential and no scheduled job, and no model field is capable of holding an
individual follower. See
[docs/visibility-manual-entry.md](docs/visibility-manual-entry.md).

The three **newsletter** lists are collected, not typed. The scheduled
`sync_smaily` command reads each list's current size through a read-only Smaily
client, and `sync_smaily_campaigns` catalogues completed sends with their
aggregate statistics. No email address, name, subscriber identifier or
per-recipient open, click or bounce is ever requested or stored. There is no
backfill for list sizes and there cannot be one: Smaily reports what a list
holds now. See [docs/newsletter-audience.md](docs/newsletter-audience.md).

**Website traffic** is collected as well. The scheduled `sync_ga4` command reads
completed reporting days from the Google Analytics Data API through a read-only
service account and stores each day as an immutable revision, with its page and
acquisition rows. GA4 revises recent days, so the job reconciles the last
several completed days rather than fetching yesterday. No individual visitor is
stored. See [docs/website-analytics.md](docs/website-analytics.md).

`/epood/` is the **E-pood analytics** module: the Koda.ee Commerce storefront's
ordered volume and value, joined to measured page traffic. Its source is a
prepared, PII-free export imported by `import_shop_snapshot`, so the pages
render their empty state until an export has been imported. Two dimensions are
deliberately withheld pending confirmation of what the source actually means.
See [docs/shop-analytics.md](docs/shop-analytics.md).

Sections with no connected source still render an explicit empty state — nothing
on those pages is a placeholder metric. Which sources are configured on the
pilot deployment, as opposed to implemented here, is recorded in
[docs/deployment-status.md](docs/deployment-status.md).

The workbook stays in OneDrive. The MVP collection route reads it through a
view-only public sharing link:

```text
public read-only OneDrive link
  → scheduled outbound HTTPS download
  → temporary XLSX
  → metadata-only artifact
  → existing importer
  → PostgreSQL snapshot
```

It needs one environment variable, `OIGUSLOOME_PUBLIC_URL`, and **no Microsoft
Entra application or credential**. No permanent copy of the workbook is kept:
the file exists only inside a temporary directory for the duration of one
command, and the artifact records its checksum and size. This is the one
recurring route; a Microsoft Graph route existed and was retired without ever
completing live acceptance.

Data collection is a scheduled command, never part of a web request. Pages read
PostgreSQL only. A failed synchronization never replaces the last good data; it
is disclosed instead. See [docs/legal-work-feed.md](docs/legal-work-feed.md).

A development/pilot deployment runs at `https://dash.orgusaar.ee`. That is
ahead of the planned operations milestone, which is **not** complete. A nightly
database backup is installed and verified, and an archive has been restored into
a throwaway database and checked. What is still missing is a restore **over the
production database**, rollback tooling and a separate staging environment — a
backup you can read is not yet a recovery you can perform. See
[docs/deployment-status.md](docs/deployment-status.md).

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

Import a legal-work workbook locally, without any Microsoft credentials:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec -T web python manage.py import_oigusloome --file /path/to/dashkoda_oigusloome.xlsx
```

It then renders at `http://127.0.0.1:8000/oigusloome/`. Never copy a real
workbook into this repository.

Synchronize it from the configured public sharing link instead, without any
Microsoft credentials:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec -T web python manage.py sync_oigusloome_public --dry-run --json
docker compose -f compose.yaml -f compose.dev.yaml exec -T web python manage.py sync_oigusloome_public --json
```

Put the sharing URL in the untracked local `.env` only. Never commit it, and
never pass it on the command line — the command accepts no `--url`.

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

Foundations:

- [docs/architecture.md](docs/architecture.md) — module boundaries and runtime
- [docs/data-model.md](docs/data-model.md) — sources, artifacts, imports, audit
- [docs/design-system.md](docs/design-system.md) — tokens, components, breakpoints
- [docs/frontend.md](docs/frontend.md) — build, assets, logo provenance, Playwright
- [docs/security.md](docs/security.md) — viewer access boundary and browser policy

Sources, one document each:

- [docs/legal-work-feed.md](docs/legal-work-feed.md) — the OneDrive legal-work
  feed, its workbook contract, both sync routes and the 07:00 schedule
- [docs/koda-public-feeds.md](docs/koda-public-feeds.md) — the public member,
  news and event feeds, what the member count means, and the 07:05 schedule
- [docs/event-programme-feed.md](docs/event-programme-feed.md) — the Chamber's
  authoritative event programme and its workbook
- [docs/internal-membership-history.md](docs/internal-membership-history.md) —
  the Chamber's own board-report membership history, why it is a separate source
  from the public member count, the one-time import and the staff entry form.
  The board documents themselves are read by a separate offline project,
  [membership-history-extractor](https://github.com/Kauror/membership-history-extractor);
  this repository only ever sees the package it produces
- [docs/website-analytics.md](docs/website-analytics.md) — the GA4 collector, the
  daily-revision model and the metric semantics
- [docs/newsletter-audience.md](docs/newsletter-audience.md) — the Smaily list
  sizes and campaign catalogue, and the segment registry
- [docs/shop-analytics.md](docs/shop-analytics.md) — E-pood commerce semantics,
  the manual export contract and the two withheld dimensions
- [docs/visibility-manual-entry.md](docs/visibility-manual-entry.md) — the
  hand-entered social audience figures and the staff data-entry hub
- [docs/metric-contract.md](docs/metric-contract.md) — the integrated metric
  contract: every major figure's definition, grain, time basis, missing
  behaviour, comparison rule and executive use, in one table

The legal-work catalogues and matchers:

- [docs/legal-current-topic-matching.md](docs/legal-current-topic-matching.md) —
  the `Hetkel käsil` catalogue and its matcher
- [docs/legal-consultation-links.md](docs/legal-consultation-links.md) — when a
  consultation link is offered, and when it is not
- [docs/legal-opinion-documents.md](docs/legal-opinion-documents.md) — the
  private opinion-document catalogue and its content-addressed store
- [docs/legal-opinion-matching.md](docs/legal-opinion-matching.md) — durable
  matter identity, the opinion matcher and the internal resource pages
- [docs/legal-opinion-public-source.md](docs/legal-opinion-public-source.md) —
  the public Koda.ee opinion source and why it is a second source

Operations:

- [docs/local-runtime.md](docs/local-runtime.md) — Compose runtime operations
- [docs/operations-runbook.md](docs/operations-runbook.md) — the scheduled jobs,
  their Tallinn times and the UTC cron pairs
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
