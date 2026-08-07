"""Migrations applied to a database that already holds data.

Every other test in this suite runs against a freshly migrated database, which
is exactly the case a backfill migration cannot fail in — there is nothing to
back-fill. Production had thirty-four documents catalogued by Phase 1 before
Phase 2's migration ran, and that is where `0006` failed:

    IntegrityError: could not create unique index
    DETAIL: Key (public_id)=(a477dbbd-…) is duplicated.

Django evaluates a callable default **once** when adding a column, so all
thirty-four rows received the same UUID, the backfill then found nothing null,
and the unique index refused. The migration is atomic so nothing was harmed —
but nothing in the test suite had a pre-existing row to notice.

This test provides one.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = ("legal_work", "0005_opinion_document_catalogue")
AFTER = ("legal_work", "0006_opinion_matching_and_resources")


def _migrate_to(target):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    executor.loader.build_graph()
    return executor


def test_the_blob_identifier_backfill_survives_pre_existing_rows():
    """Several blobs, catalogued before Phase 2, must each get their own id."""
    _migrate_to(BEFORE)

    old_apps = MigrationExecutor(connection).loader.project_state(BEFORE).apps
    Blob = old_apps.get_model("legal_work", "OpinionDocumentBlob")
    for index in range(5):
        Blob.objects.create(
            sha256=f"{index:064x}",
            storage_key=f"blobs/{index:02x}/{index:064x}.pdf",
            byte_size=1024 + index,
            page_count=1,
            validation_status="valid",
            warning_codes=[],
        )
    assert Blob.objects.count() == 5

    # The step that used to fail.
    _migrate_to(AFTER)

    new_apps = MigrationExecutor(connection).loader.project_state(AFTER).apps
    Migrated = new_apps.get_model("legal_work", "OpinionDocumentBlob")
    identifiers = list(Migrated.objects.values_list("public_id", flat=True))

    assert len(identifiers) == 5
    assert all(value is not None for value in identifiers)
    assert len(set(identifiers)) == 5, "every pre-existing blob needs its own identifier"


def test_a_blob_created_after_the_migration_still_gets_an_identifier():
    """The column keeps its callable default for ordinary inserts."""
    _migrate_to(AFTER)

    from apps.legal_work.opinion_models import OpinionDocumentBlob

    blob = OpinionDocumentBlob.objects.create(
        sha256="f" * 64,
        storage_key=f"blobs/ff/{'f' * 64}.pdf",
        byte_size=2048,
        page_count=2,
        validation_status="valid",
    )

    assert blob.public_id is not None
