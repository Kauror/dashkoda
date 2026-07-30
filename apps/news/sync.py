"""Collect the news feed and publish it as an immutable snapshot.

Independent of the other two public feeds: its own lock, its own import run, its
own transaction. A failure leaves the previously published snapshot exactly
where it was.
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

from .bootstrap import ensure_news_source
from .collector import NORMALISED_SCHEMA_VERSION, NewsCollectionError, collect_news
from .models import NewsFeedState, NewsItem, NewsSnapshot

logger = logging.getLogger("dashkoda.news.sync")

IMPORTER_NAME = "koda_news_rss"
EXTERNAL_REFERENCE = "koda-public:news-feed"
ARTIFACT_NAME = "koda-news-feed.json"
ARTIFACT_MIME = "application/json"
LOCK_NAME = "dashkoda.news.sync_koda_news"


def get_feed_state(source) -> NewsFeedState:
    state, _created = NewsFeedState.objects.get_or_create(source=source)
    return state


def synchronize_news(*, dry_run: bool = False, actor=None, collector=None) -> SourceOutcome:
    correlation_id = uuid.uuid4()
    source = ensure_news_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(source)

    state.last_checked_at = timezone.now()
    state.save(update_fields=["last_checked_at", "updated_at"])

    collect = collector or collect_news
    current = NewsSnapshot.objects.filter(source=source, is_current=True).first()

    try:
        collection = collect(
            etag=state.remote_etag if current is not None else "",
            last_modified=state.remote_last_modified if current is not None else "",
        )
    except NewsCollectionError as error:
        return _fail(state, str(error), correlation_id=correlation_id)
    except Exception as error:  # noqa: BLE001
        return _fail(
            state,
            f"{type(error).__name__}: {error}".replace("\n", " "),
            correlation_id=correlation_id,
        )

    if collection is None:
        return _record_unchanged(state, None, dry_run=dry_run, correlation_id=correlation_id)

    existing = SourceArtifact.objects.filter(source=source, sha256=collection.sha256).first()
    if existing is not None and _has_successful_live_import(existing):
        return _record_unchanged(state, collection, dry_run=dry_run, correlation_id=correlation_id)

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
            detail="Kuivkäivitus: uudisvoog on kehtiv, midagi ei avaldatud.",
            dry_run=True,
            extra={"items": len(collection.entries)},
        )

    try:
        with transaction.atomic():
            snapshot = NewsSnapshot(
                source=source,
                artifact=artifact,
                import_run=run,
                observed_at=timezone.now(),
                is_current=False,
                item_count=len(collection.entries),
            )
            snapshot.save()
            NewsItem.objects.bulk_create(
                [
                    NewsItem(
                        snapshot=snapshot,
                        guid=entry.guid,
                        title=entry.title,
                        canonical_url=entry.canonical_url,
                        published_at=entry.published_at,
                        category=entry.category,
                        summary=entry.summary,
                        source_order=entry.source_order,
                    )
                    for entry in collection.entries
                ]
            )
            _publish(snapshot)
            complete_import_run(run, rows_added=len(collection.entries), actor=actor)
            record_event(
                action=AuditAction.NEWS_SNAPSHOT_IMPORTED,
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

    _record_imported(state, collection, snapshot)
    logger.info("news.sync imported items=%s", snapshot.item_count)
    return SourceOutcome(
        result=FeedResult.IMPORTED,
        detail="Uus uudiste hetkeseis avaldatud.",
        extra={"items": snapshot.item_count},
    )


def _has_successful_live_import(artifact: SourceArtifact) -> bool:
    return artifact.import_runs.filter(
        importer_name=IMPORTER_NAME, status=ImportStatus.SUCCEEDED, dry_run=False
    ).exists()


def _publish(snapshot: NewsSnapshot) -> None:
    retired = (
        NewsSnapshot.objects.select_for_update()
        .filter(source=snapshot.source, is_current=True)
        .exclude(pk=snapshot.pk)
    )
    for previous in retired:
        previous.is_current = False
        previous.save(update_fields=["is_current"])
    snapshot.is_current = True
    snapshot.save(update_fields=["is_current"])


def _record_imported(state, collection, snapshot) -> None:
    now = timezone.now()
    state.last_result = FeedResult.IMPORTED
    state.last_error_summary = ""
    state.last_successful_sync_at = now
    state.last_changed_at = now
    state.remote_etag = collection.etag[:200]
    state.remote_last_modified = collection.last_modified[:100]
    state.current_snapshot = snapshot
    state.save(
        update_fields=[
            "last_result",
            "last_error_summary",
            "last_successful_sync_at",
            "last_changed_at",
            "remote_etag",
            "remote_last_modified",
            "current_snapshot",
            "updated_at",
        ]
    )


def _record_unchanged(state, collection, *, dry_run: bool, correlation_id) -> SourceOutcome:
    count = state.current_snapshot.item_count if state.current_snapshot else 0
    if not dry_run:
        with transaction.atomic():
            state.last_result = FeedResult.UNCHANGED
            state.last_error_summary = ""
            state.last_successful_sync_at = timezone.now()
            if collection is not None:
                state.remote_etag = collection.etag[:200]
                state.remote_last_modified = collection.last_modified[:100]
            state.save(
                update_fields=[
                    "last_result",
                    "last_error_summary",
                    "last_successful_sync_at",
                    "remote_etag",
                    "remote_last_modified",
                    "updated_at",
                ]
            )
            record_event(
                action=AuditAction.NEWS_SYNC_UNCHANGED,
                obj=state.source,
                correlation_id=correlation_id,
                change_summary={"source": state.source.slug, "item_count": count},
            )
    return SourceOutcome(
        result=FeedResult.UNCHANGED,
        detail="Uudisvoog ei ole muutunud.",
        dry_run=dry_run,
        extra={"items": count},
    )


def _fail(state, message: str, *, correlation_id) -> SourceOutcome:
    state.last_result = FeedResult.FAILED
    state.last_error_summary = message[:500]
    state.save(update_fields=["last_result", "last_error_summary", "last_checked_at", "updated_at"])
    record_event(
        action=AuditAction.NEWS_SYNC_FAILED,
        obj=state.source,
        correlation_id=correlation_id,
        change_summary={"source": state.source.slug, "detail": message[:300]},
    )
    logger.warning("news.sync failed: %s", message)
    return SourceOutcome(result=FeedResult.FAILED, detail=message)
