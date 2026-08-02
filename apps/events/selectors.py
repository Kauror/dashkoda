"""Read paths for the events dashboard. Reads the current snapshot only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.core.feeds import FeedSummaryMixin

from .models import EventFeedState, EventItem, EventSnapshot

DEFAULT_LIMIT = 20

# The near-term window the overview counts. Matches the legal-work activity
# window so the two KPI cells describe the same length of time.
NEAR_TERM_DAYS = 30


def get_current_event_snapshot() -> EventSnapshot | None:
    return (
        EventSnapshot.objects.filter(source__slug=settings.KODA_EVENTS_SOURCE_SLUG, is_current=True)
        .select_related("source")
        .first()
    )


def get_upcoming_events(snapshot: EventSnapshot | None = None, limit: int = DEFAULT_LIMIT):
    """Events that have not finished yet, soonest first.

    Filtered again at read time as well as at import time: a snapshot published
    yesterday would otherwise keep showing an event that has since ended. An
    event ending today is still upcoming.
    """
    snapshot = snapshot or get_current_event_snapshot()
    if snapshot is None:
        return EventItem.objects.none()
    today = timezone.localdate()
    return (
        EventItem.objects.filter(snapshot=snapshot)
        .filter(_not_finished(today))
        .order_by("starts_on", "title", "stable_key")[:limit]
    )


def count_upcoming_within(
    snapshot: EventSnapshot | None = None, *, days: int = NEAR_TERM_DAYS
) -> int:
    """How many unfinished events start inside the next `days` days.

    Counted by start date, not by overlap: a long-running event that began
    earlier is already under way and is not something starting in the window.
    """
    snapshot = snapshot or get_current_event_snapshot()
    if snapshot is None:
        return 0
    today = timezone.localdate()
    return (
        EventItem.objects.filter(snapshot=snapshot)
        .filter(_not_finished(today))
        .filter(starts_on__lte=today + timedelta(days=days))
        .count()
    )


def count_started_in_past_window(*, days: int = NEAR_TERM_DAYS) -> int | None:
    """How many events started inside the last `days` days, or `None`.

    The collector drops events that have already finished, so the current
    snapshot cannot answer this: counting past events in it would always return
    zero. What can answer it is the archive — an event that ran last month was
    upcoming in some earlier snapshot and its row is still there — so this counts
    distinct events across every snapshot of the source.

    `None`, not `0`, when the archive does not reach back past the window. A
    dashboard installed a fortnight ago never saw last month's calendar, and
    "0 sündmust" would state that nothing happened rather than that nothing was
    recorded.
    """
    today = timezone.localdate()
    window_start = today - timedelta(days=days)
    snapshots = EventSnapshot.objects.filter(source__slug=settings.KODA_EVENTS_SOURCE_SLUG)
    earliest = snapshots.order_by("observed_at", "id").first()
    if earliest is None or timezone.localtime(earliest.observed_at).date() > window_start:
        return None
    return (
        EventItem.objects.filter(snapshot__in=snapshots)
        .filter(starts_on__gte=window_start, starts_on__lt=today)
        .values("stable_key")
        .distinct()
        .count()
    )


def _not_finished(today) -> Q:
    """A single-day event counts until its own day is over."""
    return Q(ends_on__isnull=True, starts_on__gte=today) | Q(ends_on__gte=today)


@dataclass(frozen=True)
class EventSummary(FeedSummaryMixin):
    snapshot: EventSnapshot | None
    feed_state: EventFeedState | None

    @property
    def has_data(self) -> bool:
        return self.snapshot is not None

    @property
    def item_count(self) -> int:
        return self.snapshot.item_count if self.snapshot else 0

    @property
    def observed_at(self):
        return self.snapshot.observed_at if self.snapshot else None


def get_event_summary() -> EventSummary:
    return EventSummary(
        snapshot=get_current_event_snapshot(),
        feed_state=(
            EventFeedState.objects.filter(source__slug=settings.KODA_EVENTS_SOURCE_SLUG)
            .select_related("source")
            .first()
        ),
    )
