# DashKoda agent rules

These rules apply to the whole repository.

## Scope and delivery

- Read the current implementation brief and plan before changing the project.
- Implement only the named pull request; do not pull later milestones forward.
- Work on the branch named by the brief and use small, logical English commits.
- Open a draft pull request and do not merge it unless the user explicitly asks.
- Keep documentation truthful about what exists now and what is only planned.
- Report exact checks, deviations, blockers, commit hashes, and the next planned PR.

## Architecture boundaries

- Keep DashKoda a Django modular monolith with explicit module boundaries.
- Do not create placeholder Django apps for future modules.
- Keep the core liveness endpoint public, database-independent, and minimal.
- Do not use SQLite as a shortcut; PostgreSQL is the planned persistent database.
- Keep PostgreSQL on the private backend network and never publish its port to the host.
- Bind the local development web port to loopback unless a later brief explicitly changes it.
- Keep settings separated into base, local, test, and production modules.
- Production must never default to debug mode or a hard-coded secret.
- Use Estonian UI language and `Europe/Tallinn` application time.

## Data, security, and operations

- Never commit secrets, `.env` files, real member data, or production exports.
- Use only intentionally synthetic test data when later work requires fixtures.
- Protect application routes by default; keep public route exceptions exact and minimal.
- Keep `/admin/` behind viewer access as well as Django admin authentication.
- Store only a Django hash for the viewer PIN and use a version to invalidate sessions.
- Never persist or log raw client addresses for viewer rate limiting.
- Trust `CF-Connecting-IP` only when the explicit proxy-trust setting is enabled.
- Do not expose versions, dependencies, server details, or database state from health endpoints.
- Do not change Unraid, Docker runtime, Cloudflare, DNS, or production services unless
  the current brief explicitly authorizes it.
- `dash.orgusaar.ee` is a running development/pilot deployment, but this repository
  does not own Cloudflare, DNS, the tunnel or the Unraid host, and `cloudflared` is
  managed separately from the DashKoda Compose stack.
- Never commit a tunnel token, Cloudflare credential, production environment value or
  server path holding real data.
- The operations milestone is not complete; do not describe it as such. Nightly backup
  and a restore drill into a throwaway database are done and verified; a restore over
  the production database and rollback tooling are not.
- Keep original source files out of PostgreSQL, out of Git and off every served path;
  the only way to fetch one is the permission-guarded staff download.
- Write audit events only through `apps.audit.services.record_event`; never update or
  delete one.
- Route source, artifact and import state changes through `apps/sources/services.py`
  rather than through views, admin callbacks or signals.
- Collect external data only in scheduled management commands. A page render
  must read PostgreSQL and nothing else: never call an external API, download a
  file, parse a workbook or wait on a remote system while serving a request.
- Publish an imported dataset all-or-nothing, and never let a failed import
  replace or remove the last good data. Disclose the failure instead.
- Keep external credentials in the environment only. Ordinary application
  startup must succeed without them; the commands that need them must fail with
  an explicit message naming what is missing.
- Never commit a real workbook, a Microsoft identifier, a sharing URL, a token
  or a signed download URL, and never write file contents to logs, audit
  summaries or import diagnostics.
- Treat an anonymously readable sharing URL as a bearer-style secret: it may
  live only in the deployment environment, and it must never reach Git,
  PostgreSQL, a log, an audit summary, command output, the interface or the
  admin. A command that needs one reads it from the environment and accepts no
  URL argument, so it cannot enter shell history or a process listing.
- A collector need not retain what it downloaded. When it does not, register a
  metadata-only artifact carrying the server-computed checksum, size and MIME
  type plus a fixed non-secret provenance label, and delete the temporary file
  on every exit path. An artifact is importable when it has a trusted checksum,
  not when it still has a file.
- Do not weaken the canonical workbook contract to accommodate a defective
  source file. A workbook whose own summary disagrees with its authoritative
  table is rejected, and the fix belongs to whatever generated it.
- Collect from a public website only through a fixed, configured endpoint on an
  explicit host allowlist. Never add a route, form or setting that lets anyone
  supply a URL to fetch.
- Detect change by hashing the normalised fields the dashboard consumes, never
  the raw response. Markup churn must not republish identical data.
- When a public source exposes row-level personal or registry data that the
  product does not need, count or aggregate it in memory and discard it. Do not
  create a model field capable of holding it.
- "Uusi liikmeid sel aastal" is not a DashKoda metric and Teataja is out of
  scope. Neither may appear in a model, field, selector, template, JSON output,
  test or document. The Chamber's internal board-report new-member figures are a
  different thing and are allowed, provided they are labelled as internal
  reported data.
- The public Koda.ee member-directory count and the internal board-report
  membership history are two sources that count different things. Never merge
  their definitions, never extend one series with the other, never join them
  because two dates are adjacent, and never present two unlabelled member totals
  side by side.
- Never discard reported evidence to make a chart tidy. A conflicted, disputed
  or internally impossible value is stored with the provenance that explains it;
  what changes is whether a selector draws it. Withhold the affected **metric**,
  not the whole observation.
- A missing, withheld or conflicted value is never zero and never interpolated.
  An explicitly reported zero is a real value and must stay distinguishable from
  a blank.
- A published domain record is immutable. A correction creates a new record that
  supersedes the old one; it never rewrites history in place, and there is no
  delete action.
- Communication-channel audience figures are **entered by hand and nothing
  more**. Do not add a Smaily, Meta, LinkedIn, Instagram or YouTube client,
  credential, OAuth flow, scraper or schedule; do not store post reach,
  impressions, engagement, opens, clicks or any individual subscriber or
  follower. The fixed public profile URLs are display links, are application
  configuration rather than form values, and are never fetched. Website
  traffic is the one automated exception: the scheduled `sync_ga4` command
  reads aggregate daily figures through a read-only Google Analytics service
  account, and no individual visitor is ever stored.
- Manually entered data must never be worded as an automatic feed. Do not write
  `sünkroonitud`, `API-ga ühendatud` or `automaatselt uuendatud` beside a value
  a person typed, and do not add manual observations to `current_freshness()`
  without changing what its denominator claims.
- A staff data-entry workflow belongs behind `/admin/`, wrapped in
  `admin.site.admin_view`, and is listed in `apps/core/data_entry.py`. Do not
  create a second admin site, a separate password, a new permissions system or
  any viewer-side editing.
- Do not add a webhook, a public ingestion endpoint or any route that accepts a
  remote file or URL. Collection is outbound and read-only.
- Do not add an in-process scheduler. Scheduling belongs to the host.

## Product and visual direction

- Keep the UI server-rendered and progressively enhanced as specified in the architecture.
- Follow the Chamber CVI and `docs/design-system.md`; do not invent brand assets.
- Never commit font files, the CVI PDF, or planning documents.
- Use the lightweight system web-font stack documented in `docs/architecture.md`.
- Never display a number, trend, date, owner or deadline that is not backed by a
  verified source; use the documented empty states instead.
- Keep every frontend dependency bundled locally and the Content Security Policy
  unchanged; no CDN, no inline script or style, no `unsafe-eval`.
- Do not edit anything in `static/build/`; it is generated by `npm run build`.

## Required checks

Before publishing a change, run the locked dependency install, the frontend
build, Ruff format check, Ruff lint, pytest, Django system check, and any
brief-specific smoke tests. Container, Compose and browser acceptance run in
GitHub Actions when Docker is unavailable locally.
