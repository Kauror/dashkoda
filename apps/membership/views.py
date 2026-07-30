"""The Liikmeskond page.

An ordinary protected page that reads PostgreSQL only. It never calls Koda.ee:
collection is a separate scheduled command.
"""

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .selectors import get_membership_summary


@require_GET
def membership_overview(request):
    return render(
        request,
        "membership/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "membership",
            "freshness": current_freshness(),
            "summary": get_membership_summary(),
        },
    )
