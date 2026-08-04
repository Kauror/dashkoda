"""The Sündmused page. Reads PostgreSQL only; never fetches the calendar."""

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.dashboard.connections import planned
from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .selectors import DEFAULT_LIMIT, count_upcoming_within, get_event_summary, get_upcoming_events

# A snapshot holds the calendar as it looks now, which is a list of events still
# to come. Nothing retains an event once it has happened, so attendance and
# past-event history have no source — and saying so is more useful than an empty
# list implying the Chamber held nothing.
PAST_EVENTS = planned(
    "Toimunud sündmused",
    promise="Avalik kalender kajastab ainult eelseisvaid sündmusi.",
)


@require_GET
def events_overview(request):
    summary = get_event_summary()
    return render(
        request,
        "events/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "events",
            "freshness": current_freshness(summary),
            "summary": summary,
            "items": get_upcoming_events(summary.snapshot, limit=DEFAULT_LIMIT),
            "near_term_count": count_upcoming_within(summary.snapshot)
            if summary.has_data
            else None,
            "total_count": summary.item_count if summary.has_data else None,
            "past_events": PAST_EVENTS,
        },
    )
