"""The Uudised page. Reads PostgreSQL only; never fetches the RSS feed."""

from datetime import datetime, timedelta

from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.dashboard.connections import planned
from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .selectors import DEFAULT_LIMIT, count_published_since, get_latest_news, get_news_summary

# Matches the overview's activity window so the two pages describe the same
# period when they describe one at all.
ACTIVITY_WINDOW_DAYS = 30

# Two further news-shaped sections the design reserves. Neither is collected:
# nothing in this repository reads a press-clipping service or a mailing list,
# and no model could hold either.
PLANNED_SECTIONS = (
    planned(
        "Meediakajastused",
        promise="Koja mainimised välismeedias. Allikat ei ole veel ühendatud.",
    ),
    planned(
        "Uudiskiri",
        promise="Uudiskirja väljasaatmised ja statistika. Allikat ei ole veel ühendatud.",
    ),
)


@require_GET
def news_overview(request):
    summary = get_news_summary()
    window_start = timezone.make_aware(
        datetime.combine(
            timezone.localdate() - timedelta(days=ACTIVITY_WINDOW_DAYS), datetime.min.time()
        )
    )
    return render(
        request,
        "news/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "news",
            "freshness": current_freshness(),
            "summary": summary,
            "items": get_latest_news(summary.snapshot, limit=DEFAULT_LIMIT),
            "recent_count": (
                count_published_since(summary.snapshot, window_start) if summary.has_data else None
            ),
            "total_count": summary.item_count if summary.has_data else None,
            "planned_sections": PLANNED_SECTIONS,
        },
    )
