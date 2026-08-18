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

from .intelligence_page import FOCUS_OVERVIEW, PARAM_FOCUS, build_page, parse_focus
from .register import PARAM_PAGE, PARAM_QUERY, REGISTER_PARAMS, build_register
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

    # The standing lists and the register explorer render on the overview
    # alone, since 2026-08-18 unconditionally rather than behind their own
    # focus — `Töövoog ja arvamused` draws only charts, and pays for none of
    # this.
    if focus == FOCUS_OVERVIEW:
        open_items = list(get_open_items(snapshot))
        sent_items = list(get_latest_sent_items(snapshot))
        register = build_register(snapshot, request.GET)
    else:
        open_items = []
        sent_items = []
        register = None

    links = resolve_links_for(open_items, sent_items, page.deadlines)

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
            "register": register,
        },
    )


@require_GET
def legal_work_search_fragment(request):
    """The register-search results alone, for a reader typing in the box.

    Only the search is rebuilt. Reaching for the whole page would re-read the
    standing lists, the deadlines and the freshness row on every keystroke to
    render a table that uses none of them.

    Links are resolved here too, because a found record must be as clickable
    from a keystroke as it is from a reload — one query for one list, rather
    than the page's one query for four.

    The register is the only search this page has had since 2026-08-18, when
    the plain term-and-status search it used to carry alongside the register
    explorer was retired — the register's own text field already reaches
    everything that one did, plus the facets beside it.
    """
    snapshot = get_current_snapshot()
    register = build_register(snapshot, request.GET)
    return search_fragment(
        request,
        "legal_work/partials/_register_results.html",
        {"register": register},
        pushed=push_url(
            request,
            path=reverse("legal-work"),
            allowed=REGISTER_PARAMS,
            # Every value has been through the register's own validation, so
            # what reaches the address bar is what reached the query.
            updates={PARAM_QUERY: register.state.query, PARAM_PAGE: ""},
            anchor="#section-register",
        ),
    )
