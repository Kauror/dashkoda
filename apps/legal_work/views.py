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

from datetime import timedelta

from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.live_search import push_url, search_fragment
from apps.dashboard.navigation import NAVIGATION

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
    ACTIVITY_WINDOW_DAYS,
    DEFAULT_RECENT_LIMIT,
    count_received_since,
    count_sent_since,
    get_current_snapshot,
    get_latest_sent_items,
    get_legal_work_summary,
    get_open_items,
    get_upcoming_deadlines,
)
from .topic_links import present_deadlines, present_topics, resolve_links_for


@require_GET
def legal_work_overview(request):
    summary = get_legal_work_summary()
    snapshot = summary.snapshot
    window_start = timezone.localdate() - timedelta(days=ACTIVITY_WINDOW_DAYS)

    # Materialised first, because the link lookup needs to know every record the
    # page will draw before it runs. A record listed both as in-work and as an
    # approaching deadline is asked about once and answered once.
    #
    # Arrivals are not a list of their own here any more. A record that has just
    # come in is active work and is already in Hetkel töös; `received_recent`
    # below still counts the window, because how much arrived is a real
    # measurement even without a table repeating the rows.
    open_items = list(get_open_items(snapshot))
    sent_items = list(get_latest_sent_items(snapshot, limit=DEFAULT_RECENT_LIMIT))
    deadlines = get_upcoming_deadlines(snapshot) if snapshot else ()

    # The search is materialised alongside the standing lists, before links are
    # resolved, so it joins the page's single link query. A record found by
    # search and also sitting in `Hetkel töös` is asked about once and links
    # identically in both places.
    search = build_search(
        snapshot,
        query=parse_query(request.GET.get(PARAM_QUERY)),
        status=parse_status(request.GET.get(PARAM_STATUS)),
        page=parse_page(request.GET.get(PARAM_PAGE)),
    )
    links = resolve_links_for(open_items, sent_items, deadlines, search.results)

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
            "summary": summary,
            "open_items": present_topics(open_items, links),
            "sent_items": present_topics(sent_items, links),
            # Counters are `None` rather than `0` when no snapshot is published,
            # so an unconnected source never reads as a quiet month. The summary
            # itself reports 0 for an absent snapshot, which is the right answer
            # to "how many rows are published" and the wrong one to show as a
            # measurement, so the distinction is made here.
            "open_count": summary.open_count if summary.has_data else None,
            "total_count": summary.total_count if summary.has_data else None,
            "received_recent": count_received_since(snapshot, window_start) if snapshot else None,
            "sent_recent": count_sent_since(snapshot, window_start) if snapshot else None,
            "deadlines": present_deadlines(deadlines, links),
            "search": search.presented_with(links),
            "activity_window_days": ACTIVITY_WINDOW_DAYS,
        },
    )


#: What this page understands. A live-search fragment carries the reader's
#: current query forward so a reload keeps the status they had chosen — and
#: carries *only* these, because the value ends up in somebody's address bar.
LEGAL_WORK_PARAMS = (PARAM_QUERY, PARAM_STATUS, PARAM_PAGE)


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
    """
    snapshot = get_current_snapshot()
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
