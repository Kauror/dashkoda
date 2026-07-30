"""Collect the events calendar and publish it as an immutable snapshot.

Independent of the other two public feeds: its own lock, its own import run, its
own transaction. A failure leaves the previously published snapshot in place.
"""

from __future__ import annotations

import logging
import uuid

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.core.feeds import FeedResult, SourceOutcome
from apps.sources.models import ImportStatus, SourceArtifact
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    fail_import_run,
    register_external_reference,
    start_import_run,
)

from .bootstrap import ensure_events_source
from .collector import NORMALISED_SCHEMA_VERSION, EventCollectionError, collect_events
from .models import EventFeedState, EventItem, EventSnapshot

logger = logging.getLogger("dashkoda.events.sync")

IMPORTER_NAME = "koda_events_calendar"
EXTERNAL_REFERENCE = "koda-public:events"
ARTIFACT_NAME = "koda-events.json"
ARTIFACT_MIME = "application/json"
LOCK_NAME = "dashkoda.events.sync_koda_events"


def get_feed_state(source) -> EventFeedState:
    state, _created = EventFeedState.objects.get_or_create(source=source)
    return state


def synchronize_events(*, dry_run: bool = False, actor=None, collector=None) -> SourceOutcome:
    correlation_id = uuid.uuid4()
    source = ensure_events_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(source)

    state.last_checked_at = timezone.now()
    state.save(update_fields=["last_checked_at", "updated_at"])

    collect = collector or collect_events

    try:
        collection = collect()
    except EventCollectionError as error:
        return _fail(state, str(error), correlation_id=correlation_id)
    except Exception as error:  # noqa: BLE001
        return _fail(
            state,
            f"{type(error).__name__}: {error}".replace("\n", " "),
            correlation_id=correlation_id,
        )

    existing = SourceArtifact.objects.filter(source=source, sha256=collection.sha256).first()
    if existing is not None and _has_successful_live_import(existing):
        return _record_unchanged(state, dry_run=dry_run, correlation_id=correlation_id)

    try:
        artifact = existing or register_external_reference(
            source=source,
            external_reference=EXTERNAL_REFERENCE,
            original_name=ARTIFACT_NAME,
            mime_type=ARTIFACT_MIME,
            sha256=collection.sha256,
            size_bytes=collection.size_bytes,
            uploaded_by=actor,
            actor=actor,
            correlation_id=correlation_id,
        )
        run = build_import_run(
            artifact=artifact,
            importer_name=IMPORTER_NAME,
            schema_version=NORMALISED_SCHEMA_VERSION,
            dry_run=dry_run,
            initiated_by=actor,
            actor=actor,
            correlation_id=correlation_id,
        )
        start_import_run(run)
    except Exception as error:  # noqa: BLE001
        return _fail(
            state,
            f"{type(error).__name__}: {error}".replace("\n", " "),
            correlation_id=correlation_id,
        )

    if dry_run:
        complete_import_run(run, rows_added=0, rows_skipped=len(collection.entries), actor=actor)
        return SourceOutcome(
            result=FeedResult.IMPORTED,
            detail="Kuivkäivitus: sündmused on kehtivad, midagi ei avaldatud.",
            dry_run=True,
            extra={"items": len(collection.entries)},
        )

    try:
        with transaction.atomic():
            snapshot = EventSnapshot(
                source=source,
                artifact=artifact,
                import_run=run,
                observed_at=timezone.now(),
                is_current=False,
                item_count=len(collection.entries),
            )
            snapshot.save()
            EventItem.objects.bulk_create(
                [
                    EventItem(
                        snapshot=snapshot,
                        stable_key=entry.stable_key,
                        title=entry.title,
                        canonical_url=entry.canonical_url,
                        category=entry.category,
                        summary=entry.summary,
                        starts_on=entry.starts_on,
                        ends_on=entry.ends_on,
                        starts_at=entry.starts_at,
                        ends_at=entry.ends_at,
                        location=entry.location,
                        source_order=entry.source_order,
                    )
                    for entry in collection.entries
                ]
            )
            _publish(snapshot)
            complete_import_run(run, rows_added=len(collection.entries), actor=actor)
            record_event(
                action=AuditAction.EVENTS_SNAPSHOT_IMPORTED,
                obj=snapshot,
                actor=actor,
                correlation_id=correlation_id,
                change_summary={
                    "source": source.slug,
                    "sha256": collection.sha256,
                    "item_count": snapshot.item_count,
                    "observed_at": snapshot.observed_at.isoformat(),
                    "snapshot_id": snapshot.pk,
                },
            )
    except Exception as error:  # noqa: BLE001
        run.refresh_from_db()
        if not run.is_terminal:
            fail_import_run(run, errors=[{"type": type(error).__name__}], actor=actor)
        return _fail(
            state,
            f"{type(error).__name__}: {error}".replace("\n", " "),
            correlation_id=correlation_id,
        )

    _record_imported(state, snapshot)
    logger.info("events.sync imported items=%s", snapshot.item_count)
    return SourceOutcome(
        result=FeedResult.IMPORTED,
        detail="Uus sündmuste hetkeseis avaldatud.",
        extra={"items": snapshot.item_count},
    )


def _has_successful_live_import(artifact: SourceArtifact) -> bool:
    return artifact.import_runs.filter(
        importer_name=IMPORTER_NAME, status=ImportStatus.SUCCEEDED, dry_run=False
    ).exists()


def _publish(snapshot: EventSnapshot) -> None:
    retired = (
        EventSnapshot.objects.select_for_update()
        .filter(source=snapshot.source, is_current=True)
        .exclude(pk=snapshot.pk)
    )
    for previous in retired:
        previous.is_current = False
        previous.save(update_fields=["is_current"])
    snapshot.is_current = True
    snapshot.save(update_fields=["is_current"])


def _record_imported(state, snapshot) -> None:
    now = timezone.now()
    state.last_result = FeedResult.IMPORTED
    state.last_error_summary = ""
    state.last_successful_sync_at = now
    state.last_changed_at = now
    state.current_snapshot = snapshot
    state.save(
        update_fields=[
            "last_result",
            "last_error_summary",
            "last_successful_sync_at",
            "last_changed_at",
            "current_snapshot",
            "updated_at",
        ]
    )


def _record_unchanged(state, *, dry_run: bool, correlation_id) -> SourceOutcome:
    count = state.current_snapshot.item_count if state.current_snapshot else 0
    if not dry_run:
        with transaction.atomic():
            state.last_result = FeedResult.UNCHANGED
            state.last_error_summary = ""
            state.last_successful_sync_at = timezone.now()
            state.save(
                update_fields=[
                    "last_result",
                    "last_error_summary",
                    "last_successful_sync_at",
                    "updated_at",
                ]
            )
            record_event(
                action=AuditAction.EVENTS_SYNC_UNCHANGED,
                obj=state.source,
                correlation_id=correlation_id,
                change_summary={"source": state.source.slug, "item_count": count},
            )
    return SourceOutcome(
        result=FeedResult.UNCHANGED,
        detail="Sündmuste kalender ei ole muutunud.",
        dry_run=dry_run,
        extra={"items": count},
    )


def _fail(state, message: str, *, correlation_id) -> SourceOutcome:
    state.last_result = FeedResult.FAILED
    state.last_error_summary = message[:500]
    state.save(update_fields=["last_result", "last_error_summary", "last_checked_at", "updated_at"])
    record_event(
        action=AuditAction.EVENTS_SYNC_FAILED,
        obj=state.source,
        correlation_id=correlation_id,
        change_summary={"source": state.source.slug, "detail": message[:300]},
    )
    logger.warning("events.sync failed: %s", message)
    return SourceOutcome(result=FeedResult.FAILED, detail=message)
