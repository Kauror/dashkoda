"""Collect the published Koda.ee member directory, row by row.

`collector.py` reads the same endpoint and keeps a single integer. This module
keeps the rows: one registration code and one profile path per published member
profile. Both read the same public list, and they are deliberately separate —
the count is a settled aggregate series with its own guarantees, and neither a
failure nor a schema change on this side may touch it.

**What this adds to what the count already reads is nothing.** The endpoint has
always returned `crn` and `url` per row; the count discards them after
counting. The change is what the product does with them, not what Koda.ee is
asked for, and the fields kept here are the two the list needs: an identity to
join the roster on and a link back to the public profile. No name, county,
phone number or website is collected — the directory shows them, the roster
supplies them for matched members, and an unmatched code is shown by its link.

What the row set means, stated precisely: these are the member profiles
published in the public Koda.ee directory at the moment of collection. It is
not a list of paid members, invoiced members or active CRM contracts, and it is
not the Chamber's own roster — that is what makes comparing the two worth
doing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings

from apps.core.canonical import canonical_checksum
from apps.core.public_http import PublicFetchError, fetch

from .collector import ACCEPTED_CONTENT_TYPES, MembershipCollectionError

logger = logging.getLogger("dashkoda.membership.directory")

DATASET_KEY = "koda-member-directory"
#: Bumped only when the normalised row shape changes in a way that should force
#: a re-import of otherwise identical content.
NORMALISED_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class DirectoryRow:
    """One published profile: an identity and where it is published."""

    registry_code: str
    profile_path: str


@dataclass(frozen=True)
class DirectoryCollection:
    """The published set and its identity."""

    rows: tuple[DirectoryRow, ...]
    sha256: str
    size_bytes: int
    canonical: dict
    etag: str
    last_modified: str
    duplicate_identities: int
    rejected_rows: int

    @property
    def total_rows(self) -> int:
        return len(self.rows)


def collect_directory(
    *,
    url: str | None = None,
    etag: str = "",
    last_modified: str = "",
    session=None,
) -> DirectoryCollection | None:
    """Fetch and normalise. Returns `None` when the source reports `304`."""
    url = url or settings.KODA_MEMBERS_URL
    try:
        result = fetch(
            url,
            allowed_hosts=settings.KODA_ALLOWED_HOSTS,
            accept="application/json",
            max_bytes=settings.KODA_MEMBERS_MAX_BYTES,
            expected_content_types=ACCEPTED_CONTENT_TYPES,
            etag=etag,
            last_modified=last_modified,
            session=session,
        )
    except PublicFetchError as error:
        raise MembershipCollectionError(str(error)) from error

    if result.not_modified:
        return None

    rows, duplicates, rejected = normalise(result.content)
    canonical = {
        "dataset": DATASET_KEY,
        "schema_version": NORMALISED_SCHEMA_VERSION,
        # Sorted by code, so the digest describes the *set* the directory
        # publishes. The endpoint's own row order drifts between responses and
        # hashing it would republish an identical set every morning.
        "entries": [[row.registry_code, row.profile_path] for row in rows],
    }
    checksum, size = canonical_checksum(canonical)
    logger.info(
        "membership.directory.collect rows=%s duplicates=%s rejected=%s",
        len(rows),
        duplicates,
        rejected,
    )
    return DirectoryCollection(
        rows=rows,
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        etag=result.etag,
        last_modified=result.last_modified,
        duplicate_identities=duplicates,
        rejected_rows=rejected,
    )


def normalise(content: bytes) -> tuple[tuple[DirectoryRow, ...], int, int]:
    """Parse the list into sorted rows. Returns (rows, duplicates, rejected).

    Validity is structural in exactly the way the count's is: a row counts when
    it is an object carrying a non-empty registration code and a member profile
    URL on koda.ee. Nothing here decides whether a member "really" belongs in
    the directory — Koda's own publication decides that.
    """
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MembershipCollectionError(
            f"Liikmete loend ei ole kehtiv JSON: {type(error).__name__}."
        ) from error

    if not isinstance(payload, list):
        raise MembershipCollectionError(
            f"Liikmete loendi ülemine tase peab olema massiiv, saadi {type(payload).__name__}."
        )
    if not payload:
        raise MembershipCollectionError("Liikmete loend on tühi.")

    found: dict[str, str] = {}
    duplicates = 0
    rejected = 0
    for row in payload:
        if not isinstance(row, dict):
            rejected += 1
            continue
        identity = "".join(
            character for character in str(row.get("crn") or "").strip() if character.isdigit()
        )
        path = _member_path(str(row.get("url") or "").strip())
        if not identity or path is None:
            rejected += 1
            continue
        if identity in found:
            # One member published twice is a source quirk, not a reason to
            # refuse the whole collection. The first sighting wins.
            duplicates += 1
            continue
        found[identity] = path

    if not found:
        raise MembershipCollectionError("Liikmete loendis ei olnud ühtegi kehtivat kirjet.")
    if rejected and rejected > len(payload) // 2:
        raise MembershipCollectionError(
            f"Liikmete loendis oli liiga palju vigaseid ridu: {rejected}/{len(payload)}."
        )

    rows = tuple(
        DirectoryRow(registry_code=code[:16], profile_path=found[code][:300])
        for code in sorted(found)
    )
    return rows, duplicates, rejected


def _member_path(value: str) -> str | None:
    """The profile's path on koda.ee, or `None` if this is not one.

    A path rather than a URL: the host is application configuration and storing
    it per row would put 3 400 copies of a constant in the database and make a
    future host change a data migration.
    """
    if not value:
        return None
    if value.startswith("/"):
        return value
    try:
        parts = urlparse(value)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or host not in settings.KODA_ALLOWED_HOSTS:
        return None
    return parts.path or None
