"""Publish the archive catalogue as an immutable, resumable snapshot.

Each run writes a **complete** snapshot: every entry the index knows, carrying
whatever hydration exists at that moment. Hydration from the previous snapshot
is carried forward, so a run that reads sixty new detail pages publishes a
snapshot with those sixty plus everything read before — the backfill accumulates
across runs rather than restarting.

Hydration has **two independent priorities**, and the order between them is the
whole point of this module.

**Priority A — candidates for records that need a link, at any age.** Every
consultation-eligible record in the current legal snapshot that the current
matcher did not match is shortlisted against the *entire* archive index, all
years. Those candidates are read first and their age is irrelevant.

That is a correction of a real defect. Consultation eligibility is status-based —
open, and no opinion sent — and it says nothing about how old the consultation
was. An earlier version hydrated only the recent window, so a record whose
consultation closed two years ago could never obtain a link no matter how
obviously it matched: the right page stayed permanently unread. Age is now a
*background* consideration, never a gate on a record that is eligible today.

**Priority B — recent background coverage.** Whatever budget is left goes to
unread entries inside the recent window, newest first. This keeps the archive
corpus reasonably complete for inspection, for rarity statistics, and for legal
records that have not arrived yet. It never displaces Priority A.

The full order within one run:

1. shortlisted candidates for currently eligible records, any year;
2. previously failed candidate pages, retried;
3. newest unread entries inside the recent window;
4. nothing else.

`backfill_complete` therefore means: the listing index is whole, **every priority
candidate for the current eligible population is read or definitively failed**,
and the recent window is complete. It does not mean all eleven hundred detail
pages were fetched, and it can legitimately go back to false when a new legal
snapshot introduces a record whose candidate has never been read.

A failure leaves the previous snapshot current, exactly like every other feed.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.core.feed_sync import (
    ContentIdentity,
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
from apps.legal_work.audit_actions import LegalWorkAudit
from apps.sources.services import complete_import_run, fail_publication

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
    priority_candidate_count: int = 0
    priority_detailed_count: int = 0
    priority_pending_count: int = 0
    priority_failed_count: int = 0
    recent_detailed_count: int = 0
    recent_pending_count: int = 0
    index_complete: bool = False
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
            "priority_candidate_count": self.priority_candidate_count,
            "priority_detailed_count": self.priority_detailed_count,
            "priority_pending_count": self.priority_pending_count,
            "priority_failed_count": self.priority_failed_count,
            "recent_detailed_count": self.recent_detailed_count,
            "recent_pending_count": self.recent_pending_count,
            "index_complete": self.index_complete,
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

    # Which archive pages a link currently depends on. Read before hydration,
    # never written to: this is the one place the archive feed looks at legal
    # data, and it looks only.
    priority_urls = _priority_candidate_urls(entries)

    try:
        requested = _hydrate_within_budget(
            entries,
            details,
            budget=budget,
            session=session,
            dry_run=dry_run,
            priority_urls=priority_urls,
        )
    except Exception as error:  # noqa: BLE001
        return _fail(state, describe_error(error), correlation_id)

    checksum, size = checksum_for(entries, details)
    counts = _counts(entries, details, priority_urls=priority_urls)
    # Index completeness is a fact that persists. An incremental walk stops after
    # a couple of already-known pages and so never sets `reached_end`, but it has
    # not *disproved* anything: the index was complete and this run only looked
    # at the front of it. Recomputing completeness from this run alone made every
    # daily run after a finished backfill believe the index had regressed, fall
    # through the unchanged guard, and try to publish a second successful live
    # import for identical content — which the import registry correctly refuses.
    index_complete = index.reached_end or bool(previous and previous.index_complete)
    complete = index_complete and counts["priority_pending"] == 0 and counts["recent_pending"] == 0

    artifact, already_published = find_published_artifact(source, checksum, IMPORTER_NAME)
    # Identical content is only genuinely "unchanged" when there is no work
    # left. A run that could not finish its priority candidates within the
    # budget has produced the same rows as last time and is nonetheless not
    # done, and reporting `unchanged` would tell an operator to stop re-running
    # it exactly when re-running is what it needs.
    if already_published and complete:
        return _unchanged(
            state,
            dry_run=dry_run,
            correlation_id=correlation_id,
            counts=counts,
            pages=index.pages_fetched,
            requested=requested,
            complete=complete,
            index_complete=index_complete,
            priority_candidates=len(priority_urls),
        )

    collection = ContentIdentity(sha256=checksum, size_bytes=size)
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
            priority_candidate_count=len(priority_urls),
            priority_detailed_count=counts["priority_hydrated"],
            priority_pending_count=counts["priority_pending"],
            priority_failed_count=counts["priority_failed"],
            recent_detailed_count=counts["recent_hydrated"],
            recent_pending_count=counts["recent_pending"],
            index_complete=index_complete,
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
                priority_candidate_count=len(priority_urls),
                priority_detailed_count=counts["priority_hydrated"],
                priority_pending_count=counts["priority_pending"],
                index_complete=index_complete,
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
                action=LegalWorkAudit.ARCHIVED_TOPIC_SNAPSHOT_IMPORTED,
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
                    "priority_candidates": snapshot.priority_candidate_count,
                    "priority_detailed": snapshot.priority_detailed_count,
                    "priority_pending": snapshot.priority_pending_count,
                    "index_complete": snapshot.index_complete,
                    "backfill_complete": snapshot.backfill_complete,
                },
            )
    except Exception as error:  # noqa: BLE001
        fail_publication(run, errors=[{"type": type(error).__name__}], actor=actor)
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
        priority_candidate_count=snapshot.priority_candidate_count,
        priority_detailed_count=snapshot.priority_detailed_count,
        priority_pending_count=snapshot.priority_pending_count,
        priority_failed_count=counts["priority_failed"],
        recent_detailed_count=counts["recent_hydrated"],
        recent_pending_count=counts["recent_pending"],
        index_complete=snapshot.index_complete,
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


def _priority_candidate_urls(entries) -> dict[str, int]:
    """Archive pages a currently eligible record might depend on, at any age.

    Reads the exact current legal snapshot and the exact current-topic match
    snapshot to decide who still needs a link, then shortlists across the
    **whole** index — every year of it, because eligibility is about the
    record's status and says nothing about when its consultation ran.

    Both snapshots are read and never written. When either is missing the
    archive feed still collects and still hydrates its recent window; it simply
    has nobody to prioritise for, which the report states as a count of zero
    rather than as an error.
    """
    from .models import LegalCurrentTopicMatchSnapshot, LegalWorkSnapshot
    from .shortlist import eligible_records_needing_a_link

    legal_snapshot = LegalWorkSnapshot.objects.filter(is_current=True).first()
    if legal_snapshot is None:
        return {}
    current_match = LegalCurrentTopicMatchSnapshot.objects.filter(
        is_current=True, legal_snapshot=legal_snapshot
    ).first()
    if current_match is None:
        # Without knowing what the current listing already answered, every
        # eligible record would look like it needs the archive -- including the
        # ones the current matcher is about to answer, which would spend the
        # budget on pages no link will ever use. Skipping is the safe reading,
        # and the run still does its background window.
        return {}

    records = eligible_records_needing_a_link(legal_snapshot, current_match)
    if not records:
        return {}
    return shortlist_archive_urls(entries, records)


def _hydrate_within_budget(entries, details, *, budget, session, dry_run, priority_urls) -> int:
    """Spend the run's detail budget, priority candidates first.

    Two passes over one budget. The first reads shortlisted candidates for
    records that need a link **regardless of their age** — that is the whole
    correction, and it is why the recent-window rule appears only in the second
    pass. The second fills whatever is left with recent entries, newest first,
    stopping once the window closes.

    A URL shortlisted by five different legal records is fetched once: the pass
    walks a deduplicated set, so overlapping shortlists cost one request.
    """
    from django.conf import settings

    if dry_run or budget <= 0:
        return 0

    by_url = {entry.canonical_url: entry for entry in entries}
    requested = 0

    # -- pass one: priority candidates, any year ---------------------------
    #
    # Never-read candidates come before previously-failed ones: a page nobody
    # has looked at is more likely to yield a link than one that already
    # refused, and the failed set is retried with whatever remains.
    def priority_rank(url: str) -> tuple[int, int, int]:
        detail = details.get(url)
        failed = detail is not None and detail.status == DetailStatus.FAILED
        # Never-read before previously-failed; then strongest overlap; then the
        # archive's own order as a deterministic tie-break. A run allowed one
        # request therefore spends it on the most promising candidate rather
        # than on whichever happens to sit earliest in the archive.
        return (1 if failed else 0, -priority_urls[url], by_url[url].source_order)

    ordered_priority = sorted((url for url in priority_urls if url in by_url), key=priority_rank)
    for url in ordered_priority:
        if requested >= budget:
            break
        detail = details.get(url)
        if detail is not None and detail.status == DetailStatus.HYDRATED:
            continue
        details[url] = hydrate_detail(url, session=session)
        requested += 1

    # -- pass two: recent background coverage ------------------------------
    if requested >= budget:
        return requested

    cutoff = hydration_cutoff()
    older_run = 0
    for entry in sorted(entries, key=lambda e: e.source_order):
        if requested >= budget:
            break
        url = entry.canonical_url
        existing = details.get(url)
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
            # Retried by the priority pass when it matters; not chased here.
            continue

        detail = hydrate_detail(url, session=session)
        requested += 1
        details[url] = detail
        if detail.published_date and detail.published_date < cutoff:
            older_run += 1
            if older_run >= settings.KODA_ARCHIVE_WINDOW_STOP_AFTER_OLDER:
                break
        else:
            older_run = 0
    return requested


def _recent_window_pending(entries, details) -> int:
    """Unread entries the background pass would still like to read.

    Counted the way the pass walks: newest first, stopping at the window. An
    entry beyond the window is not pending — nothing intends to read it.
    """
    from django.conf import settings

    cutoff = hydration_cutoff()
    pending = older_run = 0
    for entry in sorted(entries, key=lambda e: e.source_order):
        detail = details.get(entry.canonical_url)
        if detail is None:
            pending += 1
            continue
        if detail.status == DetailStatus.FAILED:
            continue
        if detail.published_date and detail.published_date < cutoff:
            older_run += 1
            if older_run >= settings.KODA_ARCHIVE_WINDOW_STOP_AFTER_OLDER:
                break
        else:
            older_run = 0
    return pending


def _counts(entries, details, *, priority_urls=frozenset()) -> dict:
    """Hydration progress, split into the two priorities that produced it."""
    hydrated = failed = 0
    priority_hydrated = priority_failed = 0
    for entry in entries:
        detail = details.get(entry.canonical_url)
        if detail is None:
            continue
        is_priority = entry.canonical_url in priority_urls
        if detail.status == DetailStatus.HYDRATED:
            hydrated += 1
            priority_hydrated += is_priority
        elif detail.status == DetailStatus.FAILED:
            failed += 1
            priority_failed += is_priority

    # A priority candidate is pending only while it is neither read nor
    # definitively failed; a recorded failure is a terminal state for this run
    # and must not hold `backfill_complete` false for ever.
    priority_pending = len(priority_urls) - priority_hydrated - priority_failed
    return {
        "hydrated": hydrated,
        "failed": failed,
        "pending": len(entries) - hydrated - failed,
        "priority_hydrated": priority_hydrated,
        "priority_failed": priority_failed,
        "priority_pending": max(0, priority_pending),
        "recent_hydrated": hydrated - priority_hydrated,
        "recent_pending": _recent_window_pending(entries, details),
    }


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


def _unchanged(
    state,
    *,
    dry_run,
    correlation_id,
    counts,
    pages,
    requested,
    complete,
    index_complete,
    priority_candidates,
):
    snapshot = state.current_snapshot
    if not dry_run:
        mark_unchanged(
            state,
            correlation_id=correlation_id,
            audit_action=LegalWorkAudit.ARCHIVED_TOPIC_SYNC_UNCHANGED,
            change_summary={
                "source": state.source.slug,
                "item_count": snapshot.item_count if snapshot else 0,
                "backfill_complete": bool(snapshot and snapshot.backfill_complete),
            },
        )
    return ArchiveSyncReport(
        result=FeedResult.UNCHANGED,
        detail="Arhiiv ei ole muutunud ja lugemata prioriteetseid lehti ei ole.",
        dry_run=dry_run,
        snapshot_id=snapshot.pk if snapshot else None,
        indexed_items=snapshot.item_count if snapshot else 0,
        detailed_items=counts["hydrated"],
        pending_items=counts["pending"],
        failed_items=counts["failed"],
        # Reported even when nothing changed: an operator asking "does anything
        # still depend on a page I have not read?" must get the same answer
        # whether or not this run happened to publish.
        priority_candidate_count=priority_candidates,
        priority_detailed_count=counts["priority_hydrated"],
        priority_pending_count=counts["priority_pending"],
        priority_failed_count=counts["priority_failed"],
        recent_detailed_count=counts["recent_hydrated"],
        recent_pending_count=counts["recent_pending"],
        index_complete=index_complete,
        backfill_complete=complete,
        pages_fetched=pages,
        detail_requests=requested,
    )


def _fail(state, message: str, correlation_id) -> ArchiveSyncReport:
    outcome = fail_feed(
        state,
        message,
        correlation_id=correlation_id,
        audit_action=LegalWorkAudit.ARCHIVED_TOPIC_SYNC_FAILED,
        logger=logger,
    )
    return ArchiveSyncReport(result=outcome.result, detail=outcome.detail)
