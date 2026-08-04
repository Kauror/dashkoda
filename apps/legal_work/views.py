"""The Õigusloome page.

An ordinary protected page that reads PostgreSQL only. It never contacts
Microsoft, never downloads or parses the workbook and never waits on OneDrive:
synchronisation is a separate scheduled command, so a slow or broken OneDrive
can never make this page slow or broken.
"""

from datetime import timedelta

from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.dashboard.connections import planned
from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .selectors import (
    ACTIVITY_WINDOW_DAYS,
    DEFAULT_RECENT_LIMIT,
    count_received_since,
    count_sent_since,
    get_latest_sent_items,
    get_legal_work_summary,
    get_newest_received_items,
    get_open_items,
    get_upcoming_deadlines,
)

# Reserved by the design as a view under Õigusloome. Nothing selects or stores a
# focus topic, so the page names it as unconnected rather than showing an empty
# list a reader could mistake for "no focus topics this quarter".
FOCUS_TOPICS = planned(
    # "Juhatuse valitud prioriteetsed teemad" was removed with the section
    # descriptions. What stays is why the section is empty, which is the only
    # part a reader cannot infer from the heading.
    "Fookusteemad",
    promise="Allikat ei ole veel määratud.",
)


@require_GET
def legal_work_overview(request):
    summary = get_legal_work_summary()
    snapshot = summary.snapshot
    window_start = timezone.localdate() - timedelta(days=ACTIVITY_WINDOW_DAYS)
    return render(
        request,
        "legal_work/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "legislation",
            # The shell row speaks for all four wired modules; this page has
            # already read one of them, so it hands that one back instead of
            # paying for it twice.
            "freshness": current_freshness(summary),
            "summary": summary,
            "open_items": get_open_items(snapshot),
            "sent_items": get_latest_sent_items(snapshot, limit=DEFAULT_RECENT_LIMIT),
            "received_items": get_newest_received_items(snapshot, limit=DEFAULT_RECENT_LIMIT),
            # Counters are `None` rather than `0` when no snapshot is published,
            # so an unconnected source never reads as a quiet month. The summary
            # itself reports 0 for an absent snapshot, which is the right answer
            # to "how many rows are published" and the wrong one to show as a
            # measurement, so the distinction is made here.
            "open_count": summary.open_count if summary.has_data else None,
            "total_count": summary.total_count if summary.has_data else None,
            "received_recent": count_received_since(snapshot, window_start) if snapshot else None,
            "sent_recent": count_sent_since(snapshot, window_start) if snapshot else None,
            "deadlines": get_upcoming_deadlines(snapshot) if snapshot else (),
            "activity_window_days": ACTIVITY_WINDOW_DAYS,
            "focus_topics": FOCUS_TOPICS,
        },
    )
