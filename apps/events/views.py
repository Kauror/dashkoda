"""The Sündmused page. Reads PostgreSQL only; never fetches the calendar."""

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .selectors import DEFAULT_LIMIT, get_event_summary, get_upcoming_events


@require_GET
def events_overview(request):
    summary = get_event_summary()
    return render(
        request,
        "events/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "events",
            "freshness": current_freshness(),
            "summary": summary,
            "items": get_upcoming_events(summary.snapshot, limit=DEFAULT_LIMIT),
        },
    )
