"""Count the published Koda.ee member directory.

The endpoint returns one object per published member profile. Each carries a
registration code (`crn`) and a profile URL. Both are used **in memory only** —
`crn` to detect duplicates, `url` to check the row is a real member profile on
koda.ee — and neither is returned, stored or logged. What leaves this module is
a single integer and a checksum over that integer.

What the number means, stated precisely: it is the count of member profiles
published in the public Koda.ee member directory at the moment of collection. It
is **not** a count of paid members, invoiced members, accounting membership or
active CRM contracts. No public source establishes those definitions, so this
module does not claim them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings

from apps.core.canonical import canonical_checksum
from apps.core.public_http import PublicFetchError, fetch

logger = logging.getLogger("dashkoda.membership.collector")

DATASET_KEY = "koda-public-members"
# Bumped only when the normalised shape changes in a way that should force a
# re-import of otherwise identical content.
NORMALISED_SCHEMA_VERSION = "1.0"

ACCEPTED_CONTENT_TYPES = frozenset({"application/json", "application/vnd.api+json", "text/json"})


class MembershipCollectionError(RuntimeError):
    """The member directory could not be collected or is not usable."""


@dataclass(frozen=True)
class MembershipCollection:
    """The aggregate and its identity. Deliberately carries no rows."""

    total_members: int
    sha256: str
    size_bytes: int
    canonical: dict
    etag: str
    last_modified: str
    duplicate_identities: int
    rejected_rows: int


def collect_membership(
    *,
    url: str | None = None,
    etag: str = "",
    last_modified: str = "",
    session=None,
) -> MembershipCollection | None:
    """Fetch and count. Returns `None` when the source reports `304`."""
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

    total, duplicates, rejected = _count(result.content)
    canonical = {
        "dataset": DATASET_KEY,
        "schema_version": NORMALISED_SCHEMA_VERSION,
        "total_members": total,
    }
    checksum, size = canonical_checksum(canonical)
    logger.info(
        "membership.collect counted=%s duplicates=%s rejected=%s",
        total,
        duplicates,
        rejected,
    )
    return MembershipCollection(
        total_members=total,
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        etag=result.etag,
        last_modified=result.last_modified,
        duplicate_identities=duplicates,
        rejected_rows=rejected,
    )


def _count(content: bytes) -> tuple[int, int, int]:
    """Count valid member rows. Returns (total, duplicates, rejected).

    Validity is deliberately **structural**, not a judgement about the business:
    a row counts when it is an object carrying a non-empty registration code and
    a member profile URL on koda.ee. Nothing here decides whether a member
    "really" belongs in the directory — Koda's own publication decides that, and
    second-guessing it would silently invent a different number.
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

    seen: set[str] = set()
    duplicates = 0
    rejected = 0
    for row in payload:
        if not isinstance(row, dict):
            rejected += 1
            continue
        identity = str(row.get("crn") or "").strip()
        profile = str(row.get("url") or "").strip()
        if not identity or not _is_member_url(profile):
            rejected += 1
            continue
        if identity in seen:
            # Counted once. The directory should not list one member twice, but
            # a duplicate is a source quirk rather than a reason to refuse the
            # whole collection.
            duplicates += 1
            continue
        seen.add(identity)

    total = len(seen)
    if total == 0:
        raise MembershipCollectionError("Liikmete loendis ei olnud ühtegi kehtivat kirjet.")
    if rejected and rejected > len(payload) // 2:
        raise MembershipCollectionError(
            f"Liikmete loendis oli liiga palju vigaseid ridu: {rejected}/{len(payload)}."
        )

    # `seen` holds registration codes. It goes out of scope here and is never
    # returned, stored or logged.
    return total, duplicates, rejected


def _is_member_url(value: str) -> bool:
    if not value:
        return False
    if value.startswith("/"):
        return True
    try:
        parts = urlparse(value)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme in ("http", "https") and host in settings.KODA_ALLOWED_HOSTS


def is_change_plausible(previous: int | None, current: int) -> tuple[bool, str]:
    """Guard against a source or parsing fault masquerading as membership news.

    The first observation always publishes — there is nothing to compare it
    with. After that, a movement is refused only when it exceeds **both** a
    proportional and an absolute threshold: the absolute floor stops a small
    directory tripping the ratio, and the ratio stops a large directory tripping
    the floor. Nothing here hard-codes an expected member count.
    """
    if previous is None or previous <= 0:
        return True, ""

    delta = abs(current - previous)
    ratio = delta / previous
    if delta > settings.KODA_MEMBERS_MAX_CHANGE_ABSOLUTE and (
        ratio > settings.KODA_MEMBERS_MAX_CHANGE_RATIO
    ):
        return False, (
            f"Liikmete arv muutus ebausutavalt palju: {previous} -> {current} "
            f"({delta} liiget, {ratio:.0%}). Eelmine vaatlus jäi kehtima."
        )
    return True, ""
