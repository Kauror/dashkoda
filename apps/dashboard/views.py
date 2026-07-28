from django.shortcuts import render
from django.views.decorators.http import require_GET

from .freshness import current_freshness
from .navigation import NAVIGATION


def _shell_context(active_nav: str) -> dict:
    return {
        "navigation": NAVIGATION,
        "active_nav": active_nav,
        "freshness": current_freshness(),
    }


# Column labels only. They describe the shape the membership table will have;
# PR-04 renders the table with no rows.
MEMBERSHIP_COLUMNS: tuple[str, ...] = (
    "Periood",
    "Liikmeid",
    "Lisandunud",
    "Lahkunud",
    "Netomuutus",
)


@require_GET
def overview(request):
    context = _shell_context("overview") | {"membership_columns": MEMBERSHIP_COLUMNS}
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
