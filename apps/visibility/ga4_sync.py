"""Collect GA4 reporting days and publish each one as an immutable revision.

The same sequence every other feed follows, through the same shared bookkeeping
in :mod:`apps.core.feed_sync`: record the check, collect, recognise the content
by its canonical checksum, open an import run, publish atomically, record the
outcome. What is different is the unit and the window.

## The unit is a reporting day, and days get revised

GA4 keeps adjusting a day for several days after it ends — late hits arrive,
sessions are stitched to identities, bot traffic is reclassified. So a
collection is not "fetch yesterday and be done": each run reconciles a window of
completed days, and a day whose normalised figures have changed is republished
as a **new revision** that names the one it replaces. Nothing published is ever
rewritten, and exactly one revision per date is current.

The window is :data:`RECONCILIATION_DAYS` completed days ending yesterday — by
default yesterday plus the seven before it. Today is never collected: a partial
day would publish a figure that is wrong by construction and then have to be
revised tomorrow, which is a lot of provenance to record about the fact that the
day had not finished.

## Backfill is the same code with a longer window

`backfill` walks a bounded range in chunks and publishes each day through the
same path, so a historical import and a nightly run cannot disagree about what a
day means. A chunk that fails leaves every chunk before it published: the range
is resumable because the work is idempotent, not because a cursor is stored.

Nothing in this module logs, stores or returns a property ID, a credential path,
an access token or a Google response body.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.core.feed_sync import (
    fail_feed,
    get_feed_state,
    mark_imported,
    mark_unchanged,
    touch_checked,
)
from apps.core.feeds import FeedResult, SourceOutcome
from apps.sources.models import SourceArtifact
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    publishing_run,
    register_external_reference,
    start_import_run,
)
from apps.visibility.audit_actions import VisibilityAudit

from .bootstrap import ensure_ga4_source
from .ga4 import (
    SCHEMA_VERSION,
    CollectionCounts,
    DayReading,
    Ga4ApiCollector,
    Ga4NotConfigured,
    Ga4ResponseError,
    get_configuration,
)
from .models import Ga4ChannelDaily, Ga4DailySnapshot, Ga4FeedState, Ga4PageDaily

logger = logging.getLogger("dashkoda.visibility.ga4_sync")

IMPORTER_NAME = "ga4_daily"
ARTIFACT_NAME = "ga4-daily.json"

#: Its own name, so a GA4 run can neither block nor be blocked by any other
#: feed. The key derivation is the shared one in `apps.core.feeds`.
LOCK_NAME = "dashkoda.visibility.sync_ga4"

#: How many completed days the ordinary run re-reads: yesterday and the seven
#: before it. GA4's own guidance is that a day settles within about 48 hours,
#: and the property's data keeps moving for longer than that; eight days is
#: comfortably past it and still one cheap request per report.
#:
#: Raising it costs almost nothing — the whole window is three requests — so if
#: revisions are ever seen arriving later than this, raise it.
RECONCILIATION_DAYS = 8

#: How much of a backfill is asked for at a time. A month of page rows is a few
#: thousand on this property, well inside one page of results, and a failure
#: costs at most one month of re-reading.
BACKFILL_CHUNK_DAYS = 31


class DayAction:
    """What happened to one reporting day. Values are stable output contract."""

    IMPORTED = "imported"
    REVISED = "revised"
    UNCHANGED = "unchanged"
    KEPT = "kept"


@dataclass
class SyncCounts:
    """Aggregates for the command's JSON output. Never a list of pages."""

    days_examined: int = 0
    days_imported: int = 0
    days_revised: int = 0
    days_unchanged: int = 0
    days_kept: int = 0
    page_rows_written: int = 0
    channel_rows_written: int = 0
    chunks: int = 0
    api_requests: int = 0
    api_retries: int = 0

    def as_dict(self) -> dict:
        return {
            "days_examined": self.days_examined,
            "days_imported": self.days_imported,
            "days_revised": self.days_revised,
            "days_unchanged": self.days_unchanged,
            "days_kept": self.days_kept,
            "page_rows_written": self.page_rows_written,
            "channel_rows_written": self.channel_rows_written,
            "chunks": self.chunks,
            "api_requests": self.api_requests,
            "api_retries": self.api_retries,
        }

    def absorb(self, other: CollectionCounts) -> None:
        self.api_requests += other.requests
        self.api_retries += other.retries


@dataclass
class DayOutcome:
    report_date: date
    action: str
    snapshot_id: int | None = None
    reason: str = ""


@dataclass
class SyncReport:
    counts: SyncCounts = field(default_factory=SyncCounts)
    days: list[DayOutcome] = field(default_factory=list)
    first_date: date | None = None
    last_date: date | None = None

    @property
    def changed(self) -> bool:
        return bool(self.counts.days_imported or self.counts.days_revised)


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def last_completed_day(today: date | None = None) -> date:
    """Yesterday, in application time.

    `timezone.localdate()` rather than the container's clock: the reporting day
    is a `Europe/Tallinn` day, and a UTC container between midnight and 03:00
    would otherwise ask for the day before the one intended.
    """
    return (today or timezone.localdate()) - timedelta(days=1)


def reconciliation_window(
    today: date | None = None, *, days: int = RECONCILIATION_DAYS
) -> tuple[date, date]:
    """The completed days an ordinary run re-reads, oldest first.

    Ends yesterday and never includes today, which has not finished.
    """
    if days < 1:
        raise ValueError("Vaadeldav aken peab olema vähemalt üks päev.")
    end = last_completed_day(today)
    return end - timedelta(days=days - 1), end


def chunks(
    start: date, end: date, *, size: int = BACKFILL_CHUNK_DAYS
) -> Iterator[tuple[date, date]]:
    """Split an inclusive range into bounded inclusive chunks, oldest first."""
    if size < 1:
        raise ValueError("Tüki suurus peab olema vähemalt üks päev.")
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=size - 1), end)
        yield cursor, stop
        cursor = stop + timedelta(days=1)


def canonical_digest(reading: DayReading) -> tuple[bytes, str]:
    """The reading's canonical bytes and their SHA-256.

    The digest is over the **normalised reading**, never over the API response:
    Google is free to reorder keys or add fields without that meaning the
    Chamber's website had a different day.
    """
    payload = json.dumps(
        reading.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return payload, hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


def synchronize_ga4(
    *,
    dry_run: bool = False,
    actor=None,
    collector=None,
    start: date | None = None,
    end: date | None = None,
    with_pages: bool = True,
    with_channels: bool = True,
    chunk_days: int = BACKFILL_CHUNK_DAYS,
    today: date | None = None,
) -> SourceOutcome:
    """Reconcile a window of completed days, publishing what has changed.

    With no dates, the window is :func:`reconciliation_window`. With dates, it is
    exactly what was asked for — clamped at yesterday, because collecting today
    publishes a partial day as though it were a whole one.
    """
    correlation_id = uuid.uuid4()
    latest_allowed = last_completed_day(today)

    if start is None and end is None:
        start, end = reconciliation_window(today)
    else:
        end = min(end or latest_allowed, latest_allowed)
        start = start or end
    if start > end:
        # Asking for a window that is entirely in the future is not a failure —
        # it is a schedule that ran before its first day finished.
        return SourceOutcome(
            result=FeedResult.UNCHANGED,
            detail="Vahemikus ei ole ühtegi lõppenud päeva.",
            dry_run=dry_run,
            extra={"window_start": None, "window_end": None, **SyncCounts().as_dict()},
        )

    source = ensure_ga4_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(Ga4FeedState, source)
    touch_checked(state)

    try:
        collect = collector if collector is not None else Ga4ApiCollector(get_configuration())
    except Ga4NotConfigured as error:
        # Names the missing settings and never their values.
        return _fail(state, str(error), correlation_id)
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _fail(state, _transport_failure(error), correlation_id)

    report = SyncReport(first_date=start, last_date=end)

    for chunk_start, chunk_end in chunks(start, end, size=chunk_days):
        report.counts.chunks += 1
        try:
            collection = collect.collect_range(
                start=chunk_start,
                end=chunk_end,
                with_pages=with_pages,
                with_channels=with_channels,
            )
        except Ga4NotConfigured as error:
            return _fail(state, str(error), correlation_id)
        except Ga4ResponseError as error:
            # Our own sentence, written in `ga4.py`, safe to record verbatim.
            # Every chunk already published stays published; the range is
            # resumable by re-running.
            return _fail(state, str(error), correlation_id, report=report)
        except Exception as error:  # noqa: BLE001
            return _fail(state, _transport_failure(error), correlation_id, report=report)

        report.counts.absorb(collection.counts)

        for day in sorted(collection.days):
            reading = collection.days[day]
            report.counts.days_examined += 1
            try:
                outcome = _publish_day(
                    reading,
                    source=source,
                    actor=actor,
                    correlation_id=correlation_id,
                    dry_run=dry_run,
                    counts=report.counts,
                )
            except Exception as error:  # noqa: BLE001
                return _fail(state, _transport_failure(error), correlation_id, report=report)
            report.days.append(outcome)

    if not dry_run:
        published = _newest_current(source)
        if report.changed and published is not None:
            mark_imported(state, published, current_field="current_snapshot")
        else:
            mark_unchanged(
                state,
                correlation_id=correlation_id,
                audit_action=VisibilityAudit.GA4_SYNC_UNCHANGED,
                change_summary={"source": source.slug, "window_end": end.isoformat()},
            )
        _remember_period(state, end)

    logger.info(
        "ga4.sync window=%s..%s imported=%d revised=%d unchanged=%d",
        start.isoformat(),
        end.isoformat(),
        report.counts.days_imported,
        report.counts.days_revised,
        report.counts.days_unchanged,
    )

    return SourceOutcome(
        result=FeedResult.IMPORTED if report.changed else FeedResult.UNCHANGED,
        detail=_detail(report, dry_run=dry_run),
        dry_run=dry_run,
        extra={
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            **report.counts.as_dict(),
        },
    )


def backfill_ga4(
    *,
    start: date,
    end: date | None = None,
    dry_run: bool = False,
    actor=None,
    collector=None,
    chunk_days: int = BACKFILL_CHUNK_DAYS,
    with_pages: bool = True,
    with_channels: bool = True,
    today: date | None = None,
) -> SourceOutcome:
    """Import a bounded historical range.

    Deliberately the same publication path as the nightly run rather than a
    faster one that skips the checksum: a historical day and a reconciled day
    have to mean the same thing, or the join between them is a seam in every
    chart that crosses it.
    """
    return synchronize_ga4(
        dry_run=dry_run,
        actor=actor,
        collector=collector,
        start=start,
        end=end if end is not None else last_completed_day(today),
        with_pages=with_pages,
        with_channels=with_channels,
        chunk_days=chunk_days,
        today=today,
    )


def _publish_day(
    reading: DayReading,
    *,
    source,
    actor,
    correlation_id,
    dry_run: bool,
    counts: SyncCounts,
) -> DayOutcome:
    """Publish one day, or recognise that it has not changed."""
    day = reading.report_date
    current = (
        Ga4DailySnapshot.objects.filter(source=source, report_date=day, is_current_for_date=True)
        .only("id", "checksum", "revision", "has_page_detail", "has_channel_detail")
        .first()
    )

    _, digest = canonical_digest(reading)

    if current is not None and current.checksum == digest:
        counts.days_unchanged += 1
        return DayOutcome(report_date=day, action=DayAction.UNCHANGED, snapshot_id=current.pk)

    if current is not None and _would_lose_detail(current, reading):
        # A site-only re-read of a day that already carries page rows would
        # publish a revision with less in it than the one it replaces, and the
        # charts would lose an article's history to a run that never asked
        # about articles. Refusing is the honest outcome; collect with detail.
        counts.days_kept += 1
        return DayOutcome(
            report_date=day,
            action=DayAction.KEPT,
            snapshot_id=current.pk,
            reason="Uus lugemine on kitsam kui avaldatud päev.",
        )

    action = DayAction.REVISED if current is not None else DayAction.IMPORTED

    if dry_run:
        if action == DayAction.REVISED:
            counts.days_revised += 1
        else:
            counts.days_imported += 1
        return DayOutcome(report_date=day, action=action)

    payload, digest = canonical_digest(reading)
    artifact = SourceArtifact.objects.filter(source=source, sha256=digest).first()
    if artifact is None:
        artifact = register_external_reference(
            source=source,
            external_reference=f"ga4:data-api:{day.isoformat()}",
            original_name=ARTIFACT_NAME,
            mime_type="application/json",
            sha256=digest,
            size_bytes=len(payload),
            actor=actor,
            correlation_id=correlation_id,
        )
    run = build_import_run(
        artifact=artifact,
        importer_name=IMPORTER_NAME,
        schema_version=SCHEMA_VERSION,
        dry_run=False,
        actor=actor,
        correlation_id=correlation_id,
    )
    start_import_run(run)

    with publishing_run(run, errors=[{"type": "publish_failed"}], actor=actor):
        with transaction.atomic():
            # Locked, because the unique index allows exactly one current
            # revision per date and two runs reconciling the same window would
            # otherwise race to insert the second one.
            locked = (
                Ga4DailySnapshot.objects.select_for_update()
                .filter(source=source, report_date=day, is_current_for_date=True)
                .first()
            )
            snapshot = Ga4DailySnapshot(
                source=source,
                artifact=artifact,
                import_run=run,
                report_date=day,
                observed_at=timezone.now(),
                checksum=digest,
                revision=(locked.revision + 1) if locked is not None else 1,
                supersedes=locked,
                is_current_for_date=False,
                sessions=reading.sessions,
                active_users=reading.active_users,
                new_users=reading.new_users,
                page_views=reading.page_views,
                engaged_sessions=reading.engaged_sessions,
                user_engagement_seconds=reading.user_engagement_seconds,
                has_page_detail=reading.has_page_detail,
                has_channel_detail=reading.has_channel_detail,
            )
            snapshot.save()

            Ga4PageDaily.objects.bulk_create(
                [
                    Ga4PageDaily(
                        snapshot=snapshot,
                        report_date=day,
                        path=row.path,
                        raw_path=row.raw_path[:500],
                        page_views=row.page_views,
                        active_users=row.active_users,
                        user_engagement_seconds=row.user_engagement_seconds,
                    )
                    for row in reading.pages
                ],
                batch_size=1000,
            )
            Ga4ChannelDaily.objects.bulk_create(
                [
                    Ga4ChannelDaily(
                        snapshot=snapshot,
                        report_date=day,
                        channel=row.channel[:120],
                        sessions=row.sessions,
                        engaged_sessions=row.engaged_sessions,
                    )
                    for row in reading.channels
                ],
                batch_size=1000,
            )

            if locked is not None:
                locked.is_current_for_date = False
                locked.save(update_fields=["is_current_for_date"])
            snapshot.is_current_for_date = True
            snapshot.save(update_fields=["is_current_for_date"])

            complete_import_run(
                run,
                rows_added=1 + len(reading.pages) + len(reading.channels),
                actor=actor,
            )
            record_event(
                action=VisibilityAudit.GA4_OBSERVATION_IMPORTED,
                obj=snapshot,
                actor=actor,
                correlation_id=correlation_id,
                # Aggregates, a checksum and identifiers. No property ID, no
                # credential, no token, no Google response, no page list.
                change_summary={
                    "source": source.slug,
                    "sha256": digest,
                    "report_date": day.isoformat(),
                    "revision": snapshot.revision,
                    "supersedes_id": locked.pk if locked is not None else None,
                    "snapshot_id": snapshot.pk,
                    "page_rows": len(reading.pages),
                    "channel_rows": len(reading.channels),
                    "figures_reported": reading.has_any_figure,
                },
            )

    counts.page_rows_written += len(reading.pages)
    counts.channel_rows_written += len(reading.channels)
    if action == DayAction.REVISED:
        counts.days_revised += 1
    else:
        counts.days_imported += 1
    return DayOutcome(report_date=day, action=action, snapshot_id=snapshot.pk)


def _transport_failure(error: Exception) -> str:
    """A failure sentence that cannot carry what the failure was talking about.

    `describe_error` renders `f"{type(error).__name__}: {error}"`, which is right
    for a feed whose exceptions are its own. GA4's are not: a transport error
    raised inside `requests` carries the **request URL**, and that URL contains
    the property ID. `OSError("HTTP 403 for property …")` is exactly the shape,
    and it was reaching `Ga4FeedState.last_error_summary` — a field rendered in
    the admin — and the log line beside it.

    So only the exception's type is recorded. An operator gets the class name,
    which is what tells them whether to look at the network or at the
    credential, and `ga4_status` tells them the rest.
    """
    return f"Google Analyticsi päring ebaõnnestus ({type(error).__name__})."


def _would_lose_detail(current: Ga4DailySnapshot, reading: DayReading) -> bool:
    """Whether replacing `current` with `reading` would drop a kind of detail."""
    return (current.has_page_detail and not reading.has_page_detail) or (
        current.has_channel_detail and not reading.has_channel_detail
    )


def _newest_current(source) -> Ga4DailySnapshot | None:
    return (
        Ga4DailySnapshot.objects.filter(source=source, is_current_for_date=True)
        .order_by("-report_date")
        .first()
    )


def _remember_period(state, period: date) -> None:
    """Record which reporting day the feed has reached."""
    state.last_period_end = period
    state.save(update_fields=["last_period_end", "updated_at"])


def _detail(report: SyncReport, *, dry_run: bool) -> str:
    counts = report.counts
    if dry_run:
        return (
            f"Kuivkäivitus: {counts.days_examined} päeva vaadatud, "
            f"{counts.days_imported} uut ja {counts.days_revised} muutunut, "
            "midagi ei avaldatud."
        )
    if not report.changed:
        return f"Google Analyticsi andmed ei ole muutunud ({counts.days_examined} päeva)."
    return f"{counts.days_imported} uut päeva ja {counts.days_revised} parandatud päeva avaldatud."


def _fail(
    state, message: str, correlation_id, *, report: SyncReport | None = None
) -> SourceOutcome:
    """Record the failure and leave every published day exactly where it is."""
    outcome = fail_feed(
        state,
        message,
        correlation_id=correlation_id,
        audit_action=VisibilityAudit.GA4_SYNC_FAILED,
        logger=logger,
    )
    if report is not None:
        # What did land before the failure. A resumable import is only resumable
        # if the operator can see how far it got.
        outcome.extra.update(report.counts.as_dict())
    return outcome


__all__ = [
    "BACKFILL_CHUNK_DAYS",
    "IMPORTER_NAME",
    "LOCK_NAME",
    "RECONCILIATION_DAYS",
    "DayAction",
    "SyncCounts",
    "SyncReport",
    "backfill_ga4",
    "canonical_digest",
    "chunks",
    "last_completed_day",
    "reconciliation_window",
    "synchronize_ga4",
]
