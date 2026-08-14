"""The Uudised page. Reads PostgreSQL only; never fetches the RSS feed.

The page carries five focus views over two bodies of data: the news catalogue,
which this app owns, and the newsletter material, which it does not. Smaily's
models, collectors and selectors stay in `apps.visibility` — only the place a
reader finds them is here, because "what did we publish" and "what did we send"
are the same question to the board and were two pages apart.

So the newsletter presenters are imported rather than reimplemented, and the
templates under `visibility/` stay authoritative. What this module adds is the
composition: which parameters belong to which section, and how each section's
links carry the other's state so neither can reset it.

## Five views, one address

`fookus` chooses which view renders. Every focus is an ordinary `GET` link
carrying the whole validated state, so the period a reader set in the archive is
still in force when they come back from the publishing view. Nothing is echoed
from `request.GET`: the state is rebuilt from resolved values, so a parameter
this page does not understand cannot ride along into the next view.

**Each focus builds only what it renders.** The archive's paginated query runs
under `fookus=arhiiv`; the newsletter aggregates run under `fookus=uudiskirjad`.
Building all five on every request would put five views' worth of queries behind
whichever one is on screen.
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
from .focus import FOCUS_ARCHIVE, FOCUS_NEWSLETTERS, PARAM_FOCUS, parse_focus
from .measurement import DEFAULT_READ_PERIOD, PARAM_READ
from .page import DEFAULT_LENS, PARAM_LENS, build_news_page, parse_lens
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

#: Which view is on screen, over which reading window, through which lens. These
#: belong to the page rather than to either section, and they are in the pushed
#: URL for the same reason the others are: a reader typing in the archive's
#: search box must not be returned to the overview on the next reload.
FOCUS_PARAMS = (PARAM_FOCUS, PARAM_READ, PARAM_LENS)

#: Everything `/uudised/` reads. Both live-search fragments push through this, so
#: a keystroke in either box keeps the rest of the page exactly as it was.
PAGE_PARAMS = NEWS_PARAMS + NEWSLETTER_PARAMS + FOCUS_PARAMS


def _news_state(archive) -> str:
    """The archive's own state as a query fragment, from validated values only.

    Built from the *resolved* archive rather than from `request.GET`, so what is
    carried is what was actually applied.
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


def _page_state(request, *, exclude: tuple[str, ...] = ()) -> str:
    """Everything the page is holding, as a query fragment a control can carry.

    Assembled from validated values only, and never including the parameter the
    control being built owns — a focus link must not carry the focus it is
    leaving, and a reading-window chip must not carry the window it replaces.

    Defaults are omitted, so `/uudised/` stays the address of an untouched page
    rather than accumulating `fookus=ulevaade&loetud=30&vaade=kuu`.
    """
    parts: list[str] = []

    if PARAM_FOCUS not in exclude:
        focus = parse_focus(request.GET.get(PARAM_FOCUS))
        if not focus.is_default:
            parts.append(f"{PARAM_FOCUS}={focus.key}")

    if PARAM_READ not in exclude:
        reading_key = (request.GET.get(PARAM_READ) or "").strip()
        from .measurement import READ_PERIODS

        if reading_key in {period.key for period in READ_PERIODS} and (
            reading_key != DEFAULT_READ_PERIOD.key
        ):
            parts.append(f"{PARAM_READ}={reading_key}")

    if PARAM_LENS not in exclude:
        lens = parse_lens(request.GET.get(PARAM_LENS))
        if lens != DEFAULT_LENS:
            parts.append(f"{PARAM_LENS}={lens}")

    if not set(NEWS_PARAMS) & set(exclude):
        resolved = resolve_period(
            request.GET.get(PARAM_PERIOD),
            request.GET.get(PARAM_FROM),
            request.GET.get(PARAM_TO),
        )
        archive_state = build_query(
            period_key=resolved.key,
            sort=parse_sort(request.GET.get(PARAM_SORT)),
            search=parse_search(request.GET.get(PARAM_SEARCH)),
            category=parse_category(request.GET.get(PARAM_CATEGORY)),
            start=resolved.start,
            end=resolved.end,
        )
        if archive_state:
            parts.append(archive_state)

    if not set(NEWSLETTER_PARAMS) & set(exclude):
        carried = newsletter_state(
            newsletter_key=request.GET.get(PARAM_NEWSLETTER),
            search=request.GET.get(PARAM_NEWSLETTER_SEARCH),
        )
        if carried:
            parts.append(carried)

    return "&".join(part for part in parts if part)


@require_GET
def news_overview(request):
    """The Chamber's news intelligence dashboard, in whichever focus was asked for.

    Every parameter is validated before it reaches a selector: an unreadable
    focus, an unreadable period, a reversed date range, a rotted page number and
    an oversized search term each resolve to something renderable rather than to
    a 500.
    """
    focus = parse_focus(request.GET.get(PARAM_FOCUS))
    page = build_news_page(
        focus_key=request.GET.get(PARAM_FOCUS),
        read_key=request.GET.get(PARAM_READ),
        period_key=request.GET.get(PARAM_PERIOD),
        date_from=request.GET.get(PARAM_FROM),
        date_to=request.GET.get(PARAM_TO),
        lens_key=request.GET.get(PARAM_LENS),
        newsletter_key=_selected_newsletter(request),
        state=_page_state(request, exclude=(PARAM_FOCUS,)),
    )

    context = {
        "navigation": NAVIGATION,
        "active_nav": "news",
        "freshness": current_freshness(),
        "page": page,
        # Built without the reading parameter, so a window chip replaces the
        # window rather than appending a second one.
        "reading_state": _page_state(request, exclude=(PARAM_READ,)),
    }

    if focus.key == FOCUS_ARCHIVE:
        context.update(_archive_context(request))
    elif focus.key == FOCUS_NEWSLETTERS:
        context.update(_newsletter_context(request))

    return render(request, "news/overview.html", context)


def _selected_newsletter(request) -> str:
    """Which newsletter the reader picked, validated against the registry."""
    from apps.visibility.newsletter_page import parse_newsletter

    return parse_newsletter(request.GET.get(PARAM_NEWSLETTER))


def _archive_context(request) -> dict:
    """The archive view: the exact-lookup layer beneath the dashboard.

    Unchanged in behaviour — the same builder, the same parameters, the same
    pagination and search. What changed is that it is now a focus a reader
    chooses rather than the first thing the page shows.
    """
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
        carried=_focus_carry(request, carried_newsletter),
    )
    return {"archive": archive}


def _focus_carry(request, newsletter: str) -> str:
    """What the archive's own controls must carry besides their own state.

    The newsletter the reader chose, and the focus — without which every period
    chip in the archive would quietly return them to the overview.
    """
    parts = [f"{PARAM_FOCUS}={FOCUS_ARCHIVE}"]
    if newsletter:
        parts.append(newsletter)
    return "&".join(parts)


def _newsletter_context(request) -> dict:
    """The newsletter view. Smaily stays owned by `apps.visibility` throughout.

    The section's chips carry the **archive's** state as well as this focus, so
    a reader who narrowed the archive and then came here to check a newsletter
    still has their period, category, ordering and article search when they go
    back. The two sections no longer share a screen; they still share a URL, and
    neither may reset the other.
    """
    summary = get_visibility_summary()
    carried = f"{PARAM_FOCUS}={FOCUS_NEWSLETTERS}"
    archive_state = _carried_archive_state(request)
    if archive_state:
        carried = f"{carried}&{archive_state}"
    return {
        # The same `ChannelSlot` the overall dashboard's band renders, from the
        # same builder. No second newsletter card exists, and the three lists are
        # never totalled.
        "newsletter_slot": build_newsletter_slot(summary.newsletter),
        "newsletters": build_newsletter_section(
            newsletter_key=request.GET.get(PARAM_NEWSLETTER),
            search=request.GET.get(PARAM_NEWSLETTER_SEARCH),
            carried=carried,
        ),
    }


def _carried_archive_state(request) -> str:
    """The archive's state, rebuilt from validated values, for another focus to carry.

    Only what the archive itself would have emitted: an untouched archive
    contributes nothing beyond its default period, so an ordinary visit does not
    grow parameters that say only "the reader has not chosen anything".
    """
    resolved = resolve_period(
        request.GET.get(PARAM_PERIOD),
        request.GET.get(PARAM_FROM),
        request.GET.get(PARAM_TO),
    )
    return build_query(
        period_key=resolved.key,
        sort=parse_sort(request.GET.get(PARAM_SORT)),
        search=parse_search(request.GET.get(PARAM_SEARCH)),
        category=parse_category(request.GET.get(PARAM_CATEGORY)),
        start=resolved.start,
        end=resolved.end,
    )


@require_GET
def news_search_fragment(request):
    """The archive rows alone, for a reader typing in the news search box.

    Page one, always: a new term is a new question, and an archive narrowed from
    thousands of articles to six has no page seven. `lk` is neither read here nor
    carried into the pushed URL.

    The period, the custom range, the sort **and the category** are read exactly
    as the full page reads them, so typing narrows what the reader is already
    looking at rather than quietly widening it to everything.

    The focus, the reading window and the newsletter parameters are in
    `PAGE_PARAMS` but not in this form, so they reach the pushed URL from
    `HX-Current-URL` and survive untouched: typing in the news box must not
    return the reader to the overview.
    """
    archive = build_news_archive(
        period_key=request.GET.get(PARAM_PERIOD),
        date_from=request.GET.get(PARAM_FROM),
        date_to=request.GET.get(PARAM_TO),
        sort=parse_sort(request.GET.get(PARAM_SORT)),
        search=parse_search(request.GET.get(PARAM_SEARCH)),
        category=parse_category(request.GET.get(PARAM_CATEGORY)),
        carried=_focus_carry(
            request,
            newsletter_state(
                newsletter_key=request.GET.get(PARAM_NEWSLETTER),
                search=request.GET.get(PARAM_NEWSLETTER_SEARCH),
            ),
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
            # `Kõik` so the unfiltered page keeps an unfiltered URL. The focus is
            # asserted rather than carried: this fragment only ever answers for
            # the archive.
            updates={
                PARAM_SEARCH: archive.search,
                PARAM_CATEGORY: archive.category,
                PARAM_PAGE: "",
                PARAM_FOCUS: FOCUS_ARCHIVE,
            },
        ),
    )


@require_GET
def news_newsletter_search_fragment(request):
    """The sends table alone, for a reader typing in the newsletter box.

    Only the newsletter section is rebuilt: reaching for the news archive as well
    would re-run the catalogue query on every keystroke to render a table that
    uses none of it.

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
            updates={
                PARAM_NEWSLETTER_SEARCH: newsletters.search,
                PARAM_FOCUS: FOCUS_NEWSLETTERS,
            },
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
