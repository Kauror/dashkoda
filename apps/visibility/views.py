"""The Nähtavus page. Reads PostgreSQL only; never fetches a social profile."""

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

# Both pages have a search box and both paginate, and the two sets of parameter
# names are *not* interchangeable: the archive searches campaign subjects under
# `otsi`, the traffic section searches pages under `otsing`. Every one of the
# four is aliased, so no bare `PARAM_SEARCH` or `PARAM_PAGE` exists in this
# module to be reached for by the wrong view.
from .campaign_history import PARAM_PAGE as PARAM_ARCHIVE_PAGE
from .campaign_history import PARAM_SEARCH as PARAM_ARCHIVE_SEARCH
from .campaign_history import build_campaign_history
from .newsletter_page import PARAM_NEWSLETTER
from .page import build_visibility_page
from .traffic_page import PARAM_CONTENT, PARAM_PERIOD
from .traffic_page import PARAM_PAGE as PARAM_TRAFFIC_PAGE
from .traffic_page import PARAM_SEARCH as PARAM_TRAFFIC_SEARCH


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
                search=request.GET.get(PARAM_TRAFFIC_SEARCH),
                page=request.GET.get(PARAM_TRAFFIC_PAGE),
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
                search=request.GET.get(PARAM_ARCHIVE_SEARCH),
                page=request.GET.get(PARAM_ARCHIVE_PAGE),
            ),
        },
    )
