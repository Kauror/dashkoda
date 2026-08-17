"""The Uudised page. Reads PostgreSQL only; never fetches the RSS feed.

The page carries four focus views over the news catalogue, which this app owns.

## Newsletters are not here any more

They were: a fifth focus, plus a send archive beneath it, both composed here out
of presenters imported from `apps.visibility`. The Smaily material is
`Otsepostitused` now — its own section under Koduleht, rendered by the app that
owns the models, the collector and every Smaily query. This module imports
nothing from `apps.visibility` except the GA4 coverage the article measurements
are read against, and it holds no newsletter state, no newsletter parameter and
no newsletter template.

What is kept is arrival. Three retired addresses redirect at the bottom of this
module, and `news_overview` intercepts the retired `fookus=uudiskirjad` before
the focus parser can quietly resolve it to the overview.

## Four views, one address

`fookus` chooses which view renders. Every focus is an ordinary `GET` link
carrying the whole validated state, so the period a reader set in the archive is
still in force when they come back from the publishing view. Nothing is echoed
from `request.GET`: the state is rebuilt from resolved values, so a parameter
this page does not understand cannot ride along into the next view.

**Each focus builds only what it renders.** The archive's paginated query runs
under `fookus=arhiiv` and nowhere else. Building all four on every request would
put four views' worth of queries behind whichever one is on screen.
"""

from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.live_search import push_url, search_fragment
from apps.dashboard.navigation import NAVIGATION

from .archive import build_news_archive
from .categories import parse_category
from .focus import (
    FOCUS_ARCHIVE,
    LEGACY_FOCUS_NEWSLETTERS,
    PARAM_FOCUS,
    parse_focus,
)
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

#: Which view is on screen, over which reading window, through which lens. These
#: belong to the page rather than to the archive, and they are in the pushed URL
#: for the same reason the others are: a reader typing in the archive's search
#: box must not be returned to the overview on the next reload.
FOCUS_PARAMS = (PARAM_FOCUS, PARAM_READ, PARAM_LENS)

#: Everything `/uudised/` reads. The live-search fragment pushes through this, so
#: a keystroke keeps the rest of the page exactly as it was.
PAGE_PARAMS = NEWS_PARAMS + FOCUS_PARAMS

#: The parameters an old newsletter link carried that `Otsepostitused` still
#: understands. Everything else a saved `/uudised/?fookus=uudiskirjad` URL picked
#: up — a period, a category, an article search — belongs to the news archive and
#: means nothing on the page it is being sent to, so it is dropped rather than
#: reflected into an address that cannot answer for it.
CARRIED_TO_MAILINGS = ("uudiskiri", "otsi")


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

    return "&".join(part for part in parts if part)


@require_GET
def news_overview(request):
    """The Chamber's news intelligence dashboard, in whichever focus was asked for.

    Every parameter is validated before it reaches a selector: an unreadable
    focus, an unreadable period, a reversed date range, a rotted page number and
    an oversized search term each resolve to something renderable rather than to
    a 500.

    `fookus=uudiskirjad` is the one value handled before that. It is a real
    address people saved, and letting it fall through `parse_focus` would land
    them on the news overview with no indication that the thing they asked for
    exists somewhere else.
    """
    if (request.GET.get(PARAM_FOCUS) or "").strip() == LEGACY_FOCUS_NEWSLETTERS:
        return _redirect_to_mailings(request)

    focus = parse_focus(request.GET.get(PARAM_FOCUS))
    page = build_news_page(
        focus_key=request.GET.get(PARAM_FOCUS),
        read_key=request.GET.get(PARAM_READ),
        period_key=request.GET.get(PARAM_PERIOD),
        date_from=request.GET.get(PARAM_FROM),
        date_to=request.GET.get(PARAM_TO),
        lens_key=request.GET.get(PARAM_LENS),
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

    return render(request, "news/overview.html", context)


def _archive_context(request) -> dict:
    """The archive view: the exact-lookup layer beneath the dashboard.

    Unchanged in behaviour — the same builder, the same parameters, the same
    pagination and search. What changed is that it is now a focus a reader
    chooses rather than the first thing the page shows.
    """
    archive = build_news_archive(
        period_key=request.GET.get(PARAM_PERIOD),
        date_from=request.GET.get(PARAM_FROM),
        date_to=request.GET.get(PARAM_TO),
        sort=parse_sort(request.GET.get(PARAM_SORT)),
        search=parse_search(request.GET.get(PARAM_SEARCH)),
        category=parse_category(request.GET.get(PARAM_CATEGORY)),
        page=parse_page(request.GET.get(PARAM_PAGE)),
        carried=f"{PARAM_FOCUS}={FOCUS_ARCHIVE}",
    )
    return {"archive": archive}


@require_GET
def news_search_fragment(request):
    """The archive rows alone, for a reader typing in the news search box.

    Page one, always: a new term is a new question, and an archive narrowed from
    thousands of articles to six has no page seven. `lk` is neither read here nor
    carried into the pushed URL.

    The period, the custom range, the sort **and the category** are read exactly
    as the full page reads them, so typing narrows what the reader is already
    looking at rather than quietly widening it to everything.

    The focus and the reading window are in `PAGE_PARAMS` but not in this form,
    so they reach the pushed URL from `HX-Current-URL` and survive untouched:
    typing in the news box must not return the reader to the overview.
    """
    archive = build_news_archive(
        period_key=request.GET.get(PARAM_PERIOD),
        date_from=request.GET.get(PARAM_FROM),
        date_to=request.GET.get(PARAM_TO),
        sort=parse_sort(request.GET.get(PARAM_SORT)),
        search=parse_search(request.GET.get(PARAM_SEARCH)),
        category=parse_category(request.GET.get(PARAM_CATEGORY)),
        carried=f"{PARAM_FOCUS}={FOCUS_ARCHIVE}",
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


# ---------------------------------------------------------------------------
# Where the newsletters used to be
# ---------------------------------------------------------------------------
#
# All three are **temporary** redirects on purpose: a 301 is cached by browsers
# indefinitely and is painful to take back, and nothing here needs the
# permanence. Every target is in `apps.visibility` and none of those views
# redirects, so no loop is possible from any entry point.


def _redirect_to_mailings(request):
    """`/uudised/?fookus=uudiskirjad` — the newsletter focus this page had.

    Carries the newsletter and the subject search, which `Otsepostitused` reads
    under exactly those names, and drops everything else. A saved link that also
    named a period and a category was describing the *news archive* on the same
    address; those parameters have no meaning on the page this is going to, and
    passing them on would put keys into an address bar that nothing on the page
    reads back.
    """
    target = reverse("mailings")
    carried = {
        key: request.GET[key] for key in CARRIED_TO_MAILINGS if request.GET.get(key, "").strip()
    }
    if not carried:
        return redirect(target)
    query = "&".join(f"{key}={value}" for key, value in carried.items())
    return redirect(f"{target}?{query}")


def _redirect_keeping_query(request, route: str):
    """The same question, asked at the address that now answers it.

    The query string is passed through whole. `uudiskiri`, `otsi` and `lk` are
    exactly what a saved archive bookmark carries and mean the same thing on the
    other side, so losing them would land the reader in fourteen unfiltered
    years.
    """
    target = reverse(route)
    query = request.META.get("QUERY_STRING", "")
    return redirect(f"{target}?{query}" if query else target)


@require_GET
def legacy_newsletter_history(request):
    """`/uudised/uudiskirjad/` — where the send archive lived.

    Aimed at `mailings` rather than `mailings-history` since 2026-08-16: the
    archive moved onto `Otsepostitused` itself, and pointing here at the address
    it left would cost a second hop through a redirect kept only for bookmarks.
    """
    return _redirect_keeping_query(request, "mailings")


@require_GET
def legacy_newsletter_history_search(request):
    """`/uudised/uudiskirjad/otsi/` — the send archive's live search.

    An internal fragment rather than a bookmark, kept because htmx follows a
    redirect transparently and a cached page still holding the old attribute
    would otherwise start answering 404 mid-keystroke.
    """
    return _redirect_keeping_query(request, "mailings-history-search")


@require_GET
def legacy_newsletter_search(request):
    """`/uudised/otsi/uudiskirjad/` — the newsletter section's live search.

    Kept for the same reason as the one above. It points at the archive's
    fragment since 2026-08-16: `Saadetised` merged into `Otsepostitused`, and
    the box that survived the merge is the archive's. Both read `otsi`, so a
    request arriving here still asks the question it was asking.
    """
    return _redirect_keeping_query(request, "mailings-history-search")
