"""Synchronise the legal-work workbook from a public read-only sharing link.

This is the feed's one recurring collection route. It needs no Microsoft Entra
application, no client secret and no inbound endpoint: one outbound HTTPS
download of a view-only sharing link, validated, imported through the existing
importer, and published as an ordinary immutable snapshot.

How the bytes arrive shapes three things:

- the workbook is written to a temporary directory and deleted in every outcome,
  so no permanent copy is kept under ``SOURCE_ARTIFACT_ROOT``;
- the ``SourceArtifact`` is therefore metadata-only — a safe fixed label plus the
  server-computed checksum, size and MIME type, and no stored file;
- the checksum is the whole change-detection mechanism. There is no etag and no
  remote modification time to compare, so every run downloads and the digest
  decides.

Everything after that is the shared path: the same parser, the same import
registry, the same all-or-nothing publication, and the same guarantee that a
failed run leaves the previously published snapshot exactly where it was. The
manual ``import_oigusloome`` command reaches that path from a local file and is
not a recurring route.

The sharing URL is a bearer-style secret and appears nowhere in this module's
output: not in the returned outcome, not in the feed state, not in an audit
summary and not in a log line.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.core.feed_sync import find_published_artifact
from apps.sources.models import SourceArtifact
from apps.sources.services import register_external_reference

from .bootstrap import ensure_legal_work_source
from .importer import IMPORTER_NAME, import_artifact
from .models import LegalWorkFeedState, LegalWorkSnapshot, SyncResult
from .public_download import (
    XLSX_MIME_TYPE,
    PublicDownload,
    PublicDownloadError,
    PublicUrlNotConfigured,
    download_public_workbook,
)
from .sync import WORKBOOK_FILENAME, SyncOutcome, get_feed_state, record_failure

logger = logging.getLogger("dashkoda.legal_work.public_sync")

# The artifact's external reference. A fixed, non-secret label that names the
# provenance without exposing the sharing URL — which must never be stored,
# because possession of it is possession of the file.
PUBLIC_EXTERNAL_REFERENCE = "onedrive-public:oigusloome"


def synchronize_public_workbook(
    *,
    dry_run: bool = False,
    actor=None,
    downloader=None,
) -> SyncOutcome:
    """Run one public-link synchronisation attempt.

    `downloader` exists for the tests: it is called as
    ``downloader(destination)`` and must return a
    :class:`~apps.legal_work.public_download.PublicDownload`. Production passes
    nothing and the real HTTPS collector is used.

    The caller is expected to hold the advisory lock; the management command
    does. No result of this function ever contains the source URL.
    """
    correlation_id = uuid.uuid4()
    source = ensure_legal_work_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(source)

    state.last_checked_at = timezone.now()
    state.save(update_fields=["last_checked_at", "updated_at"])

    fetch = downloader if downloader is not None else download_public_workbook

    # One temporary directory for the whole run. `TemporaryDirectory` removes it
    # on every exit path — success, unchanged, validation failure, download
    # failure and unexpected exception alike — so the workbook never outlives
    # the command.
    with tempfile.TemporaryDirectory(prefix="dashkoda-oigusloome-public-") as directory:
        download_path = Path(directory) / WORKBOOK_FILENAME

        try:
            download = fetch(download_path)
        except PublicUrlNotConfigured:
            # An operator's configuration mistake, not a synchronisation
            # failure. The command reports it as plain text naming the missing
            # variable rather than recording it as if the remote had misbehaved.
            raise
        except PublicDownloadError as error:
            return _fail(state, str(error), correlation_id=correlation_id, stage="download")
        except Exception as error:
            # Anything else unexpected is recorded rather than allowed to escape
            # as a traceback from a cron job. The message is the exception's own
            # text; nothing the downloader raises contains the URL.
            message = f"{type(error).__name__}: {error}".replace("\n", " ")
            return _fail(state, message, correlation_id=correlation_id, stage="download")

        artifact, already_published = find_published_artifact(
            source, download.sha256, IMPORTER_NAME
        )
        if already_published:
            return _record_unchanged(
                state, download, dry_run=dry_run, correlation_id=correlation_id
            )

        try:
            # A previous dry run or a previous failed run already registered
            # this content. Reuse that artifact: the checksum is unique per
            # source, so registering a second one is both impossible and wrong.
            artifact = artifact or _register_metadata_only(
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
            # failure — including an import-registry constraint violation —
            # must be recorded and reported. The importer has already rolled
            # back, so the previously published snapshot is intact.
            message = f"{type(error).__name__}: {error}".replace("\n", " ")
            return _fail(state, message, correlation_id=correlation_id, stage="import")

    logger.info(
        "legal_work.public_sync completed rows=%s dry_run=%s size=%s",
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
        reporting_date=snapshot.reporting_date.isoformat(),
        rows_imported=result.rows_added,
        warnings=result.import_run.warnings,
    )


def _register_metadata_only(
    source,
    download: PublicDownload,
    *,
    correlation_id,
    actor=None,
) -> SourceArtifact:
    """Register what the content *was*, without keeping the file.

    The checksum and size were computed here from the bytes that were actually
    received, so they are as trustworthy as an upload's — and they are the whole
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
    state: LegalWorkFeedState,
    message: str,
    *,
    correlation_id,
    stage: str,
) -> SyncOutcome:
    record_failure(state, message, correlation_id=correlation_id)
    logger.warning("legal_work.public_sync failed during %s: %s", stage, message)
    return SyncOutcome(result=SyncResult.FAILED, detail=message)


def _record_unchanged(
    state: LegalWorkFeedState,
    download: PublicDownload,
    *,
    dry_run: bool,
    correlation_id,
) -> SyncOutcome:
    """The remote bytes are identical to content already published.

    A dry run reports the same finding but records nothing, because a dry run
    never changes what the feed state claims.
    """
    if not dry_run:
        with transaction.atomic():
            state.last_result = SyncResult.UNCHANGED
            state.last_error_summary = ""
            # An unchanged workbook is a successful verification: the link
            # answered, the bytes arrived and they proved to be the ones already
            # published. `last_changed_at` deliberately does not move, because
            # nothing changed — that is the whole distinction between the two.
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
                action=AuditAction.LEGAL_WORK_SYNC_UNCHANGED,
                obj=state.source,
                correlation_id=correlation_id,
                change_summary={
                    "source": state.source.slug,
                    "sha256": download.sha256,
                    "size_bytes": download.size_bytes,
                },
            )
    logger.info("legal_work.public_sync unchanged dry_run=%s", dry_run)
    return SyncOutcome(
        result=SyncResult.UNCHANGED,
        detail="Töövihik ei ole pärast eelmist importi muutunud.",
        snapshot_id=state.current_snapshot_id,
        dry_run=dry_run,
    )


def _record_imported(
    state: LegalWorkFeedState,
    download: PublicDownload,
    snapshot: LegalWorkSnapshot,
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
