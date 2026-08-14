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

The newsletter material left earlier and is rendered by `apps.news`; the two
archive addresses below still redirect there.
"""

from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .content_sections import PARAM_CONTENT
from .website_page import (
    FOCUS_CONTENT,
    FOCUS_PAGES,
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


#: Which Koduleht view best answers the question an old `/nahtavus/` bookmark was
#: asking. The legacy page had one screen with a period, a section filter and a
#: page search; those map onto three different focus views here.
def _legacy_focus(params) -> str:
    if params.get(PARAM_SEARCH):
        # A saved search was a reader looking for one page. That is the explorer.
        return FOCUS_PAGES
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
