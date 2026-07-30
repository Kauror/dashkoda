from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.events.selectors import get_event_summary, get_upcoming_events
from apps.legal_work.selectors import (
    get_latest_sent_items,
    get_legal_work_summary,
    get_newest_received_items,
)
from apps.membership.selectors import get_membership_summary
from apps.news.selectors import get_latest_news, get_news_summary

from .freshness import current_freshness
from .navigation import NAVIGATION

# How many rows each section previews before sending the reader to its own page.
OVERVIEW_PREVIEW_LIMIT = 5


def _shell_context(active_nav: str) -> dict:
    return {
        "navigation": NAVIGATION,
        "active_nav": active_nav,
        "freshness": current_freshness(),
    }


@require_GET
def overview(request):
    # Four sections are backed by real data: legal work, membership, news and
    # events. The rest stay explicit empty states until their own source is
    # connected. Every summary reads PostgreSQL only.
    legal_work = get_legal_work_summary()
    snapshot = legal_work.snapshot
    news = get_news_summary()
    events = get_event_summary()
    context = _shell_context("overview") | {
        "membership": get_membership_summary(),
        "news": news,
        "latest_news": get_latest_news(news.snapshot, limit=OVERVIEW_PREVIEW_LIMIT),
        "events": events,
        "upcoming_events": get_upcoming_events(events.snapshot, limit=OVERVIEW_PREVIEW_LIMIT),
        "legal_work": legal_work,
        "legal_work_received": (
            get_newest_received_items(snapshot, limit=OVERVIEW_PREVIEW_LIMIT) if snapshot else ()
        ),
        "legal_work_sent": (
            get_latest_sent_items(snapshot, limit=OVERVIEW_PREVIEW_LIMIT) if snapshot else ()
        ),
    }
    return render(request, "dashboard/overview.html", context)


@require_GET
def freshness_fragment(request):
    """Neutral HTMX fragment used to validate the partial-update pattern.

    It is an ordinary protected route: the viewer middleware guards it, and
    without JavaScript the same control falls back to reloading the overview.
    """
    return render(
        request,
        "dashboard/partials/freshness.html",
        {"freshness": current_freshness()},
    )
