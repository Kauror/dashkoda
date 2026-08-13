"""The Nähtavus page. Reads PostgreSQL only; never fetches a social profile.

One of the views here is a live-search fragment. It answers the same query its
page's form would submit and renders the same results partial the full page
renders, so what a reader sees while typing and what they see after a reload are
produced by one template rather than two that have to be kept in step. See
`apps.dashboard.live_search`.

The newsletter material — the `Uudiskirjad` card, `Uudiskirjade tulemused`, the
subject search and the full send archive — is rendered by `apps.news` now. The
Smaily models, collectors, selectors and presenters did not move: this app still
owns them, and `apps/news/views.py` imports them. What moved is where a reader
finds them, and the two archive addresses below redirect there so saved
bookmarks keep working.
"""

from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.live_search import push_url, search_fragment
from apps.dashboard.navigation import NAVIGATION

# `otsing` searches website pages. It is aliased on the way in, so no bare
# `PARAM_SEARCH` or `PARAM_PAGE` exists in this module to be reached for by the
# wrong view — a habit worth keeping now that the campaign subject search, which
# used `otsi`, has gone to `apps.news`.
from .page import build_visibility_page
from .traffic_page import PARAM_CONTENT, PARAM_PERIOD, build_traffic_section
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
                search=request.GET.get(PARAM_TRAFFIC_SEARCH),
                page=request.GET.get(PARAM_TRAFFIC_PAGE),
            ),
            "can_add_data": request.user.is_authenticated and request.user.is_staff,
        },
    )


def _redirect_keeping_query(request, route: str):
    """The same question, asked at the address that now answers it.

    Temporary rather than permanent on purpose: a 301 is cached by browsers
    indefinitely and is painful to take back, and nothing here needs the
    permanence. The query string is passed through whole — `uudiskiri`, `otsi`
    and `lk` are exactly what a saved archive bookmark carries, and losing them
    would land the reader in fourteen unfiltered years.
    """
    target = reverse(route)
    query = request.META.get("QUERY_STRING", "")
    return redirect(f"{target}?{query}" if query else target)


@require_GET
def campaign_history(request):
    """Where the send archive used to live.

    Redirects to `news-newsletter-history`. No loop is possible: the target is a
    different path in a different app and redirects nowhere itself.
    """
    return _redirect_keeping_query(request, "news-newsletter-history")


@require_GET
def campaign_history_search_fragment(request):
    """Where the archive's live search used to live.

    Kept as a compatibility alias for the same reason as the page above. htmx
    follows the redirect transparently, so the fragment that answers is the news
    one and the URL it pushes is the news archive's.
    """
    return _redirect_keeping_query(request, "news-newsletter-history-search")


#: The parameters the Nähtavus page understands. A live-search fragment carries
#: the reader's current query forward so a reload keeps the period and the
#: section — and carries *only* these, because the value ends up in somebody's
#: address bar. The two newsletter parameters left with the section they
#: belonged to; `apps.news.views.PAGE_PARAMS` declares them now.
VISIBILITY_PARAMS = (
    PARAM_PERIOD,
    PARAM_CONTENT,
    PARAM_TRAFFIC_SEARCH,
    PARAM_TRAFFIC_PAGE,
)


@require_GET
def traffic_search_fragment(request):
    """The content ranking alone, for a reader typing in the page-search box.

    A new term is a new question, so this always builds page one: `lk` is
    neither read from the request nor carried into the pushed URL. Keeping it
    would answer "Ühtegi lehte ei leitud" for every term matching fewer results
    than the page the reader happened to be on.
    """
    traffic = build_traffic_section(
        period_key=request.GET.get(PARAM_PERIOD),
        section_key=request.GET.get(PARAM_CONTENT),
        search=request.GET.get(PARAM_TRAFFIC_SEARCH),
    )
    return search_fragment(
        request,
        "visibility/partials/_traffic_results.html",
        {"traffic": traffic},
        pushed=push_url(
            request,
            path=reverse("visibility"),
            allowed=VISIBILITY_PARAMS,
            updates={
                PARAM_TRAFFIC_SEARCH: traffic.search,
                PARAM_TRAFFIC_PAGE: "",
            },
            anchor="#section-traffic",
        ),
    )
