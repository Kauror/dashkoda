"""The Uudised page. Reads PostgreSQL only; never fetches the RSS feed.

The page carries two independent sections: the news archive, which this app
owns, and the newsletter material, which it does not. Smaily's models,
collectors and selectors stay in `apps.visibility` — only the place a reader
finds them moved here, because "what did we publish" and "what did we send" are
the same question to the board and were two pages apart.

So the newsletter presenters are imported rather than reimplemented, and the
templates under `visibility/` stay authoritative. What this module adds is the
composition: which parameters belong to which section, and how each section's
links carry the other's state so neither can reset it.
"""

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.live_search import carried_query, push_url, search_fragment
from apps.dashboard.navigation import NAVIGATION
from apps.visibility.campaign_history import PARAM_PAGE as PARAM_ARCHIVE_PAGE
from apps.visibility.campaign_history import PARAM_SEARCH as PARAM_ARCHIVE_SEARCH
from apps.visibility.campaign_history import build_campaign_history
from apps.visibility.newsletter_page import (
    PARAM_NEWSLETTER,
    build_newsletter_section,
    newsletter_state,
)
from apps.visibility.newsletter_page import PARAM_SEARCH as PARAM_NEWSLETTER_SEARCH
from apps.visibility.page import build_newsletter_slot
from apps.visibility.selectors import get_visibility_summary

from .archive import build_news_archive
from .categories import parse_category
from .periods import (
    PARAM_CATEGORY,
    PARAM_FROM,
    PARAM_PAGE,
    PARAM_PERIOD,
    PARAM_SEARCH,
    PARAM_SORT,
    PARAM_TO,
    build_query,
    parse_page,
    parse_search,
    parse_sort,
    resolve_period,
)

#: What the news archive understands. Only these are carried into a pushed URL,
#: because that value ends up in somebody's address bar.
NEWS_PARAMS = (
    PARAM_PERIOD,
    PARAM_FROM,
    PARAM_TO,
    PARAM_SORT,
    PARAM_SEARCH,
    PARAM_CATEGORY,
    PARAM_PAGE,
)

#: What the newsletter section understands. A separate tuple, and the two are
#: deliberately disjoint: `otsing` searches articles and `otsi` searches campaign
#: subjects. They are two boxes asking two questions, and renaming either into
#: the other would quietly feed one query the other's term.
NEWSLETTER_PARAMS = (PARAM_NEWSLETTER, PARAM_NEWSLETTER_SEARCH)

#: Everything `/uudised/` reads. Both live-search fragments push through this, so
#: a keystroke in either box keeps the other section exactly as it was.
PAGE_PARAMS = NEWS_PARAMS + NEWSLETTER_PARAMS


def _news_state(archive) -> str:
    """The archive's own state as a query fragment, from validated values only.

    Handed to the newsletter section so its chips and its "clear search" link
    keep the period, the category, the ordering, the news search and the page
    the reader had. Built from the *resolved* archive rather than from
    `request.GET`, so what is carried is what was actually applied.
    """
    return build_query(
        period_key=archive.period.key,
        sort=archive.sort,
        search=archive.search,
        category=archive.category,
        page=archive.page_number,
        start=archive.period.start,
        end=archive.period.end,
    )


def _carried_news_state(request) -> str:
    """The same state, for a keystroke that carries only its own form.

    A live-search request has no archive to read it off, so it comes from
    `HX-Current-URL` through `carried_query` — which keeps only the keys this
    page declares — and is then rebuilt through `build_query` from validated
    values. Nothing from the header is echoed through verbatim.

    The period is resolved rather than trusted, so a hand-typed `periood=zzz`
    carries as the default window the page actually renders.
    """
    current = carried_query(request, NEWS_PARAMS)
    resolved = resolve_period(
        current.get(PARAM_PERIOD), current.get(PARAM_FROM), current.get(PARAM_TO)
    )
    return build_query(
        period_key=resolved.key,
        sort=parse_sort(current.get(PARAM_SORT)),
        search=parse_search(current.get(PARAM_SEARCH)),
        category=parse_category(current.get(PARAM_CATEGORY)),
        page=parse_page(current.get(PARAM_PAGE)),
        start=resolved.start,
        end=resolved.end,
    )


@require_GET
def news_overview(request):
    """The Chamber's news archive, and underneath it how the newsletters did.

    The feed's own state is no longer shown here. It has not gone anywhere —
    `NewsFeedState`, the collector, the import history and the source's health
    are untouched, and the shell's freshness row still speaks for this source on
    every page. What changed is that a reader opening the news archive meets the
    news, rather than a status panel about how the news arrived.

    Every parameter is validated before it reaches a selector: an unreadable
    period, a reversed date range, a rotted page number and an oversized search
    term each resolve to something renderable rather than to a 500.

    The newsletter half reads only what it renders. Building the whole
    `VisibilityPage` to get at it would run the GA4 traffic queries, the website
    ranking and every social metric on a page that shows none of them.
    """
    # The newsletter state first, because the archive's links carry it. Both
    # directions are needed and neither can be built from the other, so they are
    # built in the one order that has no cycle: newsletter parameters are read
    # straight from the request, the archive resolves with them in hand, and the
    # newsletter section then receives the archive's resolved state.
    carried_newsletter = newsletter_state(
        newsletter_key=request.GET.get(PARAM_NEWSLETTER),
        search=request.GET.get(PARAM_NEWSLETTER_SEARCH),
    )
    archive = build_news_archive(
        period_key=request.GET.get(PARAM_PERIOD),
        date_from=request.GET.get(PARAM_FROM),
        date_to=request.GET.get(PARAM_TO),
        sort=parse_sort(request.GET.get(PARAM_SORT)),
        search=parse_search(request.GET.get(PARAM_SEARCH)),
        category=parse_category(request.GET.get(PARAM_CATEGORY)),
        page=parse_page(request.GET.get(PARAM_PAGE)),
        carried=carried_newsletter,
    )
    summary = get_visibility_summary()
    return render(
        request,
        "news/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "news",
            "freshness": current_freshness(),
            "archive": archive,
            # The same `ChannelSlot` the overall dashboard's band renders, from
            # the same builder. No second newsletter card exists.
            "newsletter_slot": build_newsletter_slot(summary.newsletter),
            "newsletters": build_newsletter_section(
                newsletter_key=request.GET.get(PARAM_NEWSLETTER),
                search=request.GET.get(PARAM_NEWSLETTER_SEARCH),
                carried=_news_state(archive),
            ),
        },
    )


@require_GET
def news_search_fragment(request):
    """The archive rows alone, for a reader typing in the news search box.

    Page one, always: a new term is a new question, and an archive narrowed from
    thousands of articles to six has no page seven. `lk` is neither read here nor
    carried into the pushed URL.

    The period, the custom range, the sort **and the category** are read exactly
    as the full page reads them, so typing narrows what the reader is already
    looking at rather than quietly widening it to everything. The category is the
    one the form submits as a hidden field; dropping it here filtered on `Kõik`
    while the chip above still read `Koja uudised`, and pushed a URL that lost
    the filter for good on the next reload.

    The newsletter parameters are in `PAGE_PARAMS` but not in this form, so they
    reach the pushed URL from `HX-Current-URL` and survive untouched: typing in
    the news box must not clear the newsletter the reader chose below.
    """
    archive = build_news_archive(
        period_key=request.GET.get(PARAM_PERIOD),
        date_from=request.GET.get(PARAM_FROM),
        date_to=request.GET.get(PARAM_TO),
        sort=parse_sort(request.GET.get(PARAM_SORT)),
        search=parse_search(request.GET.get(PARAM_SEARCH)),
        category=parse_category(request.GET.get(PARAM_CATEGORY)),
        carried=newsletter_state(
            newsletter_key=request.GET.get(PARAM_NEWSLETTER),
            search=request.GET.get(PARAM_NEWSLETTER_SEARCH),
        ),
    )
    return search_fragment(
        request,
        "news/partials/_news_search_response.html",
        {"archive": archive},
        pushed=push_url(
            request,
            path=reverse("news"),
            allowed=PAGE_PARAMS,
            # The validated category rather than the raw parameter, and empty for
            # `Kõik` so the unfiltered page keeps an unfiltered URL.
            updates={
                PARAM_SEARCH: archive.search,
                PARAM_CATEGORY: archive.category,
                PARAM_PAGE: "",
            },
        ),
    )


@require_GET
def news_newsletter_search_fragment(request):
    """The sends table alone, for a reader typing in the newsletter box.

    The same fragment the Nähtavus page used to answer, moved here with the
    section. Only the newsletter section is rebuilt: reaching for the news
    archive as well would re-run the catalogue query on every keystroke to
    render a table that uses none of it.

    It pushes `/uudised/`. That is the whole reason this view exists rather than
    the visibility one being pointed at a new template — a fragment whose URL
    state named `/nahtavus/` would have put the reader's newsletter search into
    the address of a page that no longer has one.
    """
    newsletters = build_newsletter_section(
        newsletter_key=request.GET.get(PARAM_NEWSLETTER),
        search=request.GET.get(PARAM_NEWSLETTER_SEARCH),
        # The archive's state as the reader's current URL reports it, validated
        # through the same builder the full page uses. A keystroke carries only
        # its own form, so without this the chips rendered into the swapped
        # region would come back having forgotten the news archive.
        carried=_carried_news_state(request),
    )
    return search_fragment(
        request,
        "visibility/partials/_newsletter_results.html",
        {"newsletters": newsletters},
        pushed=push_url(
            request,
            path=reverse("news"),
            allowed=PAGE_PARAMS,
            # The section's own parsing has already trimmed and bounded the
            # term, so what reaches the address bar is what reached the query.
            updates={PARAM_NEWSLETTER_SEARCH: newsletters.search},
            anchor="#section-newsletter-analytics",
        ),
    )


#: What the newsletter archive page understands. A shorter list than the Uudised
#: page's, and `otsi` means the same thing on both.
ARCHIVE_PARAMS = (PARAM_NEWSLETTER, PARAM_ARCHIVE_SEARCH, PARAM_ARCHIVE_PAGE)


@require_GET
def newsletter_history(request):
    """Every completed Smaily send, filterable and searchable.

    The archive behind the Uudised page's most-recent list: fourteen years of
    campaigns, including every one that matches none of the three newsletters.
    Reads PostgreSQL only — the subject search never contacts Smaily.

    It is a news route because that is where a reader now finds the newsletters,
    and `/nahtavus/uudiskirjad/` still resolves: it redirects here, query string
    intact, so a saved bookmark keeps its newsletter, its term and its page.
    """
    return render(
        request,
        "visibility/campaign_history.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "news",
            "history": build_campaign_history(
                newsletter_key=request.GET.get(PARAM_NEWSLETTER),
                search=request.GET.get(PARAM_ARCHIVE_SEARCH),
                page=request.GET.get(PARAM_ARCHIVE_PAGE),
            ),
        },
    )


@require_GET
def newsletter_history_search_fragment(request):
    """One page of the archive, for a reader typing in the subject box.

    Page one, always, for the reason the news fragment resets its own: a new
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
            path=reverse("news-newsletter-history"),
            allowed=ARCHIVE_PARAMS,
            updates={
                PARAM_ARCHIVE_SEARCH: history.search,
                PARAM_ARCHIVE_PAGE: "",
            },
        ),
    )
