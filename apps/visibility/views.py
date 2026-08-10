"""The Nähtavus page. Reads PostgreSQL only; never fetches a social profile."""

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

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
            ),
            "can_add_data": request.user.is_authenticated and request.user.is_staff,
        },
    )
