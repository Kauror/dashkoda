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


RELATION_BEFORE = "0007_catalogue_filename_normaliser_version"
RELATION_AFTER = "0008_public_opinion_source"


def seed_matched_world(apps):
    """The 0007 state a real deployment carries: a matched relation whose only
    document pointer is its private catalogue entry."""
    import datetime as dt

    now = dt.datetime(2026, 8, 1, 6, 0, tzinfo=dt.UTC)
    source = apps.get_model("sources", "DataSource").objects.create(
        slug="seed-source",
        name="Seed",
        source_type="document",
        expected_update_frequency="irregular",
    )
    artifact = apps.get_model("sources", "SourceArtifact").objects.create(
        source=source,
        original_name="seed",
        sha256="a" * 64,
        size_bytes=1024,
        external_reference="seed:artifact",
    )
    run = apps.get_model("sources", "ImportRun").objects.create(
        source=source,
        artifact=artifact,
        importer_name="seed",
        schema_version="1.0",
        import_key="k" * 64,
        dry_run=False,
        status="completed",
    )
    legal = apps.get_model("legal_work", "LegalWorkSnapshot").objects.create(
        source=source,
        artifact=artifact,
        import_run=run,
        schema_version="1.0",
        reporting_date=dt.date(2026, 7, 1),
        workbook_generated_at=now,
        is_current=True,
    )
    item = apps.get_model("legal_work", "LegalWorkItem").objects.create(
        snapshot=legal,
        record_id="OIG-2026-0001",
        source_year=2026,
        topic="Seemneteema",
        sent_status="sent",
        sent_date=dt.date(2026, 7, 2),
        is_open=False,
        source_row=2,
    )
    run2 = apps.get_model("sources", "ImportRun").objects.create(
        source=source,
        artifact=artifact,
        importer_name="seed-opinions",
        schema_version="1.0",
        import_key="m" * 64,
        dry_run=False,
        status="completed",
    )
    catalogue = apps.get_model("legal_work", "OpinionCatalogueSnapshot").objects.create(
        source=source,
        artifact=artifact,
        import_run=run2,
        source_manifest_checksum="b" * 64,
        extractor_version="1.0",
        observed_at=now,
        entry_count=1,
        valid_count=1,
        extracted_count=1,
        is_current=True,
    )
    blob = apps.get_model("legal_work", "OpinionDocumentBlob").objects.create(
        sha256="c" * 64,
        storage_key=f"blobs/cc/{'c' * 64}.pdf",
        byte_size=1024,
        page_count=1,
        validation_status="valid",
    )
    extraction = apps.get_model("legal_work", "OpinionDocumentExtraction").objects.create(
        blob=blob,
        extractor_name="pypdf",
        extractor_version="1.0",
        status="extracted",
        text="Seemnetekst",
        page_count=1,
    )
    entry = apps.get_model("legal_work", "OpinionCatalogueEntry").objects.create(
        snapshot=catalogue,
        source_provider="directory",
        source_entry_key="seed.pdf",
        original_filename="seed.pdf",
        display_filename="seed.pdf",
        classification="opinion",
        blob=blob,
        extraction=extraction,
        source_order=0,
    )
    matter = apps.get_model("legal_work", "LegalMatter").objects.create(
        matter_key="d" * 64,
        identity_version="1.0",
        last_known_topic="Seemneteema",
    )
    match = apps.get_model("legal_work", "LegalOpinionMatchSnapshot").objects.create(
        legal_snapshot=legal,
        opinion_catalogue_snapshot=catalogue,
        matcher_version="opinion-1.1-norm1.0-extract1.0",
        considered_item_count=1,
        matched_count=1,
        is_current=True,
    )
    decision = apps.get_model("legal_work", "LegalOpinionDecision").objects.create(
        snapshot=match,
        legal_item=item,
        matter=matter,
        decision="matched",
        score=90,
        runner_up_score=0,
        score_margin=90,
        candidate_count=1,
    )
    apps.get_model("legal_work", "LegalOpinionDocumentRelation").objects.create(
        decision=decision,
        entry=entry,
        role="primary",
        is_primary=True,
        score=90,
    )


def test_the_relation_backfill_gives_every_existing_relation_its_document(
    populated_migration,
):
    """Phase 42: the 0008 backfill against a populated 0007 world.

    Every pre-existing relation must gain its entry's blob and extraction,
    keep its entry, and satisfy the new provenance constraint — with no
    resource id, matter key or blob duplicated along the way.
    """
    apps = populated_migration(
        "legal_work", before=RELATION_BEFORE, after=RELATION_AFTER, seed=seed_matched_world
    )

    relation = apps.get_model("legal_work", "LegalOpinionDocumentRelation").objects.get()
    entry = apps.get_model("legal_work", "OpinionCatalogueEntry").objects.get()

    assert relation.blob_id == entry.blob_id
    assert relation.extraction_id == entry.extraction_id
    assert relation.entry_id == entry.pk
    assert relation.public_document_id is None
    assert apps.get_model("legal_work", "OpinionDocumentBlob").objects.count() == 1
    match = apps.get_model("legal_work", "LegalOpinionMatchSnapshot").objects.get()
    assert match.public_opinion_snapshot_id is None
    assert match.is_current


def test_the_relation_migration_survives_an_empty_table(populated_migration):
    apps = populated_migration(
        "legal_work", before=RELATION_BEFORE, after=RELATION_AFTER, seed=lambda apps: None
    )
    assert apps.get_model("legal_work", "LegalOpinionDocumentRelation").objects.count() == 0


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
