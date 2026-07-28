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

## Runtime settings

- `VIEWER_PIN_HASH`: output from `generate_viewer_pin_hash`
- `VIEWER_PIN_VERSION`: positive integer stored in each authenticated session
- `VIEWER_RATE_LIMIT_SECRET`: independent secret for HMAC client pseudonyms
- `TRUST_CLOUDFLARE_IP_HEADER`: `false` unless the application is known to be
  reached only through the trusted Cloudflare proxy path

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
