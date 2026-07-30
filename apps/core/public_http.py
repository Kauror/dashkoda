"""Low-level HTTPS fetching for a small, fixed set of public endpoints.

The three Koda.ee collectors need identical transport behaviour — a host
allowlist, explicit timeouts, bounded retries, conditional requests, a size cap
and sanitized errors — so that behaviour lives here once. What does **not** live
here is any business validation: parsing, schema checks and publication belong
to the domain apps, which is why this module knows nothing about members, news
or events.

This is deliberately **not** a general URL fetcher. Every caller passes its own
host allowlist, and there is no route, form or setting through which a viewer or
an administrator can introduce a URL. Collection is outbound, anonymous and
read-only.

Nothing here ever logs a response body, a cookie or an authorization header.
Errors name statuses, sizes, types and hostnames only, so they are safe to store
in PostgreSQL and to print in a cron log.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger("dashkoda.core.public_http")

USER_AGENT = "DashKoda/1.0 (+internal Chamber dashboard; contact via koda.ee)"

CONNECT_TIMEOUT_SECONDS = 15.0
READ_TIMEOUT_SECONDS = 60.0
MAX_REDIRECTS = 3
MAX_ATTEMPTS = 3
MAX_RETRY_SLEEP_SECONDS = 30.0

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

CHUNK_BYTES = 64 * 1024

BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


class PublicFetchError(RuntimeError):
    """A public fetch failed. Messages are safe to log and to store."""


class RetryableFetchError(PublicFetchError):
    """A transient failure worth one bounded retry."""

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class FetchResult:
    """Non-secret facts about one completed fetch."""

    status_code: int
    content: bytes
    content_type: str
    etag: str
    last_modified: str
    final_host: str

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304

    @property
    def size_bytes(self) -> int:
        return len(self.content)

    def text(self, *, fallback_encoding: str = "utf-8") -> str:
        try:
            return self.content.decode("utf-8")
        except UnicodeDecodeError:
            return self.content.decode(fallback_encoding, errors="replace")


def require_allowed_url(url: str, *, allowed_hosts: frozenset[str]) -> str:
    """Validate one URL against the caller's allowlist and return its hostname.

    Refuses anything that is not HTTPS on a named, allowlisted host. IP literals
    and loopback names are refused rather than resolved: a configured public
    endpoint is always a named host, and an IP literal is the shape an SSRF
    attempt would take.
    """
    try:
        parts = urlparse(url)
        hostname = parts.hostname
    except ValueError as error:
        raise PublicFetchError("Vigane URL.") from error

    if parts.scheme.lower() != "https":
        raise PublicFetchError("Lubatud on ainult HTTPS.")
    if not hostname:
        raise PublicFetchError("URL-il puudub hostinimi.")

    hostname = hostname.lower().rstrip(".")
    if hostname in BLOCKED_HOSTNAMES:
        raise PublicFetchError("Kohalikule hostile ei pöörduta.")
    try:
        ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        pass
    else:
        raise PublicFetchError("IP-aadressile ei pöörduta.")

    if hostname not in allowed_hosts:
        raise PublicFetchError(f"Host {hostname} ei ole lubatud allikate hulgas.")
    return hostname


def fetch(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    accept: str = "*/*",
    max_bytes: int,
    expected_content_types: frozenset[str] | None = None,
    etag: str = "",
    last_modified: str = "",
    session: requests.Session | None = None,
) -> FetchResult:
    """Fetch one public resource.

    `etag` and `last_modified` turn the request into a conditional one; a `304`
    comes back as a `FetchResult` with `not_modified` set and no body, which is
    how an unchanged source costs almost nothing.
    """
    require_allowed_url(url, allowed_hosts=allowed_hosts)

    owns_session = session is None
    session = session or requests.Session()
    try:
        response = _attempt(
            session,
            url,
            allowed_hosts=allowed_hosts,
            accept=accept,
            etag=etag,
            last_modified=last_modified,
        )
        try:
            if response.status_code == 304:
                return FetchResult(
                    status_code=304,
                    content=b"",
                    content_type=_content_type(response),
                    etag=response.headers.get("ETag", "") or etag,
                    last_modified=response.headers.get("Last-Modified", "") or last_modified,
                    final_host=_final_host(response),
                )

            content_type = _content_type(response)
            if expected_content_types is not None and content_type not in expected_content_types:
                raise PublicFetchError(f"Ootamatu sisutüüp: {content_type or '(määramata)'}.")
            _reject_declared_size(response, max_bytes)
            content = _read_capped(response, max_bytes)
            if not content:
                raise PublicFetchError("Vastus oli tühi.")

            return FetchResult(
                status_code=response.status_code,
                content=content,
                content_type=content_type,
                etag=response.headers.get("ETag", ""),
                last_modified=response.headers.get("Last-Modified", ""),
                final_host=_final_host(response),
            )
        finally:
            response.close()
    finally:
        if owns_session:
            # A fresh session per call, closed here: no cookie jar survives.
            session.close()


def _attempt(session, url, *, allowed_hosts, accept, etag, last_modified):
    last_error: PublicFetchError | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _follow(
                session,
                url,
                allowed_hosts=allowed_hosts,
                accept=accept,
                etag=etag,
                last_modified=last_modified,
            )
        except RetryableFetchError as error:
            last_error = error
            delay = error.retry_after or min(2.0**attempt, MAX_RETRY_SLEEP_SECONDS)
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(delay)
    raise last_error or PublicFetchError("Päring ebaõnnestus.")


def _follow(session, url, *, allowed_hosts, accept, etag, last_modified):
    """Follow redirects by hand so every hop is checked before it is requested."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        response = _request_once(
            session, current, accept=accept, etag=etag, last_modified=last_modified
        )
        if response.status_code not in REDIRECT_STATUSES:
            return _require_success(response)

        location = response.headers.get("Location")
        response.close()
        if not location:
            raise PublicFetchError("Ümbersuunamine ei sisaldanud sihtkohta.")
        current = urljoin(current, location)
        require_allowed_url(current, allowed_hosts=allowed_hosts)

    raise PublicFetchError(f"Liiga palju ümbersuunamisi (üle {MAX_REDIRECTS}).")


def _request_once(session, url, *, accept, etag, last_modified):
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        return session.get(
            url,
            headers=headers,
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            allow_redirects=False,
            stream=True,
        )
    except requests.Timeout as error:
        raise RetryableFetchError("Päring aegus.") from error
    except requests.RequestException as error:
        raise RetryableFetchError(f"Ühendus ebaõnnestus: {type(error).__name__}.") from error


def _require_success(response):
    status = response.status_code
    if status < 400:
        return response

    retry_after = _retry_after(response)
    response.close()
    if status == 404:
        raise PublicFetchError("Allikat ei leitud (404).")
    if status in (401, 403):
        raise PublicFetchError(f"Allikas keeldus ligipääsust ({status}).")
    if status == 429:
        raise RetryableFetchError(
            "Allikas piiras päringute sagedust (429).", retry_after=retry_after
        )
    if status in RETRYABLE_STATUSES:
        raise RetryableFetchError(f"Allikas vastas koodiga {status}.", retry_after=retry_after)
    raise PublicFetchError(f"Allikas vastas koodiga {status}.")


def _retry_after(response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(float(raw), MAX_RETRY_SLEEP_SECONDS)
    except ValueError:
        # A Retry-After may be an HTTP date. Falling back to the caller's
        # exponential backoff is safer than trying to parse a date here.
        return None


def _reject_declared_size(response, max_bytes: int) -> None:
    declared = response.headers.get("Content-Length")
    if declared and declared.strip().isdigit() and int(declared) > max_bytes:
        raise PublicFetchError(
            f"Vastus on liiga suur: {int(declared)} baiti. Lubatud kuni {max_bytes} baiti."
        )


def _read_capped(response, max_bytes: int) -> bytes:
    """Read the body, stopping at the cap.

    An undeclared Content-Length is not a way past the limit: the running count
    is what stops the read.
    """
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise PublicFetchError(f"Vastus ületab lubatud suuruse ({max_bytes} baiti).")
            chunks.append(chunk)
    except requests.RequestException as error:
        raise PublicFetchError(f"Vastuse lugemine katkes: {type(error).__name__}.") from error
    return b"".join(chunks)


def _content_type(response) -> str:
    return (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()


def _final_host(response) -> str:
    try:
        return (urlparse(response.url).hostname or "").lower()
    except ValueError:
        return ""
