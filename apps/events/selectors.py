"""Read paths for the events dashboard. Reads the current snapshot only."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.core.feeds import FeedSummaryMixin

from .models import EventFeedState, EventItem, EventSnapshot

DEFAULT_LIMIT = 20


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
