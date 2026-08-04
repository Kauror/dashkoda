# Viewer access security

## Purpose and boundary

PR-03 adds one shared viewer-access gate for the still-empty DashKoda
application. It is not staff identity, authorization, membership access
control, or a replacement for Django admin authentication. It provides a
minimal first boundary while those later product concerns remain out of scope.

The middleware protects every application route by default. Its complete public
allowlist is:

- `GET` and `POST /sisene/`
- `GET /health/live/`
- `GET /health/ready/`
- `GET /robots.txt`
- static files under the configured Django `STATIC_URL`

`/admin/` is intentionally absent. A viewer must pass the shared gate before
Django admin performs its own normal user login.

## Private source artifacts

Original source files registered from PR-05 onward are the most sensitive thing
this application stores. They are protected by construction rather than by a
rule someone has to remember:

- they never enter PostgreSQL;
- they live under `SOURCE_ARTIFACT_ROOT`, outside `STATIC_ROOT` and outside every
  `STATICFILES_DIRS` entry, so WhiteNoise cannot serve them;
- there is no media URL, no media route and no viewer-facing download;
- the storage backend raises instead of returning a URL, so a template cannot
  leak one;
- the stored path is a random UUID; a client-supplied filename never becomes a
  path component;
- production requires the root setting explicitly, with no fallback.

The single download route lives under `/admin/`, so it is behind the viewer PIN
gate and then Django admin authentication, and additionally requires the
`sources.download_sourceartifact` permission — with superuser status on top for
artifacts marked `restricted`. Responses are attachment-only, typed
`application/octet-stream`, `nosniff` and `private, no-store`. Every successful
download is audited.

Uploads are stored and checksummed, never parsed. Only inert document and data
formats are accepted, with a conservative size limit; executables, scripts,
archives and macro-enabled office formats are refused.

See [data-model.md](data-model.md) for the full model, and note that the audit
trail's append-only guarantee has documented limits.

## Staff data entry

Two workflows write domain data from a browser, indexed by the hub at
`/admin/data-entry/`:

- the internal membership report form at
  `/admin/membership/internal-report/new/`;
- the communication-channel figures at `/admin/data-entry/visibility/new/`.

All of it is guarded twice over — the viewer PIN middleware covers all of
`/admin/`, and every view is wrapped in `admin.site.admin_view`, which requires
an active Django staff account. A PIN-only viewer therefore reaches the admin
login page and stops there, and has no account to get past it. The hub is a
signpost inside that same boundary: it adds no second admin site, no separate
password, no new permission model and no viewer-side editing.

Three properties keep it from becoming a general write surface:

- the preview step is stateless and saves nothing, so an abandoned form leaves
  no draft record and no session copy behind;
- a submission is hashed as canonical JSON, so a double submit is recognised as
  the same report rather than published twice;
- published records are immutable. A correction creates a new record that
  supersedes the old one, and there is no delete action.

The imported quality warnings are the one thing an administrator may edit, and
only their resolution fields — recording that a person looked at a warning must
never be able to change what the source said.

### Communication-channel figures

The newsletter and social audience sizes widen the write surface by one form and
nothing else:

- **no social-platform credential exists.** There is no Smaily, Meta, LinkedIn,
  Instagram or YouTube client, no token, no OAuth flow and no model field
  capable of holding one;
- **nothing is fetched.** No page render, command or background job contacts a
  social platform. The four public profile URLs are fixed application
  configuration used as display links; they are never fetched, never editable and
  never stored as an artifact reference;
- **no personal data is stored.** These are aggregate counts. No subscriber
  address, no individual follower and no per-person record exists in the schema;
- the optional note is bounded to 500 characters, is plain text, is escaped
  normally by Django and is deliberately kept out of the audit summary;
- no file upload, no API endpoint, no public POST route and no CSP change.

Website traffic is the one automated exception, and it stays aggregate-only:
the scheduled `sync_ga4` command reads one completed day of session, user and
page-view totals through a **read-only** service account. `GA4_PROPERTY_ID` and
`GA4_CREDENTIALS_FILE` are optional and blank by default; the credentials file
is mounted into the deployment, belongs in the server environment only and must
never reach Git, PostgreSQL, a log line, an audit summary or the interface.
Only `sync_ga4` reads it — never a page render.

The membership package import is an **operator command**, not a route. It reads a
path the operator supplies on the server; nothing in the application accepts an
uploaded package, and the archive is never stored. The registered artifact is
metadata-only, carrying the server-computed checksum under a fixed non-secret
reference, which also keeps ZIP out of the upload allowlist above.

## External data collection

The legal-work feed reads one OneDrive workbook. Two routes exist — a public
read-only sharing link and Microsoft Graph — and both widen the boundary as
little as possible:

- collection is **read-only** and **outbound only**. It happens solely in a
  scheduled command. There is **no webhook, no public ingestion endpoint, no
  upload endpoint and no route that accepts a remote file or a URL**. Nothing
  external can push data into DashKoda, and no viewer or administrator can make
  it fetch a URL of their choosing.
- the target is one workbook fixed in configuration. There is no file browsing,
  no folder crawling and no arbitrary path.
- credentials and the sharing URL live only in the deployment environment. They
  are never committed, never stored in the database, never logged and never
  rendered.
- downloads are size-capped while streaming, structurally validated, and written
  to a temporary directory that is removed in every outcome.
- workbook contents never reach logs, audit summaries or import diagnostics.
- `LegalWorkFeedState` records only non-secret content metadata — etag, size and
  modification time — plus a sanitized, truncated error summary.

### The public sharing link

`OIGUSLOOME_PUBLIC_URL` is trusted operator configuration, not user input.
Even so, it is treated as a **bearer-style secret**, because the link is
anonymously readable: whoever holds it can download the workbook.

- it never enters Git, PostgreSQL, a log record, an audit summary, the JSON
  command output, the dashboard or the Django admin;
- the command offers no `--url` option, so it cannot reach shell history or a
  process listing either;
- the stored external reference is the fixed label `onedrive-public:oigusloome`,
  and the model independently refuses any reference containing `@` or `?`;
- only HTTPS is accepted, for the configured URL and for every redirect hop;
- loopback names and IP literals are refused rather than resolved;
- redirects are followed by hand, at most five, so each hop is checked before it
  is requested — and no `Location` value is ever logged or placed in an error;
- both a connect and a read timeout are explicit; retries are bounded and honour
  `Retry-After`;
- no authentication header is sent and no cookie jar survives a run;
- HTML, plain text and JSON responses are refused, and `Content-Type` is treated
  as a signal rather than as proof: the bytes must additionally pass the ZIP
  signature, `zipfile.is_zipfile` and the required XLSX package members, so an
  HTML viewer page labelled `application/octet-stream` is still rejected;
- every exception message is sanitized by construction — statuses, sizes, types
  and hostnames only.

Because this route keeps no permanent copy, its artifact is metadata-only: the
server-computed checksum, size and MIME type, and no stored file. The staff
download route returns `404` for such an artifact, and the admin offers no
download link.

### The public Koda.ee feeds

Three anonymous, read-only endpoints on the Chamber's own website. There is no
credential, so nothing here is a secret — but the collection boundary stays
just as narrow:

- HTTPS only, on an allowlist of `www.koda.ee` and `koda.ee`, for the configured
  endpoint **and every redirect hop**, each checked before it is requested;
- loopback names and IP literals refused rather than resolved;
- explicit connect and read timeouts, bounded retries honouring `Retry-After`,
  and a streamed response cap;
- content types checked, no cookies, no authentication header;
- **no route, form or setting through which a viewer or an administrator can
  introduce a URL.** The three endpoints are fixed in configuration;
- no response body reaches any log, and every error message is sanitized.

**The member endpoint returns row-level data that is never stored.** Each row
carries a registration code and a member profile URL; both are read in memory to
count and deduplicate, then discarded. No member name, registration code or
profile URL exists in PostgreSQL, in an audit summary, in a log line, in command
output or in the interface — there is no model field capable of holding one.

Article and event summaries are stored as sanitized plain text with scripts,
styles and all markup removed and a length cap. No article or event-page HTML is
retained.

### Microsoft Graph

Optional, and not required for the MVP route. The application holds the
`Files.Read.All` application permission and never requests write access.
Downloads follow Graph's redirect to a pre-authenticated URL, and the bearer
token is deliberately not forwarded to that host; that signed URL is never
logged or stored. This route retains the workbook as an ordinary private
artifact under the storage rules above.

`dash.orgusaar.ee` now serves real internal Chamber information rather than
empty states. Cloudflare Access in front of the tunnel remains the recommended
next control before treating the pilot as a production system for confidential
data. **This pull request does not change Cloudflare, DNS or the tunnel.**

## Runtime settings

- `VIEWER_PIN_HASH`: output from `generate_viewer_pin_hash`
- `VIEWER_PIN_VERSION`: positive integer stored in each authenticated session
- `VIEWER_RATE_LIMIT_SECRET`: independent secret for HMAC client pseudonyms
- `TRUST_CLOUDFLARE_IP_HEADER`: `false` unless the application is known to be
  reached only through the trusted Cloudflare proxy path
- `OIGUSLOOME_PUBLIC_URL`: the view-only workbook sharing link. Optional and
  blank by default; required only by `sync_oigusloome_public`. Treat it like a
  credential and keep it only in the server environment.
- `GA4_PROPERTY_ID`, `GA4_CREDENTIALS_FILE`: the Google Analytics property and
  the mounted read-only service-account key for the scheduled `sync_ga4`
  collector. Optional and blank by default; only that command reads them, and
  the application, the tests and every page work with both unset.

The real PIN and its plaintext value must never be written to source, tests,
workflow files, documentation, shell history, or logs. The browser smoke suite
uses a separate synthetic PIN that exists only for CI; it grants nothing outside
a throwaway container. Generate the real hash using hidden terminal input:

```powershell
python manage.py generate_viewer_pin_hash
```

The command accepts no PIN command-line argument and writes only the hash to
standard output. Store that output in the local untracked environment file.

Increment `VIEWER_PIN_VERSION` to invalidate every existing viewer session.
Rotate `VIEWER_RATE_LIMIT_SECRET` only deliberately: existing pseudonymous
rate-limit buckets then become unreachable and can later be purged.

## Sessions and redirects

Successful login rotates the Django session key and creates a seven-day
database-backed viewer session. Logout is POST-only, CSRF protected, and flushes
the session. Login accepts a `next` destination only when Django verifies it as
an internal URL, preventing open redirects.

The response for an unauthenticated protected request is a normal HTTP redirect
to `/sisene/`. A request carrying `HX-Request: true` receives `204` with an
`HX-Redirect` header holding the same login URL, which makes htmx navigate the
browser instead of swapping the login page into a fragment. Both paths use the
identical default-deny route policy and internal destination validation, and no
HTMX route is on the public allowlist.

## Brute-force control and client identity

PostgreSQL stores one `ViewerRateLimitBucket` per HMAC-SHA256 client pseudonym.
It contains only the pseudonym, window time, failure count, lock expiry, and
update time. The raw address is neither stored nor logged.

Five failures in a 15-minute window cause a 15-minute lock. Locked responses use
HTTP 429 and `Retry-After`; even a correct PIN cannot bypass an active lock.
Successful login deletes the bucket. Updates run in a database transaction with
a row lock so simultaneous failures cannot lose increments.

`REMOTE_ADDR` is the client address source by default. `X-Forwarded-For` and
`X-Real-IP` are ignored. `CF-Connecting-IP` is accepted only when
`TRUST_CLOUDFLARE_IP_HEADER=true`.

Inactive unlocked buckets older than 30 days can be removed with:

```powershell
python manage.py purge_viewer_rate_limits
```

## Browser policy

Responses use a self-only Content Security Policy without inline-script,
evaluation, wildcard, or CDN exceptions. Browser policy also denies framing,
MIME sniffing, camera, microphone, and geolocation; restricts referrers to the
same origin; and sends anti-indexing headers. Protected HTML uses
`Cache-Control: private, no-store`. `/robots.txt` disallows all crawling as an
additional signal, not as an access-control mechanism.

The policy is unchanged by the PR-04 frontend. It remains exactly:

```text
default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none';
form-action 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;
connect-src 'self'
```

Keeping it that strict required three deliberate choices:

- every script and stylesheet is bundled locally and loaded from `/static/`;
  there is no CDN, no external font and no inline `<script>` or `style`
  attribute in any template;
- Alpine.js runs as its CSP build, so directives name component properties and
  methods instead of being evaluated as expressions;
- htmx is configured with `includeIndicatorStyles: false`, so it never injects an
  inline `<style>` element, and with `allowEval: false` and
  `allowScriptTags: false`. `hx-on` attributes and `js:` values are not used.

Chart payloads are read from non-executable `<script type="application/json">`
blocks, so adding charts later does not require relaxing the policy either.

Static file serving does not widen the boundary: the middleware exempts only
paths under the configured `STATIC_URL`, which WhiteNoise serves from the
collected output directory. No application route lives under that prefix.
Production builds emit no source maps, so none are served.
