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

import logging
import uuid
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.core.feeds import FeedLocked
from apps.core.feeds import advisory_lock as _advisory_lock
from apps.core.feeds import advisory_lock_key as _advisory_lock_key
from apps.sources.workbook_sync import (
    ATTEMPT_FAILED,
    ATTEMPT_UNCHANGED,
    WorkbookFeed,
    attempt_workbook_sync,
)

from .bootstrap import ensure_event_programme_source
from .importer import IMPORTER_NAME, import_artifact
from .models import EventProgrammeFeedState, EventProgrammeSnapshot, SyncResult
from .public_download import (
    PublicDownload,
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

#: What distinguishes this feed from the other workbook feed. Everything
#: else about a public-workbook run is mechanical and lives in
#: `apps.sources.workbook_sync`.
WORKBOOK_FEED = WorkbookFeed(
    importer_name=IMPORTER_NAME,
    filename=WORKBOOK_FILENAME,
    external_reference=PUBLIC_EXTERNAL_REFERENCE,
    temp_prefix="dashkoda-event-programme-",
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_LOCKED = 3


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


#: The wording this feed has always used when its lock is held. Passed to the
#: shared helper so the operator-visible message is byte-identical to what it
#: was when this module carried its own copy of the lock.
LOCKED_MESSAGE = "Teine sünkroonimine juba käib."

#: This feed's own exception name, kept because `except SyncLocked` reads better
#: beside the feed's code and every caller and test already uses it. It **is**
#: `FeedLocked` rather than a subclass, so a lock taken through the shared
#: helper is caught by either name.
SyncLocked = FeedLocked


def advisory_lock_key(name: str = ADVISORY_LOCK_NAMESPACE) -> int:
    """This feed's lock key, from the canonical derivation."""
    return _advisory_lock_key(name)


def advisory_lock(name: str = ADVISORY_LOCK_NAMESPACE):
    """This feed's session-level lock, from the canonical helper.

    The name stays this module's, which is what keeps the feed independent of
    every other one; only the mechanism is shared.
    """
    return _advisory_lock(name, locked_message=LOCKED_MESSAGE)


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
    allow_collapse: bool = False,
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

    attempt = attempt_workbook_sync(
        feed=WORKBOOK_FEED,
        source=source,
        fetch=downloader if downloader is not None else download_public_workbook,
        import_artifact=import_artifact,
        dry_run=dry_run,
        allow_collapse=allow_collapse,
        actor=actor,
        correlation_id=correlation_id,
    )

    if attempt.status == ATTEMPT_FAILED:
        return _fail(state, attempt.message, correlation_id=correlation_id, stage=attempt.stage)
    if attempt.status == ATTEMPT_UNCHANGED:
        return _record_unchanged(
            state, attempt.download, dry_run=dry_run, correlation_id=correlation_id
        )

    download, result = attempt.download, attempt.result

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
            # An unchanged export is a successful verification: the link
            # answered, the bytes arrived and they proved to be the ones already
            # published. `last_changed_at` deliberately does not move, because
            # nothing changed - that is the whole distinction between the two.
            state.last_successful_sync_at = timezone.now()
            state.remote_size_bytes = download.size_bytes
            state.save(
                update_fields=[
                    "last_result",
                    "last_error_summary",
                    "last_successful_sync_at",
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
