"""Read paths for the legal-work dashboard.

Every query lives here rather than in a template or a view, so the definition
of "currently open" or "latest sent" has exactly one home.

All of them read the current snapshot only. When an older snapshot exists but
none is current, the answer is an empty state: showing retired data as if it
were live would be worse than showing nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from apps.core.feeds import FeedSummaryMixin

from .models import LegalWorkFeedState, LegalWorkItem, LegalWorkSnapshot, SentStatus

DEFAULT_RECENT_LIMIT = 15
# The open list is bounded so a workbook that grows cannot produce an
# unbounded page.
MAX_OPEN_ITEMS = 200

# The window the overview reports activity over. A month is short enough that a
# board recognises the period and long enough that a quiet fortnight does not
# read as a stalled department.
ACTIVITY_WINDOW_DAYS = 30

# How far ahead a deadline has to be before it stops being something the board
# needs to see on the front page.
DEADLINE_HORIZON_DAYS = 21
DEADLINE_PREVIEW_LIMIT = 5

# Thresholds for how a remaining deadline is described. Expressed in whole days
# because the workbook records a date and never a time.
DEADLINE_URGENT_DAYS = 3
DEADLINE_SOON_DAYS = 10


def get_current_snapshot() -> LegalWorkSnapshot | None:
    return (
        LegalWorkSnapshot.objects.filter(
            source__slug=settings.LEGAL_WORK_SOURCE_SLUG,
            is_current=True,
        )
        .select_related("source")
        .first()
    )


def _items(snapshot: LegalWorkSnapshot | None):
    if snapshot is None:
        return LegalWorkItem.objects.none()
    return LegalWorkItem.objects.filter(snapshot=snapshot)


def get_open_items(snapshot: LegalWorkSnapshot | None = None, limit: int | None = MAX_OPEN_ITEMS):
    """Topics still being worked on, most recently received first."""
    snapshot = snapshot or get_current_snapshot()
    # PostgreSQL puts NULLs first in a descending order, so `nulls_last` is
    # explicit: dated records lead and undated ones trail by topic.
    queryset = (
        _items(snapshot)
        .filter(is_open=True)
        .order_by(F("received_date").desc(nulls_last=True), "topic", "record_id")
    )
    return queryset[:limit] if limit else queryset


def get_latest_sent_items(
    snapshot: LegalWorkSnapshot | None = None, limit: int = DEFAULT_RECENT_LIMIT
):
    """Most recently sent opinions.

    `not_sent` never appears here: a record that was explicitly not sent is not
    a recent send, and the model constraint already guarantees it carries no
    date to sort by.
    """
    snapshot = snapshot or get_current_snapshot()
    return (
        _items(snapshot)
        .filter(sent_status=SentStatus.SENT, sent_date__isnull=False)
        .order_by("-sent_date", "topic", "record_id")[:limit]
    )


def get_newest_received_items(
    snapshot: LegalWorkSnapshot | None = None, limit: int = DEFAULT_RECENT_LIMIT
):
    """Most recently received topics.

    A received date in the future is a known workbook data problem, flagged by
    the generator's `received_date_in_future` warning. Such a record would
    otherwise sit permanently at the top of this list, so it is excluded here
    while remaining fully present in the imported data.
    """
    snapshot = snapshot or get_current_snapshot()
    return (
        _items(snapshot)
        .filter(received_date__isnull=False, received_date__lte=_today())
        .order_by("-received_date", "topic", "record_id")[:limit]
    )


def count_received_since(snapshot: LegalWorkSnapshot | None, since: date) -> int:
    """Topics received between `since` and today, both inclusive.

    Bounded at both ends. The upper bound matters: the workbook is known to
    carry the occasional future received date, and counting those would make
    the window report more arrivals than actually arrived.
    """
    snapshot = snapshot or get_current_snapshot()
    return _items(snapshot).filter(received_date__gte=since, received_date__lte=_today()).count()


def count_sent_since(snapshot: LegalWorkSnapshot | None, since: date) -> int:
    """Opinions sent between `since` and today, both inclusive."""
    snapshot = snapshot or get_current_snapshot()
    return (
        _items(snapshot)
        .filter(sent_status=SentStatus.SENT, sent_date__gte=since, sent_date__lte=_today())
        .count()
    )


@dataclass(frozen=True)
class Deadline:
    """One approaching opinion deadline, and how close it is.

    The urgency is derived here rather than in the template so that "urgent"
    means the same thing everywhere it is drawn, and so the label exists as text
    beside the colour.
    """

    item: LegalWorkItem
    days_remaining: int

    @property
    def is_urgent(self) -> bool:
        return self.days_remaining <= DEADLINE_URGENT_DAYS

    @property
    def variant(self) -> str:
        if self.is_urgent:
            return "danger"
        return "warning" if self.days_remaining <= DEADLINE_SOON_DAYS else "info"

    @property
    def remaining_label(self) -> str:
        if self.days_remaining == 0:
            return "täna"
        if self.days_remaining == 1:
            return "1 päev"
        return f"{self.days_remaining} päeva"


def get_upcoming_deadlines(
    snapshot: LegalWorkSnapshot | None = None,
    *,
    within_days: int = DEADLINE_HORIZON_DAYS,
    limit: int = DEADLINE_PREVIEW_LIMIT,
) -> tuple[Deadline, ...]:
    """Open topics whose opinion deadline falls inside the horizon.

    Only open records: a deadline on something already concluded is history, not
    something the board can still act on. A deadline that has already passed is
    excluded too — the workbook is the place to correct it, and surfacing it
    here would read as an action that is still available.
    """
    snapshot = snapshot or get_current_snapshot()
    today = _today()
    items = (
        _items(snapshot)
        .filter(
            is_open=True,
            deadline_date__isnull=False,
            deadline_date__gte=today,
            deadline_date__lte=today + timedelta(days=within_days),
        )
        .order_by("deadline_date", "topic", "record_id")[:limit]
    )
    return tuple(
        Deadline(item=item, days_remaining=(item.deadline_date - today).days) for item in items
    )


def _today() -> date:
    return timezone.localdate()


@dataclass(frozen=True)
class LegalWorkSummary(FeedSummaryMixin):
    """Everything the dashboard needs to describe the data's state honestly."""

    snapshot: LegalWorkSnapshot | None
    feed_state: LegalWorkFeedState | None

    @property
    def has_data(self) -> bool:
        return self.snapshot is not None

    @property
    def open_count(self) -> int:
        return self.snapshot.open_record_count if self.snapshot else 0

    @property
    def total_count(self) -> int:
        return self.snapshot.total_record_count if self.snapshot else 0

    @property
    def reporting_date(self):
        return self.snapshot.reporting_date if self.snapshot else None

    @property
    def generated_at(self):
        return self.snapshot.workbook_generated_at if self.snapshot else None


def get_legal_work_summary() -> LegalWorkSummary:
    snapshot = get_current_snapshot()
    feed_state = (
        LegalWorkFeedState.objects.filter(source__slug=settings.LEGAL_WORK_SOURCE_SLUG)
        .select_related("source")
        .first()
    )
    return LegalWorkSummary(snapshot=snapshot, feed_state=feed_state)
