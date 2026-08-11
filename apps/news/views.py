"""The Uudised page. Reads PostgreSQL only; never fetches the RSS feed."""

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .archive import build_news_archive
from .categories import parse_category
from .periods import (
    PARAM_CATEGORY,
    PARAM_FROM,
    PARAM_PAGE,
    PARAM_PERIOD,
    PARAM_SEARCH,
    PARAM_SORT,
    PARAM_TO,
    parse_page,
    parse_search,
    parse_sort,
)


@require_GET
def news_overview(request):
    """The Chamber's news archive, browsable by publication period.

    The feed's own state is no longer shown here. It has not gone anywhere —
    `NewsFeedState`, the collector, the import history and the source's health
    are untouched, and the shell's freshness row still speaks for this source on
    every page. What changed is that a reader opening the news archive meets the
    news, rather than a status panel about how the news arrived.

    Every parameter is validated before it reaches a selector: an unreadable
    period, a reversed date range, a rotted page number and an oversized search
    term each resolve to something renderable rather than to a 500.
    """
    archive = build_news_archive(
        period_key=request.GET.get(PARAM_PERIOD),
        date_from=request.GET.get(PARAM_FROM),
        date_to=request.GET.get(PARAM_TO),
        sort=parse_sort(request.GET.get(PARAM_SORT)),
        search=parse_search(request.GET.get(PARAM_SEARCH)),
        category=parse_category(request.GET.get(PARAM_CATEGORY)),
        page=parse_page(request.GET.get(PARAM_PAGE)),
    )
    return render(
        request,
        "news/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "news",
            "freshness": current_freshness(),
            "archive": archive,
        },
    )
