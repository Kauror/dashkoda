"""Shared bookkeeping for the public feed synchronisations.

The three Koda.ee feeds publish through the same sequence — record the check,
collect, deduplicate by checksum, open an import run, publish atomically,
record the outcome — and the bookkeeping of that sequence used to be written
out three times. It lives here once.

What stays in each domain app is what genuinely differs: the collector, the
plausibility rules, the published models, the Estonian outcome messages and the
audit summaries. Failure isolation is unchanged: every feed still runs under
its own lock, its own import run and its own transaction.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.core.feeds import FeedResult, SourceOutcome
from apps.sources.models import ImportStatus, SourceArtifact
from apps.sources.services import (
    build_import_run,
    register_external_reference,
    start_import_run,
)

# Every feed registers its normalised canonical JSON, never the raw response.
ARTIFACT_MIME = "application/json"


def describe_error(error: Exception) -> str:
    """A one-line, log-safe description of an unexpected failure."""
    return f"{type(error).__name__}: {error}".replace("\n", " ")


def get_feed_state(model, source):
    state, _created = model.objects.get_or_create(source=source)
    return state


def touch_checked(state) -> None:
    """Record that a check happened, before its outcome is known."""
    state.last_checked_at = timezone.now()
    state.save(update_fields=["last_checked_at", "updated_at"])


def find_published_artifact(source, sha256: str, importer_name: str):
    """Look up this content under the source.

    Returns ``(artifact, already_published)``: the artifact registered for
    these bytes if any, and whether a successful live import has already
    published them. An artifact left behind by a dry run or a failed run is
    reusable and must not count as published.
    """
    artifact = SourceArtifact.objects.filter(source=source, sha256=sha256).first()
    if artifact is None:
        return None, False
    already_published = artifact.import_runs.filter(
        importer_name=importer_name,
        status=ImportStatus.SUCCEEDED,
        dry_run=False,
    ).exists()
    return artifact, already_published


def start_run(
    source,
    collection,
    *,
    existing_artifact,
    importer_name: str,
    external_reference: str,
    artifact_name: str,
    schema_version: str,
    dry_run: bool,
    actor,
    correlation_id,
):
    """Register the artifact (or reuse the existing one) and open a started run."""
    artifact = existing_artifact or register_external_reference(
        source=source,
        external_reference=external_reference,
        original_name=artifact_name,
        mime_type=ARTIFACT_MIME,
        sha256=collection.sha256,
        size_bytes=collection.size_bytes,
        uploaded_by=actor,
        actor=actor,
        correlation_id=correlation_id,
    )
    run = build_import_run(
        artifact=artifact,
        importer_name=importer_name,
        schema_version=schema_version,
        dry_run=dry_run,
        initiated_by=actor,
        actor=actor,
        correlation_id=correlation_id,
    )
    start_import_run(run)
    return artifact, run


def publish_current(published) -> None:
    """Make ``published`` the only current row of its model for its source."""
    model = type(published)
    retired = (
        model.objects.select_for_update()
        .filter(source=published.source, is_current=True)
        .exclude(pk=published.pk)
    )
    for previous in retired:
        previous.is_current = False
        previous.save(update_fields=["is_current"])
    published.is_current = True
    published.save(update_fields=["is_current"])


def mark_imported(state, published, *, current_field: str, etag=None, last_modified=None) -> None:
    """Record a successful publication on the feed state.

    ``etag`` is ``None`` for a feed that does not use conditional requests;
    passing a value (even an empty one) stores the remote validators.
    """
    now = timezone.now()
    state.last_result = FeedResult.IMPORTED
    state.last_error_summary = ""
    state.last_successful_sync_at = now
    state.last_changed_at = now
    update_fields = [
        "last_result",
        "last_error_summary",
        "last_successful_sync_at",
        "last_changed_at",
    ]
    if etag is not None:
        state.remote_etag = etag[:200]
        state.remote_last_modified = (last_modified or "")[:100]
        update_fields += ["remote_etag", "remote_last_modified"]
    setattr(state, current_field, published)
    state.save(update_fields=[*update_fields, current_field, "updated_at"])


def mark_unchanged(
    state,
    *,
    correlation_id,
    audit_action: str,
    change_summary: dict,
    etag=None,
    last_modified=None,
) -> None:
    """Record that the source was checked and nothing changed."""
    with transaction.atomic():
        state.last_result = FeedResult.UNCHANGED
        state.last_error_summary = ""
        state.last_successful_sync_at = timezone.now()
        update_fields = ["last_result", "last_error_summary", "last_successful_sync_at"]
        if etag is not None:
            state.remote_etag = etag[:200]
            state.remote_last_modified = (last_modified or "")[:100]
            update_fields += ["remote_etag", "remote_last_modified"]
        state.save(update_fields=[*update_fields, "updated_at"])
        record_event(
            action=audit_action,
            obj=state.source,
            correlation_id=correlation_id,
            change_summary=change_summary,
        )


def fail_feed(
    state,
    message: str,
    *,
    correlation_id,
    audit_action: str,
    logger: logging.Logger,
) -> SourceOutcome:
    """Record a sanitized failure without touching the published data."""
    state.last_result = FeedResult.FAILED
    state.last_error_summary = message[:500]
    state.save(update_fields=["last_result", "last_error_summary", "last_checked_at", "updated_at"])
    record_event(
        action=audit_action,
        obj=state.source,
        correlation_id=correlation_id,
        change_summary={"source": state.source.slug, "detail": message[:300]},
    )
    logger.warning("sync failed: %s", message)
    return SourceOutcome(result=FeedResult.FAILED, detail=message)
