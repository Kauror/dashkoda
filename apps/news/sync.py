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
from apps.core.feed_sync import (
    describe_error,
    fail_feed,
    find_published_artifact,
    get_feed_state,
    mark_imported,
    mark_unchanged,
    publish_current,
    start_run,
    touch_checked,
)
from apps.core.feeds import FeedResult, SourceOutcome
from apps.sources.services import complete_import_run, fail_import_run

from .bootstrap import ensure_news_source
from .collector import NORMALISED_SCHEMA_VERSION, NewsCollectionError, collect_news
from .models import NewsFeedState, NewsItem, NewsSnapshot

logger = logging.getLogger("dashkoda.news.sync")

IMPORTER_NAME = "koda_news_rss"
EXTERNAL_REFERENCE = "koda-public:news-feed"
ARTIFACT_NAME = "koda-news-feed.json"
LOCK_NAME = "dashkoda.news.sync_koda_news"


def synchronize_news(*, dry_run: bool = False, actor=None, collector=None) -> SourceOutcome:
    correlation_id = uuid.uuid4()
    source = ensure_news_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(NewsFeedState, source)
    touch_checked(state)

    collect = collector or collect_news
    current = NewsSnapshot.objects.filter(source=source, is_current=True).first()

    try:
        collection = collect(
            etag=state.remote_etag if current is not None else "",
            last_modified=state.remote_last_modified if current is not None else "",
        )
    except NewsCollectionError as error:
        return _fail(state, str(error), correlation_id)
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _fail(state, describe_error(error), correlation_id)

    if collection is None:
        # The source answered 304 and a snapshot is already published.
        return _unchanged(state, None, dry_run=dry_run, correlation_id=correlation_id)

    artifact, already_published = find_published_artifact(source, collection.sha256, IMPORTER_NAME)
    if already_published:
        return _unchanged(state, collection, dry_run=dry_run, correlation_id=correlation_id)

    try:
        artifact, run = start_run(
            source,
            collection,
            existing_artifact=artifact,
            importer_name=IMPORTER_NAME,
            external_reference=EXTERNAL_REFERENCE,
            artifact_name=ARTIFACT_NAME,
            schema_version=NORMALISED_SCHEMA_VERSION,
            dry_run=dry_run,
            actor=actor,
            correlation_id=correlation_id,
        )
    except Exception as error:  # noqa: BLE001
        return _fail(state, describe_error(error), correlation_id)

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
            publish_current(snapshot)
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
        return _fail(state, describe_error(error), correlation_id)

    mark_imported(
        state,
        snapshot,
        current_field="current_snapshot",
        etag=collection.etag,
        last_modified=collection.last_modified,
    )
    logger.info("news.sync imported items=%s", snapshot.item_count)
    return SourceOutcome(
        result=FeedResult.IMPORTED,
        detail="Uus uudiste hetkeseis avaldatud.",
        extra={"items": snapshot.item_count},
    )


def _unchanged(state, collection, *, dry_run: bool, correlation_id) -> SourceOutcome:
    count = state.current_snapshot.item_count if state.current_snapshot else 0
    if not dry_run:
        mark_unchanged(
            state,
            correlation_id=correlation_id,
            audit_action=AuditAction.NEWS_SYNC_UNCHANGED,
            change_summary={"source": state.source.slug, "item_count": count},
            etag=collection.etag if collection is not None else None,
            last_modified=collection.last_modified if collection is not None else None,
        )
    return SourceOutcome(
        result=FeedResult.UNCHANGED,
        detail="Uudisvoog ei ole muutunud.",
        dry_run=dry_run,
        extra={"items": count},
    )


def _fail(state, message: str, correlation_id) -> SourceOutcome:
    return fail_feed(
        state,
        message,
        correlation_id=correlation_id,
        audit_action=AuditAction.NEWS_SYNC_FAILED,
        logger=logger,
    )
