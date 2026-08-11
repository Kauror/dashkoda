"""What the stored E-pood dataset can answer, and what it must refuse to.

Every query reads only **current** revisions: a superseded row is provenance,
not arithmetic.

## The two windows

This module's central idea is that a shop question has *two* date ranges, not
one, and conflating them produces the most confidently wrong number available
here.

- the **Commerce window** is what the reader chose, clamped to what the imported
  dataset actually covers (2020-10-22 → the export's `coverage_end`);
- the **web window** is the part of that which GA4 also measured, clamped at
  *both* ends — at the bottom by the GA4 property's earliest day (2023-06-16 on
  the real property), and at the top by **Commerce coverage end**.

That upper clamp is the one that is easy to forget and expensive to get wrong.
GA4 keeps collecting after a manual Commerce export stops. If a reader picks
"last 30 days" in September against an August export, the honest answer is that
there is no comparable period at all — not "September traffic, zero purchases",
which is what an unclamped calculation produces and which reads as a product
that suddenly stopped selling.

**A conversion rate uses acquisitions from the web window, never from the
Commerce window.** Six years of acquisitions over three years of page views is
not a rate of anything.

## Missing is not zero

A path with no stored `Ga4PageDaily` rows yields `None`, not `0` — except where
page-detail coverage is complete across the whole web window, in which case a
page nobody visited genuinely measured zero. `PageViewFigure` carries which of
the two it is so a template never has to guess.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth

from apps.visibility.ga4_selectors import Coverage as Ga4Coverage
from apps.visibility.ga4_selectors import current_days, current_pages, get_coverage

from .models import (
    MemberStatus,
    PageRole,
    PaymentClass,
    ProductType,
    ShopDailyFact,
    ShopProduct,
    ShopProductPage,
    ShopProductSnapshot,
    ShopSourceState,
)

ZERO = Decimal("0")

#: How many acquisitions per this many product-page views. Stated as a rate per
#: hundred views rather than as a percentage, because "conversion" implies a
#: visitor-level funnel and page views are not visitors.
CONVERSION_BASE = 100


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShopCoverage:
    """What the imported dataset covers, and which semantics may be shown."""

    source_as_of: date | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None
    member_semantics_verified: bool = False
    public_listing_semantics_verified: bool = False

    @property
    def has_data(self) -> bool:
        return self.coverage_start is not None and self.coverage_end is not None


def get_shop_coverage() -> ShopCoverage:
    """One query. Called by every page in this module."""
    state = (
        ShopSourceState.objects.filter(is_current=True)
        .only(
            "source_as_of",
            "coverage_start",
            "coverage_end",
            "member_semantics_verified",
            "public_listing_semantics_verified",
        )
        .first()
    )
    if state is None:
        return ShopCoverage()
    return ShopCoverage(
        source_as_of=state.source_as_of,
        coverage_start=state.coverage_start,
        coverage_end=state.coverage_end,
        member_semantics_verified=state.member_semantics_verified,
        public_listing_semantics_verified=state.public_listing_semantics_verified,
    )


@dataclass(frozen=True)
class ComparisonWindow:
    """One resolved question: which days the two halves of it may read.

    Both halves are computed here so no template and no view has to, and so the
    single rule about clamping the web window at Commerce coverage end lives in
    exactly one place.
    """

    commerce_start: date | None
    commerce_end: date | None
    web_start: date | None
    web_end: date | None
    page_detail_complete: bool = False

    @property
    def has_commerce(self) -> bool:
        return (
            self.commerce_start is not None
            and self.commerce_end is not None
            and self.commerce_start <= self.commerce_end
        )

    @property
    def has_web(self) -> bool:
        return (
            self.web_start is not None
            and self.web_end is not None
            and self.web_start <= self.web_end
        )

    @property
    def web_is_partial(self) -> bool:
        """Whether the web window is narrower than the Commerce one.

        True is the ordinary case rather than an error: Commerce history begins
        in 2020 and GA4's in 2023. The interface states the web interval
        whenever this is true, so a reader is never left to infer it.
        """
        if not self.has_web or not self.has_commerce:
            return self.has_commerce
        return self.web_start != self.commerce_start or self.web_end != self.commerce_end


def clamp_windows(
    *,
    start: date | None,
    end: date | None,
    shop: ShopCoverage,
    ga4: Ga4Coverage,
) -> ComparisonWindow:
    """The clamping rule on its own, reading nothing.

    Separated from :func:`resolve_comparison` so the arithmetic that decides
    whether September traffic may be divided by an August export can be tested
    without a database — which is also the only part of this module a reviewer
    needs to check by hand.

    `start`/`end` of `None` mean "everything", which resolves to the dataset's
    own coverage rather than to today.
    """
    if not shop.has_data:
        return ComparisonWindow(None, None, None, None)

    commerce_start = max(start, shop.coverage_start) if start else shop.coverage_start
    commerce_end = min(end, shop.coverage_end) if end else shop.coverage_end

    if not ga4.has_data or commerce_start > commerce_end:
        return ComparisonWindow(commerce_start, commerce_end, None, None)

    web_start = max(commerce_start, ga4.earliest)
    # Both clamps matter. The GA4 latest keeps the window inside measured
    # traffic; `commerce_end` keeps it inside imported orders, which is what
    # stops live September traffic being divided by an August export.
    web_end = min(commerce_end, ga4.latest)
    if web_start > web_end:
        return ComparisonWindow(commerce_start, commerce_end, None, None)

    return ComparisonWindow(
        commerce_start=commerce_start,
        commerce_end=commerce_end,
        web_start=web_start,
        web_end=web_end,
    )


def resolve_comparison(
    *,
    start: date | None,
    end: date | None,
    shop: ShopCoverage | None = None,
    ga4: Ga4Coverage | None = None,
) -> ComparisonWindow:
    """Clamp a chosen period to what each source can actually answer.

    The clamping is :func:`clamp_windows`; what this adds is the one fact that
    needs the database — whether GA4 page detail covers every day of the web
    window, which decides if an absent path is a measured zero or an unknown.
    """
    shop = shop if shop is not None else get_shop_coverage()
    ga4 = ga4 if ga4 is not None else get_coverage()
    window = clamp_windows(start=start, end=end, shop=shop, ga4=ga4)
    if not window.has_web:
        return window
    return ComparisonWindow(
        commerce_start=window.commerce_start,
        commerce_end=window.commerce_end,
        web_start=window.web_start,
        web_end=window.web_end,
        page_detail_complete=_page_detail_is_complete(window.web_start, window.web_end),
    )


def _page_detail_is_complete(start: date, end: date) -> bool:
    """Whether every day in the window carries GA4 page-level detail.

    Decides the difference between "this page measured zero views" and "nobody
    measured this page". Only when every day has detail may an absent path be
    read as a real zero.
    """
    expected = (end - start).days + 1
    have = (
        current_days()
        .filter(report_date__gte=start, report_date__lte=end, has_page_detail=True)
        .count()
    )
    return have >= expected


# ---------------------------------------------------------------------------
# Page views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageViewFigure:
    """Views for one path in one window, or the honest absence of them."""

    path: str
    views: int | None
    is_measured_zero: bool = False

    @property
    def is_known(self) -> bool:
        return self.views is not None


def page_views_in_window(
    paths: Iterable[str], *, window: ComparisonWindow
) -> dict[str, PageViewFigure]:
    """Views per path inside the web window, in **one** grouped query.

    A path absent from the stored rows is absent from the result unless page
    detail is complete for the whole window, in which case it is present as a
    measured zero. Nothing here runs per product.
    """
    wanted = [path for path in dict.fromkeys(paths) if path]
    if not wanted or not window.has_web:
        return {}

    rows = (
        current_pages()
        .filter(
            path__in=wanted,
            report_date__gte=window.web_start,
            report_date__lte=window.web_end,
        )
        .values("path")
        .annotate(views=Sum("page_views"))
    )
    figures = {
        row["path"]: PageViewFigure(path=row["path"], views=row["views"] or 0) for row in rows
    }
    if window.page_detail_complete:
        for path in wanted:
            figures.setdefault(path, PageViewFigure(path=path, views=0, is_measured_zero=True))
    return figures


def acquisitions_per_hundred(units: Decimal | None, views: int | None) -> Decimal | None:
    """Acquisitions per hundred product-page views, or `None`.

    `None` — never infinity, never zero — when the denominator is unknown or
    absent. A product with acquisitions and no measured views has no rate; that
    is a gap in measurement, not a rate of zero.
    """
    if units is None or views is None or views <= 0:
        return None
    return (Decimal(units) * CONVERSION_BASE / Decimal(views)).quantize(Decimal("0.1"))


# ---------------------------------------------------------------------------
# Commerce aggregates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommerceTotals:
    orders: int = 0
    units: Decimal = ZERO
    ordered_value_net: Decimal = ZERO

    @property
    def has_activity(self) -> bool:
        return self.orders > 0 or self.units > 0


def _facts_in_window(
    window: ComparisonWindow,
    *,
    start: date | None = None,
    end: date | None = None,
    product_types: Sequence[str] = (),
    category_term_ids: Sequence[int] = (),
    member_status: str = "",
    product_ids: Sequence[int] = (),
):
    """Current daily facts inside a window, narrowed by the page's filters.

    `start`/`end` override the Commerce window so the same builder serves the
    conversion numerator, which must read the **web** window instead.
    """
    lower = start or window.commerce_start
    upper = end or window.commerce_end
    if lower is None or upper is None:
        return ShopDailyFact.objects.none()

    rows = ShopDailyFact.objects.filter(
        is_current=True, report_date__gte=lower, report_date__lte=upper
    )
    if product_types:
        rows = rows.filter(product__product_type__in=product_types)
    if product_ids:
        rows = rows.filter(product__source_product_id__in=product_ids)
    if member_status:
        rows = rows.filter(member_status=member_status)
    if category_term_ids:
        rows = rows.filter(product_id__in=_product_pks_in_categories(category_term_ids))
    return rows


def _product_pks_in_categories(category_term_ids: Sequence[int]) -> list[int]:
    """Products whose **latest current** snapshot names one of these categories.

    The category test runs after `DISTINCT ON`, not before it, and the
    difference is not cosmetic: filtering first would pick the latest snapshot
    *among those in the category*, so a product moved out of Töösuhted last year
    would still be counted there forever on the strength of an old observation.
    One query either way.
    """
    allowed = set(category_term_ids)
    return [
        product_id
        for product_id, term_id in latest_snapshots().values_list("product_id", "category_term_id")
        if term_id in allowed
    ]


def get_totals(window: ComparisonWindow, **filters) -> CommerceTotals:
    """One aggregate query over the Commerce window."""
    if not window.has_commerce:
        return CommerceTotals()
    row = _facts_in_window(window, **filters).aggregate(
        orders=Sum("order_count"), units=Sum("units"), value=Sum("ordered_value_net")
    )
    return CommerceTotals(
        orders=row["orders"] or 0,
        units=row["units"] or ZERO,
        ordered_value_net=row["value"] or ZERO,
    )


def get_member_split(window: ComparisonWindow, **filters) -> dict[str, CommerceTotals]:
    """Totals per member status. Read only when the gate allows it.

    The selector always works; whether the interface may show it is
    `ShopCoverage.member_semantics_verified`, checked by the caller. Keeping the
    gate out of here means a later verification flips one flag rather than
    reinstating a query.
    """
    if not window.has_commerce:
        return {}
    rows = (
        _facts_in_window(window, **filters)
        .values("member_status")
        .annotate(orders=Sum("order_count"), units=Sum("units"), value=Sum("ordered_value_net"))
    )
    return {
        row["member_status"]: CommerceTotals(
            orders=row["orders"] or 0,
            units=row["units"] or ZERO,
            ordered_value_net=row["value"] or ZERO,
        )
        for row in rows
    }


def get_payment_split(window: ComparisonWindow, **filters) -> dict[str, CommerceTotals]:
    """Totals per payment class.

    Worth showing even while the member dimension is gated: it is the difference
    between value that settled immediately and value that was invoiced and whose
    receipt this application cannot see.
    """
    if not window.has_commerce:
        return {}
    rows = (
        _facts_in_window(window, **filters)
        .values("payment_class")
        .annotate(orders=Sum("order_count"), units=Sum("units"), value=Sum("ordered_value_net"))
    )
    return {
        row["payment_class"]: CommerceTotals(
            orders=row["orders"] or 0,
            units=row["units"] or ZERO,
            ordered_value_net=row["value"] or ZERO,
        )
        for row in rows
    }


@dataclass(frozen=True)
class MonthPoint:
    month: date
    orders: int
    units: Decimal
    ordered_value_net: Decimal


def get_monthly_series(window: ComparisonWindow, **filters) -> tuple[MonthPoint, ...]:
    """Monthly acquisitions and value, bucketed in PostgreSQL.

    Stops at Commerce coverage end by construction, because the window does.
    """
    if not window.has_commerce:
        return ()
    rows = (
        _facts_in_window(window, **filters)
        .annotate(bucket=TruncMonth("report_date"))
        .values("bucket")
        .annotate(orders=Sum("order_count"), units=Sum("units"), value=Sum("ordered_value_net"))
        .order_by("bucket")
    )
    return tuple(
        MonthPoint(
            month=row["bucket"],
            orders=row["orders"] or 0,
            units=row["units"] or ZERO,
            ordered_value_net=row["value"] or ZERO,
        )
        for row in rows
    )


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def latest_snapshots():
    """The newest current observation of every product. PostgreSQL `DISTINCT ON`.

    Catalogue metadata accumulates one row per observation date, so "the current
    title" is the latest of them rather than all of them. Doing this in the
    database keeps a ranking at a handful of queries however long the snapshot
    history grows.
    """
    return (
        ShopProductSnapshot.objects.filter(is_current=True)
        .order_by("product_id", "-observed_on")
        .distinct("product_id")
    )


def get_categories() -> tuple[tuple[int, str], ...]:
    """Every category the current catalogue names, as (term id, name).

    Read from the data rather than hard-coded: the shop's taxonomy is Koda's to
    change, and a fixed list would quietly drop a new category's products from
    the filter while still counting them in the totals.
    """
    rows = (
        latest_snapshots()
        .exclude(category_term_id__isnull=True)
        .values_list("category_term_id", "category_name")
    )
    seen: dict[int, str] = {}
    for term_id, name in rows:
        seen.setdefault(term_id, name or "")
    return tuple(sorted(seen.items(), key=lambda item: item[1].casefold()))


def paths_by_product(product_pks: Sequence[int] = ()) -> dict[int, dict[str, str]]:
    """Current paths per product pk, keyed by role. One query."""
    rows = ShopProductPage.objects.filter(is_current=True)
    if product_pks:
        rows = rows.filter(product_id__in=list(product_pks))
    mapping: dict[int, dict[str, str]] = {}
    for product_id, role, path in rows.values_list("product_id", "page_role", "path"):
        mapping.setdefault(product_id, {})[role] = path
    return mapping


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductRow:
    """One product in a ranking, with both halves of its answer."""

    source_product_id: int
    product_type: str
    title: str
    category_term_id: int | None
    category_name: str
    published: bool | None
    publicly_listed: bool | None
    product_path: str = ""
    information_path: str = ""
    event_path: str = ""
    orders: int = 0
    units: Decimal = ZERO
    ordered_value_net: Decimal = ZERO
    product_page_views: PageViewFigure | None = None
    information_page_views: PageViewFigure | None = None
    conversion_units: Decimal = ZERO

    @property
    def product_type_label(self) -> str:
        return ProductType(self.product_type).label

    @property
    def acquisitions_per_hundred(self) -> Decimal | None:
        """The rate, using the product page and the web window on both sides."""
        if self.product_page_views is None:
            return None
        return acquisitions_per_hundred(self.conversion_units, self.product_page_views.views)

    @property
    def has_information_page(self) -> bool:
        return bool(self.information_path)


@dataclass(frozen=True)
class CategoryRow:
    category_term_id: int | None
    category_name: str
    product_count: int
    orders: int
    units: Decimal
    ordered_value_net: Decimal
    product_page_views: int | None
    conversion_units: Decimal

    @property
    def acquisitions_per_hundred(self) -> Decimal | None:
        return acquisitions_per_hundred(self.conversion_units, self.product_page_views)


def _measures(rows) -> dict[int, dict]:
    """Aggregate a fact queryset by product pk. One grouped query."""
    return {
        row["product_id"]: row
        for row in rows.values("product_id").annotate(
            orders=Sum("order_count"), units=Sum("units"), value=Sum("ordered_value_net")
        )
    }


def build_product_rows(
    window: ComparisonWindow,
    *,
    product_types: Sequence[str] = (),
    category_term_ids: Sequence[int] = (),
    member_status: str = "",
    search: str = "",
) -> tuple[ProductRow, ...]:
    """Every product in scope, with Commerce measures and web figures attached.

    **Bounded queries.** Aggregation happens in PostgreSQL; what comes back into
    Python is one row per product, not one per historical fact. Page views for
    the whole population arrive in a single grouped query, so a ranking of
    twelve hundred products costs the same handful of round trips as a ranking
    of twelve.

    The whole population is returned rather than a slice, because search and
    sorting both have to run across all of it — a product ranked #347 is exactly
    the kind somebody looks up, and searching the visible rows would answer only
    for rows already on screen. The caller slices.
    """
    if not window.has_commerce:
        return ()

    filters = {
        "product_types": product_types,
        "category_term_ids": category_term_ids,
        "member_status": member_status,
    }
    commerce = _measures(_facts_in_window(window, **filters))
    # The conversion numerator reads the *web* window, never the Commerce one.
    conversion = (
        _measures(_facts_in_window(window, start=window.web_start, end=window.web_end, **filters))
        if window.has_web
        else {}
    )

    snapshots = {
        snapshot.product_id: snapshot for snapshot in latest_snapshots().select_related("product")
    }
    wanted_pks = set(commerce) | set(snapshots)
    if product_types:
        wanted_pks = {
            pk
            for pk in wanted_pks
            if pk in snapshots and snapshots[pk].product.product_type in product_types
        }
    if category_term_ids:
        allowed = set(category_term_ids)
        wanted_pks = {
            pk
            for pk in wanted_pks
            if snapshots.get(pk) and snapshots[pk].category_term_id in allowed
        }

    pages = paths_by_product(tuple(wanted_pks))
    all_paths = [path for roles in pages.values() for path in roles.values()]
    figures = page_views_in_window(all_paths, window=window)

    term = (search or "").strip().casefold()
    rows: list[ProductRow] = []
    for pk in wanted_pks:
        snapshot = snapshots.get(pk)
        if snapshot is None:
            # A fact whose product has no catalogue observation: keep it out of a
            # ranking rather than inventing a title for it. It still counts in
            # the period totals, which read the facts directly.
            continue
        roles = pages.get(pk, {})
        product_path = roles.get(PageRole.PRODUCT, "")
        information_path = roles.get(PageRole.INFORMATION, "")
        event_path = roles.get(PageRole.EVENT, "")
        measures = commerce.get(pk, {})
        product = snapshot.product

        if term:
            haystack_id = str(product.source_product_id)
            if not (
                term in snapshot.title.casefold()
                or term in product_path.casefold()
                or term in information_path.casefold()
                or term in event_path.casefold()
                or term == haystack_id
            ):
                continue

        rows.append(
            ProductRow(
                source_product_id=product.source_product_id,
                product_type=product.product_type,
                title=snapshot.title,
                category_term_id=snapshot.category_term_id,
                category_name=snapshot.category_name,
                published=snapshot.published,
                publicly_listed=snapshot.publicly_listed,
                product_path=product_path,
                information_path=information_path,
                event_path=event_path,
                orders=measures.get("orders") or 0,
                units=measures.get("units") or ZERO,
                ordered_value_net=measures.get("value") or ZERO,
                product_page_views=figures.get(product_path) if product_path else None,
                information_page_views=(
                    figures.get(information_path) if information_path else None
                ),
                conversion_units=(conversion.get(pk, {}).get("units") or ZERO),
            )
        )
    return tuple(rows)


def build_category_rows(rows: Sequence[ProductRow]) -> tuple[CategoryRow, ...]:
    """Roll product rows up by category, reusing figures already loaded.

    Deliberately derived from the product rows rather than re-queried: two
    aggregations of the same facts are two chances to disagree, and the category
    table and the product table sitting on one page must add up.
    """
    buckets: dict[tuple[int | None, str], dict] = {}
    for row in rows:
        key = (row.category_term_id, row.category_name)
        bucket = buckets.setdefault(
            key,
            {
                "product_count": 0,
                "orders": 0,
                "units": ZERO,
                "value": ZERO,
                "views": None,
                "conversion_units": ZERO,
            },
        )
        bucket["product_count"] += 1
        bucket["orders"] += row.orders
        bucket["units"] += row.units
        bucket["value"] += row.ordered_value_net
        bucket["conversion_units"] += row.conversion_units
        figure = row.product_page_views
        if figure is not None and figure.views is not None:
            bucket["views"] = (bucket["views"] or 0) + figure.views

    return tuple(
        CategoryRow(
            category_term_id=term_id,
            category_name=name,
            product_count=bucket["product_count"],
            orders=bucket["orders"],
            units=bucket["units"],
            ordered_value_net=bucket["value"],
            product_page_views=bucket["views"],
            conversion_units=bucket["conversion_units"],
        )
        for (term_id, name), bucket in buckets.items()
    )


def get_product(source_product_id: int) -> ShopProduct | None:
    return ShopProduct.objects.filter(source_product_id=source_product_id).first()


def latest_snapshot_for(product: ShopProduct) -> ShopProductSnapshot | None:
    return (
        ShopProductSnapshot.objects.filter(product=product, is_current=True)
        .order_by("-observed_on")
        .first()
    )


def catalogue_counts() -> dict[str, int]:
    """How many products the catalogue holds, per type. Aggregate only."""
    rows = latest_snapshots().values("product__product_type").annotate(total=Count("product_id"))
    return {row["product__product_type"]: row["total"] for row in rows}


__all__ = [
    "CONVERSION_BASE",
    "CategoryRow",
    "CommerceTotals",
    "ComparisonWindow",
    "MemberStatus",
    "MonthPoint",
    "PageViewFigure",
    "PaymentClass",
    "ProductRow",
    "ProductType",
    "ShopCoverage",
    "acquisitions_per_hundred",
    "build_category_rows",
    "build_product_rows",
    "catalogue_counts",
    "get_categories",
    "get_member_split",
    "get_monthly_series",
    "get_payment_split",
    "get_product",
    "get_shop_coverage",
    "get_totals",
    "latest_snapshot_for",
    "latest_snapshots",
    "page_views_in_window",
    "paths_by_product",
    "resolve_comparison",
]
