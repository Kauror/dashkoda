"""Publish the archive catalogue as an immutable, resumable snapshot.

Each run writes a **complete** snapshot: every entry the index knows, carrying
whatever hydration exists at that moment. Hydration from the previous snapshot
is carried forward, so a run that reads sixty new detail pages publishes a
snapshot with those sixty plus everything read before — the backfill accumulates
across runs rather than restarting.

Hydration order is deliberate, because the budget is finite:

1. entries shortlisted as plausible candidates for a consultation-eligible legal
   record that the current matcher did not already match — those are the ones a
   link actually depends on;
2. everything else newest-first, until the window closes.

The window is what keeps this bounded. Archive cards carry no year, so hydration
walks newest-first and stops once it has seen a page's worth of consecutive
entries published before the cutoff. `backfill_complete` means the index is
whole and everything **inside that window** has been read or has definitively
failed — not that all eleven hundred were fetched.

A failure leaves the previous snapshot current, exactly like every other feed.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

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
from apps.core.feeds import FeedResult
from apps.sources.services import complete_import_run, fail_import_run

from .archive_bootstrap import ensure_archive_source
from .archived_topics import (
    NORMALISED_SCHEMA_VERSION,
    ArchiveCollectionError,
    ArchiveDetail,
    checksum_for,
    collect_archive_index,
    hydrate_detail,
    hydration_cutoff,
    listing_signature,
)
from .models import (
    ArchivedTopicFeedState,
    ArchivedTopicItem,
    ArchivedTopicSnapshot,
    DetailStatus,
)
from .shortlist import shortlist_archive_urls

logger = logging.getLogger("dashkoda.legal_work.archived_topic_sync")

IMPORTER_NAME = "koda_archived_topics"
EXTERNAL_REFERENCE = "koda-public:archived-topics"
ARTIFACT_NAME = "koda-archived-topics.json"
LOCK_NAME = "dashkoda.legal_work.sync_archived_topics"


@dataclass
class ArchiveSyncReport:
    """Aggregate progress. Never carries a title, a URL or page text."""

    result: str
    detail: str = ""
    dry_run: bool = False
    snapshot_id: int | None = None
    indexed_items: int = 0
    detailed_items: int = 0
    pending_items: int = 0
    failed_items: int = 0
    new_items: int = 0
    changed_items: int = 0
    backfill_complete: bool = False
    pages_fetched: int = 0
    detail_requests: int = 0
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "detail": self.detail,
            "dry_run": self.dry_run,
            "snapshot_id": self.snapshot_id,
            "indexed_items": self.indexed_items,
            "detailed_items": self.detailed_items,
            "pending_items": self.pending_items,
            "failed_items": self.failed_items,
            "new_items": self.new_items,
            "changed_items": self.changed_items,
            "backfill_complete": self.backfill_complete,
            "pages_fetched": self.pages_fetched,
            "detail_requests": self.detail_requests,
        }


def synchronize_archived_topics(
    *,
    dry_run: bool = False,
    full: bool = False,
    max_detail_pages: int | None = None,
    actor=None,
    session=None,
) -> ArchiveSyncReport:
    """Collect the archive index, hydrate a bounded slice, publish a snapshot."""
    from django.conf import settings

    correlation_id = uuid.uuid4()
    source = ensure_archive_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(ArchivedTopicFeedState, source)
    touch_checked(state)

    budget = (
        settings.KODA_ARCHIVE_MAX_DETAIL_PAGES_PER_RUN
        if max_detail_pages is None
        else max(0, int(max_detail_pages))
    )

    previous = state.current_snapshot
    previous_items = {item.canonical_url: item for item in previous.items.all()} if previous else {}

    try:
        index = collect_archive_index(
            session=session,
            full=full or previous is None,
            known_keys=frozenset(item.content_key for item in previous_items.values()),
            # Recomputed from what was stored, so an edited listing title or
            # summary counts as changed and keeps the walk going.
            known_signatures={
                item.content_key: listing_signature(item.title, item.listing_summary)
                for item in previous_items.values()
            },
        )
    except ArchiveCollectionError as error:
        return _fail(state, str(error), correlation_id)
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _fail(state, describe_error(error), correlation_id)

    # An incremental walk only saw the newest pages. Everything it did not visit
    # keeps its previous place and presence: not having looked is not evidence
    # of absence.
    entries = _merge_with_previous(index, previous_items, full=full)

    details = _carry_forward_details(entries, previous_items)
    new_urls = [e.canonical_url for e in entries if e.canonical_url not in previous_items]

    try:
        requested = _hydrate_within_budget(
            entries, details, budget=budget, session=session, dry_run=dry_run
        )
    except Exception as error:  # noqa: BLE001
        return _fail(state, describe_error(error), correlation_id)

    checksum, size = checksum_for(entries, details)
    counts = _counts(entries, details)
    complete = index.reached_end and counts["pending"] == 0

    artifact, already_published = find_published_artifact(source, checksum, IMPORTER_NAME)
    if already_published:
        return _unchanged(
            state,
            dry_run=dry_run,
            correlation_id=correlation_id,
            counts=counts,
            pages=index.pages_fetched,
            requested=requested,
            complete=complete,
        )

    collection = type("Collection", (), {"sha256": checksum, "size_bytes": size})()
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
        complete_import_run(run, rows_added=0, rows_skipped=len(entries), actor=actor)
        return ArchiveSyncReport(
            result=FeedResult.IMPORTED,
            detail="Kuivkäivitus: arhiiv on kehtiv, midagi ei avaldatud.",
            dry_run=True,
            indexed_items=len(entries),
            detailed_items=counts["hydrated"],
            pending_items=counts["pending"],
            failed_items=counts["failed"],
            new_items=len(new_urls),
            backfill_complete=complete,
            pages_fetched=index.pages_fetched,
            detail_requests=requested,
        )

    try:
        with transaction.atomic():
            snapshot = ArchivedTopicSnapshot(
                source=source,
                artifact=artifact,
                import_run=run,
                observed_at=timezone.now(),
                is_current=False,
                item_count=len(entries),
                detailed_item_count=counts["hydrated"],
                pending_detail_count=counts["pending"],
                failed_detail_count=counts["failed"],
                pages_fetched=index.pages_fetched,
                backfill_complete=complete,
            )
            snapshot.save()
            ArchivedTopicItem.objects.bulk_create(
                [_row(snapshot, entry, details.get(entry.canonical_url)) for entry in entries]
            )
            _verify_written(snapshot, expected=len(entries))
            publish_current(snapshot)
            complete_import_run(run, rows_added=len(entries), actor=actor)
            record_event(
                action=AuditAction.ARCHIVED_TOPIC_SNAPSHOT_IMPORTED,
                obj=snapshot,
                actor=actor,
                correlation_id=correlation_id,
                change_summary={
                    "source": source.slug,
                    "sha256": checksum,
                    "snapshot_id": snapshot.pk,
                    "item_count": snapshot.item_count,
                    "detailed": snapshot.detailed_item_count,
                    "pending": snapshot.pending_detail_count,
                    "failed": snapshot.failed_detail_count,
                    "pages_fetched": snapshot.pages_fetched,
                    "backfill_complete": snapshot.backfill_complete,
                },
            )
    except Exception as error:  # noqa: BLE001
        run.refresh_from_db()
        if not run.is_terminal:
            fail_import_run(run, errors=[{"type": type(error).__name__}], actor=actor)
        return _fail(state, describe_error(error), correlation_id)

    mark_imported(state, snapshot, current_field="current_snapshot")
    logger.info(
        "archived_topics.sync items=%s hydrated=%s pending=%s complete=%s",
        snapshot.item_count,
        snapshot.detailed_item_count,
        snapshot.pending_detail_count,
        snapshot.backfill_complete,
    )
    return ArchiveSyncReport(
        result=FeedResult.IMPORTED,
        detail="Uus arhiivi hetkeseis avaldatud.",
        snapshot_id=snapshot.pk,
        indexed_items=snapshot.item_count,
        detailed_items=snapshot.detailed_item_count,
        pending_items=snapshot.pending_detail_count,
        failed_items=snapshot.failed_detail_count,
        new_items=len(new_urls),
        backfill_complete=snapshot.backfill_complete,
        pages_fetched=snapshot.pages_fetched,
        detail_requests=requested,
    )


# --------------------------------------------------------------------------


def _merge_with_previous(index, previous_items, *, full: bool):
    """Combine what this walk saw with what earlier walks already knew."""
    seen = {entry.canonical_url for entry in index.entries}
    merged = list(index.entries)
    if full:
        return merged

    from .archived_topics import ArchiveListingEntry

    order = len(merged)
    for url, item in previous_items.items():
        if url in seen:
            continue
        merged.append(
            ArchiveListingEntry(
                content_key=item.content_key,
                canonical_url=item.canonical_url,
                title=item.title,
                listing_summary=item.listing_summary,
                source_page=item.source_page,
                source_order=order,
            )
        )
        order += 1
    return merged


def _carry_forward_details(entries, previous_items) -> dict[str, ArchiveDetail]:
    """Reuse hydration already paid for."""
    carried: dict[str, ArchiveDetail] = {}
    for entry in entries:
        previous = previous_items.get(entry.canonical_url)
        if previous is None or previous.detail_status == DetailStatus.PENDING:
            continue
        carried[entry.canonical_url] = ArchiveDetail(
            canonical_url=entry.canonical_url,
            status=previous.detail_status,
            detail_title=previous.detail_title,
            body_text=previous.body_text,
            published_date=previous.published_date,
            feedback_deadline=previous.feedback_deadline,
            named_organization=previous.named_organization,
            content_hash=previous.detail_content_hash,
            failure_code=previous.detail_failure_code,
        )
    return carried


def _hydrate_within_budget(entries, details, *, budget, session, dry_run) -> int:
    """Read detail pages in priority order until the budget or window closes."""
    from django.conf import settings

    if dry_run or budget <= 0:
        return 0

    cutoff = hydration_cutoff()
    priority = shortlist_archive_urls(entries)
    order = sorted(
        entries,
        key=lambda e: (0 if e.canonical_url in priority else 1, e.source_order),
    )

    requested = 0
    older_run = 0
    for entry in order:
        if requested >= budget:
            break
        existing = details.get(entry.canonical_url)
        if existing is not None and existing.status == DetailStatus.HYDRATED:
            # Already read: it still tells us whether the window has closed.
            if existing.published_date and existing.published_date < cutoff:
                older_run += 1
                if older_run >= settings.KODA_ARCHIVE_WINDOW_STOP_AFTER_OLDER:
                    break
            else:
                older_run = 0
            continue
        if existing is not None and existing.status == DetailStatus.FAILED:
            # Retried on a later run, but not ahead of never-read entries.
            continue

        detail = hydrate_detail(entry.canonical_url, session=session)
        requested += 1
        details[entry.canonical_url] = detail
        if detail.published_date and detail.published_date < cutoff:
            older_run += 1
            if older_run >= settings.KODA_ARCHIVE_WINDOW_STOP_AFTER_OLDER:
                break
        else:
            older_run = 0
    return requested


def _counts(entries, details) -> dict:
    hydrated = failed = 0
    for entry in entries:
        detail = details.get(entry.canonical_url)
        if detail is None:
            continue
        if detail.status == DetailStatus.HYDRATED:
            hydrated += 1
        elif detail.status == DetailStatus.FAILED:
            failed += 1
    return {"hydrated": hydrated, "failed": failed, "pending": len(entries) - hydrated - failed}


def _row(snapshot, entry, detail):
    detail = detail or ArchiveDetail(canonical_url=entry.canonical_url, status=DetailStatus.PENDING)
    return ArchivedTopicItem(
        snapshot=snapshot,
        content_key=entry.content_key,
        canonical_url=entry.canonical_url,
        title=entry.title,
        listing_summary=entry.listing_summary,
        source_page=entry.source_page,
        source_order=entry.source_order,
        is_present=True,
        detail_status=detail.status,
        detail_title=detail.detail_title,
        body_text=detail.body_text,
        published_date=detail.published_date,
        feedback_deadline=detail.feedback_deadline,
        named_organization=detail.named_organization,
        detail_content_hash=detail.content_hash,
        detail_fetched_at=timezone.now() if detail.status != DetailStatus.PENDING else None,
        detail_failure_code=detail.failure_code,
    )


def _verify_written(snapshot, *, expected: int) -> None:
    written = snapshot.items.count()
    if written != expected:
        raise ArchiveCollectionError("Kirjutatud arhiivikirjete arv ei vasta indeksile.")
    declared = (
        snapshot.detailed_item_count + snapshot.pending_detail_count + snapshot.failed_detail_count
    )
    if declared != snapshot.item_count:
        raise ArchiveCollectionError("Arhiivi hetkeseisu arvud ei vasta ridadele.")


def _unchanged(state, *, dry_run, correlation_id, counts, pages, requested, complete):
    snapshot = state.current_snapshot
    if not dry_run:
        mark_unchanged(
            state,
            correlation_id=correlation_id,
            audit_action=AuditAction.ARCHIVED_TOPIC_SYNC_UNCHANGED,
            change_summary={
                "source": state.source.slug,
                "item_count": snapshot.item_count if snapshot else 0,
                "backfill_complete": bool(snapshot and snapshot.backfill_complete),
            },
        )
    return ArchiveSyncReport(
        result=FeedResult.UNCHANGED,
        detail="Arhiiv ei ole muutunud.",
        dry_run=dry_run,
        snapshot_id=snapshot.pk if snapshot else None,
        indexed_items=snapshot.item_count if snapshot else 0,
        detailed_items=counts["hydrated"],
        pending_items=counts["pending"],
        failed_items=counts["failed"],
        backfill_complete=complete,
        pages_fetched=pages,
        detail_requests=requested,
    )


def _fail(state, message: str, correlation_id) -> ArchiveSyncReport:
    outcome = fail_feed(
        state,
        message,
        correlation_id=correlation_id,
        audit_action=AuditAction.ARCHIVED_TOPIC_SYNC_FAILED,
        logger=logger,
    )
    return ArchiveSyncReport(result=outcome.result, detail=outcome.detail)
