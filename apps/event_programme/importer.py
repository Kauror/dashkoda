"""Turn a registered XLSX artifact into a published event-programme snapshot.

One importer serves both the manual command and the scheduled OneDrive sync, so
there is exactly one definition of what a valid workbook is.

Publication rule: either the whole snapshot is written and becomes current, or
nothing changes and the previously current snapshot stays exactly as it was. No
partial snapshot is ever visible to the dashboard.
"""

from __future__ import annotations

import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.core.collapse_guard import collapse_reason
from apps.event_programme.audit_actions import EventProgrammeAudit
from apps.sources.models import ImportRun, SourceArtifact
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    fail_publication,
    start_import_run,
)

from .models import EventProgrammeItem, EventProgrammeSnapshot
from .workbook import ParsedWorkbook, WorkbookContractError, parse_workbook

IMPORTER_NAME = "event_programme_xlsx"

# The importer's own contract version, which is what `ImportRun.import_key` is
# built from. It is not the workbook's declared `schema_version`: this one
# importer reads every workbook version it supports, and the version the file
# declared is recorded on the snapshot instead.
IMPORTER_SCHEMA_VERSION = "1.0"

ALLOWED_EXTENSION = ".xlsx"
BATCH_SIZE = 500


class EventProgrammeImportError(RuntimeError):
    """The import could not be completed. The previous snapshot is untouched."""


@dataclass(frozen=True)
class ImportResult:
    import_run: ImportRun
    snapshot: EventProgrammeSnapshot | None
    dry_run: bool
    rows_added: int
    review_required_count: int

    @property
    def created_snapshot(self) -> bool:
        return self.snapshot is not None


def _require_importable_artifact(artifact: SourceArtifact) -> Path:
    if artifact.is_external:
        raise EventProgrammeImportError(
            "Välise viitega algfaili ei saa importida: privaatset faili ei ole."
        )
    if not artifact.file:
        raise EventProgrammeImportError("Algfailil puudub salvestatud fail.")
    if Path(artifact.original_name or artifact.file.name).suffix.lower() != ALLOWED_EXTENSION:
        raise EventProgrammeImportError("Sündmuste importija loeb ainult .xlsx faile.")
    return Path(artifact.file.path)


def _require_supplied_workbook(workbook_path: Path | str) -> Path:
    """Accept an explicit workbook the caller holds only temporarily.

    This is what lets the public-link synchronisation import without keeping a
    permanent copy: the artifact carries the content identity, and the bytes
    exist only inside the caller's temporary directory for the duration of one
    command. The path itself is never written to PostgreSQL, to the audit trail
    or to import diagnostics.
    """
    path = Path(workbook_path)
    if not path.is_file():
        raise EventProgrammeImportError("Antud töövihiku faili ei leitud.")
    if path.suffix.lower() != ALLOWED_EXTENSION and not zipfile.is_zipfile(path):
        raise EventProgrammeImportError("Sündmuste importija loeb ainult .xlsx faile.")
    return path


def _aware(value):
    if value is not None and timezone.is_naive(value):
        return timezone.make_aware(value)
    return value


def _published_event_count(artifact: SourceArtifact) -> int | None:
    """The event count of the snapshot on the dashboard now, or None if there is none."""
    current = (
        EventProgrammeSnapshot.objects.filter(source=artifact.source, is_current=True)
        .values_list("canonical_event_count", flat=True)
        .first()
    )
    return current


def _build_snapshot(
    *,
    artifact: SourceArtifact,
    run: ImportRun,
    parsed: ParsedWorkbook,
) -> EventProgrammeSnapshot:
    control = parsed.control
    return EventProgrammeSnapshot(
        source=artifact.source,
        artifact=artifact,
        import_run=run,
        schema_version=control.schema_version,
        generator_version=control.generator_version,
        export_refreshed_at=_aware(control.export_refreshed_at),
        is_current=False,
        canonical_event_count=len(parsed.rows),
        dated_event_count=parsed.dated_event_count,
        linked_public_url_count=parsed.linked_public_url_count,
        review_required_count=parsed.review_required_count,
        repeated_service_code_count=control.repeated_service_code_count,
        excluded_event_count=control.excluded_event_count,
        warning_count=control.warning_count,
    )


def _item_for(snapshot: EventProgrammeSnapshot, row) -> EventProgrammeItem:
    return EventProgrammeItem(
        snapshot=snapshot,
        event_id=row.event_id,
        service_code=row.service_code,
        event_name=row.event_name,
        start_date=row.start_date,
        end_date=row.end_date,
        event_year=row.event_year,
        event_month_key=row.event_month_key,
        event_month_label=row.event_month_label,
        event_quarter=row.event_quarter,
        event_status=row.event_status,
        tag_key=row.tag_key,
        tag_label=row.tag_label,
        event_type_key=row.event_type_key,
        event_type_label=row.event_type_label,
        delivery_mode=row.delivery_mode,
        include_status=row.include_status,
        public_url=row.public_url,
        public_link_status=row.public_link_status,
        source_year=row.source_year,
        source_sheet=row.source_sheet,
        source_row=row.source_row,
        source_occurrence_count=row.source_occurrence_count,
        date_parse_status=row.date_parse_status,
        review_required=row.review_required,
        warning_codes=row.warning_codes,
    )


def import_artifact(
    artifact: SourceArtifact,
    *,
    workbook_path: Path | str | None = None,
    dry_run: bool = True,
    allow_collapse: bool = False,
    actor=None,
    correlation_id: uuid.UUID | None = None,
) -> ImportResult:
    """Import one registered artifact.

    Without `workbook_path` the artifact's own stored private file is parsed.
    With `workbook_path` the caller supplies the bytes it just downloaded into a
    temporary directory, and the artifact may then be a metadata-only external
    reference carrying nothing but the content identity. Both paths run this one
    parser: a workbook that validates one way validates the other.

    A dry run validates everything and records the attempt, but writes no
    snapshot and never touches what the dashboard is showing.
    """
    path = (
        _require_supplied_workbook(workbook_path)
        if workbook_path is not None
        else _require_importable_artifact(artifact)
    )

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

        # Checked before the dry-run branch as well: a dry run exists to find out
        # whether the real import would be accepted, so it must ask this too.
        refusal = collapse_reason(
            current_count=_published_event_count(artifact),
            incoming_count=len(parsed.rows),
            noun="sündmust",
            allow_collapse=allow_collapse,
        )
        if refusal is not None:
            raise EventProgrammeImportError(refusal)

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
                review_required_count=parsed.review_required_count,
            )

        with transaction.atomic():
            snapshot = _build_snapshot(artifact=artifact, run=run, parsed=parsed)
            snapshot.save()
            EventProgrammeItem.objects.bulk_create(
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
                action=EventProgrammeAudit.SNAPSHOT_IMPORTED,
                obj=snapshot,
                actor=actor,
                correlation_id=run.correlation_id,
                change_summary={
                    "export_refreshed_at": snapshot.export_refreshed_at.isoformat(),
                    "schema_version": snapshot.schema_version,
                    "canonical_event_count": snapshot.canonical_event_count,
                    "dated_event_count": snapshot.dated_event_count,
                    "review_required_count": snapshot.review_required_count,
                },
            )

        return ImportResult(
            import_run=run,
            snapshot=snapshot,
            dry_run=False,
            rows_added=len(parsed.rows),
            review_required_count=parsed.review_required_count,
        )

    except Exception as error:
        # The atomic block above has already rolled back, so the previous
        # current snapshot is intact and the run can be closed cleanly.
        fail_publication(run, errors=[_sanitized(error)], actor=actor)
        if isinstance(error, WorkbookContractError):
            raise EventProgrammeImportError(str(error)) from error
        raise


def _publish(snapshot: EventProgrammeSnapshot, *, actor=None) -> None:
    """Make `snapshot` the only current one for its source."""
    retired = (
        EventProgrammeSnapshot.objects.select_for_update()
        .filter(source=snapshot.source, is_current=True)
        .exclude(pk=snapshot.pk)
    )
    for previous in retired:
        previous.is_current = False
        previous.save(update_fields=["is_current"])

    snapshot.is_current = True
    snapshot.save(update_fields=["is_current"])

    record_event(
        action=EventProgrammeAudit.SNAPSHOT_PUBLISHED,
        obj=snapshot,
        actor=actor,
        correlation_id=snapshot.import_run.correlation_id,
        change_summary={
            "export_refreshed_at": snapshot.export_refreshed_at.isoformat(),
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
    return [{"code": code, "events": total} for code, total in sorted(counts.items())]


def _sanitized(error: Exception) -> dict:
    """A short, safe description of a failure.

    Only the exception type and a truncated message, never a traceback and
    never file contents.
    """
    message = str(error).strip().replace("\n", " ")
    return {"type": type(error).__name__, "message": message[:300]}
