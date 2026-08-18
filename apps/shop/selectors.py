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

from django.db.models import Sum
from django.db.models.functions import TruncMonth

from apps.visibility.ga4_selectors import Coverage as Ga4Coverage
from apps.visibility.ga4_selectors import current_days, current_pages, get_coverage

from .models import (
    MemberStatus,
    PageRole,
    PaymentClass,
    ProductType,
    ShopDailyFact,
    ShopDailySummary,
    ShopProduct,
    ShopProductPage,
    ShopProductSnapshot,
    ShopSourceState,
)
from .web_effectiveness import WebAggregate, aggregate_web, denominator_path

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
    #: Which package contract published this dataset. Shown in the methodology
    #: because it decides which dimensions exist at all: a 1.0 package carries
    #: no distinct-order summaries and no free/paid classification.
    schema_version: str = ""

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
            "schema_version",
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
        schema_version=state.schema_version,
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
    """Totals per member status. Used only by one product's own detail page now
    — the overview's member breakdown left with the Ostud tab on 2026-08-18.
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
    """Totals per payment class. Used only by one product's own detail page now
    — the overview's payment breakdown left with the Ostud tab on 2026-08-18.
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
    event_page_views: PageViewFigure | None = None
    conversion_units: Decimal = ZERO
    #: Units in the equal-length window immediately before this one, when one
    #: was requested. Zero, never `None` — a product with no previous activity
    #: is the ordinary case `is_new` below is for, not an unmeasured one.
    previous_units: Decimal = ZERO

    @property
    def product_type_label(self) -> str:
        return ProductType(self.product_type).label

    @property
    def change(self) -> Decimal:
        return self.units - self.previous_units

    @property
    def is_new(self) -> bool:
        """Nothing before, something now — which has no percentage."""
        return self.previous_units == 0 and self.units > 0

    @property
    def percentage_change(self) -> Decimal | None:
        if self.previous_units == 0:
            return None
        return ((self.units - self.previous_units) / self.previous_units * 100).quantize(
            Decimal("1")
        )

    @property
    def denominator_path(self) -> str:
        """The page whose views may divide this product's acquisitions.

        Decided by `web_effectiveness`, which is the only module that knows a
        template is sold from its product page and a registration from its event
        page. Empty when this product has no such page — never another role's.
        """
        return denominator_path(
            self.product_type,
            {
                PageRole.PRODUCT: self.product_path,
                PageRole.INFORMATION: self.information_path,
                PageRole.EVENT: self.event_path,
            },
        )

    @property
    def denominator_page_views(self) -> PageViewFigure | None:
        """Views for the acquisition page, whichever page that is for this type."""
        path = self.denominator_path
        if not path:
            return None
        if path == self.product_path:
            return self.product_page_views
        if path == self.event_path:
            return self.event_page_views
        if path == self.information_path:
            return self.information_page_views
        return None

    @property
    def acquisitions_per_hundred(self) -> Decimal | None:
        """The rate, over the acquisition page and the web window on both sides.

        An event registration divides by its event page, a template by its
        product page. A product with no acquisition page has no rate at all —
        `None`, never zero, and never the information page as a stand-in.
        """
        figure = self.denominator_page_views
        if figure is None:
            return None
        return acquisitions_per_hundred(self.conversion_units, figure.views)

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


@dataclass(frozen=True)
class TopProduct:
    """One product and what it acquired, for a caller that wants only the top few.

    Deliberately thinner than `ProductRow`: no web figures, no conversion rate,
    no category. The overview's E-pood panel names the leading product and says
    how many units it moved, and every other field on `ProductRow` costs a query
    that panel would not read.
    """

    source_product_id: int
    title: str
    product_type: str
    units: Decimal = ZERO

    @property
    def product_type_label(self) -> str:
        return ProductType(self.product_type).label


def get_top_products(window: ComparisonWindow, *, limit: int = 5, **filters):
    """The most-acquired products of a window, ranked and sliced in PostgreSQL.

    `build_product_rows` returns the whole population on purpose — search and
    sorting run across all of it — but a caller that wants three rows should not
    pay for twelve hundred. This orders and limits in the database, then names
    the survivors from `latest_snapshots`, so the cost is two queries whatever
    the catalogue holds.

    Ordering breaks ties on `source_product_id` so the ranking is stable between
    requests; two products on equal units would otherwise swap places between
    page loads.
    """
    if not window.has_commerce:
        return ()

    ranked = list(
        _facts_in_window(window, **filters)
        .values("product_id")
        .annotate(units=Sum("units"))
        .filter(units__gt=0)
        .order_by("-units", "product_id")[:limit]
    )
    if not ranked:
        return ()

    product_pks = [row["product_id"] for row in ranked]
    # `select_related`, because the type and the source id live on the product
    # and a per-row lazy load would be an N+1 over the slice.
    named = {
        snapshot.product_id: snapshot
        for snapshot in latest_snapshots()
        .filter(product_id__in=product_pks)
        .select_related("product")
    }
    rows = []
    for row in ranked:
        snapshot = named.get(row["product_id"])
        if snapshot is None:
            # A fact whose product has no current snapshot cannot be named, and
            # a row titled with a database id is not a product anybody
            # recognises. Skipped rather than shown nameless.
            continue
        rows.append(
            TopProduct(
                source_product_id=snapshot.product.source_product_id,
                title=snapshot.title,
                product_type=snapshot.product.product_type,
                units=row["units"] or ZERO,
            )
        )
    return tuple(rows)


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
    previous_start: date | None = None,
    previous_end: date | None = None,
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

    `previous_start`/`previous_end` are optional and, when given, attach each
    row's units from the equal-length window before this one — the same grouped
    query `get_product_movers` already runs, joined here so a table of every
    product can state a change without a second round trip per row.
    """
    if not window.has_commerce:
        return ()

    filters = {
        "product_types": product_types,
        "category_term_ids": category_term_ids,
        "member_status": member_status,
    }
    commerce = _measures(_facts_in_window(window, **filters))
    previous_units = _units_by_product(previous_start, previous_end, **filters)
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
                event_page_views=figures.get(event_path) if event_path else None,
                conversion_units=(conversion.get(pk, {}).get("units") or ZERO),
                previous_units=previous_units.get(pk, ZERO),
            )
        )
    return tuple(rows)


def build_category_rows(rows: Sequence[ProductRow]) -> tuple[CategoryRow, ...]:
    """Roll product rows up by category, reusing figures already loaded.

    Deliberately derived from the product rows rather than re-queried: two
    aggregations of the same facts are two chances to disagree, and the category
    table and the product table sitting on one page must add up.

    Views are aggregated over **unique acquisition paths** through
    `web_effectiveness.aggregate_web`, not summed per product. Two products
    sharing one event page contribute that page's traffic once; adding each
    row's own figure would count it twice, and would do so precisely for the
    products most likely to share a page.
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
                "rows": [],
            },
        )
        bucket["product_count"] += 1
        bucket["orders"] += row.orders
        bucket["units"] += row.units
        bucket["value"] += row.ordered_value_net
        bucket["rows"].append(row)

    for bucket in buckets.values():
        web = aggregate_web(bucket["rows"])
        bucket["views"] = web.views
        bucket["conversion_units"] = web.units

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


# ---------------------------------------------------------------------------
# Distinct orders, and the free/paid mix
# ---------------------------------------------------------------------------


def distinct_orders_supported(
    *, category_term_ids: Sequence[int] = (), member_status: str = "", search: str = ""
) -> bool:
    """Whether `ShopDailySummary` can answer the population now on screen.

    The summary grain is **day × product type**, and nothing else. It carries no
    category, no member status and no product identity, because an order
    routinely spans categories and a per-category distinct count could not be
    summed into anything.

    So the moment a reader narrows past that grain, the stored distinct-order
    total stops describing what the other figures describe. Showing it anyway is
    the specific error this guard exists to prevent: a category-filtered page
    would put the whole type's order count beside that category's units and
    value, and nothing on screen would say the two came from different
    populations.

    When this returns false the caller counts order **lines** instead and
    relabels the figure, which is a smaller number that is actually about the
    selected products.
    """
    return not (category_term_ids or member_status or search)


def get_distinct_orders(
    *, start: date | None, end: date | None, product_type: str = ""
) -> int | None:
    """How many distinct Commerce orders a window carried.

    `None` when the imported dataset cannot answer — a package published under
    schema 1.0 carries no order counts at all, and the interface then falls back
    to counting order **lines** and says which it is showing. Zero would be a
    different and false claim.

    Summing across days is safe because an order belongs to exactly one day.
    Summing across product types is **not**, which is why a blank-type row
    exists and is what an unfiltered call reads.
    """
    if start is None or end is None:
        return None
    rows = ShopDailySummary.objects.filter(
        is_current=True,
        report_date__gte=start,
        report_date__lte=end,
        product_type=product_type or ShopDailySummary.ALL_TYPES,
    )
    total = rows.aggregate(total=Sum("distinct_order_count"))["total"]
    if total is not None:
        return total
    # Nothing for this window: distinguish "no summaries exist at all" from
    # "this window genuinely had none".
    return 0 if ShopDailySummary.objects.filter(is_current=True).exists() else None


def get_monthly_distinct_orders(
    *, start: date | None, end: date | None, product_type: str = ""
) -> tuple[tuple[date, int], ...]:
    """Distinct orders per month, for the trend's order series.

    Reads the same `ShopDailySummary` rows `get_distinct_orders` totals, bucketed
    in PostgreSQL. Summing across days is safe because an order belongs to one
    day; summing across product types is not, which is why this reads exactly one
    type row — the blank one when no type is selected.

    Empty when the source published no summaries, which the caller reads as "draw
    order lines instead" rather than as a flat zero line.
    """
    if start is None or end is None:
        return ()
    rows = (
        ShopDailySummary.objects.filter(
            is_current=True,
            report_date__gte=start,
            report_date__lte=end,
            product_type=product_type or ShopDailySummary.ALL_TYPES,
        )
        .annotate(bucket=TruncMonth("report_date"))
        .values("bucket")
        .annotate(total=Sum("distinct_order_count"))
        .order_by("bucket")
    )
    return tuple((row["bucket"], row["total"] or 0) for row in rows)


@dataclass(frozen=True)
class MixBreakdown:
    """How units divide between free, paid and unclassified."""

    free: Decimal | None = None
    paid: Decimal | None = None
    unknown: Decimal | None = None

    @property
    def is_known(self) -> bool:
        return self.free is not None and self.paid is not None

    @property
    def total(self) -> Decimal:
        return (self.free or ZERO) + (self.paid or ZERO) + (self.unknown or ZERO)

    @property
    def free_share(self) -> Decimal | None:
        """Free units as a percentage of the units that were classified.

        The denominator excludes the unclassified remainder on purpose: a share
        of a total that includes "we do not know" is a share of nothing in
        particular.
        """
        if not self.is_known:
            return None
        classified = (self.free or ZERO) + (self.paid or ZERO)
        if classified <= 0:
            return None
        return (self.free / classified * 100).quantize(Decimal("0.1"))


def get_free_paid_split(window: ComparisonWindow, **filters) -> MixBreakdown:
    """The free/paid mix over the Commerce window, or an unknown one."""
    if not window.has_commerce:
        return MixBreakdown()
    row = _facts_in_window(window, **filters).aggregate(
        free=Sum("free_units"), paid=Sum("paid_units"), unknown=Sum("unknown_units")
    )
    if row["free"] is None and row["paid"] is None:
        return MixBreakdown()
    return MixBreakdown(
        free=row["free"] or ZERO, paid=row["paid"] or ZERO, unknown=row["unknown"] or ZERO
    )


# ---------------------------------------------------------------------------
# Movers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MoverRow:
    """One product's change in acquired units between two equal windows."""

    source_product_id: int
    title: str
    category_name: str
    current_units: Decimal
    previous_units: Decimal

    @property
    def change(self) -> Decimal:
        return self.current_units - self.previous_units

    @property
    def is_new(self) -> bool:
        """Nothing before, something now — which has no percentage."""
        return self.previous_units == 0 and self.current_units > 0

    @property
    def percentage_change(self) -> Decimal | None:
        if self.previous_units == 0:
            return None
        return ((self.change) / self.previous_units * 100).quantize(Decimal("1"))


def _units_by_product(start: date | None, end: date | None, **filters) -> dict[int, Decimal]:
    if start is None or end is None:
        return {}
    rows = ShopDailyFact.objects.filter(
        is_current=True, report_date__gte=start, report_date__lte=end
    )
    product_types = filters.get("product_types") or ()
    if product_types:
        rows = rows.filter(product__product_type__in=product_types)
    category_term_ids = filters.get("category_term_ids") or ()
    if category_term_ids:
        rows = rows.filter(product_id__in=_product_pks_in_categories(category_term_ids))
    return {
        row["product_id"]: row["units"] or ZERO
        for row in rows.values("product_id").annotate(units=Sum("units"))
    }


def get_product_movers(
    *,
    current_start: date | None,
    current_end: date | None,
    previous_start: date | None,
    previous_end: date | None,
    limit: int = 5,
    **filters,
) -> tuple[tuple[MoverRow, ...], tuple[MoverRow, ...]]:
    """The products that rose and fell most, by **absolute** unit change.

    Ranked on the absolute change rather than the percentage on purpose: a
    product that went from one unit to four has grown 300% and moved three
    units, and a list ordered by percentage is a list of the smallest products
    in the catalogue. The percentage is offered beside the number, not instead
    of it.

    Two grouped queries, whatever the catalogue size.
    """
    if previous_start is None or previous_end is None:
        return (), ()

    current = _units_by_product(current_start, current_end, **filters)
    previous = _units_by_product(previous_start, previous_end, **filters)
    if not current and not previous:
        return (), ()

    snapshots = {
        snapshot.product_id: snapshot for snapshot in latest_snapshots().select_related("product")
    }
    rows: list[MoverRow] = []
    for pk in set(current) | set(previous):
        snapshot = snapshots.get(pk)
        if snapshot is None:
            continue
        rows.append(
            MoverRow(
                source_product_id=snapshot.product.source_product_id,
                title=snapshot.title,
                category_name=snapshot.category_name,
                current_units=current.get(pk, ZERO),
                previous_units=previous.get(pk, ZERO),
            )
        )

    risers = sorted([r for r in rows if r.change > 0], key=lambda r: (-r.change, r.title))
    fallers = sorted([r for r in rows if r.change < 0], key=lambda r: (r.change, r.title))
    return tuple(risers[:limit]), tuple(fallers[:limit])


# ---------------------------------------------------------------------------
# Web effectiveness
#: The share of demand the long-tail statistic is measured against. A round
#: number chosen for being conventional and legible rather than derived: the
#: statement is "how few products carry most of the shop", and eighty per cent
#: is what a reader already understands that to mean.
LONG_TAIL_SHARE = Decimal("80")


@dataclass(frozen=True)
class Concentration:
    """How few products account for most of a population.

    Two statistics rather than one because they answer different questions. The
    top-ten share says how top-heavy the shop is; the long-tail count says how
    many products a reader would have to care about to cover most of it.

    Neither is good or bad. A catalogue of contract templates is *expected* to
    be concentrated, and the figure is offered as a description rather than as a
    verdict.
    """

    top_share: Decimal | None = None
    top_n: int = 10
    long_tail_count: int | None = None
    long_tail_share: Decimal = LONG_TAIL_SHARE
    population: int = 0

    @property
    def has_top_share(self) -> bool:
        return self.top_share is not None

    @property
    def has_long_tail(self) -> bool:
        return self.long_tail_count is not None


def _amounts(rows: Sequence[ProductRow], *, by_value: bool) -> list[Decimal]:
    return sorted(
        ((row.ordered_value_net if by_value else row.units) for row in rows), reverse=True
    )


def long_tail_count(rows: Sequence[ProductRow], *, by_value: bool = False) -> int | None:
    """How many products it takes to reach `LONG_TAIL_SHARE` of the total.

    `None` when the total is zero — there is no such thing as 80% of nothing —
    and counted from the largest product down, so the answer is the smallest
    number of products that together cross the threshold.
    """
    amounts = [amount for amount in _amounts(rows, by_value=by_value) if amount > 0]
    total = sum(amounts, ZERO)
    if total <= 0:
        return None
    target = total * LONG_TAIL_SHARE / 100
    running = ZERO
    for index, amount in enumerate(amounts, start=1):
        running += amount
        if running >= target:
            return index
    return len(amounts)


def get_concentration(
    rows: Sequence[ProductRow], *, top: int = 10, by_value: bool = False
) -> Concentration:
    """Both concentration statistics for one population, computed once."""
    return Concentration(
        top_share=concentration_share(rows, top=top, by_value=by_value),
        top_n=top,
        long_tail_count=long_tail_count(rows, by_value=by_value),
        population=len(rows),
    )


def concentration_share(
    rows: Sequence[ProductRow], *, top: int = 10, by_value: bool = False
) -> Decimal | None:
    """What share of acquired units the largest `top` products account for.

    `None` when the population is too small for the statement to mean anything:
    "the top 10 of 9 products are 100%" is arithmetic, not insight.
    """
    amounts = _amounts(rows, by_value=by_value)
    total = sum(amounts, ZERO)
    if len(amounts) <= top or total <= 0:
        return None
    return (sum(amounts[:top], ZERO) / total * 100).quantize(Decimal("1"))


def get_product(source_product_id: int) -> ShopProduct | None:
    return ShopProduct.objects.filter(source_product_id=source_product_id).first()


def latest_snapshot_for(product: ShopProduct) -> ShopProductSnapshot | None:
    return (
        ShopProductSnapshot.objects.filter(product=product, is_current=True)
        .order_by("-observed_on")
        .first()
    )


__all__ = [
    "CONVERSION_BASE",
    "LONG_TAIL_SHARE",
    "CategoryRow",
    "CommerceTotals",
    "ComparisonWindow",
    "Concentration",
    "MixBreakdown",
    "MoverRow",
    "WebAggregate",
    "aggregate_web",
    "concentration_share",
    "denominator_path",
    "distinct_orders_supported",
    "get_concentration",
    "get_distinct_orders",
    "get_monthly_distinct_orders",
    "long_tail_count",
    "get_free_paid_split",
    "get_product_movers",
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
    "get_categories",
    "get_monthly_series",
    "get_product",
    "get_member_split",
    "get_payment_split",
    "get_shop_coverage",
    "get_totals",
    "latest_snapshot_for",
    "latest_snapshots",
    "page_views_in_window",
    "paths_by_product",
    "resolve_comparison",
]
