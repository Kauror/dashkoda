"""The source-document uniqueness change, applied over an existing history.

`0005` widens `membershipsourcedoc_unique_source_id` from
`(source, external_source_id)` to `(source, external_source_id, import_run)`.

The narrow key made `--supersede-previous` impossible to complete. Superseding
keeps every old row and writes a new generation beside it, and a rebuilt package
necessarily re-describes the same underlying Word documents — so the second
import failed on the first document it met, and the whole transaction rolled
back. Production carries 148 of those documents.

A widened unique constraint cannot fail on rows that already satisfied the
narrow one, which is exactly the claim worth proving rather than assuming: this
is the same class of change as `legal_work` `0006`, which passed every test and
then failed against thirty-four real rows.

Both halves are asserted — that the existing history survives the migration, and
that after it a second import run may re-describe the same documents while the
same run still may not list one twice.
"""

from __future__ import annotations

import pytest
from django.db.utils import IntegrityError

BEFORE = "0004_internal_membership_batch_evidence"
AFTER = "0005_source_document_unique_per_import_run"

#: Enough rows to be a real table rather than a smoke test, and the shape the
#: 2026-08-11 production read reported.
DOCUMENT_COUNT = 148


def seed_history(count: int):
    """One import run's worth of source documents, as `0004` left them."""

    def seed(apps):
        DataSource = apps.get_model("sources", "DataSource")
        SourceArtifact = apps.get_model("sources", "SourceArtifact")
        ImportRun = apps.get_model("sources", "ImportRun")
        Document = apps.get_model("membership", "MembershipHistoricalSourceDocument")

        source = DataSource.objects.create(
            slug="koda-internal-membership-history",
            name="Sisemine liikmeskonna ajalugu",
        )
        artifact = SourceArtifact.objects.create(
            source=source,
            external_reference="synthetic:history",
            original_name="history.zip",
            mime_type="application/zip",
            sha256="a" * 64,
            size_bytes=10,
        )
        run = ImportRun.objects.create(
            source=source,
            artifact=artifact,
            importer_name="membership_history",
            schema_version="2.0",
            status="succeeded",
            dry_run=False,
            started_at="2026-08-01T09:00:00+00:00",
            finished_at="2026-08-01T09:01:00+00:00",
        )
        Document.objects.bulk_create(
            Document(
                source=source,
                import_run=run,
                external_source_id=f"src_{index:016d}",
                filename=f"aruanne-{index:04d}.docx",
                extension=".docx",
                file_sha256=f"{index:064d}",
                observation_date="2020-01-15",
                observation_date_precision="day",
            )
            for index in range(count)
        )
        assert Document.objects.count() == count

    return seed


def test_the_widened_key_survives_an_existing_history(populated_migration):
    """148 documents written under the narrow key must all still be there."""
    apps = populated_migration(
        "membership", before=BEFORE, after=AFTER, seed=seed_history(DOCUMENT_COUNT)
    )

    Document = apps.get_model("membership", "MembershipHistoricalSourceDocument")

    assert Document.objects.count() == DOCUMENT_COUNT
    identifiers = list(Document.objects.values_list("external_source_id", flat=True))
    assert len(set(identifiers)) == DOCUMENT_COUNT, "no row was merged away"


def test_a_second_run_may_re_describe_the_same_documents(populated_migration):
    """The point of the change: superseding writes a new generation beside the
    old one, and both name the same files."""
    apps = populated_migration(
        "membership", before=BEFORE, after=AFTER, seed=seed_history(DOCUMENT_COUNT)
    )

    Document = apps.get_model("membership", "MembershipHistoricalSourceDocument")
    ImportRun = apps.get_model("sources", "ImportRun")
    first = Document.objects.first()
    second_run = ImportRun.objects.create(
        source_id=first.source_id,
        artifact_id=first.import_run.artifact_id,
        importer_name="membership_history",
        schema_version="2.0",
        status="succeeded",
        dry_run=False,
        started_at="2026-08-11T09:00:00+00:00",
        finished_at="2026-08-11T09:01:00+00:00",
    )

    Document.objects.create(
        source_id=first.source_id,
        import_run=second_run,
        external_source_id=first.external_source_id,
        filename=first.filename,
        extension=".docx",
        file_sha256=first.file_sha256,
        observation_date="2020-01-15",
        observation_date_precision="day",
    )

    assert Document.objects.filter(external_source_id=first.external_source_id).count() == 2, (
        "the same file is described once per import generation"
    )


def test_one_run_still_may_not_list_a_document_twice(populated_migration):
    """The guarantee the narrow key was protecting is kept, not dropped."""
    apps = populated_migration(
        "membership", before=BEFORE, after=AFTER, seed=seed_history(DOCUMENT_COUNT)
    )

    Document = apps.get_model("membership", "MembershipHistoricalSourceDocument")
    first = Document.objects.first()

    with pytest.raises(IntegrityError):
        Document.objects.create(
            source_id=first.source_id,
            import_run_id=first.import_run_id,
            external_source_id=first.external_source_id,
            filename=first.filename,
            extension=".docx",
            file_sha256=first.file_sha256,
            observation_date="2020-01-15",
            observation_date_precision="day",
        )
