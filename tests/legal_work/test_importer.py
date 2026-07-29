"""Snapshot publication, immutability and failure behaviour."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.audit.models import AuditAction, AuditEvent
from apps.legal_work.importer import IMPORTER_NAME, LegalWorkImportError, import_artifact
from apps.legal_work.models import (
    LegalWorkItem,
    LegalWorkSnapshot,
    SentStatus,
    SnapshotImmutable,
)
from apps.sources.models import ImportRun, ImportStatus

from .workbook_factory import REPORTING_DATE, synthetic_row

pytestmark = pytest.mark.django_db


def test_live_import_creates_a_complete_current_snapshot(make_workbook, register_workbook):
    artifact = register_workbook(make_workbook())

    result = import_artifact(artifact, dry_run=False)
    snapshot = result.snapshot

    assert snapshot.is_current is True
    assert snapshot.total_record_count == 3
    assert snapshot.items.count() == 3
    assert snapshot.reporting_date == REPORTING_DATE
    assert snapshot.schema_version == "1.1"
    assert result.import_run.status == ImportStatus.SUCCEEDED
    assert result.import_run.rows_added == 3


def test_dry_run_creates_no_snapshot(make_workbook, register_workbook):
    artifact = register_workbook(make_workbook())

    result = import_artifact(artifact, dry_run=True)

    assert result.snapshot is None
    assert LegalWorkSnapshot.objects.count() == 0
    assert result.import_run.dry_run is True
    assert result.import_run.status == ImportStatus.SUCCEEDED


def test_dry_run_does_not_replace_existing_published_data(
    imported_snapshot, make_workbook, register_workbook
):
    replacement = make_workbook(
        rows=[synthetic_row(record_id="SYN-NEW", source_row=2)],
    )
    artifact = register_workbook(replacement)

    import_artifact(artifact, dry_run=True)

    imported_snapshot.refresh_from_db()
    assert imported_snapshot.is_current is True
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1


def test_a_new_snapshot_atomically_replaces_the_previous_one(
    imported_snapshot, make_workbook, register_workbook
):
    replacement = make_workbook(rows=[synthetic_row(record_id="SYN-NEW", source_row=2)])

    new_snapshot = import_artifact(register_workbook(replacement), dry_run=False).snapshot

    imported_snapshot.refresh_from_db()
    assert imported_snapshot.is_current is False
    assert new_snapshot.is_current is True
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1


def test_only_one_current_snapshot_can_exist(imported_snapshot, make_workbook, register_workbook):
    second = import_artifact(
        register_workbook(make_workbook(rows=[synthetic_row(record_id="SYN-X", source_row=2)])),
        dry_run=False,
    ).snapshot

    with pytest.raises(IntegrityError), transaction.atomic():
        LegalWorkSnapshot.objects.filter(pk=imported_snapshot.pk).update(is_current=True)

    assert second.is_current is True


def test_a_failed_import_leaves_the_previous_snapshot_current(
    imported_snapshot, make_workbook, register_workbook
):
    broken = make_workbook(
        rows=[synthetic_row(record_id="SYN-BAD", source_row=2)],
        control_overrides={"total_record_count": 42},
    )
    artifact = register_workbook(broken)

    with pytest.raises(LegalWorkImportError):
        import_artifact(artifact, dry_run=False)

    imported_snapshot.refresh_from_db()
    assert imported_snapshot.is_current is True
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1
    assert LegalWorkSnapshot.objects.count() == 1


def test_a_failed_import_closes_its_run_as_failed(make_workbook, register_workbook):
    artifact = register_workbook(make_workbook(dataset_key="wrong"))

    with pytest.raises(LegalWorkImportError):
        import_artifact(artifact, dry_run=False)

    run = artifact.import_runs.get()
    assert run.status == ImportStatus.FAILED
    assert run.finished_at is not None


def test_failure_diagnostics_carry_no_workbook_content(make_workbook, register_workbook):
    secret_topic = "Sünteetiline salajane pealkiri"
    broken = make_workbook(
        rows=[synthetic_row(record_id="SYN-1", topic=secret_topic, is_open="jah")],
    )

    with pytest.raises(LegalWorkImportError):
        import_artifact(register_workbook(broken), dry_run=False)

    stored = ImportRun.objects.get()
    assert secret_topic not in str(stored.errors)


def test_import_diagnostics_are_counts_not_rows(make_workbook, register_workbook):
    rows = [
        synthetic_row(record_id="SYN-1", warning_codes="missing_stage", source_row=2),
        synthetic_row(record_id="SYN-2", warning_codes="missing_stage", source_row=3),
    ]
    artifact = register_workbook(make_workbook(rows=rows))

    result = import_artifact(artifact, dry_run=False)

    assert result.import_run.warnings == [{"code": "missing_stage", "records": 2}]


def test_reimporting_the_same_content_is_refused_as_a_duplicate_live_import(
    make_workbook, register_workbook
):
    path = make_workbook()
    artifact = register_workbook(path)
    import_artifact(artifact, dry_run=False)

    # Same artifact, same importer, same schema: the import key already has a
    # successful live run, so the registry refuses a second one.
    with pytest.raises(ValidationError, match="importrun_unique_successful_live_import"):
        import_artifact(artifact, dry_run=False)


def test_the_source_artifact_stays_immutable_through_import(make_workbook, register_workbook):
    artifact = register_workbook(make_workbook())
    original_checksum = artifact.sha256

    import_artifact(artifact, dry_run=False)

    artifact.refresh_from_db()
    assert artifact.sha256 == original_checksum


def test_an_imported_row_cannot_be_edited(imported_snapshot):
    item = imported_snapshot.items.first()
    item.topic = "muudetud"

    with pytest.raises(SnapshotImmutable):
        item.save()


def test_a_snapshot_may_only_change_its_current_flag(imported_snapshot):
    imported_snapshot.total_record_count = 999

    with pytest.raises(SnapshotImmutable):
        imported_snapshot.save()


def test_record_id_is_unique_within_a_snapshot(imported_snapshot):
    existing = imported_snapshot.items.first()

    with pytest.raises(IntegrityError), transaction.atomic():
        LegalWorkItem.objects.create(
            snapshot=imported_snapshot,
            record_id=existing.record_id,
            source_year=2099,
            source_nr=99,
            topic="Sünteetiline duplikaat",
            is_open=True,
            source_row=999,
        )


def test_a_sent_record_must_carry_a_sent_date(imported_snapshot):
    with pytest.raises(IntegrityError), transaction.atomic():
        LegalWorkItem.objects.create(
            snapshot=imported_snapshot,
            record_id="SYN-NO-DATE",
            source_year=2099,
            topic="Sünteetiline",
            sent_status=SentStatus.SENT,
            sent_date=None,
            is_open=False,
            source_row=900,
        )


def test_the_model_has_no_lawyer_or_feedback_fields():
    names = {field.name for field in LegalWorkItem._meta.get_fields()}

    for forbidden in (
        "responsible_person",
        "lawyer",
        "assignee",
        "owner",
        "member_feedback_count",
        "feedback",
        "opinion",
        "opinion_document",
    ):
        assert forbidden not in names


def test_import_records_audit_events(make_workbook, register_workbook):
    artifact = register_workbook(make_workbook())

    result = import_artifact(artifact, dry_run=False)

    actions = set(
        AuditEvent.objects.filter(correlation_id=result.import_run.correlation_id).values_list(
            "action", flat=True
        )
    )
    assert AuditAction.LEGAL_WORK_SNAPSHOT_IMPORTED in actions
    assert AuditAction.LEGAL_WORK_SNAPSHOT_PUBLISHED in actions


def test_external_reference_artifacts_cannot_be_imported(legal_work_source):
    from apps.sources.services import register_external_reference

    artifact = register_external_reference(
        source=legal_work_source,
        external_reference="https://example.invalid/synthetic",
    )

    with pytest.raises(LegalWorkImportError, match="Välise viitega"):
        import_artifact(artifact, dry_run=False)


def test_the_importer_declares_a_stable_identity():
    assert IMPORTER_NAME == "legal_work_xlsx"
