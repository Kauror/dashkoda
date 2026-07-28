from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.legal_work.selectors import (
    get_latest_sent_items,
    get_legal_work_summary,
    get_newest_received_items,
)

from .freshness import current_freshness
from .navigation import NAVIGATION

# How many legal-work rows the overview previews before sending the reader to
# the dedicated page.
OVERVIEW_PREVIEW_LIMIT = 5


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
    # The legal-work block is the only section backed by real data. Every other
    # section stays an explicit empty state until its own source is connected.
    legal_work = get_legal_work_summary()
    snapshot = legal_work.snapshot
    context = _shell_context("overview") | {
        "membership_columns": MEMBERSHIP_COLUMNS,
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
