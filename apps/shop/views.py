"""The E-pood pages. Read PostgreSQL only; never contact Koda.ee."""

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .models import MemberStatus, ProductType
from .page import build_overview, build_product_detail
from .periods import (
    PARAM_CATEGORY,
    PARAM_FOCUS,
    PARAM_FROM,
    PARAM_MEMBER,
    PARAM_METRIC,
    PARAM_PAGE,
    PARAM_PERIOD,
    PARAM_SEARCH,
    PARAM_SORT,
    PARAM_TO,
    PARAM_TYPE,
    parse_focus,
    parse_int_list,
    parse_metric,
    parse_page,
    parse_search,
    parse_sort,
)


def _product_type(raw: str | None) -> str:
    """A known product type, or every type. A rotted bookmark is not a 500."""
    value = (raw or "").strip()
    return value if value in ProductType.values else ""


def _member_status(raw: str | None) -> str:
    value = (raw or "").strip()
    return value if value in MemberStatus.values else ""


@require_GET
def shop_overview(request):
    """Which E-pood products are acquired, and how that relates to traffic.

    Every parameter is validated before it reaches a selector: an unreadable
    period, a reversed range, an unknown product type, an unknown focus, a
    rotted page number and an oversized search term each resolve to something
    renderable. An unrecognised focus lands on the overview rather than raising,
    so a shared link that outlives a rename still opens the page.
    """
    overview = build_overview(
        period_key=request.GET.get(PARAM_PERIOD),
        date_from=request.GET.get(PARAM_FROM),
        date_to=request.GET.get(PARAM_TO),
        product_type=_product_type(request.GET.get(PARAM_TYPE)),
        categories=parse_int_list(request.GET.getlist(PARAM_CATEGORY)),
        member_status=_member_status(request.GET.get(PARAM_MEMBER)),
        search=parse_search(request.GET.get(PARAM_SEARCH)),
        sort=parse_sort(request.GET.get(PARAM_SORT)),
        page=parse_page(request.GET.get(PARAM_PAGE)),
        focus=parse_focus(request.GET.get(PARAM_FOCUS)),
        metric=parse_metric(request.GET.get(PARAM_METRIC)),
    )
    return render(
        request,
        "shop/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "shop",
            "freshness": current_freshness(),
            "overview": overview,
        },
    )


@require_GET
def shop_product(request, source_product_id: int):
    """One product's own history.

    Keyed by the Commerce product ID, never by a title or a slug: a renamed
    product must keep its address, and a bookmarked link must not silently start
    describing a different product.
    """
    detail = build_product_detail(
        source_product_id,
        period_key=request.GET.get(PARAM_PERIOD),
        date_from=request.GET.get(PARAM_FROM),
        date_to=request.GET.get(PARAM_TO),
    )
    if not detail.found:
        raise Http404("Toodet ei ole.")
    return render(
        request,
        "shop/product.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "shop",
            "freshness": current_freshness(),
            "detail": detail,
        },
    )
