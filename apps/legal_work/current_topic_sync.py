"""Collect the current-topic catalogue and publish it as an immutable snapshot.

The same sequence as the other three public feeds, through the same shared
bookkeeping in :mod:`apps.core.feed_sync`: record the check, collect, recognise
the content by its canonical checksum, open an import run, publish atomically,
record the outcome.

Independent of the workbook feed in every way that matters. Its own source, its
own advisory lock, its own import run, its own transaction and its own audit
actions, so a Koda.ee outage cannot touch `LegalWorkSnapshot`, `/oigusloome/`
or the dashboard's freshness row.

The artifact registered here is metadata-only: the collector computed a digest
over the *normalised* catalogue and kept no file, which is exactly the shape the
repository's external-reference artifact is for. No HTML is retained anywhere.
"""

from __future__ import annotations

import logging
import uuid

from django.db import transaction
from django.utils import timezone

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
from apps.legal_work.audit_actions import LegalWorkAudit
from apps.sources.services import complete_import_run, fail_publication

from .bootstrap import ensure_current_topics_source
from .current_topics import (
    NORMALISED_SCHEMA_VERSION,
    CurrentTopicCollectionError,
    collect_current_topics,
)
from .models import CurrentTopicFeedState, CurrentTopicItem, CurrentTopicSnapshot

logger = logging.getLogger("dashkoda.legal_work.current_topic_sync")

IMPORTER_NAME = "koda_current_topics"
EXTERNAL_REFERENCE = "koda-public:current-topics"
ARTIFACT_NAME = "koda-current-topics.json"
LOCK_NAME = "dashkoda.legal_work.sync_current_topics"


def synchronize_current_topics(
    *, dry_run: bool = False, actor=None, collector=None
) -> SourceOutcome:
    """Collect the catalogue and publish it if it changed."""
    correlation_id = uuid.uuid4()
    source = ensure_current_topics_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(CurrentTopicFeedState, source)
    touch_checked(state)

    collect = collector or collect_current_topics

    try:
        # No conditional request: the listing is server-rendered HTML with no
        # useful validator, so the canonical checksum over the normalised
        # catalogue is what decides whether anything changed.
        collection = collect()
    except CurrentTopicCollectionError as error:
        return _fail(state, str(error), correlation_id)
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _fail(state, describe_error(error), correlation_id)

    artifact, already_published = find_published_artifact(source, collection.sha256, IMPORTER_NAME)
    if already_published:
        return _unchanged(state, dry_run=dry_run, correlation_id=correlation_id)

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
            detail="Kuivkäivitus: teemad on kehtivad, midagi ei avaldatud.",
            dry_run=True,
            extra={"items": len(collection.entries)},
        )

    try:
        with transaction.atomic():
            snapshot = CurrentTopicSnapshot(
                source=source,
                artifact=artifact,
                import_run=run,
                observed_at=timezone.now(),
                is_current=False,
                item_count=len(collection.entries),
            )
            snapshot.save()
            CurrentTopicItem.objects.bulk_create(
                [
                    CurrentTopicItem(
                        snapshot=snapshot,
                        content_key=entry.content_key,
                        canonical_url=entry.canonical_url,
                        title=entry.title,
                        listing_summary=entry.listing_summary,
                        body_text=entry.body_text,
                        published_date=entry.published_date,
                        feedback_deadline=entry.feedback_deadline,
                        named_organization=entry.named_organization,
                        source_order=entry.source_order,
                    )
                    for entry in collection.entries
                ]
            )
            publish_current(snapshot)
            complete_import_run(run, rows_added=len(collection.entries), actor=actor)
            record_event(
                action=LegalWorkAudit.CURRENT_TOPIC_SNAPSHOT_IMPORTED,
                obj=snapshot,
                actor=actor,
                correlation_id=correlation_id,
                # Counts, identifiers and a checksum. No title, no URL, no page
                # text: an audit summary is a trail, not a copy of the source.
                change_summary={
                    "source": source.slug,
                    "sha256": collection.sha256,
                    "item_count": snapshot.item_count,
                    "pages_fetched": collection.pages_fetched,
                    "details_fetched": collection.details_fetched,
                    "snapshot_id": snapshot.pk,
                },
            )
    except Exception as error:  # noqa: BLE001
        fail_publication(run, errors=[{"type": type(error).__name__}], actor=actor)
        return _fail(state, describe_error(error), correlation_id)

    mark_imported(state, snapshot, current_field="current_snapshot")
    logger.info("current_topics.sync imported items=%s", snapshot.item_count)
    return SourceOutcome(
        result=FeedResult.IMPORTED,
        detail="Uus hetkel käsil hetkeseis avaldatud.",
        extra={"items": snapshot.item_count, "snapshot_id": snapshot.pk},
    )


def _unchanged(state, *, dry_run: bool, correlation_id) -> SourceOutcome:
    count = state.current_snapshot.item_count if state.current_snapshot else 0
    snapshot_id = state.current_snapshot.pk if state.current_snapshot else None
    if not dry_run:
        mark_unchanged(
            state,
            correlation_id=correlation_id,
            audit_action=LegalWorkAudit.CURRENT_TOPIC_SYNC_UNCHANGED,
            change_summary={"source": state.source.slug, "item_count": count},
        )
    return SourceOutcome(
        result=FeedResult.UNCHANGED,
        detail="Hetkel käsil loend ei ole muutunud.",
        dry_run=dry_run,
        extra={"items": count, "snapshot_id": snapshot_id},
    )


def _fail(state, message: str, correlation_id) -> SourceOutcome:
    return fail_feed(
        state,
        message,
        correlation_id=correlation_id,
        audit_action=LegalWorkAudit.CURRENT_TOPIC_SYNC_FAILED,
        logger=logger,
    )


__all__ = ["IMPORTER_NAME", "LOCK_NAME", "synchronize_current_topics"]
