"""Migrations applied to a database that already holds data.

`0006` added a unique `public_id` with a callable default to a table that
already had thirty-four rows in production:

    IntegrityError: could not create unique index
    DETAIL: Key (public_id)=(a477dbbd-…) is duplicated.

Django evaluates a callable default **once** when adding a column, so every
existing row received the same UUID, the backfill then found nothing null, and
the unique index refused. The migration is atomic so nothing was harmed — but
nothing in the test suite had a pre-existing row to notice.

This file provides those rows. The mechanism it used to carry inline now lives
in `tests/migration_harness.py`, because the gap it closes is not specific to
this migration: see `AGENTS.md` for which future migrations are required to
bring a test like this with them.
"""

from __future__ import annotations

import pytest

BEFORE = "0005_opinion_document_catalogue"
AFTER = "0006_opinion_matching_and_resources"


def seed_blobs(count: int):
    """Catalogue `count` documents the way Phase 1 left them: no `public_id`."""

    def seed(apps):
        Blob = apps.get_model("legal_work", "OpinionDocumentBlob")
        for index in range(count):
            Blob.objects.create(
                sha256=f"{index:064x}",
                storage_key=f"blobs/{index:02x}/{index:064x}.pdf",
                byte_size=1024 + index,
                page_count=1,
                validation_status="valid",
                warning_codes=[],
            )
        assert Blob.objects.count() == count

    return seed


def test_the_blob_identifier_backfill_survives_pre_existing_rows(populated_migration):
    """Several blobs, catalogued before Phase 2, must each get their own id."""
    apps = populated_migration("legal_work", before=BEFORE, after=AFTER, seed=seed_blobs(5))

    identifiers = list(
        apps.get_model("legal_work", "OpinionDocumentBlob").objects.values_list(
            "public_id", flat=True
        )
    )

    assert len(identifiers) == 5
    assert all(value is not None for value in identifiers)
    assert len(set(identifiers)) == 5, "every pre-existing blob needs its own identifier"


def test_one_pre_existing_row_is_enough_to_reach_the_backfill(populated_migration):
    """The single-row case still goes through the backfill, not the default."""
    apps = populated_migration("legal_work", before=BEFORE, after=AFTER, seed=seed_blobs(1))

    blob = apps.get_model("legal_work", "OpinionDocumentBlob").objects.get()

    assert blob.public_id is not None


def test_an_empty_table_still_migrates(populated_migration):
    """The case that always worked, kept so a fix cannot break it."""
    apps = populated_migration("legal_work", before=BEFORE, after=AFTER, seed=lambda apps: None)

    assert apps.get_model("legal_work", "OpinionDocumentBlob").objects.count() == 0


@pytest.mark.django_db
def test_a_blob_created_after_the_migration_still_gets_an_identifier():
    """The column keeps its callable default for ordinary inserts.

    No harness: this is the ordinary post-migration state every other test runs
    in, which is exactly the point — the backfill must not have cost the column
    its default.
    """
    from apps.legal_work.opinion_models import OpinionDocumentBlob

    blob = OpinionDocumentBlob.objects.create(
        sha256="f" * 64,
        storage_key=f"blobs/ff/{'f' * 64}.pdf",
        byte_size=2048,
        page_count=2,
        validation_status="valid",
    )

    assert blob.public_id is not None
