"""Download one configured Microsoft public-sharing workbook link, hardened.

This is the single implementation behind ``apps/legal_work/public_download.py``
and ``apps/event_programme/public_download.py``. Those modules used to be
character-for-character copies with a note saying "a security fix to one is a
security fix owed to the other"; the shared logic now lives here once, and each
domain keeps only a thin wrapper naming its own settings variable, size cap and
user agent. A transport or validation fix made here reaches every feed.

It is **not** a generic remote-file downloader. Each target is one configured
workbook on a Microsoft OneDrive/SharePoint host, described by a fixed
:class:`WorkbookSource` constant in the owning domain module, and there is
deliberately no way for a viewer, an administrator or a request to supply a URL.

Secret handling: a sharing URL is a bearer-style secret, because anyone holding
it can download the file. Nothing here ever logs, returns or embeds the
configured URL, a redirect ``Location``, the final signed URL or any part of a
response body. Error messages are written to be safe to store in PostgreSQL and
to print in a cron log.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# The sharing link must live on a Microsoft host. This is not a security
# boundary on its own — it is a configuration guard that catches a mistyped or
# swapped-in URL before any request is made.
ALLOWED_HOST_SUFFIXES = (
    ".sharepoint.com",
    ".onedrive.com",
    "onedrive.live.com",
    "1drv.ms",
)

# Hostnames that must never be a collection target, even though an operator
# supplies the configuration.
BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

DOWNLOAD_QUERY_PARAMETER = "download"
DOWNLOAD_QUERY_VALUE = "1"

CONNECT_TIMEOUT_SECONDS = 15.0
READ_TIMEOUT_SECONDS = 120.0
MAX_REDIRECTS = 5
MAX_ATTEMPTS = 3
MAX_RETRY_SLEEP_SECONDS = 30.0
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

DOWNLOAD_CHUNK_BYTES = 64 * 1024

XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Microsoft answers a sharing link with an HTML viewer page unless the download
# parameter is present, and it may label a real download `application/
# octet-stream`. Content-Type is therefore a useful signal but never the proof:
# these types are refused outright, and everything else still has to survive
# structural XLSX validation below.
REJECTED_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "text/plain",
        "text/xml",
        "application/json",
        "application/xhtml+xml",
        "application/xml",
    }
)

ZIP_LOCAL_FILE_SIGNATURE = b"PK\x03\x04"
REQUIRED_XLSX_MEMBERS = ("[Content_Types].xml", "xl/workbook.xml")


class PublicDownloadError(RuntimeError):
    """The workbook could not be downloaded or is not a valid XLSX package.

    Messages are sanitized by construction: they name statuses, sizes, types
    and hostnames, never the configured URL, a redirect target or file content.
    """


class RetryablePublicDownloadError(PublicDownloadError):
    """A transient transport failure worth one bounded retry."""

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class PublicUrlNotConfigured(ImproperlyConfigured):
    """The source's sharing-URL setting is unset or unusable."""


@dataclass(frozen=True)
class WorkbookSource:
    """Fixed, code-defined description of one public workbook feed.

    Instances are constants in the owning domain module. Nothing constructs one
    from request data or from the database, so which setting supplies the URL
    and which supplies the size cap stay decisions made in code review.
    """

    # The settings name holding the sharing URL. Named in error messages so an
    # operator knows which variable to fix; the value itself is never echoed.
    url_setting: str
    # The settings name holding the streamed download cap in bytes.
    max_bytes_setting: str
    user_agent: str
    # Logger suffix and log-line prefix, e.g. "legal_work.public_download",
    # kept per feed so operators can keep filtering logs by source.
    log_prefix: str

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f"dashkoda.{self.log_prefix}")

    @property
    def max_download_bytes(self) -> int:
        return int(getattr(settings, self.max_bytes_setting))


@dataclass(frozen=True)
class PublicDownload:
    """Non-secret facts about one completed download."""

    path: Path
    size_bytes: int
    sha256: str
    content_type: str
    final_host: str


def load_public_url(source: WorkbookSource) -> str:
    """Return the configured sharing URL, or fail naming only the variable.

    Only commands call this. Ordinary web startup never does, so the
    application still starts and every page still renders with the variable
    unset. The value itself is never echoed, not even truncated.
    """
    url = (getattr(settings, source.url_setting, "") or "").strip()
    if not url:
        raise PublicUrlNotConfigured(
            "Avaliku töövihiku seadistus on puudulik. Puudub keskkonnamuutuja: "
            f"{source.url_setting}."
        )
    validate_public_url(url, source=source)
    return url


def validate_public_url(url: str, *, source: WorkbookSource) -> str:
    """Check the configured URL and return its hostname.

    Raises :class:`PublicUrlNotConfigured` without repeating the URL.
    """
    try:
        parts = urlparse(url)
    except ValueError as error:
        raise PublicUrlNotConfigured(f"{source.url_setting} ei ole kehtiv URL.") from error

    if parts.scheme.lower() != "https":
        raise PublicUrlNotConfigured(f"{source.url_setting} peab kasutama HTTPS-i.")

    try:
        hostname = parts.hostname
    except ValueError as error:
        raise PublicUrlNotConfigured(f"{source.url_setting} ei ole kehtiv URL.") from error
    if not hostname:
        raise PublicUrlNotConfigured(f"{source.url_setting}-il puudub hostinimi.")

    hostname = hostname.lower().rstrip(".")
    _require_routable_hostname(hostname, error_class=PublicUrlNotConfigured)

    if not any(hostname == suffix or hostname.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES):
        raise PublicUrlNotConfigured(
            f"{source.url_setting} peab viitama Microsofti OneDrive'i või SharePointi hostile."
        )
    return hostname


def _require_routable_hostname(hostname: str, *, error_class: type[Exception]) -> None:
    """Refuse loopback names and IP literals.

    A collection target is always a named public host. An IP literal cannot be
    the configured workbook and is the shape an SSRF attempt would take, so it
    is refused rather than resolved.
    """
    if hostname in BLOCKED_HOSTNAMES:
        raise error_class("Kohalikule hostile ei laadita alla.")
    literal = hostname.strip("[]")
    try:
        address = ipaddress.ip_address(literal)
    except ValueError:
        return
    raise error_class(
        "IP-aadressile ei laadita alla."
        if address.is_global
        else "Kohalikule või privaatvõrgu aadressile ei laadita alla."
    )


def build_download_url(url: str) -> str:
    """Return `url` with `download=1` set, preserving every other parameter.

    A sharing link answers with an HTML viewer page without it. Any existing
    ``download`` value is replaced rather than duplicated, so a URL copied from
    the browser with ``download=0`` still downloads.
    """
    parts = urlparse(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() != DOWNLOAD_QUERY_PARAMETER
    ]
    query.append((DOWNLOAD_QUERY_PARAMETER, DOWNLOAD_QUERY_VALUE))
    return urlunparse(parts._replace(query=urlencode(query)))


def download_public_workbook(
    source: WorkbookSource,
    destination: Path,
    *,
    url: str | None = None,
    session: requests.Session | None = None,
) -> PublicDownload:
    """Download the configured workbook to `destination` and validate it.

    The caller owns `destination`; it is expected to live in a temporary
    directory that the caller removes in every outcome. On failure this
    function removes the partial file itself, so nothing is left behind even if
    the caller's cleanup is bypassed.
    """
    source_url = url if url is not None else load_public_url(source)
    if url is not None:
        validate_public_url(source_url, source=source)

    owns_session = session is None
    session = session or requests.Session()
    try:
        response = _fetch(session, build_download_url(source_url), source=source)
        try:
            content_type = _content_type(response)
            _reject_by_content_type(content_type)
            _reject_declared_size(response, source=source)
            size, checksum = _stream_to_file(response, destination, source=source)
            final_host = _final_host(response)
        finally:
            response.close()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if owns_session:
            # A fresh session per run, closed here: no cookie jar survives to
            # the next invocation.
            session.close()

    try:
        _require_xlsx_package(destination, size=size)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    source.logger.info(
        "%s completed host=%s size=%s type=%s",
        source.log_prefix,
        final_host,
        size,
        content_type or "(määramata)",
    )
    return PublicDownload(
        path=destination,
        size_bytes=size,
        sha256=checksum,
        content_type=content_type,
        final_host=final_host,
    )


# -- transport ----------------------------------------------------------


def _fetch(session: requests.Session, url: str, *, source: WorkbookSource) -> requests.Response:
    """Perform the request with bounded retries, following redirects manually.

    Redirects are followed by hand so every hop can be checked before it is
    requested. ``Location`` is never logged and never appears in an error.
    """
    last_error: PublicDownloadError | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _follow(session, url, source=source)
        except RetryablePublicDownloadError as error:
            last_error = error
            delay = error.retry_after or min(2.0**attempt, MAX_RETRY_SLEEP_SECONDS)

        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(delay)

    raise last_error or PublicDownloadError("Töövihiku allalaadimine ebaõnnestus.")


def _follow(session: requests.Session, url: str, *, source: WorkbookSource) -> requests.Response:
    """One request chain: at most `MAX_REDIRECTS` hops, all HTTPS."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        response = _request_once(session, current, source=source)
        if response.status_code not in REDIRECT_STATUSES:
            return _require_success(response)

        location = response.headers.get("Location")
        response.close()
        if not location:
            raise PublicDownloadError("Ümbersuunamine ei sisaldanud sihtkohta.")
        current = _validated_redirect_target(current, location)

    raise PublicDownloadError(f"Liiga palju ümbersuunamisi (üle {MAX_REDIRECTS}).")


def _request_once(session: requests.Session, url: str, *, source: WorkbookSource):
    try:
        return session.get(
            url,
            headers={"User-Agent": source.user_agent, "Accept": "*/*"},
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            allow_redirects=False,
            stream=True,
        )
    except requests.Timeout as error:
        raise RetryablePublicDownloadError("Töövihiku allalaadimine aegus.") from error
    except requests.RequestException as error:
        raise RetryablePublicDownloadError(
            f"Ühendus ebaõnnestus: {type(error).__name__}."
        ) from error


def _validated_redirect_target(current: str, location: str) -> str:
    """Resolve and check one hop without ever exposing it."""
    try:
        target = urljoin(current, location)
        parts = urlparse(target)
        hostname = parts.hostname
    except ValueError as error:
        raise PublicDownloadError("Ümbersuunamise sihtkoht ei ole kehtiv URL.") from error

    if parts.scheme.lower() != "https":
        raise PublicDownloadError("Ümbersuunamine HTTPS-ilt mujale ei ole lubatud.")
    if not hostname:
        raise PublicDownloadError("Ümbersuunamise sihtkohal puudub hostinimi.")
    _require_routable_hostname(hostname.lower().rstrip("."), error_class=PublicDownloadError)
    return target


def _require_success(response: requests.Response) -> requests.Response:
    status = response.status_code
    if status < 400:
        return response

    retry_after = _retry_after(response)
    response.close()
    if status == 404:
        raise PublicDownloadError(
            "Jagamislink ei ole kättesaadav (404). Kontrolli, kas link on endiselt kehtiv."
        )
    if status in (401, 403):
        raise PublicDownloadError(
            f"Jagamislink keeldus ligipääsust ({status}). Anonüümne ligipääs võib olla tühistatud."
        )
    if status == 429:
        raise RetryablePublicDownloadError(
            "Microsoft piiras päringute sagedust (429).", retry_after=retry_after
        )
    if status in RETRYABLE_STATUSES:
        raise RetryablePublicDownloadError(
            f"Microsoft vastas koodiga {status}.", retry_after=retry_after
        )
    raise PublicDownloadError(f"Töövihiku allalaadimine ebaõnnestus (kood {status}).")


def _retry_after(response: requests.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(float(raw), MAX_RETRY_SLEEP_SECONDS)
    except ValueError:
        return None


def _final_host(response: requests.Response) -> str:
    """The host that actually served the file, without any query parameters."""
    try:
        return (urlparse(response.url).hostname or "").lower()
    except ValueError:
        return ""


def _content_type(response: requests.Response) -> str:
    return (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()


# -- validation ---------------------------------------------------------


def _reject_by_content_type(content_type: str) -> None:
    if content_type in REJECTED_CONTENT_TYPES:
        raise PublicDownloadError(
            f"Vastuseks tuli {content_type} asemel Exceli faili. "
            "Kontrolli, kas jagamislink lubab endiselt allalaadimist."
        )


def _reject_declared_size(response: requests.Response, *, source: WorkbookSource) -> None:
    limit = source.max_download_bytes
    declared = response.headers.get("Content-Length")
    if declared and declared.strip().isdigit() and int(declared) > limit:
        raise PublicDownloadError(
            f"Töövihik on liiga suur: {int(declared)} baiti. Lubatud kuni {limit} baiti."
        )


def _stream_to_file(
    response: requests.Response, destination: Path, *, source: WorkbookSource
) -> tuple[int, str]:
    """Stream the body to disk, checksumming and size-capping as it goes.

    An undeclared Content-Length is not a way past the limit: the running count
    is what stops the write.
    """
    limit = source.max_download_bytes
    digest = hashlib.sha256()
    written = 0
    try:
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                if not chunk:
                    continue
                written += len(chunk)
                if written > limit:
                    raise PublicDownloadError(f"Töövihik ületab lubatud suuruse ({limit} baiti).")
                digest.update(chunk)
                handle.write(chunk)
    except requests.Timeout as error:
        raise PublicDownloadError("Töövihiku allalaadimine aegus.") from error
    except requests.RequestException as error:
        raise PublicDownloadError(f"Allalaadimine katkes: {type(error).__name__}.") from error

    if written == 0:
        raise PublicDownloadError("Allalaaditud töövihik on tühi.")
    return written, digest.hexdigest()


def _require_xlsx_package(path: Path, *, size: int) -> None:
    """Prove the bytes really are an XLSX package before anything imports them.

    Three independent checks, because a Content-Type header is a claim and this
    is evidence: a non-zero size, the ZIP local-file signature, and the two
    package members every XLSX workbook must contain.
    """
    if size <= 0:
        raise PublicDownloadError("Allalaaditud töövihik on tühi.")

    with path.open("rb") as handle:
        signature = handle.read(len(ZIP_LOCAL_FILE_SIGNATURE))
    if signature != ZIP_LOCAL_FILE_SIGNATURE:
        raise PublicDownloadError("Allalaaditud fail ei ole ZIP-pakend, seega ka mitte XLSX.")

    if not zipfile.is_zipfile(path):
        raise PublicDownloadError("Allalaaditud ZIP-pakend on vigane.")

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile as error:
        raise PublicDownloadError("Allalaaditud ZIP-pakend on vigane.") from error

    missing = [member for member in REQUIRED_XLSX_MEMBERS if member not in names]
    if missing:
        raise PublicDownloadError(
            "Allalaaditud ZIP-pakend ei ole XLSX töövihik: puuduvad " + ", ".join(missing) + "."
        )
