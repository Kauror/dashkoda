"""Deterministic canonical JSON and its checksum.

The checksum that decides "has anything changed?" must describe **the data
DashKoda consumes**, not the bytes a website happened to send. A public page
re-renders with different whitespace, a reordered attribute or a new build hash
on every deploy; hashing the raw response would report a change every morning
and republish identical data.

So each collector normalises its source into a small structure containing only
the fields the dashboard uses, and hashes *that*. Two responses that differ only
in markup, header order or element order produce the same checksum and are
correctly reported as unchanged.

Rules, so the digest is reproducible across processes and Python versions:

- UTF-8, with real characters rather than ``\\uXXXX`` escapes;
- object keys sorted;
- list order fixed by the collector before it gets here;
- compact separators, so spacing cannot drift;
- no fetch timestamp anywhere inside — when the data is identical the checksum
  must be identical, and *when it was fetched* is recorded separately on the
  observation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

# Separators without spaces: a formatter change must never alter the digest.
_SEPARATORS = (",", ":")


def _default(value):
    """Stable text for the few non-JSON types collectors legitimately hold."""
    if isinstance(value, dt.datetime):
        # Always offset-aware and normalised to UTC, so the same instant written
        # in two timezones hashes identically.
        if value.tzinfo is None:
            raise TypeError("A naive datetime cannot be canonicalised.")
        return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dt.date):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not canonicalisable.")


def canonical_json(payload) -> bytes:
    """Serialise `payload` deterministically as UTF-8 bytes."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=_SEPARATORS,
        default=_default,
        allow_nan=False,
    ).encode("utf-8")


def canonical_checksum(payload) -> tuple[str, int]:
    """Return the SHA-256 hex digest and byte length of the canonical form.

    The length is the size of the *normalised* document, which is what the
    artifact records — not the size of the original response.
    """
    encoded = canonical_json(payload)
    return hashlib.sha256(encoded).hexdigest(), len(encoded)
