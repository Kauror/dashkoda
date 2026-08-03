"""Cron-safe synchronisation of the event-programme workbook from OneDrive.

One collection route, the public read-only sharing link. It needs no Microsoft
Entra application, no Graph credentials and no inbound endpoint: one outbound
HTTPS download, validated, imported through the existing importer, and published
as an ordinary immutable snapshot.

The whole run is guarded by a PostgreSQL advisory lock, so two overlapping
invocations can never both import. A host-side `flock` is documented as defence
in depth, but the application-level guarantee lives here: it survives being
started from a different host, container or shell.

The workbook is written to a temporary directory and deleted on every exit path,
so no permanent copy is kept under ``SOURCE_ARTIFACT_ROOT``. The
``SourceArtifact`` is therefore metadata-only - a fixed non-secret provenance
label plus the checksum, size and MIME type computed here from the bytes that
actually arrived. The checksum is the entire change-detection mechanism: this
route has no trustworthy etag or remote modification time, so every run
downloads and the digest decides.

Failure is never destructive. Whatever goes wrong, the previously published
snapshot stays current and the dashboard keeps showing the last good data with
an honest "last check failed" note.

The sharing URL is a bearer-style secret and appears nowhere in this module's
output: not in the returned outcome, not in the feed state, not in an audit
summary and not in a log line.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from django.db import connection, transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.sources.models import ImportStatus, SourceArtifact
from apps.sources.services import register_external_reference

from .bootstrap import ensure_event_programme_source
from .importer import IMPORTER_NAME, import_artifact
from .models import EventProgrammeFeedState, EventProgrammeSnapshot, SyncResult
from .public_download import (
    XLSX_MIME_TYPE,
    PublicDownload,
    PublicDownloadError,
    PublicUrlNotConfigured,
    download_public_workbook,
)

logger = logging.getLogger("dashkoda.event_programme.sync")

WORKBOOK_FILENAME = "dashkoda_events.xlsx"

# Stable, derived from a name so it cannot collide with an ad-hoc integer
# someone else picks later, nor with the legal-work feed's own lock.
ADVISORY_LOCK_NAMESPACE = "dashkoda.event_programme.sync_event_programme"

# The artifact's external reference. A fixed, non-secret label that names the
# provenance without exposing the sharing URL - possession of that URL is
# possession of the file.
PUBLIC_EXTERNAL_REFERENCE = "onedrive-public:sundmuste-programm"

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
    export_refreshed_at: str | None = None
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
            "export_refreshed_at": self.export_refreshed_at,
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


def get_feed_state(source) -> EventProgrammeFeedState:
    state, _created = EventProgrammeFeedState.objects.get_or_create(source=source)
    return state


def record_failure(state: EventProgrammeFeedState, message: str, *, correlation_id) -> None:
    """Record a sanitized failure without touching the published snapshot."""
    state.last_result = SyncResult.FAILED
    state.last_error_summary = message[:500]
    state.save(update_fields=["last_result", "last_error_summary", "last_checked_at", "updated_at"])
    record_event(
        action=AuditAction.EVENT_PROGRAMME_SYNC_FAILED,
        obj=state.source,
        correlation_id=correlation_id,
        change_summary={"detail": message[:300]},
    )


def synchronize_public_workbook(
    *,
    dry_run: bool = False,
    actor=None,
    downloader=None,
) -> SyncOutcome:
    """Run one synchronisation attempt.

    `downloader` exists for the tests: it is called as ``downloader(destination)``
    and must return a :class:`~apps.event_programme.public_download.PublicDownload`.
    Production passes nothing and the real HTTPS collector is used.

    The caller is expected to hold the advisory lock; the management command
    does. No result of this function ever contains the source URL.
    """
    correlation_id = uuid.uuid4()
    source = ensure_event_programme_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(source)

    state.last_checked_at = timezone.now()
    state.save(update_fields=["last_checked_at", "updated_at"])

    fetch = downloader if downloader is not None else download_public_workbook

    # One temporary directory for the whole run. `TemporaryDirectory` removes it
    # on every exit path - success, unchanged, validation failure, download
    # failure and unexpected exception alike - so the workbook never outlives
    # the command.
    with tempfile.TemporaryDirectory(prefix="dashkoda-event-programme-") as directory:
        download_path = Path(directory) / WORKBOOK_FILENAME

        try:
            download = fetch(download_path)
        except PublicUrlNotConfigured:
            # An operator's configuration mistake, not a synchronisation
            # failure. The command reports it as plain text naming the missing
            # variable rather than recording it as if the remote misbehaved.
            raise
        except PublicDownloadError as error:
            return _fail(state, str(error), correlation_id=correlation_id, stage="download")
        except Exception as error:
            # Anything else unexpected is recorded rather than allowed to escape
            # as a traceback from a cron job. The message is the exception's own
            # text; nothing the downloader raises contains the URL.
            message = f"{type(error).__name__}: {error}".replace("\n", " ")
            return _fail(state, message, correlation_id=correlation_id, stage="download")

        existing = SourceArtifact.objects.filter(source=source, sha256=download.sha256).first()
        if existing is not None and _has_successful_live_import(existing):
            return _record_unchanged(
                state, download, dry_run=dry_run, correlation_id=correlation_id
            )

        try:
            # A previous dry run or a previous failed run already registered
            # this content. Reuse that artifact: the checksum is unique per
            # source, so registering a second one is both impossible and wrong.
            artifact = existing or _register_metadata_only(
                source, download, correlation_id=correlation_id, actor=actor
            )
            result = import_artifact(
                artifact,
                workbook_path=download.path,
                dry_run=dry_run,
                actor=actor,
                correlation_id=correlation_id,
            )
        except Exception as error:
            # Deliberately broad: this runs unattended every morning, and every
            # failure - including an import-registry constraint violation - must
            # be recorded and reported. The importer has already rolled back, so
            # the previously published snapshot is intact.
            message = f"{type(error).__name__}: {error}".replace("\n", " ")
            return _fail(state, message, correlation_id=correlation_id, stage="import")

    logger.info(
        "event_programme.sync completed rows=%s dry_run=%s size=%s",
        result.rows_added,
        dry_run,
        download.size_bytes,
    )

    if dry_run:
        # Validated but published nothing. Only `last_checked_at`, written
        # above, has moved: a dry run must leave the recorded state alone.
        return SyncOutcome(
            result=SyncResult.IMPORTED,
            detail="Kuivkäivitus: töövihik on kehtiv, andmeid ei avaldatud.",
            rows_imported=0,
            dry_run=True,
            warnings=result.import_run.warnings,
        )

    snapshot = result.snapshot
    _record_imported(state, download, snapshot, correlation_id=correlation_id)
    return SyncOutcome(
        result=SyncResult.IMPORTED,
        detail="Uus hetkeseis avaldatud.",
        snapshot_id=snapshot.pk,
        export_refreshed_at=snapshot.export_refreshed_at.isoformat(),
        rows_imported=result.rows_added,
        warnings=result.import_run.warnings,
    )


def _has_successful_live_import(artifact: SourceArtifact) -> bool:
    """Whether this exact content has already been published.

    Only a *successful live* import counts. An artifact left behind by a dry run
    or by a failed run is reusable and must not be treated as "already
    imported" - otherwise a dry run would permanently block the live import of
    the same bytes.
    """
    return artifact.import_runs.filter(
        importer_name=IMPORTER_NAME,
        status=ImportStatus.SUCCEEDED,
        dry_run=False,
    ).exists()


def _register_metadata_only(
    source,
    download: PublicDownload,
    *,
    correlation_id,
    actor=None,
) -> SourceArtifact:
    """Register what the content *was*, without keeping the file.

    The checksum and size were computed here from the bytes that were actually
    received, so they are as trustworthy as an upload's - and they are the whole
    content identity the import registry needs.
    """
    return register_external_reference(
        source=source,
        external_reference=PUBLIC_EXTERNAL_REFERENCE,
        original_name=WORKBOOK_FILENAME,
        mime_type=XLSX_MIME_TYPE,
        sha256=download.sha256,
        size_bytes=download.size_bytes,
        uploaded_by=actor,
        actor=actor,
        correlation_id=correlation_id,
    )


def _fail(
    state: EventProgrammeFeedState,
    message: str,
    *,
    correlation_id,
    stage: str,
) -> SyncOutcome:
    record_failure(state, message, correlation_id=correlation_id)
    logger.warning("event_programme.sync failed during %s: %s", stage, message)
    return SyncOutcome(result=SyncResult.FAILED, detail=message)


def _record_unchanged(
    state: EventProgrammeFeedState,
    download: PublicDownload,
    *,
    dry_run: bool,
    correlation_id,
) -> SyncOutcome:
    """The remote bytes are identical to content already published.

    Expected on most mornings the operational workbook did not change, and on
    every morning the export ran but produced identical bytes. A dry run reports
    the same finding but records nothing.
    """
    if not dry_run:
        with transaction.atomic():
            state.last_result = SyncResult.UNCHANGED
            state.last_error_summary = ""
            state.remote_size_bytes = download.size_bytes
            state.save(
                update_fields=[
                    "last_result",
                    "last_error_summary",
                    "remote_size_bytes",
                    "updated_at",
                ]
            )
            record_event(
                action=AuditAction.EVENT_PROGRAMME_SYNC_UNCHANGED,
                obj=state.source,
                correlation_id=correlation_id,
                change_summary={
                    "source": state.source.slug,
                    "sha256": download.sha256,
                    "size_bytes": download.size_bytes,
                },
            )
    logger.info("event_programme.sync unchanged dry_run=%s", dry_run)
    return SyncOutcome(
        result=SyncResult.UNCHANGED,
        detail="Töövihik ei ole pärast eelmist importi muutunud.",
        snapshot_id=state.current_snapshot_id,
        dry_run=dry_run,
    )


def _record_imported(
    state: EventProgrammeFeedState,
    download: PublicDownload,
    snapshot: EventProgrammeSnapshot,
    *,
    correlation_id,
) -> None:
    """Record a successful publication.

    `remote_etag` stays blank and `remote_modified_at` stays null: this route
    has no trustworthy non-secret value for either, and the checksum belongs on
    the artifact. Storing a digest in an etag field would make both fields lie.
    """
    now = timezone.now()
    state.last_result = SyncResult.IMPORTED
    state.last_error_summary = ""
    state.last_successful_sync_at = now
    state.last_changed_at = now
    state.remote_size_bytes = download.size_bytes
    state.current_snapshot = snapshot
    state.save(
        update_fields=[
            "last_result",
            "last_error_summary",
            "last_successful_sync_at",
            "last_changed_at",
            "remote_size_bytes",
            "current_snapshot",
            "updated_at",
        ]
    )
