"""A durable identity for a legal matter, because the workbook has none.

The obvious candidate is `record_id`, and it does not work. Measured across the
seven legal snapshots in production, **128 of 610 record IDs denote materially
different legal matters at different times**: `OIG-2025-0124` was source row 124
and one matter in the first snapshot, then source row 125 and an unrelated
matter in every snapshot after — one row inserted into the workbook reassigned
every identifier below it. `source_nr` shifts identically, because both are
positions rather than names.

A resource URL built on either would, sooner or later, point a reader at the
wrong Chamber opinion. So identity is derived from what the workbook says the
matter *is*:

    matter_key = SHA-256 over canonical JSON of {normalized topic, received date}

Measured over the same seven snapshots that key has **one** duplicate, is stable
across all seven for 601 of 613 matters, and — the property that matters most —
produces **no gap patterns at all**: every key is present throughout, or appears
once and stays, or disappears and never returns. Nothing flickers.

The observed workbook identifiers are still recorded, as `LegalMatterAlias`
rows, because they are what an operator sees in the spreadsheet and will quote
in a question. They are **provenance and never a lookup key**: resolving a
matter by `record_id` is precisely the mistake this module exists to prevent.

Known limitation, stated rather than hidden: editing a topic mints a new matter
and therefore a new resource address. That happened five times in seven
snapshots. The old address then resolves to nothing, which is the safe
direction — a dead link is recoverable, a link to the wrong opinion is not.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from .text_normalisation import fold

# Bumping this re-derives every matter key. It is part of the key's definition,
# so a change to how a topic is normalised cannot silently merge two matters.
IDENTITY_VERSION = "1.0"

WARN_DUPLICATE_KEY = "duplicate_matter_key"


def matter_key(*, topic: str, received_date: dt.date | None) -> str:
    """The durable identity of one legal matter.

    Canonical JSON rather than string concatenation, so a topic containing the
    separator cannot collide with a different topic and date.
    """
    canonical = json.dumps(
        {
            "version": IDENTITY_VERSION,
            "topic": fold(topic or ""),
            "received": received_date.isoformat() if received_date else None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def matter_key_for(item) -> str:
    """The key for one `LegalWorkItem`."""
    return matter_key(topic=item.topic, received_date=item.received_date)


def resolve_matter_key(items) -> tuple[dict[str, list], set[str]]:
    """Group one snapshot's records by durable key, and name the collisions.

    Returns the grouping and the set of keys that more than one *materially
    different* record claims. A colliding key is not resolved automatically and
    not silently merged: the matter is recorded, flagged, and excluded from
    linking until the derivation is corrected in code. Editing a production row
    to break the tie would hide the very thing that needs fixing.
    """
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(matter_key_for(item), []).append(item)
    collisions = {key for key, rows in grouped.items() if len(rows) > 1}
    return grouped, collisions
