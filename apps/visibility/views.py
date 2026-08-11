"""The Nähtavus page. Reads PostgreSQL only; never fetches a social profile.

Three of the views here are live-search fragments. Each answers the same query
its page's form would submit and renders the same results partial the full page
renders, so what a reader sees while typing and what they see after a reload are
produced by one template rather than two that have to be kept in step. See
`apps.dashboard.live_search`.
"""

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.live_search import push_url, search_fragment
from apps.dashboard.navigation import NAVIGATION

# Three search boxes across the two pages, and their parameter names are *not*
# interchangeable. `otsi` searches campaign subjects — on the archive page and,
# since the Nähtavus page grew its own box, on the recent-sends table too, which
# is the same question so it keeps the same name. `otsing` searches website
# pages. Every one of them is aliased, so no bare `PARAM_SEARCH` or `PARAM_PAGE`
# exists in this module to be reached for by the wrong view.
from .campaign_history import PARAM_PAGE as PARAM_ARCHIVE_PAGE
from .campaign_history import PARAM_SEARCH as PARAM_ARCHIVE_SEARCH
from .campaign_history import build_campaign_history
from .newsletter_page import PARAM_NEWSLETTER, build_newsletter_section
from .newsletter_page import PARAM_SEARCH as PARAM_NEWSLETTER_SEARCH
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
                newsletter_key=request.GET.get(PARAM_NEWSLETTER),
                newsletter_search=request.GET.get(PARAM_NEWSLETTER_SEARCH),
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


#: The parameters the Nähtavus page understands. A live-search fragment carries
#: the reader's current query forward so a reload keeps the period, the section
#: and the other search — and carries *only* these, because the value ends up in
#: somebody's address bar.
VISIBILITY_PARAMS = (
    PARAM_PERIOD,
    PARAM_CONTENT,
    PARAM_NEWSLETTER,
    PARAM_NEWSLETTER_SEARCH,
    PARAM_TRAFFIC_SEARCH,
    PARAM_TRAFFIC_PAGE,
)


@require_GET
def newsletter_search_fragment(request):
    """The sends table alone, for a reader typing in the newsletter box.

    Only the newsletter section is rebuilt. Reaching for `build_visibility_page`
    would read the GA4 traffic, the channel band and every social metric on
    every keystroke to render a table that uses none of them.
    """
    newsletters = build_newsletter_section(
        newsletter_key=request.GET.get(PARAM_NEWSLETTER),
        search=request.GET.get(PARAM_NEWSLETTER_SEARCH),
    )
    return search_fragment(
        request,
        "visibility/partials/_newsletter_results.html",
        {"newsletters": newsletters},
        pushed=push_url(
            request,
            path=reverse("visibility"),
            allowed=VISIBILITY_PARAMS,
            # The section's own parsing has already trimmed and bounded the
            # term, so what reaches the address bar is what reached the query.
            updates={PARAM_NEWSLETTER_SEARCH: newsletters.search},
            anchor="#section-newsletter-analytics",
        ),
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


#: What the archive page understands. A shorter list than the Nähtavus page's,
#: and `otsi` means the same thing on both.
ARCHIVE_PARAMS = (PARAM_NEWSLETTER, PARAM_ARCHIVE_SEARCH, PARAM_ARCHIVE_PAGE)


@require_GET
def campaign_history_search_fragment(request):
    """One page of the archive, for a reader typing in the subject box.

    Page one, always, for the reason the traffic fragment resets its own: a new
    term is a new question, and 3 194 sends narrowed to four have no page 40.
    """
    history = build_campaign_history(
        newsletter_key=request.GET.get(PARAM_NEWSLETTER),
        search=request.GET.get(PARAM_ARCHIVE_SEARCH),
    )
    return search_fragment(
        request,
        "visibility/partials/_campaign_history_results.html",
        {"history": history},
        pushed=push_url(
            request,
            path=reverse("visibility-campaign-history"),
            allowed=ARCHIVE_PARAMS,
            updates={
                PARAM_ARCHIVE_SEARCH: history.search,
                PARAM_ARCHIVE_PAGE: "",
            },
        ),
    )
