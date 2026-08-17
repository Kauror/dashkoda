"""The Õigusloome page.

An ordinary protected page that reads PostgreSQL only. It never contacts
Microsoft, never downloads or parses the workbook and never waits on OneDrive:
synchronisation is a separate scheduled command, so a slow or broken OneDrive
can never make this page slow or broken. The same holds for Koda.ee — the
automatic topic links below are resolved from rows a scheduled command already
published, and rendering this page makes no outbound request of any kind.

Every collection on the page is materialised before links are resolved, so the
whole page costs **one** link query no matter how many rows it draws, and a
record appearing in two lists is answered once. That is what guarantees a topic
cannot be a link in one section and plain text in another.
"""

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.live_search import push_url, search_fragment
from apps.dashboard.navigation import NAVIGATION

from .intelligence_page import (
    FOCUS_OVERVIEW,
    FOCUS_REGISTER,
    PARAM_FOCUS,
    build_page,
    parse_focus,
)
from .register import REGISTER_PARAMS, build_register
from .search import (
    PARAM_PAGE,
    PARAM_QUERY,
    PARAM_STATUS,
    build_search,
    parse_page,
    parse_query,
    parse_status,
)
from .selectors import (
    get_current_snapshot,
    get_latest_sent_items,
    get_legal_work_summary,
    get_open_items,
)
from .topic_links import present_deadlines, present_topics, resolve_links_for


@require_GET
def legal_work_overview(request):
    """One page, one URL, one render, and exactly the focus that was asked for.

    `fookus` selects the analytical surface. It is validated against a closed
    set and an unknown value resolves to the overview, so a truncated link or an
    old bookmark lands somewhere real instead of raising.

    Every collection is materialised before links are resolved, so the whole
    page still costs **one** link query however many rows it draws, and a record
    appearing in two lists is asked about once. That is what guarantees a topic
    cannot be a link in one section and plain text in another.
    """
    summary = get_legal_work_summary()
    snapshot = summary.snapshot
    focus = parse_focus(request.GET.get(PARAM_FOCUS))

    page = build_page(snapshot, focus=focus, page_url=reverse("legal-work"))

    # The standing lists render on the overview alone since 2026-08-16 — the
    # sub-focuses repeated them wholesale under their charts — so only the
    # overview pays for the queries. Bounded exactly as before.
    if focus == FOCUS_OVERVIEW:
        open_items = list(get_open_items(snapshot))
        sent_items = list(get_latest_sent_items(snapshot))
    else:
        open_items = []
        sent_items = []

    # The register focus gets the full explorer: facets, filters and per-record
    # detail. Every other focus keeps the plain term-and-status search, which is
    # what the overview has always carried.
    register = build_register(snapshot, request.GET) if focus == FOCUS_REGISTER else None

    search = build_search(
        snapshot,
        query=parse_query(request.GET.get(PARAM_QUERY)),
        status=parse_status(request.GET.get(PARAM_STATUS)),
        page=parse_page(request.GET.get(PARAM_PAGE)),
    )
    links = resolve_links_for(open_items, sent_items, page.deadlines, search.results)

    return render(
        request,
        "legal_work/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "legislation",
            # The shell row speaks for all four wired modules; this page has
            # already read one of them, so it hands that one back instead of
            # paying for it twice.
            "freshness": current_freshness(summary),
            # Not the eyebrow line any more — just the one thing that decides
            # whether the page has anything to draw at all. See the truthful
            # empty state right under the focus nav in the template.
            "summary": summary,
            "page": page,
            "open_items": present_topics(open_items, links),
            "sent_items": present_topics(sent_items, links),
            "deadlines": present_deadlines(page.deadlines, links),
            "search": search.presented_with(links),
            "register": register,
        },
    )


#: What this page understands. A live-search fragment carries the reader's
#: current query forward so a reload keeps the status they had chosen — and
#: carries *only* these, because the value ends up in somebody's address bar.
LEGAL_WORK_PARAMS = (PARAM_QUERY, PARAM_STATUS, PARAM_PAGE, PARAM_FOCUS)


@require_GET
def legal_work_search_fragment(request):
    """The register-search results alone, for a reader typing in the box.

    Only the search is rebuilt. Reaching for the whole page would re-read the
    standing lists, the deadlines and the freshness row on every keystroke to
    render a table that uses none of them.

    Links are resolved here too, because a found record must be as clickable
    from a keystroke as it is from a reload — one query for one list, rather
    than the page's one query for four.

    Page one, always: a new term is a new question, and a reader on page 3 of
    one search would otherwise be told there are no results for the next.

    On the register focus the same route answers with the register's own rows,
    because the reader there is typing into a box that has six filters beside
    it. Rebuilding the plain search would silently drop every one of them and
    hand back a wider answer than the page claims to be showing.
    """
    snapshot = get_current_snapshot()

    if parse_focus(request.GET.get(PARAM_FOCUS)) == FOCUS_REGISTER:
        register = build_register(snapshot, request.GET)
        return search_fragment(
            request,
            "legal_work/partials/_register_results.html",
            {"register": register},
            pushed=push_url(
                request,
                path=reverse("legal-work"),
                allowed=(PARAM_FOCUS, *REGISTER_PARAMS),
                # Every value has been through the register's own validation, so
                # what reaches the address bar is what reached the query.
                updates={PARAM_QUERY: register.state.query, PARAM_PAGE: ""},
                anchor="#section-register",
            ),
        )

    search = build_search(
        snapshot,
        query=parse_query(request.GET.get(PARAM_QUERY)),
        status=parse_status(request.GET.get(PARAM_STATUS)),
    )
    return search_fragment(
        request,
        "legal_work/partials/_search_results.html",
        {"search": search.presented_with(resolve_links_for(search.results))},
        pushed=push_url(
            request,
            path=reverse("legal-work"),
            allowed=LEGAL_WORK_PARAMS,
            # The section's own parsing has already trimmed and bounded the
            # term, so what reaches the address bar is what reached the query.
            updates={PARAM_QUERY: search.query, PARAM_STATUS: search.status, PARAM_PAGE: ""},
            anchor="#section-search",
        ),
    )
