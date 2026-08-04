"""Read paths for the public Koda.ee calendar. Reads the current snapshot only.

This feed is **not** the dashboard's event source. `apps.event_programme` is: it
imports the Chamber's canonical Excel programme, which carries the whole
available history, the real dates, the tags, the types and the validated public
links. Nothing here may supply, extend, correct or override any of that.

What this module still answers is what the public calendar itself can honestly
answer: whether the collector is publishing, when it last succeeded, and how many
publicly announced events are still to come. Both are shown on the Sündmused page
as a named secondary connection beside the programme, never as a total of its
own.

`count_started_in_past_window` used to live here. It reconstructed a past-event
count by scanning every archived snapshot, because the collector drops an event
once it has finished. The workbook retains what actually happened, so that figure
now comes from the programme — and reading it out of this feed's archive is
exactly the kind of derived history the public calendar must not produce.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.core.feeds import FeedSummaryMixin

from .models import EventFeedState, EventItem, EventSnapshot

# The near-term window this feed's one figure describes. It matches the
# programme's window so the two are the same length of time, which is the only
# thing the two counts have in common.
NEAR_TERM_DAYS = 30


def get_current_event_snapshot() -> EventSnapshot | None:
    return (
        EventSnapshot.objects.filter(source__slug=settings.KODA_EVENTS_SOURCE_SLUG, is_current=True)
        .select_related("source")
        .first()
    )


def count_upcoming_within(
    snapshot: EventSnapshot | None = None, *, days: int = NEAR_TERM_DAYS
) -> int:
    """How many unfinished publicly announced events start inside `days` days.

    Filtered again at read time as well as at import time: a snapshot published
    yesterday would otherwise keep counting an event that has since ended. An
    event ending today is still upcoming.

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
