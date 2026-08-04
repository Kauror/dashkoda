"""Download the canonical legal-work workbook from a public sharing link.

The hardened implementation — host allowlist, loopback and IP-literal refusal,
manual redirect following, bounded retries, the streamed size cap and checksum,
the structural XLSX proof and the secret-free error messages — lives once in
:mod:`apps.sources.public_download`. This module only names what is specific to
the legal-work feed: which setting supplies the sharing URL, which supplies the
size cap, and how the feed identifies itself in logs.

The target stays one configured workbook on a Microsoft OneDrive/SharePoint
host, and there is deliberately no way for a viewer, an administrator or a
request to supply a URL. The sharing URL is a bearer-style secret and never
reaches a log, an error message or a return value.
"""

from __future__ import annotations

from pathlib import Path

import requests

from apps.sources import public_download as _shared
from apps.sources.public_download import (
    XLSX_MIME_TYPE,
    PublicDownload,
    PublicDownloadError,
    PublicUrlNotConfigured,
    RetryablePublicDownloadError,
    WorkbookSource,
    build_download_url,
)

__all__ = [
    "XLSX_MIME_TYPE",
    "PublicDownload",
    "PublicDownloadError",
    "PublicUrlNotConfigured",
    "RetryablePublicDownloadError",
    "WORKBOOK_SOURCE",
    "build_download_url",
    "download_public_workbook",
    "load_public_url",
    "validate_public_url",
]

WORKBOOK_SOURCE = WorkbookSource(
    url_setting="OIGUSLOOME_PUBLIC_URL",
    max_bytes_setting="LEGAL_WORK_MAX_DOWNLOAD_BYTES",
    user_agent="DashKoda/1.0 (+legal-work workbook sync)",
    log_prefix="legal_work.public_download",
)


def load_public_url() -> str:
    return _shared.load_public_url(WORKBOOK_SOURCE)


def validate_public_url(url: str) -> str:
    return _shared.validate_public_url(url, source=WORKBOOK_SOURCE)


def download_public_workbook(
    destination: Path,
    *,
    url: str | None = None,
    session: requests.Session | None = None,
) -> PublicDownload:
    return _shared.download_public_workbook(WORKBOOK_SOURCE, destination, url=url, session=session)
