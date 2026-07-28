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

## Runtime settings

- `VIEWER_PIN_HASH`: output from `generate_viewer_pin_hash`
- `VIEWER_PIN_VERSION`: positive integer stored in each authenticated session
- `VIEWER_RATE_LIMIT_SECRET`: independent secret for HMAC client pseudonyms
- `TRUST_CLOUDFLARE_IP_HEADER`: `false` unless the application is known to be
  reached only through the trusted Cloudflare proxy path

The real PIN and its plaintext value must never be written to source, tests,
workflow files, documentation, shell history, or logs. Generate its hash using
hidden terminal input:

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

The current response for an unauthenticated protected request is a normal HTTP
redirect to `/sisene/`. Future HTMX integration may translate that response to
`HX-Redirect`, but it must retain the same default-deny route policy and internal
destination validation.

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
