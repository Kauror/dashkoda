import hashlib
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.models import AuditAction, AuditEvent
from apps.sources.models import ImportRun, ImportStatus
from apps.sources.services import (
    InvalidImportTransition,
    build_import_run,
    calculate_import_key,
    complete_import_run,
    fail_import_run,
    register_artifact,
    register_external_reference,
    start_import_run,
)

pytestmark = pytest.mark.django_db

IMPORTER = "synthetic-importer"
SCHEMA = "v1"


@pytest.fixture
def artifact(data_source, upload):
    return register_artifact(source=data_source, upload=upload())


def test_import_key_is_sha256_of_the_normalised_triple():
    expected = hashlib.sha256(b"importer\x1fv2\x1fabc").hexdigest()

    assert calculate_import_key("importer", "v2", "abc") == expected


def test_import_key_normalises_whitespace_and_digest_case():
    assert calculate_import_key(" importer ", " v2 ", "ABC") == calculate_import_key(
        "importer", "v2", "abc"
    )


def test_import_key_parts_cannot_collide_by_concatenation():
    assert calculate_import_key("ab", "c", "d" * 64) != calculate_import_key("a", "bc", "d" * 64)


def test_import_key_requires_a_checksum():
    with pytest.raises(ValidationError):
        calculate_import_key(IMPORTER, SCHEMA, "")


def test_external_reference_artifact_cannot_be_imported_yet(data_source):
    external = register_external_reference(
        source=data_source,
        external_reference="https://example.invalid/synthetic",
    )

    with pytest.raises(ValidationError, match="kontrollsumma"):
        build_import_run(artifact=external, importer_name=IMPORTER, schema_version=SCHEMA)


def test_run_takes_its_source_from_the_artifact(artifact, data_source):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)

    assert run.source_id == data_source.pk
    assert run.artifact_id == artifact.pk


def test_mismatched_source_and_artifact_are_refused(artifact, other_data_source):
    run = ImportRun(
        source=other_data_source,
        artifact=artifact,
        importer_name=IMPORTER,
        schema_version=SCHEMA,
        import_key="x" * 64,
    )

    with pytest.raises(ValidationError, match="samale andmeallikale"):
        run.full_clean()


def test_new_run_starts_pending_with_no_timestamps(artifact):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)

    assert run.status == ImportStatus.PENDING
    assert run.started_at is None
    assert run.finished_at is None
    assert run.is_terminal is False


def test_legal_transition_pending_running_succeeded(artifact):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)

    start_import_run(run)
    assert run.status == ImportStatus.RUNNING
    assert run.started_at is not None
    assert run.finished_at is None

    complete_import_run(run, rows_added=3, rows_skipped=1, rows_invalid=0)
    assert run.status == ImportStatus.SUCCEEDED
    assert run.finished_at >= run.started_at
    assert run.is_terminal is True


def test_a_pending_run_may_fail_without_starting(artifact):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)

    fail_import_run(run, errors=[{"row": 1, "message": "synthetic failure"}])

    assert run.status == ImportStatus.FAILED
    assert run.finished_at is not None
    assert run.started_at is not None


@pytest.mark.parametrize("terminal", ["complete", "fail"])
def test_a_terminal_run_cannot_move_again(artifact, terminal):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)
    start_import_run(run)
    if terminal == "complete":
        complete_import_run(run)
    else:
        fail_import_run(run)

    with pytest.raises(InvalidImportTransition):
        start_import_run(run)
    with pytest.raises(InvalidImportTransition):
        complete_import_run(run)
    with pytest.raises(InvalidImportTransition):
        fail_import_run(run)


def test_a_pending_run_cannot_jump_straight_to_success(artifact):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)

    with pytest.raises(InvalidImportTransition):
        complete_import_run(run)


def test_counts_cannot_be_negative(artifact):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)
    run.rows_added = -1

    with pytest.raises(IntegrityError), transaction.atomic():
        run.save()


def test_finished_at_cannot_precede_started_at(artifact):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)
    now = timezone.now()
    run.status = ImportStatus.SUCCEEDED
    run.started_at = now
    run.finished_at = now - timedelta(minutes=1)

    with pytest.raises(IntegrityError), transaction.atomic():
        run.save()


def test_terminal_state_requires_a_finish_time(artifact):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)
    run.status = ImportStatus.SUCCEEDED

    with pytest.raises(IntegrityError), transaction.atomic():
        run.save()


def test_non_terminal_state_cannot_claim_to_have_finished(artifact):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)
    run.status = ImportStatus.RUNNING
    run.started_at = timezone.now()
    run.finished_at = timezone.now()

    with pytest.raises(IntegrityError), transaction.atomic():
        run.save()


def _succeed(artifact, *, dry_run):
    run = build_import_run(
        artifact=artifact,
        importer_name=IMPORTER,
        schema_version=SCHEMA,
        dry_run=dry_run,
    )
    start_import_run(run)
    return complete_import_run(run)


def test_dry_runs_may_repeat_and_do_not_block_a_later_live_import(artifact):
    _succeed(artifact, dry_run=True)
    _succeed(artifact, dry_run=True)

    live = _succeed(artifact, dry_run=False)

    assert live.status == ImportStatus.SUCCEEDED
    assert ImportRun.objects.filter(dry_run=True, status=ImportStatus.SUCCEEDED).count() == 2


def test_a_failed_live_run_may_be_repeated(artifact):
    first = build_import_run(
        artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA, dry_run=False
    )
    fail_import_run(first)

    second = _succeed(artifact, dry_run=False)

    assert second.status == ImportStatus.SUCCEEDED


def test_only_one_successful_live_import_per_key(artifact):
    _succeed(artifact, dry_run=False)

    # The service validates constraints before writing, so the duplicate is
    # reported as a validation error rather than as a database failure.
    with pytest.raises(ValidationError, match="importrun_unique_successful_live_import"):
        _succeed(artifact, dry_run=False)


def test_the_database_also_refuses_a_duplicate_successful_live_import(artifact):
    first = _succeed(artifact, dry_run=False)

    duplicate = ImportRun(
        source=artifact.source,
        artifact=artifact,
        importer_name=IMPORTER,
        schema_version=SCHEMA,
        import_key=first.import_key,
        dry_run=False,
        status=ImportStatus.SUCCEEDED,
        started_at=first.started_at,
        finished_at=first.finished_at,
    )

    # Bypassing full_clean proves the guarantee lives in the database too.
    with pytest.raises(IntegrityError), transaction.atomic():
        duplicate.save()


def test_a_different_schema_version_is_a_different_key(artifact):
    first = _succeed(artifact, dry_run=False)
    second_run = build_import_run(
        artifact=artifact, importer_name=IMPORTER, schema_version="v2", dry_run=False
    )
    start_import_run(second_run)
    complete_import_run(second_run)

    assert first.import_key != second_run.import_key


def test_creation_and_terminal_transitions_are_audited(artifact):
    run = _succeed(artifact, dry_run=True)

    actions = set(
        AuditEvent.objects.filter(correlation_id=run.correlation_id).values_list(
            "action", flat=True
        )
    )
    assert AuditAction.IMPORT_RUN_CREATED in actions
    assert AuditAction.IMPORT_RUN_SUCCEEDED in actions


def test_failure_is_audited_without_leaking_diagnostics(artifact):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)

    fail_import_run(run, errors=[{"detail": "synthetic-secret-value"}])

    event = AuditEvent.objects.get(action=AuditAction.IMPORT_RUN_FAILED)
    # Only a count reaches the audit trail, never the diagnostics themselves.
    assert event.change_summary["error_count"] == 1
    assert "synthetic-secret-value" not in str(event.change_summary)


def test_diagnostics_are_structured_for_a_later_qa_report(artifact):
    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)
    start_import_run(run)
    complete_import_run(run, warnings=[{"row": 4, "code": "unknown_column", "column": "extra"}])

    stored = ImportRun.objects.get(pk=run.pk)
    assert stored.warnings[0]["code"] == "unknown_column"
    assert stored.errors == []


def test_no_domain_records_are_created_by_an_import_run(artifact):
    _succeed(artifact, dry_run=False)

    # PR-05 has no domain models at all; the registry stands alone.
    assert ImportRun.objects.count() == 1
