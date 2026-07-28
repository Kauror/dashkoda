"""Turn a registered XLSX artifact into a published legal-work snapshot.

One importer serves both the manual command and the scheduled OneDrive sync, so
there is exactly one definition of what a valid workbook is.

Publication rule: either the whole snapshot is written and becomes current, or
nothing changes and the previously current snapshot stays exactly as it was. No
partial snapshot is ever visible to the dashboard.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.sources.models import ImportRun, SourceArtifact
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    fail_import_run,
    start_import_run,
)

from .models import LegalWorkItem, LegalWorkSnapshot
from .workbook import ParsedWorkbook, WorkbookContractError, parse_workbook

IMPORTER_NAME = "legal_work_xlsx"

# The importer's own contract version, which is what `ImportRun.import_key` is
# built from. It is not the workbook's declared `schema_version`: this one
# importer reads every workbook version it supports, and the version the file
# declared is recorded on the snapshot instead.
IMPORTER_SCHEMA_VERSION = "1.0"

ALLOWED_EXTENSION = ".xlsx"
BATCH_SIZE = 500


class LegalWorkImportError(RuntimeError):
    """The import could not be completed. The previous snapshot is untouched."""


@dataclass(frozen=True)
class ImportResult:
    import_run: ImportRun
    snapshot: LegalWorkSnapshot | None
    dry_run: bool
    rows_added: int
    warning_record_count: int

    @property
    def created_snapshot(self) -> bool:
        return self.snapshot is not None


def _require_importable_artifact(artifact: SourceArtifact) -> Path:
    if artifact.is_external:
        raise LegalWorkImportError(
            "Välise viitega algfaili ei saa importida: privaatset faili ei ole."
        )
    if not artifact.file:
        raise LegalWorkImportError("Algfailil puudub salvestatud fail.")
    if Path(artifact.original_name or artifact.file.name).suffix.lower() != ALLOWED_EXTENSION:
        raise LegalWorkImportError("Õigusloome importija loeb ainult .xlsx faile.")
    return Path(artifact.file.path)


def _build_snapshot(
    *,
    artifact: SourceArtifact,
    run: ImportRun,
    parsed: ParsedWorkbook,
) -> LegalWorkSnapshot:
    control = parsed.control
    generated_at = control.generated_at
    if timezone.is_naive(generated_at):
        generated_at = timezone.make_aware(generated_at)
    modified_at = control.source_modified_at
    if modified_at is not None and timezone.is_naive(modified_at):
        modified_at = timezone.make_aware(modified_at)

    return LegalWorkSnapshot(
        source=artifact.source,
        artifact=artifact,
        import_run=run,
        schema_version=control.schema_version,
        reporting_date=control.reporting_date,
        workbook_generated_at=generated_at,
        source_file_modified_at=modified_at,
        is_current=False,
        total_record_count=len(parsed.rows),
        open_record_count=parsed.open_count,
        sent_record_count=parsed.sent_count,
        warning_record_count=parsed.warning_record_count,
    )


def _item_for(snapshot: LegalWorkSnapshot, row) -> LegalWorkItem:
    refreshed_at = row.refreshed_at
    if refreshed_at is not None and timezone.is_naive(refreshed_at):
        refreshed_at = timezone.make_aware(refreshed_at)
    return LegalWorkItem(
        snapshot=snapshot,
        record_id=row.record_id,
        source_year=row.source_year,
        source_nr=row.source_nr,
        topic=row.topic,
        act_type=row.act_type,
        received_date=row.received_date,
        deadline_date=row.deadline_date,
        sent_date=row.sent_date,
        sent_status=row.sent_status,
        recipient=row.recipient,
        stage=row.stage,
        stage_key=row.stage_key,
        next_step=row.next_step,
        is_open=row.is_open,
        warning_codes=row.warning_codes,
        source_row=row.source_row,
        refreshed_at=refreshed_at,
    )


def import_artifact(
    artifact: SourceArtifact,
    *,
    dry_run: bool = True,
    actor=None,
    correlation_id: uuid.UUID | None = None,
) -> ImportResult:
    """Import one registered artifact.

    A dry run validates everything and records the attempt, but writes no
    snapshot and never touches what the dashboard is showing.
    """
    path = _require_importable_artifact(artifact)

    run = build_import_run(
        artifact=artifact,
        importer_name=IMPORTER_NAME,
        schema_version=IMPORTER_SCHEMA_VERSION,
        dry_run=dry_run,
        initiated_by=actor,
        actor=actor,
        correlation_id=correlation_id,
    )
    start_import_run(run)

    try:
        parsed = parse_workbook(path)

        if dry_run:
            complete_import_run(
                run,
                rows_added=0,
                rows_skipped=len(parsed.rows),
                warnings=_diagnostics(parsed),
                actor=actor,
            )
            return ImportResult(
                import_run=run,
                snapshot=None,
                dry_run=True,
                rows_added=0,
                warning_record_count=parsed.warning_record_count,
            )

        with transaction.atomic():
            snapshot = _build_snapshot(artifact=artifact, run=run, parsed=parsed)
            snapshot.save()
            LegalWorkItem.objects.bulk_create(
                [_item_for(snapshot, row) for row in parsed.rows],
                batch_size=BATCH_SIZE,
            )
            _publish(snapshot, actor=actor)
            complete_import_run(
                run,
                rows_added=len(parsed.rows),
                warnings=_diagnostics(parsed),
                actor=actor,
            )
            record_event(
                action=AuditAction.LEGAL_WORK_SNAPSHOT_IMPORTED,
                obj=snapshot,
                actor=actor,
                correlation_id=run.correlation_id,
                change_summary={
                    "reporting_date": snapshot.reporting_date.isoformat(),
                    "schema_version": snapshot.schema_version,
                    "total_record_count": snapshot.total_record_count,
                    "open_record_count": snapshot.open_record_count,
                    "warning_record_count": snapshot.warning_record_count,
                },
            )

        return ImportResult(
            import_run=run,
            snapshot=snapshot,
            dry_run=False,
            rows_added=len(parsed.rows),
            warning_record_count=parsed.warning_record_count,
        )

    except Exception as error:
        # The atomic block above has already rolled back, so the previous
        # current snapshot is intact and the run can be closed cleanly.
        run.refresh_from_db()
        if not run.is_terminal:
            fail_import_run(run, errors=[_sanitized(error)], actor=actor)
        if isinstance(error, WorkbookContractError):
            raise LegalWorkImportError(str(error)) from error
        raise


def _publish(snapshot: LegalWorkSnapshot, *, actor=None) -> None:
    """Make `snapshot` the only current one for its source."""
    retired = (
        LegalWorkSnapshot.objects.select_for_update()
        .filter(source=snapshot.source, is_current=True)
        .exclude(pk=snapshot.pk)
    )
    for previous in retired:
        previous.is_current = False
        previous.save(update_fields=["is_current"])

    snapshot.is_current = True
    snapshot.save(update_fields=["is_current"])

    record_event(
        action=AuditAction.LEGAL_WORK_SNAPSHOT_PUBLISHED,
        obj=snapshot,
        actor=actor,
        correlation_id=snapshot.import_run.correlation_id,
        change_summary={
            "reporting_date": snapshot.reporting_date.isoformat(),
            "retired_snapshot_ids": [previous.pk for previous in retired],
        },
    )


def _diagnostics(parsed: ParsedWorkbook) -> list[dict]:
    """Structured counts only.

    Never whole rows and never workbook text: a diagnostics blob is not a place
    to accumulate source content.
    """
    counts: dict[str, int] = {}
    for row in parsed.rows:
        for code in row.warning_codes:
            counts[code] = counts.get(code, 0) + 1
    return [{"code": code, "records": total} for code, total in sorted(counts.items())]


def _sanitized(error: Exception) -> dict:
    """A short, safe description of a failure.

    Only the exception type and a truncated message, never a traceback and
    never file contents.
    """
    message = str(error).strip().replace("\n", " ")
    return {"type": type(error).__name__, "message": message[:300]}
