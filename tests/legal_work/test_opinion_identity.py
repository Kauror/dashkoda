"""Durable legal-matter identity, and why it cannot be the workbook's own.

Phase 0 measured the reason these tests exist: across seven production
snapshots, 128 of 610 `record_id` values denoted materially different legal
matters, because the identifier tracks a row's *position* and an inserted row
shifts every identifier below it. A resource address built on one would
eventually point a reader at the wrong Chamber opinion.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.legal_work.opinion_identity import (
    IDENTITY_VERSION,
    matter_key,
    matter_key_for,
    resolve_matter_key,
)


class Row:
    """The two fields identity is derived from, and the positional ones it ignores."""

    def __init__(self, topic, received, record_id="OIG-2026-0001", source_nr=1, source_row=1):
        self.topic = topic
        self.received_date = received
        self.record_id = record_id
        self.source_year = 2026
        self.source_nr = source_nr
        self.source_row = source_row


DAY = dt.date(2026, 3, 4)


# -- what identity is, and is not -------------------------------------------


def test_the_same_matter_keeps_its_key_when_its_record_id_moves():
    """The measured failure: an inserted row renumbers everything below it."""
    before = Row("Maksukorralduse seaduse muutmine", DAY, record_id="OIG-2026-0124", source_row=124)
    after = Row("Maksukorralduse seaduse muutmine", DAY, record_id="OIG-2026-0125", source_row=125)

    assert matter_key_for(before) == matter_key_for(after)


def test_a_reused_record_id_does_not_merge_two_matters():
    """The same identifier denoting different business must stay different."""
    one = Row("Kagu-Eesti ettevõtluse arengutoetus", DAY, record_id="OIG-2025-0124")
    two = Row("Ettepanek liigutada saartalituse reservi", DAY, record_id="OIG-2025-0124")

    assert matter_key_for(one) != matter_key_for(two)


def test_the_key_ignores_every_positional_field():
    left = Row("Sama teema", DAY, record_id="A-1", source_nr=1, source_row=1)
    right = Row("Sama teema", DAY, record_id="Z-999", source_nr=999, source_row=999)

    assert matter_key_for(left) == matter_key_for(right)


def test_a_different_received_date_is_a_different_matter():
    assert matter_key_for(Row("Sama teema", DAY)) != matter_key_for(
        Row("Sama teema", dt.date(2026, 5, 5))
    )


def test_a_missing_received_date_still_produces_a_key():
    assert len(matter_key_for(Row("Teema ilma kuupäevata", None))) == 64


def test_the_key_is_a_lower_case_digest():
    key = matter_key_for(Row("Teema", DAY))

    assert len(key) == 64
    assert key == key.lower()
    assert all(c in "0123456789abcdef" for c in key)


def test_the_key_is_deterministic():
    assert matter_key_for(Row("Teema", DAY)) == matter_key_for(Row("Teema", DAY))


def test_normalisation_absorbs_incidental_topic_differences():
    """Whitespace and case are not a new legal matter."""
    assert matter_key(topic="  Maksukorralduse   seadus ", received_date=DAY) == matter_key(
        topic="maksukorralduse seadus", received_date=DAY
    )


def test_diacritics_are_preserved_because_they_change_meaning():
    """`ohutus` and `õhutus` are different words, so different matters."""
    assert matter_key(topic="ohutus", received_date=DAY) != matter_key(
        topic="õhutus", received_date=DAY
    )


def test_a_topic_containing_the_separator_cannot_collide():
    """Canonical JSON rather than concatenation, so no crafted topic collides."""
    assert matter_key(topic='a", "received": "2026-01-01', received_date=DAY) != matter_key(
        topic="a", received_date=dt.date(2026, 1, 1)
    )


def test_the_identity_version_participates_in_the_key():
    """A future canonicalisation change must mint new identities, not silently
    reinterpret established ones."""
    import apps.legal_work.opinion_identity as identity

    first = matter_key(topic="Teema", received_date=DAY)
    original = identity.IDENTITY_VERSION
    try:
        identity.IDENTITY_VERSION = "2.0"
        second = matter_key(topic="Teema", received_date=DAY)
    finally:
        identity.IDENTITY_VERSION = original

    assert first != second
    assert IDENTITY_VERSION == original


# -- collisions ------------------------------------------------------------


def test_two_records_sharing_a_key_are_reported_as_a_collision():
    rows = [Row("Identne teema", DAY, record_id="A"), Row("Identne teema", DAY, record_id="B")]

    grouped, collisions = resolve_matter_key(rows)

    assert len(collisions) == 1
    assert len(grouped[matter_key_for(rows[0])]) == 2


def test_distinct_matters_produce_no_collision():
    rows = [Row("Esimene teema", DAY), Row("Teine teema", DAY)]

    _grouped, collisions = resolve_matter_key(rows)

    assert collisions == set()


@pytest.mark.parametrize("count", [0, 1, 5])
def test_grouping_covers_every_record(count):
    rows = [Row(f"Teema {n}", DAY) for n in range(count)]

    grouped, _collisions = resolve_matter_key(rows)

    assert sum(len(v) for v in grouped.values()) == count
