"""Cron-safe synchronisation of the legal-work workbook from OneDrive.

The whole run is guarded by a PostgreSQL advisory lock, so two overlapping
invocations can never both import. A host-side `flock` is documented as
defence in depth, but the application-level guarantee lives here: it survives
being started from a different host, container or shell.

Failure is never destructive. Whatever goes wrong, the previously published
snapshot stays current and the dashboard keeps showing the last good data with
an honest "last check failed" note.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import connection, transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.core.feed_sync import find_published_artifact
from apps.sources.models import SourceArtifact
from apps.sources.services import calculate_sha256, register_artifact

from .bootstrap import ensure_legal_work_source
from .graph import GraphClient, GraphError, RemoteFile, load_graph_settings
from .importer import IMPORTER_NAME, import_artifact
from .models import LegalWorkFeedState, LegalWorkSnapshot, SyncResult

logger = logging.getLogger("dashkoda.legal_work.sync")

WORKBOOK_FILENAME = "dashkoda_oigusloome.xlsx"

# Stable, derived from a name so it cannot collide with an ad-hoc integer
# someone else picks later.
ADVISORY_LOCK_NAMESPACE = "dashkoda.legal_work.sync_oigusloome"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_LOCKED = 3


class SyncLocked(RuntimeError):
    """Another synchronisation is already running."""


@dataclass
class SyncOutcome:
    result: str
    detail: str = ""
    snapshot_id: int | None = None
    reporting_date: str | None = None
    rows_imported: int = 0
    dry_run: bool = False
    warnings: list = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.result == SyncResult.FAILED:
            return EXIT_FAILED
        return EXIT_OK

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "detail": self.detail,
            "snapshot_id": self.snapshot_id,
            "reporting_date": self.reporting_date,
            "rows_imported": self.rows_imported,
            "dry_run": self.dry_run,
            "warnings": self.warnings,
        }


def advisory_lock_key(name: str = ADVISORY_LOCK_NAMESPACE) -> int:
    """A stable signed 64-bit key for `pg_try_advisory_lock`."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


@contextmanager
def advisory_lock(name: str = ADVISORY_LOCK_NAMESPACE):
    """Session-level advisory lock held for the whole run.

    Session level rather than transaction level, because the sync downloads and
    parses outside any transaction and holding one open for that long would pin
    a connection and bloat the database.
    """
    key = advisory_lock_key(name)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = cursor.fetchone()[0]
    if not acquired:
        raise SyncLocked("Teine sünkroonimine juba käib.")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def get_feed_state(source) -> LegalWorkFeedState:
    state, _created = LegalWorkFeedState.objects.get_or_create(source=source)
    return state


def record_failure(state: LegalWorkFeedState, message: str, *, correlation_id) -> None:
    """Record a sanitized failure without touching the published snapshot.

    Shared by both collection routes, because "the last check failed and the
    dashboard keeps showing the previous data" has to mean the same thing
    whichever way the workbook was fetched.
    """
    state.last_result = SyncResult.FAILED
    state.last_error_summary = message[:500]
    state.save(update_fields=["last_result", "last_error_summary", "last_checked_at", "updated_at"])
    record_event(
        action=AuditAction.LEGAL_WORK_SYNC_FAILED,
        obj=state.source,
        correlation_id=correlation_id,
        change_summary={"detail": message[:300]},
    )


def synchronize(
    *,
    dry_run: bool = False,
    force: bool = False,
    actor=None,
    client: GraphClient | None = None,
) -> SyncOutcome:
    """Run one synchronisation attempt.

    `force` re-downloads and re-imports even when the remote metadata looks
    unchanged, which is how an operator recovers after fixing a bad workbook.
    """
    correlation_id = uuid.uuid4()
    source = ensure_legal_work_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(source)

    now = timezone.now()
    state.last_checked_at = now
    state.save(update_fields=["last_checked_at", "updated_at"])

    if client is None:
        client = GraphClient(load_graph_settings())

    try:
        remote = client.get_item_metadata()
    except GraphError as error:
        record_failure(state, str(error), correlation_id=correlation_id)
        logger.warning("legal_work.sync failed during metadata: %s", error)
        return SyncOutcome(result=SyncResult.FAILED, detail=str(error))

    unchanged = not force and remote.matches(
        etag=state.remote_etag,
        modified_at=state.remote_modified_at,
        size_bytes=state.remote_size_bytes,
    )
    if unchanged and state.current_snapshot_id is not None:
        return _record_unchanged(state, remote, correlation_id=correlation_id)

    with tempfile.TemporaryDirectory(prefix="dashkoda-oigusloome-") as directory:
        download_path = Path(directory) / WORKBOOK_FILENAME
        try:
            size = client.download_to(download_path)
        except GraphError as error:
            record_failure(state, str(error), correlation_id=correlation_id)
            logger.warning("legal_work.sync failed during download: %s", error)
            return SyncOutcome(result=SyncResult.FAILED, detail=str(error))

        with download_path.open("rb") as handle:
            checksum, _size = calculate_sha256(handle)

        # An artifact left behind by a dry run or a failed run is reused rather
        # than counted as published: a dry run must never block the later live
        # import of the same bytes.
        artifact, already_published = find_published_artifact(source, checksum, IMPORTER_NAME)
        if already_published and not force:
            return _record_unchanged(state, remote, correlation_id=correlation_id)

        try:
            artifact = artifact or _register(source, download_path, checksum, correlation_id, actor)
            result = import_artifact(
                artifact,
                dry_run=dry_run,
                actor=actor,
                correlation_id=correlation_id,
            )
        except Exception as error:
            # Deliberately broad. This is a nightly unattended job: every
            # failure, including a constraint violation from the import
            # registry, must be recorded and reported rather than escaping as a
            # traceback. The previous snapshot is already safe, because the
            # importer rolled its transaction back.
            message = f"{type(error).__name__}: {error}".replace("\n", " ")
            record_failure(state, message, correlation_id=correlation_id)
            logger.warning("legal_work.sync failed during import: %s", message)
            return SyncOutcome(result=SyncResult.FAILED, detail=message)

    logger.info(
        "legal_work.sync imported rows=%s dry_run=%s size=%s",
        result.rows_added,
        dry_run,
        size,
    )

    if dry_run:
        # A dry run must leave the published data and the recorded feed state
        # exactly as they were, so only `last_checked_at` above has moved.
        return SyncOutcome(
            result=SyncResult.IMPORTED,
            detail="Kuivkäivitus: andmeid ei avaldatud.",
            rows_imported=0,
            dry_run=True,
            warnings=result.import_run.warnings,
        )

    snapshot = result.snapshot
    _record_imported(state, remote, snapshot, correlation_id=correlation_id)
    return SyncOutcome(
        result=SyncResult.IMPORTED,
        detail="Uus hetkeseis avaldatud.",
        snapshot_id=snapshot.pk,
        reporting_date=snapshot.reporting_date.isoformat(),
        rows_imported=result.rows_added,
        warnings=result.import_run.warnings,
    )


def _register(source, path: Path, checksum: str, correlation_id, actor) -> SourceArtifact:
    with path.open("rb") as handle:
        return register_artifact(
            source=source,
            upload=File(handle, name=WORKBOOK_FILENAME),
            original_name=WORKBOOK_FILENAME,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            uploaded_by=actor,
            actor=actor,
            correlation_id=correlation_id,
        )


def _record_unchanged(state, remote: RemoteFile, *, correlation_id) -> SyncOutcome:
    with transaction.atomic():
        state.last_result = SyncResult.UNCHANGED
        state.last_error_summary = ""
        state.remote_etag = remote.etag
        state.remote_modified_at = remote.modified_at
        state.remote_size_bytes = remote.size_bytes
        state.save(
            update_fields=[
                "last_result",
                "last_error_summary",
                "remote_etag",
                "remote_modified_at",
                "remote_size_bytes",
                "updated_at",
            ]
        )
        record_event(
            action=AuditAction.LEGAL_WORK_SYNC_UNCHANGED,
            obj=state.source,
            correlation_id=correlation_id,
            change_summary={"remote_name": remote.name},
        )
    logger.info("legal_work.sync unchanged")
    return SyncOutcome(
        result=SyncResult.UNCHANGED,
        detail="Kaugfail ei ole pärast eelmist importi muutunud.",
        snapshot_id=state.current_snapshot_id,
    )


def _record_imported(
    state, remote: RemoteFile, snapshot: LegalWorkSnapshot, *, correlation_id
) -> None:
    now = timezone.now()
    state.last_result = SyncResult.IMPORTED
    state.last_error_summary = ""
    state.last_successful_sync_at = now
    state.last_changed_at = now
    state.remote_etag = remote.etag
    state.remote_modified_at = remote.modified_at
    state.remote_size_bytes = remote.size_bytes
    state.current_snapshot = snapshot
    state.save(
        update_fields=[
            "last_result",
            "last_error_summary",
            "last_successful_sync_at",
            "last_changed_at",
            "remote_etag",
            "remote_modified_at",
            "remote_size_bytes",
            "current_snapshot",
            "updated_at",
        ]
    )


def source_slug() -> str:
    return settings.LEGAL_WORK_SOURCE_SLUG
