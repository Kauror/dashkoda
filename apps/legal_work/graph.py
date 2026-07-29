"""Read-only Microsoft Graph collector for exactly one OneDrive workbook.

Scope is deliberately narrow: fetch metadata for one configured drive item and
download it. This is not a general Microsoft 365 integration and must not grow
into one.

Security rules enforced here:

- credentials come from settings and are never written to the database or logs;
- the download follows Graph's ``302`` to a pre-authenticated URL, and the
  ``Authorization`` header is **not** forwarded to that host — the URL is
  already signed, and sending the token to a CDN would leak it;
- that signed URL is never logged, stored or returned;
- every request has an explicit timeout, retries are bounded, and ``Retry-After``
  is honoured as Microsoft's throttling guidance requires;
- the download is size-capped while streaming, so an unexpectedly huge file
  cannot exhaust the container's disk.

Least privilege: the runtime path needs only the **Files.Read.All** application
permission. Resolving a sharing URL through ``/shares/`` needs the broader
``Files.ReadWrite.All``, so that call lives in a one-time administrator command
and never runs during a scheduled sync.
"""

from __future__ import annotations

import base64
import datetime as dt
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
AUTHORITY_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}"

XLSX_CONTENT_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    }
)
DOWNLOAD_CHUNK_BYTES = 64 * 1024
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_SLEEP_SECONDS = 60


class GraphError(RuntimeError):
    """A Graph call failed. Messages here are safe to log and to store."""


class GraphNotConfigured(ImproperlyConfigured):
    """Required Graph configuration is missing."""


@dataclass(frozen=True)
class GraphSettings:
    tenant_id: str
    client_id: str
    client_secret: str
    drive_id: str
    item_id: str
    timeout_seconds: float
    max_attempts: int
    max_download_bytes: int


@dataclass(frozen=True)
class RemoteFile:
    """Non-secret metadata about the configured drive item."""

    item_id: str
    name: str
    size_bytes: int | None
    etag: str
    modified_at: dt.datetime | None
    drive_id: str = ""

    def matches(self, *, etag: str, modified_at, size_bytes) -> bool:
        """Whether the remote file looks unchanged since the last sync.

        An etag comparison alone is enough when both sides have one; size and
        modification time are the fallback when the tenant omits it.
        """
        if self.etag and etag:
            return self.etag == etag
        return (
            modified_at is not None
            and self.modified_at is not None
            and modified_at == self.modified_at
            and size_bytes == self.size_bytes
        )


def load_graph_settings(*, require_item: bool = True) -> GraphSettings:
    """Read Graph configuration, failing clearly when something is missing.

    Only commands call this. Ordinary web startup never does, so the
    application still runs locally and in CI without any Graph credentials.
    """
    required = {
        "MS_GRAPH_TENANT_ID": settings.MS_GRAPH_TENANT_ID,
        "MS_GRAPH_CLIENT_ID": settings.MS_GRAPH_CLIENT_ID,
        "MS_GRAPH_CLIENT_SECRET": settings.MS_GRAPH_CLIENT_SECRET,
    }
    if require_item:
        required["OIGUSLOOME_DRIVE_ID"] = settings.OIGUSLOOME_DRIVE_ID
        required["OIGUSLOOME_ITEM_ID"] = settings.OIGUSLOOME_ITEM_ID

    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        raise GraphNotConfigured(
            "Microsoft Graphi seadistus on puudulik. Puuduvad keskkonnamuutujad: "
            + ", ".join(missing)
        )

    return GraphSettings(
        tenant_id=settings.MS_GRAPH_TENANT_ID,
        client_id=settings.MS_GRAPH_CLIENT_ID,
        client_secret=settings.MS_GRAPH_CLIENT_SECRET,
        drive_id=settings.OIGUSLOOME_DRIVE_ID,
        item_id=settings.OIGUSLOOME_ITEM_ID,
        timeout_seconds=float(settings.MS_GRAPH_TIMEOUT_SECONDS),
        max_attempts=int(settings.MS_GRAPH_MAX_ATTEMPTS),
        max_download_bytes=int(settings.LEGAL_WORK_MAX_DOWNLOAD_BYTES),
    )


def encode_sharing_url(url: str) -> str:
    """Encode a sharing URL as Graph's ``u!`` sharing token.

    Microsoft's documented rule: base64, strip ``=`` padding, then ``/`` to
    ``_`` and ``+`` to ``-``, prefixed with ``u!``.
    """
    encoded = base64.b64encode(url.encode("utf-8")).decode("ascii")
    return "u!" + encoded.rstrip("=").replace("/", "_").replace("+", "-")


def _parse_timestamp(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _remote_file_from_payload(payload: dict) -> RemoteFile:
    parent = payload.get("parentReference") or {}
    return RemoteFile(
        item_id=str(payload.get("id") or ""),
        name=str(payload.get("name") or ""),
        size_bytes=payload.get("size"),
        etag=str(payload.get("cTag") or payload.get("eTag") or ""),
        modified_at=_parse_timestamp(payload.get("lastModifiedDateTime")),
        drive_id=str(parent.get("driveId") or ""),
    )


class GraphClient:
    """Minimal app-only Graph client for one workbook."""

    def __init__(self, config: GraphSettings, *, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        self._application = None

    # -- authentication -------------------------------------------------

    def _acquire_token(self) -> str:
        """Client-credentials token.

        MSAL caches the token internally and only calls the identity provider
        on a cache miss, so this is safe to call per request.
        """
        import msal

        if self._application is None:
            self._application = msal.ConfidentialClientApplication(
                client_id=self.config.client_id,
                authority=AUTHORITY_TEMPLATE.format(tenant_id=self.config.tenant_id),
                client_credential=self.config.client_secret,
            )

        result = self._application.acquire_token_for_client(scopes=[GRAPH_SCOPE])
        token = result.get("access_token") if isinstance(result, dict) else None
        if not token:
            # `error_description` can echo tenant details, so only the stable
            # error code is surfaced.
            code = result.get("error", "unknown_error") if isinstance(result, dict) else "no_result"
            raise GraphError(f"Microsoft Graphi autentimine ebaõnnestus: {code}.")
        return token

    # -- requests -------------------------------------------------------

    def _retry_delay(self, response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), MAX_RETRY_SLEEP_SECONDS)
            except ValueError:
                pass
        return min(2.0**attempt, MAX_RETRY_SLEEP_SECONDS)

    def _request(self, method: str, url: str, *, stream: bool = False) -> requests.Response:
        """Perform one Graph request with bounded retries.

        Throttling and transient server errors are retried; every other status
        is returned to the caller to interpret. Retries are capped, so a
        persistently failing Graph never turns a nightly job into an infinite
        loop.
        """
        last_error: GraphError | None = None
        for attempt in range(self.config.max_attempts):
            delay = min(2.0**attempt, MAX_RETRY_SLEEP_SECONDS)
            try:
                token = self._acquire_token()
                response = self.session.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=self.config.timeout_seconds,
                    allow_redirects=False,
                    stream=stream,
                )
            except requests.Timeout:
                last_error = GraphError("Microsoft Graphi päring aegus.")
            except requests.RequestException as error:
                last_error = GraphError(
                    f"Microsoft Graphi ühendus ebaõnnestus: {type(error).__name__}."
                )
            else:
                if response.status_code not in RETRYABLE_STATUSES:
                    return response
                last_error = GraphError(f"Microsoft Graph vastas koodiga {response.status_code}.")
                delay = self._retry_delay(response, attempt)
                response.close()

            if attempt < self.config.max_attempts - 1:
                time.sleep(delay)

        raise last_error or GraphError("Microsoft Graphi päring ebaõnnestus.")

    def _get_json(self, path: str) -> dict:
        response = self._request("GET", f"{GRAPH_BASE_URL}{path}")
        if response.status_code == 404:
            raise GraphError("Microsoft Graph ei leidnud küsitud faili (404).")
        if response.status_code in (401, 403):
            raise GraphError(
                f"Microsoft Graph keeldus ligipääsust ({response.status_code}). "
                "Kontrolli rakenduse õigusi ja nõusolekut."
            )
        if response.status_code >= 400:
            raise GraphError(f"Microsoft Graph vastas koodiga {response.status_code}.")
        return response.json()

    # -- operations -----------------------------------------------------

    def get_item_metadata(self) -> RemoteFile:
        payload = self._get_json(
            f"/drives/{self.config.drive_id}/items/{self.config.item_id}"
            "?$select=id,name,size,eTag,cTag,lastModifiedDateTime,file,parentReference"
        )
        if "file" not in payload:
            raise GraphError("Seadistatud OneDrive'i kirje ei ole fail.")
        return _remote_file_from_payload(payload)

    def resolve_share_url(self, url: str) -> RemoteFile:
        """One-time resolution of a sharing URL to stable identifiers."""
        return _remote_file_from_payload(
            self._get_json(f"/shares/{encode_sharing_url(url)}/driveItem")
        )

    def resolve_user_path(self, user_principal_name: str, item_path: str) -> RemoteFile:
        """Least-privilege alternative that works with Files.Read.All."""
        clean_path = item_path.strip("/")
        return _remote_file_from_payload(
            self._get_json(f"/users/{user_principal_name}/drive/root:/{clean_path}")
        )

    def download_to(self, destination: Path) -> int:
        """Stream the workbook to ``destination`` and return its byte count."""
        response = self._request(
            "GET",
            f"{GRAPH_BASE_URL}/drives/{self.config.drive_id}/items/{self.config.item_id}/content",
            stream=True,
        )
        try:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise GraphError("Microsoft Graph ei tagastanud allalaadimise asukohta.")
                response.close()
                # Pre-authenticated URL: the bearer token must not travel here,
                # and the URL itself is a secret that is never logged or stored.
                response = self.session.get(
                    location,
                    timeout=self.config.timeout_seconds,
                    stream=True,
                    allow_redirects=True,
                )
            if response.status_code >= 400:
                raise GraphError(
                    f"Algfaili allalaadimine ebaõnnestus (kood {response.status_code})."
                )

            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > self.config.max_download_bytes:
                raise GraphError(
                    f"Algfail on liiga suur: {declared} baiti. "
                    f"Lubatud kuni {self.config.max_download_bytes} baiti."
                )

            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
            if content_type and content_type not in XLSX_CONTENT_TYPES:
                raise GraphError(f"Ootamatu failitüüp: {content_type}. Oodati XLSX faili.")

            written = 0
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > self.config.max_download_bytes:
                        raise GraphError(
                            "Algfail ületab lubatud suuruse "
                            f"({self.config.max_download_bytes} baiti)."
                        )
                    handle.write(chunk)

            if written == 0:
                raise GraphError("Allalaaditud algfail on tühi.")
            return written
        finally:
            response.close()
