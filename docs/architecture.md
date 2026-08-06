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
[data-model.md](data-model.md) for the source, import and audit foundation,
[legal-work-feed.md](legal-work-feed.md) for the first module carrying real
business data, and [visibility-manual-entry.md](visibility-manual-entry.md) for
the manually entered audience figures and the Google Analytics collector.

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
- `legal_work` owns the imported legal-work snapshots and rows, their
  selectors, the Õigusloome page, the workbook importer and the OneDrive feed
  state. It owns no generic artifact or import-run lifecycle, no audit
  infrastructure, no authentication and no other OneDrive file.
- `membership` owns the total-member observations, their selectors, the
  Liikmeskond page, the member-directory collector and its feed state. It owns
  no member record: the aggregate is all it stores.
- `news` owns the imported news snapshots and items, their selectors, the
  Uudised page, the RSS collector and its feed state.
- `event_programme` owns the Chamber's authoritative event programme: the
  canonical Excel workbook parser and importer, the immutable programme
  snapshots and rows, their selectors and filters, the Sündmused page, and the
  OneDrive feed state. It is the source of truth for every event figure on the
  dashboard, including the whole available history.
- `events` owns the imported public-calendar snapshots and items, their
  selectors, the calendar collector and its feed state. It is **supplementary**:
  it has no route, it produces no dashboard total and it never overrides an
  event-programme field. The Sündmused page names it as a secondary connection.
- `visibility` owns the manually observed audience sizes — the two newsletter
  lists and their overlap, and the four social follower counts — their metric
  registry, selectors, staff entry workflow, the Nähtavus page, the overview's
  channel band and the Google Analytics website-traffic collector (`sync_ga4`,
  optional and off until the deployment supplies a property ID and a read-only
  service-account key). It stores no platform credential and no individual
  subscriber, follower or visitor.

Each public feed is its own business app rather than one generic "web scraper"
domain, because what makes a member count valid has nothing to do with what
makes an event valid. Only the low-level transport is shared, in
`apps/core/public_http.py`; every schema check, normalisation rule and
publication decision stays in its own app.
- dashboard modules will read prepared application data and present focused views.

Exact future app names and models are introduced only by their implementing pull
requests. No pull request creates placeholder business apps or models. The
overview, Liikmeskond, Õigusloome, Sündmused, Uudised and Nähtavus are routed;
every
still-planned module appears in the navigation as an inert entry marked
`Lisamisel` and has no route. That includes the nested entries — Fookusteemad
under Õigusloome, and Projektid with its two views — which exist so the sidebar
states the intended scope without a placeholder app behind any of them.

The overview assembles its view-model in `apps/dashboard/overview.py`, which
reads each module through that module's own `selectors.py` and decides what the
board sees. The view renders; the template lays out. Neither holds a business
rule.

## Data collection boundary

Collecting data from an external system is a scheduled command, never part of a
web request. A page render reads PostgreSQL and nothing else: it does not
contact Microsoft, download or parse a workbook, or wait on OneDrive. A slow or
broken external system can therefore delay tomorrow's data, but it can never
make the dashboard slow, broken or untruthful.

Collection is outbound and read-only in both directions of that boundary: there
is no webhook, no ingestion endpoint, no upload route and no route that accepts a
remote file or a URL. A URL is operator configuration read from the environment,
never user input.

Publication is all-or-nothing, and a failed collection never replaces or removes
the last good data. See [legal-work-feed.md](legal-work-feed.md).

### Two collection routes, one publication path

The legal-work workbook arrives one recurring way:

- **public read-only sharing link** (`sync_oigusloome_public`) — one outbound
  HTTPS download of a view-only OneDrive link into a temporary directory. No
  Entra application and no Microsoft credential. The workbook is never
  retained: the artifact is **metadata-only**, carrying the server-computed
  checksum, size, MIME type and a fixed non-secret provenance label instead of a
  stored file.

An operator can additionally import a workbook from a local path with
`import_oigusloome`, which is not scheduled. A Microsoft Graph route existed and
was retired without ever completing live acceptance; see
[legal-work-feed.md](legal-work-feed.md).

Both entry points then use the same parser, the same import registry, the same
all-or-nothing snapshot publication and the same feed state. An artifact is
importable when it has a trusted SHA-256 content identity, not when it still has
a file on disk.

### Public Koda.ee feeds

Three further sources — the public member directory, the news RSS feed and the
events calendar — follow the same shape and keep no raw response at all. Each
normalises its source into deterministic canonical JSON, hashes **that** rather
than the response bytes, and publishes through a metadata-only artifact. Hashing
the raw response would republish identical data every time the CMS re-rendered.

Each runs under its own advisory lock and transaction, so one failing source
never blocks another and a failed source keeps its previous good data. See
[koda-public-feeds.md](koda-public-feeds.md).

### The current-topic catalogue, and enrichment as a separate concern

A fourth Koda.ee source collects the public `Hetkel käsil` listing and the
detail pages it links to. It is not a dashboard metric and has no viewer page:
it exists to enrich legal-work records with a public address, and it is
deliberately **absent from `current_freshness()`**, whose denominator still
counts the four modules a viewer actually reads.

A deterministic matcher — no model, no embedding, no external service — proposes
which open legal record corresponds to which catalogue entry, and writes its
decisions to their own immutable snapshot. Enrichment is kept structurally
separate from the enriched data: an imported `LegalWorkItem` is rebuilt from the
workbook on every synchronisation, so a match result stored on one would be
erased overnight. The results therefore reference the exact rows they describe
rather than annotating them, and the relations carry no reverse accessor, so a
selector cannot decorate viewer data with them by accident.

A `matched` decision becomes a **link on the topic title** on `/oigusloome/` and
on the overview card; `ambiguous` and `unmatched` stay plain text. A **second**
catalogue collects the `Hetkel käsil` archive and supplies a fallback link once a
consultation has closed but the legal matter has not — the address is unchanged,
so the link continues rather than reappearing. The two are separate feeds with
separate matchers, corpora and thresholds, because a word rare among seven live
consultations is unremarkable among a decade of them. A consultation link is
offered only while the workbook says the matter is open and no opinion has been
sent; see [legal-consultation-links.md](legal-consultation-links.md). The address is
resolved at read time, in one bounded query per page, and only when the current
match snapshot was computed from exactly the legal snapshot and catalogue being
displayed — a stale match is never applied. See
[legal-current-topic-matching.md](legal-current-topic-matching.md).

### The Chamber's own opinion documents

A source unlike every other, and the first that is **private**. The Chamber's
outgoing opinion letters arrive as PDFs in a directory on the host rather than
from a URL, and they are never served from a public path.

Two roots, both fixed configuration: a **read-only source inbox** the Chamber
owns, and a **read-write managed store** DashKoda is answerable for. No command
accepts a path or a URL, so nothing an operator, a viewer or a scheduled job
supplies can steer a read or a write. The bootstrap archive is treated as
untrusted and is read in place, never unpacked into the inbox.

Documents are stored **content-addressed by their own SHA-256**, written
temporary → `fsync` → verify → atomic rename. Identical bytes are stored once
however many filenames they arrive under, a blob is never rewritten, and a
source file disappearing removes neither the blob nor any historical row.
PostgreSQL holds normalised text and metadata; it never holds a PDF, a
filesystem path, or anything a viewer can turn into one.

Validation refuses encryption and active content, and **decides from the parsed
object model rather than from raw bytes** — measured against the real catalogue,
a byte scan for `/JS` matches six documents whose object models contain nothing
of the sort. Extraction is versioned, so improving the text layer produces new
immutable rows and a later match can always name the exact reading it used.
There is no OCR and no page rendering.

The build is bounded and resumable: a slice per run, and a snapshot published
only once every entry has a terminal state, so a partial backfill never becomes
current. See [legal-opinion-documents.md](legal-opinion-documents.md).

Phase 1 catalogues only. No legal topic links to a document, no resource page
exists and no PDF is served; that is the next phase.

### Data that is not collected at all

The Chamber's internal board-report membership history has no remote source to
collect from. It arrives once as an approved package through an operator-run
command, and every later report is typed by a staff user in the admin. There is
no schedule, no ingestion endpoint and no route that accepts a file.

The manual form is nevertheless not a shortcut around the boundary above. A
submission becomes canonical JSON, is hashed, is carried by a metadata-only
artifact and is recorded by an ordinary `ImportRun`, so typed data and imported
data reach the database through the same publication path and obey the same
quality rules. That is what will let an automated route replace the form later
without rewriting any historical row.

The communication-channel figures are the second dataset of this shape, and the
first where an automated route plausibly exists but is deliberately not built.
The newsletter list sizes and the four social follower counts are read by a
staff user from each platform's own statistics screen and typed in. There is no
Smaily, Meta, LinkedIn, Instagram or YouTube client in this repository, no
credential that would let one exist and no field capable of holding a token; a
page render never touches a social platform, and the fixed profile URLs are
display links only. See [visibility-manual-entry.md](visibility-manual-entry.md).

Two staff workflows now write domain data from a browser, so `/admin/data-entry/`
indexes them. It is not a second admin: it lives inside `/admin/`, every view is
wrapped in `admin.site.admin_view`, and it adds no authentication, permission
model or session of its own. `apps/core/data_entry.py` holds the index as URL
names rather than imports, so `core` depends on no domain module.

Two membership sources exist and are never merged. The public directory count
and the internal board-report history count different things; see
[internal-membership-history.md](internal-membership-history.md).

## Implemented so far

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
- locally bundled ECharts bootstrap, rendered by the Liikmeskond page
- Playwright browser smoke suite across four viewports in CI
- `sources` app: `DataSource`, private `SourceArtifact`, `ImportRun` registry
- private artifact storage outside every served path, with a staff-only,
  permission-guarded download
- `audit` app: append-only `AuditEvent` with redaction and a database trigger
- explicit service layer for checksums, import keys and import state transitions
- `legal_work` app: immutable `LegalWorkSnapshot`, `LegalWorkItem` and the
  `LegalWorkFeedState` record of the last synchronisation attempt
- deterministic `legal_work_xlsx` importer with an all-or-nothing snapshot
  publication and a documented workbook contract
- public read-only sharing-link collector with HTTPS-only redirects, a streamed
  size cap and structural XLSX validation
- metadata-only source artifacts, so a collected workbook need not be retained
- `sync_oigusloome_public` and `import_oigusloome` commands
- the Õigusloome page and the overview's legal-work summary
- `membership`, `news` and `events` apps reading three public Koda.ee sources,
  with the `sync_koda_public` command and the Liikmeskond and Uudised pages
- `event_programme` app: the canonical Excel event programme, its
  `sync_event_programme` collector, the filtered and paginated Sündmused page,
  and the workbook-backed event figures on the overview and in the shell
  freshness row
- board-briefing overview: a four-indicator strip, approaching opinion
  deadlines, one fixed activity window, module cards for all four connected
  feeds, and named `Ühendamata` slots for everything that has no source
- server-rendered SVG sparklines and proportion meters, so the overview draws
  trends without loading the chart bundle
- `visibility` app: manually observed newsletter and social audience sizes, a
  fixed metric registry, the staff entry and correction workflow, the Nähtavus
  page and the six-slot channel band
- `/admin/data-entry/`, one staff-only index of every manual-entry workflow
- Unraid script templates for 07:00 and 07:05 `Europe/Tallinn` schedules
- the Koda.ee `Hetkel käsil` current-topic catalogue, a deterministic matcher
  over the current legal snapshot's consultation-eligible records, immutable
  match snapshots, their read-only admin, and the automatic topic links those
  matches produce on the Õigusloome page and the overview card
- the `Hetkel käsil` **archive** as a fallback source for the same links, with
  its own bounded and resumable backfill, its own matcher and thresholds, and a
  strict current-listing-first precedence
- the private catalogue of the Chamber's own opinion documents: a read-only
  source inbox, a content-addressed managed blob store, PDF validation,
  versioned text extraction, deterministic classification, a bounded resumable
  build and a read-only admin

## Not implemented yet

There is no Unraid override, Cloudflare or DNS configuration, backup or restore
automation, rollback tooling, staging environment, membership domain model,
chart or demo data in this repository.

Automatic legal-topic links cover the current `Hetkel käsil` listing and its
archive. Opinion documents are now catalogued but **not yet linked**: a record
whose opinion has been sent still renders as plain text, because matching,
resource pages and the protected PDF endpoint are the next phase. `Meie arvamus`
pages, public opinion PDFs, news items and attached draft legislation are not
collected and are not modelled. Neither opinion command is scheduled by this
repository; the intended times are documented and the Unraid templates are
examples, not installations.

Arvamused, Finantsid, Fookusteemad and Projektid are inert navigation entries,
because no source is connected for any of them.

Nothing **collects** the communication-channel audience figures. Six of the
seven can be stored — a staff user types them in and `apps/visibility`
publishes them through the ordinary artifact and import path — but no Smaily,
Meta, LinkedIn, Instagram or YouTube integration exists, and none is planned in
this stage. Website visits are the exception: the scheduled `sync_ga4` command
can collect one completed day of Google Analytics traffic when the deployment
supplies `GA4_PROPERTY_ID` and a mounted read-only service-account key. Until
an observation has actually been published the website slot stays `Lisamisel`;
configuration alone never makes the page claim a connection.

Press coverage, the newsletter itself and event history once an event has passed
remain entirely unconnected, and no model is capable of holding those.

The 07:00 schedule is **documented here as a template**; this repository installs
no schedule. An administrator has installed it on the pilot host. See
[deployment-status.md](deployment-status.md).

The Microsoft Graph collection route was retired without ever completing live
acceptance; the public sharing link is the one recurring legal-work route.

The public sharing-link collector has been verified against the live link for
download, URL handling, XLSX validation and temporary-file cleanup, across more
than one published revision of the workbook. The end-to-end import **has since
completed live** on the pilot deployment, once the two things it needed were in
place: a PostgreSQL instance and a workbook published with its `tbl_oigusloome`
Excel Table intact. See [legal-work-feed.md](legal-work-feed.md).

An application deployment exists at `dash.orgusaar.ee`, but the operations
milestone it belongs to is not complete. See
[deployment-status.md](deployment-status.md).

The legal-work feed is a deliberate change in feature order: it is the first
end-to-end proof of concept with real business data, delivered ahead of the
membership domain. The next planned stage remains `membership-domain`.
