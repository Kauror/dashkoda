"""Service layer for sources, artifacts and import runs.

All state changes live here rather than in views, admin callbacks or signals, so
the rules are readable in one place and the audit trail cannot be forgotten.
"""

import hashlib
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event

from .models import DataSource, ImportRun, ImportStatus, SourceArtifact

CHUNK_SIZE = 64 * 1024

# Legal moves of the import state machine. Terminal states have no successors.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    ImportStatus.PENDING: frozenset({ImportStatus.RUNNING, ImportStatus.FAILED}),
    ImportStatus.RUNNING: frozenset({ImportStatus.SUCCEEDED, ImportStatus.FAILED}),
    ImportStatus.SUCCEEDED: frozenset(),
    ImportStatus.FAILED: frozenset(),
}


class InvalidImportTransition(ValidationError):
    """Raised when an import run is asked to move to an illegal state."""


class ArtifactRejected(ValidationError):
    """Raised when an upload fails a registration rule."""


# --------------------------------------------------------------------------
# Data sources
# --------------------------------------------------------------------------


def create_data_source(*, actor=None, correlation_id=None, **fields) -> DataSource:
    source = DataSource(**fields)
    source.full_clean()
    with transaction.atomic():
        source.save()
        record_event(
            action=AuditAction.DATA_SOURCE_CREATED,
            obj=source,
            actor=actor,
            correlation_id=correlation_id,
            change_summary={"slug": source.slug, "name": source.name},
        )
    return source


def update_data_source(source: DataSource, *, actor=None, correlation_id=None, **fields):
    """Apply a material update and record what changed.

    Only the fields that actually changed are summarised, so the audit trail
    stays readable.
    """
    changed = {}
    for field, value in fields.items():
        previous = getattr(source, field)
        if previous != value:
            changed[field] = {"from": _plain(previous), "to": _plain(value)}
            setattr(source, field, value)

    if not changed:
        return source

    source.full_clean()
    with transaction.atomic():
        source.save()
        deactivated = changed.get("is_active", {}).get("to") is False
        record_event(
            action=(
                AuditAction.DATA_SOURCE_DEACTIVATED
                if deactivated
                else AuditAction.DATA_SOURCE_UPDATED
            ),
            obj=source,
            actor=actor,
            correlation_id=correlation_id,
            change_summary={"changed": changed},
        )
    return source


def deactivate_data_source(source: DataSource, *, actor=None, correlation_id=None) -> DataSource:
    """Deactivate rather than delete. Referenced sources are never removed."""
    return update_data_source(source, actor=actor, correlation_id=correlation_id, is_active=False)


def _plain(value):
    """Make a value safe to store in JSON."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# --------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------


def calculate_sha256(file_object) -> tuple[str, int]:
    """Stream the upload and return its checksum and true byte count.

    The checksum is always computed here from the actual bytes. A client-
    supplied checksum or size is never trusted.
    """
    digest = hashlib.sha256()
    size = 0
    file_object.seek(0)
    for chunk in iter(lambda: file_object.read(CHUNK_SIZE), b""):
        digest.update(chunk)
        size += len(chunk)
    file_object.seek(0)
    return digest.hexdigest(), size


def register_artifact(
    *,
    source: DataSource,
    upload,
    original_name: str | None = None,
    mime_type: str = "",
    access_level: str | None = None,
    uploaded_by=None,
    actor=None,
    correlation_id=None,
) -> SourceArtifact:
    """Register one immutable original file under a source."""
    name = original_name or getattr(upload, "name", "") or ""
    extension = Path(name).suffix.lower()
    allowed = settings.SOURCE_ARTIFACT_ALLOWED_EXTENSIONS
    if extension not in allowed:
        raise ArtifactRejected(
            f"Faili laiend ei ole lubatud: {extension or '(puudub)'}. "
            f"Lubatud: {', '.join(sorted(allowed))}."
        )

    checksum, size = calculate_sha256(upload)
    limit = settings.SOURCE_ARTIFACT_MAX_BYTES
    if size > limit:
        raise ArtifactRejected(f"Fail on liiga suur: {size} baiti. Lubatud kuni {limit} baiti.")
    if size == 0:
        raise ArtifactRejected("Tühja faili ei registreerita.")

    if SourceArtifact.objects.filter(source=source, sha256=checksum).exists():
        raise ArtifactRejected("Selle allika all on sama sisuga fail juba registreeritud.")

    artifact = SourceArtifact(
        source=source,
        original_name=name[:255],
        mime_type=mime_type[:128],
        sha256=checksum,
        size_bytes=size,
        access_level=access_level or SourceArtifact._meta.get_field("access_level").default,
        uploaded_by=uploaded_by,
    )
    with transaction.atomic():
        # The stored path is generated; the client filename is only metadata.
        artifact.file.save(name, upload, save=False)
        artifact.full_clean(exclude=["file"])
        artifact.save()
        record_event(
            action=AuditAction.ARTIFACT_REGISTERED,
            obj=artifact,
            actor=actor or uploaded_by,
            correlation_id=correlation_id,
            change_summary={
                "source": source.slug,
                "original_name": artifact.original_name,
                "sha256": checksum,
                "size_bytes": size,
            },
        )
    return artifact


def register_external_reference(
    *,
    source: DataSource,
    external_reference: str,
    original_name: str = "",
    access_level: str | None = None,
    uploaded_by=None,
    actor=None,
    correlation_id=None,
) -> SourceArtifact:
    """Register a controlled reference to material held elsewhere.

    No checksum exists for these, so they cannot be imported yet; see
    :func:`build_import_run`.
    """
    artifact = SourceArtifact(
        source=source,
        original_name=original_name[:255],
        external_reference=external_reference.strip(),
        access_level=access_level or SourceArtifact._meta.get_field("access_level").default,
        uploaded_by=uploaded_by,
    )
    artifact.full_clean()
    with transaction.atomic():
        artifact.save()
        record_event(
            action=AuditAction.ARTIFACT_REGISTERED,
            obj=artifact,
            actor=actor or uploaded_by,
            correlation_id=correlation_id,
            change_summary={
                "source": source.slug,
                "external_reference": artifact.external_reference,
            },
        )
    return artifact


def record_artifact_download(artifact: SourceArtifact, *, actor, correlation_id=None) -> None:
    record_event(
        action=AuditAction.ARTIFACT_DOWNLOADED,
        obj=artifact,
        actor=actor,
        correlation_id=correlation_id,
        change_summary={
            "source": artifact.source.slug,
            "original_name": artifact.original_name,
            "sha256": artifact.sha256,
        },
    )


# --------------------------------------------------------------------------
# Import runs
# --------------------------------------------------------------------------


def calculate_import_key(importer_name: str, schema_version: str, artifact_sha256: str) -> str:
    """SHA-256 over the normalised importer, schema version and artifact digest.

    Normalisation strips surrounding whitespace and lowercases the digest, and
    the parts are joined with a separator that cannot occur inside them, so two
    different triples can never collide by concatenation.
    """
    if not artifact_sha256:
        raise ValidationError("Impordivõti nõuab algfaili kontrollsummat.")
    parts = (
        importer_name.strip(),
        schema_version.strip(),
        artifact_sha256.strip().lower(),
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def build_import_run(
    *,
    artifact: SourceArtifact,
    importer_name: str,
    schema_version: str,
    dry_run: bool = True,
    initiated_by=None,
    actor=None,
    correlation_id=None,
) -> ImportRun:
    """Create a pending run for an artifact.

    External-reference artifacts are refused: the import key is defined over the
    raw file digest, and inventing one for material we do not hold would make
    idempotency meaningless.
    """
    if artifact.is_external:
        raise ValidationError(
            "Välise viitega algfaili ei saa veel importida: kontrollsumma puudub."
        )

    run = ImportRun(
        source=artifact.source,
        artifact=artifact,
        importer_name=importer_name,
        schema_version=schema_version,
        import_key=calculate_import_key(importer_name, schema_version, artifact.sha256),
        dry_run=dry_run,
        status=ImportStatus.PENDING,
        initiated_by=initiated_by,
        correlation_id=correlation_id or uuid.uuid4(),
    )
    run.full_clean()
    with transaction.atomic():
        run.save()
        record_event(
            action=AuditAction.IMPORT_RUN_CREATED,
            obj=run,
            actor=actor or initiated_by,
            correlation_id=run.correlation_id,
            change_summary={
                "importer_name": run.importer_name,
                "schema_version": run.schema_version,
                "import_key": run.import_key,
                "dry_run": run.dry_run,
            },
        )
    return run


def _require_transition(run: ImportRun, new_status: str) -> None:
    allowed = ALLOWED_TRANSITIONS[run.status]
    if new_status not in allowed:
        raise InvalidImportTransition(f"Olekut ei saa muuta: {run.status} -> {new_status}.")


def start_import_run(run: ImportRun) -> ImportRun:
    _require_transition(run, ImportStatus.RUNNING)
    run.status = ImportStatus.RUNNING
    run.started_at = run.started_at or timezone.now()
    run.save(update_fields=["status", "started_at", "updated_at"])
    return run


def complete_import_run(
    run: ImportRun,
    *,
    rows_added: int = 0,
    rows_skipped: int = 0,
    rows_invalid: int = 0,
    warnings: list | None = None,
    actor=None,
) -> ImportRun:
    _require_transition(run, ImportStatus.SUCCEEDED)
    now = timezone.now()
    run.status = ImportStatus.SUCCEEDED
    run.started_at = run.started_at or now
    run.finished_at = now
    run.rows_added = rows_added
    run.rows_skipped = rows_skipped
    run.rows_invalid = rows_invalid
    run.warnings = warnings or []
    with transaction.atomic():
        run.full_clean()
        run.save()
        record_event(
            action=AuditAction.IMPORT_RUN_SUCCEEDED,
            obj=run,
            actor=actor or run.initiated_by,
            correlation_id=run.correlation_id,
            change_summary={
                "import_key": run.import_key,
                "dry_run": run.dry_run,
                "rows_added": rows_added,
                "rows_skipped": rows_skipped,
                "rows_invalid": rows_invalid,
            },
        )
    return run


def fail_import_run(run: ImportRun, *, errors: list | None = None, actor=None) -> ImportRun:
    _require_transition(run, ImportStatus.FAILED)
    now = timezone.now()
    run.status = ImportStatus.FAILED
    # A run can fail before it starts; the timestamps must still be coherent.
    run.started_at = run.started_at or now
    run.finished_at = now
    run.errors = errors or []
    with transaction.atomic():
        run.full_clean()
        run.save()
        record_event(
            action=AuditAction.IMPORT_RUN_FAILED,
            obj=run,
            actor=actor or run.initiated_by,
            correlation_id=run.correlation_id,
            change_summary={
                "import_key": run.import_key,
                "dry_run": run.dry_run,
                "error_count": len(run.errors),
            },
        )
    return run
