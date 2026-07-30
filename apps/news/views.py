"""The Uudised page. Reads PostgreSQL only; never fetches the RSS feed."""

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .selectors import DEFAULT_LIMIT, get_latest_news, get_news_summary


@require_GET
def news_overview(request):
    summary = get_news_summary()
    return render(
        request,
        "news/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "news",
            "freshness": current_freshness(),
            "summary": summary,
            "items": get_latest_news(summary.snapshot, limit=DEFAULT_LIMIT),
        },
    )
