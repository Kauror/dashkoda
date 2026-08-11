"""The Nähtavus page. Reads PostgreSQL only; never fetches a social profile."""

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .campaign_history import (
    PARAM_PAGE,
    PARAM_SEARCH,
    build_campaign_history,
)
from .newsletter_page import PARAM_NEWSLETTER
from .page import build_visibility_page
from .traffic_page import PARAM_CONTENT, PARAM_PERIOD


@require_GET
def visibility_overview(request):
    """Current audience figures, their trends and the whole observation history.

    The "Lisa andmed" action is offered only to active staff. An ordinary viewer
    holds the shared PIN and no Django account, so showing them an editing
    control would advertise a door they cannot open.
    """
    return render(
        request,
        "visibility/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "visibility",
            "freshness": current_freshness(),
            "page": build_visibility_page(
                detail_url=reverse("visibility"),
                period_key=request.GET.get(PARAM_PERIOD),
                section_key=request.GET.get(PARAM_CONTENT),
                newsletter_key=request.GET.get(PARAM_NEWSLETTER),
            ),
            "can_add_data": request.user.is_authenticated and request.user.is_staff,
        },
    )


@require_GET
def campaign_history(request):
    """Every completed Smaily send, filterable and searchable.

    The archive behind the Nähtavus page's most-recent list: fourteen years of
    campaigns, including every one that matches none of the three newsletters.
    Reads PostgreSQL only — the subject search never contacts Smaily.
    """
    return render(
        request,
        "visibility/campaign_history.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "visibility",
            "history": build_campaign_history(
                newsletter_key=request.GET.get(PARAM_NEWSLETTER),
                search=request.GET.get(PARAM_SEARCH),
                page=request.GET.get(PARAM_PAGE),
            ),
        },
    )
