"""The Õigusloome page.

An ordinary protected page that reads PostgreSQL only. It never calls Microsoft
Graph, never downloads or parses the workbook and never waits on OneDrive:
synchronisation is a separate scheduled command, so a slow or broken OneDrive
can never make this page slow or broken.
"""

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .selectors import (
    DEFAULT_RECENT_LIMIT,
    get_latest_sent_items,
    get_legal_work_summary,
    get_newest_received_items,
    get_open_items,
)


@require_GET
def legal_work_overview(request):
    summary = get_legal_work_summary()
    snapshot = summary.snapshot
    return render(
        request,
        "legal_work/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "legislation",
            "freshness": current_freshness(),
            "summary": summary,
            "open_items": get_open_items(snapshot),
            "sent_items": get_latest_sent_items(snapshot, limit=DEFAULT_RECENT_LIMIT),
            "received_items": get_newest_received_items(snapshot, limit=DEFAULT_RECENT_LIMIT),
        },
    )
