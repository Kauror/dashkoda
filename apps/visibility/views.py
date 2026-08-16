"""The Koduleht page. Reads PostgreSQL only; never contacts Google.

The website surface used to be called **Nähtavus** and lived at `/nahtavus/`,
where it carried a five-slot social channel band above a traffic section. It is
`Koduleht` now, at `/koduleht/`, and it answers questions about the website.

Three notes about what did *not* change:

- **the Django app is still `apps.visibility`** and the route is still named
  `visibility`. Renaming an established app, its migration namespace and its
  model labels is not justified by a change of product name, and every existing
  `reverse("visibility")` caller keeps working and now resolves to the canonical
  address;
- **no social data or functionality was touched.** The four hand-entered figures
  keep their models, their history and their admin entry; `build_channel_band`
  still renders them on the overall DashKoda overview. What changed is that
  Koduleht is not where they are shown;
- **`/nahtavus/` still resolves.** It redirects, carrying the reader's window and
  section into the view that now answers for them, so a saved bookmark lands on
  the state it named rather than on a 404.

The newsletter material is `Otsepostitused`, further down this module: it spent
a while rendered by `apps.news` and has come back to the app that owns Smaily,
as its own section under Koduleht rather than as a focus of the website page.
Every old address still resolves.
"""

from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.live_search import push_url, search_fragment
from apps.dashboard.navigation import NAVIGATION

from .campaign_history import PARAM_PAGE as PARAM_HISTORY_PAGE
from .campaign_history import PARAM_SEARCH as PARAM_HISTORY_SEARCH
from .campaign_history import build_campaign_history
from .content_sections import PARAM_CONTENT
from .mailings_page import build_mailings_page
from .newsletter_page import (
    ALL_NEWSLETTERS,
    PARAM_NEWSLETTER,
    build_newsletter_section,
    parse_newsletter,
)
from .newsletter_page import PARAM_SEARCH as PARAM_NEWSLETTER_SEARCH
from .page import build_newsletter_slot
from .selectors import get_visibility_summary
from .website_page import (
    FOCUS_CONTENT,
    PARAM_DETAIL,
    PARAM_FOCUS,
    PARAM_METRIC,
    PARAM_PAGE,
    PARAM_SEARCH,
    build_website_page,
)
from .website_period import PARAM_FROM, PARAM_PERIOD, PARAM_TO


@require_GET
def koduleht(request):
    """How Koda.ee is used, what changed, and how much of it can be trusted.

    Every parameter is validated inside `build_website_page` against a closed
    registry or bounded, and every link on the page is rebuilt from the resolved
    values — so nothing a reader types reaches a query or is reflected into an
    address.
    """
    return render(
        request,
        "visibility/koduleht.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "visibility",
            "freshness": current_freshness(),
            "page": build_website_page(
                focus_key=request.GET.get(PARAM_FOCUS),
                period_key=request.GET.get(PARAM_PERIOD),
                date_from=request.GET.get(PARAM_FROM),
                date_to=request.GET.get(PARAM_TO),
                metric_key=request.GET.get(PARAM_METRIC),
                section_key=request.GET.get(PARAM_CONTENT),
                search=request.GET.get(PARAM_SEARCH),
                page=request.GET.get(PARAM_PAGE),
                detail_path=request.GET.get(PARAM_DETAIL),
            ),
            # The manual-entry action is offered to active staff only, and no
            # longer from the page header: an ordinary viewer holds the shared
            # PIN and no Django account, so an editing control would advertise a
            # door they cannot open. It sits in `Andmete kohta` now.
            "can_add_data": request.user.is_authenticated and request.user.is_staff,
        },
    )


#: The parameters Koduleht understands. A live-search fragment carries the
#: reader's current query forward so a reload keeps the window and the view — and
#: carries *only* these, because the value ends up in somebody's address bar.
KODULEHT_PARAMS = (
    PARAM_FOCUS,
    PARAM_PERIOD,
    PARAM_FROM,
    PARAM_TO,
    PARAM_METRIC,
    PARAM_CONTENT,
    PARAM_SEARCH,
    PARAM_PAGE,
    PARAM_DETAIL,
)


@require_GET
def koduleht_search_fragment(request):
    """The explorer's results region alone, for a reader typing in the box.

    Answers the same query the form would submit and renders the same partial
    the full page renders, so what a reader sees while typing and what they see
    after a reload cannot drift apart.

    A new term is a new question, so this always builds page one: `lk` is
    neither read from the request nor carried into the pushed URL. Keeping it
    would answer "Ühtegi lehte ei leitud" for every term matching fewer results
    than the page the reader happened to be on.

    A reader without JavaScript never reaches this: the form on the page submits
    to the page itself.
    """
    page = build_website_page(
        focus_key=FOCUS_CONTENT,
        period_key=request.GET.get(PARAM_PERIOD),
        date_from=request.GET.get(PARAM_FROM),
        date_to=request.GET.get(PARAM_TO),
        section_key=request.GET.get(PARAM_CONTENT),
        search=request.GET.get(PARAM_SEARCH),
        # Just the results region: running the content view's own analysis to
        # answer a keystroke would make typing cost what a page load costs.
        search_only=True,
    )
    return search_fragment(
        request,
        "visibility/koduleht/_search_results.html",
        {"page": page},
        pushed=push_url(
            request,
            path=reverse("visibility"),
            allowed=KODULEHT_PARAMS,
            updates={
                PARAM_FOCUS: FOCUS_CONTENT,
                PARAM_SEARCH: page.query.search,
                PARAM_PAGE: "",
                # Selecting a page is a different question from searching for
                # one, and a new term should not keep the old page open.
                PARAM_DETAIL: "",
            },
            anchor="#section-otsing",
        ),
    )


#: Which Koduleht view best answers the question an old `/nahtavus/` bookmark was
#: asking. The legacy page had one screen with a period, a section filter and a
#: page search; those map onto three different focus views here.
def _legacy_focus(params) -> str:
    if params.get(PARAM_SEARCH):
        # A saved search was a reader looking for one page. The explorer lives
        # at the foot of `Sisu ja lehed` since the `lehed` view retired.
        return FOCUS_CONTENT
    section = (params.get(PARAM_CONTENT) or "").strip()
    if section and section != "koik":
        # A saved section filter was a reader asking about content.
        return FOCUS_CONTENT
    return ""


@require_GET
def legacy_visibility(request):
    """`/nahtavus/` — the address the website surface used to have.

    A **temporary** redirect on purpose: a 301 is cached by browsers
    indefinitely and is painful to take back, and nothing here needs the
    permanence.

    The query string is carried through rather than dropped, and the focus is
    chosen from what the bookmark was asking for, so
    `/nahtavus/?periood=90&sisu=uudised` reaches the Koduleht content view over
    ninety days with the news section selected — the state the old URL named.
    No loop is possible: the target is a different path whose view redirects
    nowhere.
    """
    target = reverse("visibility")
    params = request.GET.copy()
    # Only the parameters Koduleht understands travel on. Anything else a saved
    # link picked up — a campaign tag, a tracking parameter — is dropped rather
    # than reflected into the address this hands the reader.
    allowed = (PARAM_PERIOD, PARAM_FROM, PARAM_TO, PARAM_CONTENT, PARAM_SEARCH, PARAM_PAGE)
    carried = {key: params[key] for key in allowed if params.get(key)}

    focus = _legacy_focus(params)
    if focus:
        carried[PARAM_FOCUS] = focus

    if not carried:
        return redirect(target)

    query = "&".join(f"{key}={value}" for key, value in carried.items())
    return redirect(f"{target}?{query}")


def _redirect_keeping_query(request, route: str):
    """The same question, asked at the address that now answers it.

    Temporary rather than permanent for the same reason as above. The query
    string is passed through whole — `uudiskiri`, `otsi` and `lk` are exactly
    what a saved archive bookmark carries, and losing them would land the reader
    in fourteen unfiltered years.
    """
    target = reverse(route)
    query = request.META.get("QUERY_STRING", "")
    return redirect(f"{target}?{query}" if query else target)


@require_GET
def campaign_history(request):
    """Where the send archive used to live, before Uudised and before this.

    Redirects to `mailings-history`. No loop is possible: the target is a
    different path whose view renders rather than redirects. It used to point at
    `news-newsletter-history`, which now redirects to the same place — so this
    is aimed at the final destination instead of chaining through it, and an
    ancient bookmark costs one hop rather than two.
    """
    return _redirect_keeping_query(request, "mailings-history")


@require_GET
def campaign_history_search_fragment(request):
    """Where the archive's live search used to live.

    Kept as a compatibility alias for the same reason as the page above. htmx
    follows the redirect transparently, so the fragment that answers is the
    Otsepostitused one and the URL it pushes is that section's.
    """
    return _redirect_keeping_query(request, "mailings-history-search")


# ---------------------------------------------------------------------------
# Otsepostitused
# ---------------------------------------------------------------------------

#: What the Otsepostitused overview understands. Two parameters and no more:
#: which newsletter is chosen and what is being searched for in the sends. The
#: news archive's period, category and ordering are **not** here — this page has
#: no article archive to preserve, and carrying them would put parameters into
#: an address that nothing on it reads.
MAILINGS_PARAMS = (PARAM_NEWSLETTER, PARAM_NEWSLETTER_SEARCH)

#: What the send history understands.
MAILINGS_HISTORY_PARAMS = (PARAM_NEWSLETTER, PARAM_HISTORY_SEARCH, PARAM_HISTORY_PAGE)


@require_GET
def mailings(request):
    """`Otsepostitused` — how the Chamber's newsletters perform.

    The Smaily intelligence that was `/uudised/?fookus=uudiskirjad`, at an
    address of its own. Nothing about the figures changed in the move: the same
    selectors answer the same questions over the same windows, and
    `build_newsletter_section` renders the same searchable sends table it did
    under Uudised.

    Reads PostgreSQL only. The subject search never contacts Smaily, and no page
    render ever does.
    """
    newsletter_key = parse_newsletter(request.GET.get(PARAM_NEWSLETTER))
    summary = get_visibility_summary()
    return render(
        request,
        "visibility/otsepostitused.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "mailings",
            "active_section": "overview",
            # No `freshness`: only `dashboard/partials/freshness.html` reads that
            # key and only the fragment endpoint renders it, so passing it here
            # would be a source-state query for something nothing displays. Each
            # figure on this page states its own recency where it needs to.
            # `parse_newsletter` answers `koik` for "all three", which is not a
            # newsletter the aggregates can be read for. The builder wants the
            # empty string for that state, so the landing view asks for the
            # comparison and nothing that would need one list chosen.
            "page": build_mailings_page(
                newsletter_key="" if newsletter_key == ALL_NEWSLETTERS else newsletter_key
            ),
            # The same `ChannelSlot` the overall dashboard's band renders, from
            # the same builder. No second newsletter card exists, and the three
            # lists are never totalled.
            "newsletter_slot": build_newsletter_slot(summary.newsletter),
            "newsletters": build_newsletter_section(
                newsletter_key=request.GET.get(PARAM_NEWSLETTER),
                search=request.GET.get(PARAM_NEWSLETTER_SEARCH),
                # Nothing to carry. `carried` exists so this section could not
                # reset the news archive it used to share a URL with; this page
                # has no such neighbour, so every link it builds carries its own
                # state and only that.
                carried="",
            ),
        },
    )


@require_GET
def mailings_search_fragment(request):
    """The sends table alone, for a reader typing in the subject box.

    Only the sends section is rebuilt. The comparison, the block change and the
    rankings above it are unaffected by a subject search and re-running their
    aggregates on every keystroke would buy nothing.
    """
    newsletters = build_newsletter_section(
        newsletter_key=request.GET.get(PARAM_NEWSLETTER),
        search=request.GET.get(PARAM_NEWSLETTER_SEARCH),
        carried="",
    )
    return search_fragment(
        request,
        "visibility/partials/_newsletter_results.html",
        {"newsletters": newsletters},
        pushed=push_url(
            request,
            path=reverse("mailings"),
            allowed=MAILINGS_PARAMS,
            # The section's own parsing has already trimmed and bounded the
            # term, so what reaches the address bar is what reached the query.
            updates={PARAM_NEWSLETTER_SEARCH: newsletters.search},
            anchor="#section-newsletter-analytics",
        ),
    )


@require_GET
def mailings_history(request):
    """Every completed Smaily send, filterable and searchable.

    The archive behind the overview's most-recent list: fourteen years of
    campaigns, including every one that matches none of the three newsletters.
    Reads PostgreSQL only — the subject search never contacts Smaily.

    A second route rather than a focus of the page above, because fourteen years
    of sends is not a section: rendering three thousand rows into the overview
    would make every visit pay for history almost nobody wants on that visit.
    """
    return render(
        request,
        "visibility/campaign_history.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "mailings",
            "active_section": "history",
            "history": build_campaign_history(
                newsletter_key=request.GET.get(PARAM_NEWSLETTER),
                search=request.GET.get(PARAM_HISTORY_SEARCH),
                page=request.GET.get(PARAM_HISTORY_PAGE),
            ),
        },
    )


@require_GET
def mailings_history_search_fragment(request):
    """One page of the archive, for a reader typing in the subject box.

    Page one, always: a new term is a new question, and 3 194 sends narrowed to
    four have no page 40.
    """
    history = build_campaign_history(
        newsletter_key=request.GET.get(PARAM_NEWSLETTER),
        search=request.GET.get(PARAM_HISTORY_SEARCH),
    )
    return search_fragment(
        request,
        "visibility/partials/_campaign_history_results.html",
        {"history": history},
        pushed=push_url(
            request,
            path=reverse("mailings-history"),
            allowed=MAILINGS_HISTORY_PARAMS,
            updates={PARAM_HISTORY_SEARCH: history.search, PARAM_HISTORY_PAGE: ""},
        ),
    )
