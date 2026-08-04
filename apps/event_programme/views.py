"""The Sündmused page.

An ordinary protected page that reads PostgreSQL only. It never downloads the
workbook and never calls Koda.ee: collection is the scheduled
`sync_event_programme` command, and the public calendar has its own scheduled
collector.

The page's subject is the Chamber's own event programme — the whole available
history from the canonical Excel export. The public Koda.ee calendar appears
once, at the foot, as a named secondary connection. It is read here only to say
whether it is collecting and how many publicly announced upcoming events it
currently lists; it supplies no figure the programme states, no link and no row.
"""

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

# The one deliberate cross-domain read on this page. `apps.events` is a separate
# feed with separate snapshots; nothing below merges it into the programme.
from apps.events.selectors import count_upcoming_within, get_event_summary

from .page import build_programme_page
from .selectors import get_event_programme_summary


@require_GET
def event_programme_overview(request):
    summary = get_event_programme_summary()
    public_calendar = get_event_summary()
    return render(
        request,
        "event_programme/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "events",
            # The shell row reuses the summary this page already read, so the
            # programme is loaded exactly once per request.
            "freshness": current_freshness(summary),
            "summary": summary,
            "page": build_programme_page(summary, request.GET),
            "public_calendar": public_calendar,
            "public_upcoming_count": (
                count_upcoming_within(public_calendar.snapshot)
                if public_calendar.has_data
                else None
            ),
        },
    )
