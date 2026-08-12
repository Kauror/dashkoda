"""Assembling the E-pood pages, so the templates hold no arithmetic.

Everything a template needs arrives pre-decided: which window each half of the
answer used, whether a figure is known or merely absent, which controls carry
which state, and whether a gated dimension may be shown at all. A template that
computes a rate is a template that can disagree with the selector that computed
the same rate elsewhere.

Two disclosures are built here rather than in the markup, because both are
statements about truth rather than about layout:

- **the as-of line.** The dataset is a manual export that stops on a stated day,
  so every page says so, generated from `ShopSourceState` and never hard-coded;
- **the comparison interval.** Whenever the web window is narrower than the
  Commerce window — which is the ordinary case, GA4 starting two and a half
  years after Commerce — the page states the interval the web figures actually
  cover. A reader is never left to infer it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from apps.core.formatting import euros, group_thousands, long_date
from apps.dashboard.sparkline import TrendSource, build_trend_chart

from .comparison import FLAT, MetricComparison, derive_period_pair
from .models import MemberStatus, PageRole, PaymentClass, ProductType
from .periods import (
    PARAM_CATEGORY,
    PARAM_MEMBER,
    PARAM_SEARCH,
    PARAM_TYPE,
    SORT_COLUMNS,
    SORT_CONVERSION,
    SORT_LABELS,
    SORT_TITLE,
    SORT_UNITS,
    SORT_VALUE,
    SORT_VIEWS,
    build_query,
    parse_int_list,
    parse_page,
    parse_search,
    parse_sort,
    period_options,
    resolve_period,
)
from .selectors import (
    MIN_VIEWS_FOR_OPPORTUNITY,
    CategoryRow,
    ComparisonWindow,
    ProductRow,
    acquisitions_per_hundred,
    build_category_rows,
    build_product_rows,
    concentration_share,
    get_categories,
    get_distinct_orders,
    get_free_paid_split,
    get_member_split,
    get_monthly_series,
    get_payment_split,
    get_product,
    get_product_movers,
    get_shop_coverage,
    get_totals,
    get_web_opportunities,
    latest_snapshot_for,
    paths_by_product,
    resolve_comparison,
)

#: How many products a ranking page shows. Deliberately a page rather than a
#: Top 20: the shop question "which of our products are ignored" needs the
#: bottom of the list as much as the top.
PAGE_SIZE = 25

#: The public site, for the outbound links beside a product.
PUBLIC_BASE = "https://www.koda.ee"

DASH = "—"


def _figure(value: int | None) -> str:
    """A measured count, or a dash. Never a zero standing in for an absence."""
    return group_thousands(value) if value is not None else DASH


def _rate(value: Decimal | None) -> str:
    if value is None:
        return DASH
    return f"{value}".replace(".", ",")


@dataclass(frozen=True)
class TypeOption:
    key: str
    label: str
    is_active: bool
    query: str


@dataclass(frozen=True)
class CategoryOption:
    term_id: int
    label: str
    is_active: bool
    query: str


@dataclass(frozen=True)
class SortOption:
    key: str
    label: str
    is_active: bool
    query: str


@dataclass(frozen=True)
class ProductPresenter:
    """One ranking row, already formatted."""

    row: ProductRow

    @property
    def source_product_id(self) -> int:
        return self.row.source_product_id

    @property
    def title(self) -> str:
        return self.row.title

    @property
    def category_name(self) -> str:
        return self.row.category_name or DASH

    @property
    def type_label(self) -> str:
        return self.row.product_type_label

    @property
    def orders(self) -> str:
        return group_thousands(self.row.orders)

    @property
    def units(self) -> str:
        return group_thousands(int(self.row.units))

    @property
    def value(self) -> str:
        return euros(self.row.ordered_value_net)

    @property
    def product_views(self) -> str:
        figure = self.row.product_page_views
        return _figure(figure.views if figure else None)

    @property
    def information_views(self) -> str:
        figure = self.row.information_page_views
        return _figure(figure.views if figure else None)

    @property
    def has_information_page(self) -> bool:
        return self.row.has_information_page

    @property
    def rate(self) -> str:
        return _rate(self.row.acquisitions_per_hundred)

    @property
    def public_url(self) -> str:
        path = self.row.product_path or self.row.event_path
        return f"{PUBLIC_BASE}{path}" if path else ""

    @property
    def information_url(self) -> str:
        return f"{PUBLIC_BASE}{self.row.information_path}" if self.row.information_path else ""


@dataclass(frozen=True)
class CategoryPresenter:
    row: CategoryRow

    @property
    def name(self) -> str:
        return self.row.category_name or "Kategooriata"

    @property
    def product_count(self) -> str:
        return group_thousands(self.row.product_count)

    @property
    def orders(self) -> str:
        return group_thousands(self.row.orders)

    @property
    def units(self) -> str:
        return group_thousands(int(self.row.units))

    @property
    def value(self) -> str:
        return euros(self.row.ordered_value_net)

    @property
    def views(self) -> str:
        return _figure(self.row.product_page_views)

    @property
    def rate(self) -> str:
        return _rate(self.row.acquisitions_per_hundred)


def _sort_key(sort: str):
    """How a ranking is ordered, with unknown figures kept last.

    A product nobody measured must not win an ordering by views, and must not
    be mistaken for a product measured at zero: it sorts behind both.
    """
    if sort == SORT_VALUE:
        return lambda row: (-row.ordered_value_net, row.title.casefold())
    if sort == SORT_VIEWS:
        return lambda row: (
            0 if (row.product_page_views and row.product_page_views.views is not None) else 1,
            -((row.product_page_views.views if row.product_page_views else 0) or 0),
            row.title.casefold(),
        )
    if sort == SORT_CONVERSION:
        return lambda row: (
            0 if row.acquisitions_per_hundred is not None else 1,
            -(row.acquisitions_per_hundred or Decimal(0)),
            row.title.casefold(),
        )
    if sort == SORT_TITLE:
        return lambda row: (row.title.casefold(),)
    return lambda row: (-row.units, -row.ordered_value_net, row.title.casefold())


@dataclass(frozen=True)
class KpiCard:
    """One headline figure and how it moved, already formatted.

    The template renders what it is given. Every decision a delta needs — is
    there a comparison at all, is this new, is a percentage meaningful — was
    made in `MetricComparison` and is spelled out here as strings and flags.
    """

    label: str
    value: str
    unit: str = ""
    secondary: str = ""
    change_label: str = ""
    change_direction: str = FLAT
    comparison_label: str = ""
    is_available: bool = False
    is_new: bool = False

    @property
    def has_change(self) -> bool:
        return bool(self.change_label)


@dataclass(frozen=True)
class MoverPresenter:
    row: object

    @property
    def title(self) -> str:
        return self.row.title

    @property
    def category_name(self) -> str:
        return self.row.category_name or ""

    @property
    def source_product_id(self) -> int:
        return self.row.source_product_id

    @property
    def change_label(self) -> str:
        change = int(self.row.change)
        return f"{change:+d}".replace("-", "−")

    @property
    def context(self) -> str:
        """`Uus` where there is no percentage, otherwise a quiet one."""
        if self.row.is_new:
            return "uus"
        percentage = self.row.percentage_change
        return "" if percentage is None else f"{int(percentage):+d}%".replace("-", "−")


@dataclass(frozen=True)
class RankingBar:
    """One row of the horizontal ranking, with its bar already proportioned."""

    source_product_id: int
    title: str
    category_name: str
    value: str
    #: 0–100, a width on the chart's own viewBox. Geometry, never a style.
    width: float


@dataclass(frozen=True)
class OpportunityPresenter:
    row: object

    @property
    def title(self) -> str:
        return self.row.title

    @property
    def source_product_id(self) -> int:
        return self.row.source_product_id

    @property
    def views(self) -> str:
        return group_thousands(self.row.views)

    @property
    def units(self) -> str:
        return group_thousands(int(self.row.units))

    @property
    def rate(self) -> str:
        return _rate(self.row.rate)


@dataclass(frozen=True)
class MixPresenter:
    """The free/paid split as one part-to-whole bar."""

    is_known: bool = False
    free_units: str = DASH
    paid_units: str = DASH
    unknown_units: str = DASH
    free_percent: float = 0.0
    paid_percent: float = 0.0
    free_label: str = ""
    paid_label: str = ""
    previous_note: str = ""
    has_unknown: bool = False


@dataclass(frozen=True)
class ShopOverview:
    """Everything the E-pood overview renders."""

    has_source: bool
    as_of_label: str
    coverage_label: str
    window: ComparisonWindow
    web_interval_label: str
    web_is_partial: bool

    period: object = None
    periods: tuple = ()
    type_options: tuple[TypeOption, ...] = ()
    category_options: tuple[CategoryOption, ...] = ()
    sort_options: tuple[SortOption, ...] = ()

    orders: str = DASH
    units: str = DASH
    value: str = DASH
    product_views: str = DASH
    rate: str = DASH

    invoice_value: str = DASH
    settled_value: str = DASH

    member_gate_open: bool = False
    listing_gate_open: bool = False

    months: tuple = ()
    max_month_units: int = 0
    categories: tuple[CategoryPresenter, ...] = ()
    products: tuple[ProductPresenter, ...] = ()

    search: str = ""
    sort: str = SORT_UNITS
    page: int = 1
    total_pages: int = 1
    total_products: int = 0
    result_summary: str = ""
    previous_query: str = ""
    next_query: str = ""

    # --- the redesign ----------------------------------------------------
    #: The three headline figures, already compared.
    kpis: tuple[KpiCard, ...] = ()
    #: The web pair, kept apart from the headline so the GA4 caveats do not have
    #: to be explained beside the Commerce figures.
    web_kpis: tuple[KpiCard, ...] = ()
    comparison_label: str = ""
    comparison_available: bool = False
    #: Whether the order figure counts distinct orders or order lines.
    orders_are_distinct: bool = False
    trend: object = None
    trend_metric: str = ""
    trend_options: tuple = ()
    risers: tuple[MoverPresenter, ...] = ()
    fallers: tuple[MoverPresenter, ...] = ()
    ranking: tuple[RankingBar, ...] = ()
    ranking_note: str = ""
    mix: MixPresenter = field(default_factory=MixPresenter)
    weak_acquisition: tuple[OpportunityPresenter, ...] = ()
    strong_acquisition: tuple[OpportunityPresenter, ...] = ()
    opportunity_threshold: int = 0
    period_options_primary: tuple = ()
    period_options_secondary: tuple = ()

    @property
    def has_rows(self) -> bool:
        return bool(self.products)

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages


def _mix_presenter(mix, previous) -> MixPresenter:
    """The free/paid bar, or an honest unknown.

    Percentages are of the **classified** units, matching `MixBreakdown`, so the
    two segments always fill the bar and the unclassified remainder is stated
    separately rather than silently widening one side.
    """
    if not mix.is_known or mix.free_share is None:
        return MixPresenter()
    free_share = float(mix.free_share)
    note = ""
    if previous is not None and previous.free_share is not None:
        note = (
            f"Eelmisel perioodil oli tasuta soetuste osakaal {previous.free_share:.0f}%.".replace(
                ".", ","
            )
        )
    return MixPresenter(
        is_known=True,
        free_units=group_thousands(int(mix.free)),
        paid_units=group_thousands(int(mix.paid)),
        unknown_units=group_thousands(int(mix.unknown or 0)),
        free_percent=free_share,
        paid_percent=100.0 - free_share,
        free_label=f"Tasuta {free_share:.0f}%".replace(".", ","),
        paid_label=f"Tasuline {100 - free_share:.0f}%".replace(".", ","),
        previous_note=note,
        has_unknown=bool(mix.unknown and mix.unknown > 0),
    )


def _change_label(comparison: MetricComparison, *, money: bool = False) -> str:
    """The delta as a reader sees it, or nothing at all.

    Percentage where one exists, `uus` where the previous period was empty, and
    an empty string where no comparison was available — which the card renders
    as no delta line rather than as a zero.
    """
    if not comparison.is_available:
        return ""
    if comparison.is_new:
        return "uus"
    percentage = comparison.percentage_change
    if percentage is None:
        return ""
    return f"{percentage:+.1f}%".replace("-", "−").replace(".", ",")


def _kpi(
    label: str,
    comparison: MetricComparison,
    *,
    formatter,
    unit: str = "",
    secondary: str = "",
    comparison_label: str = "",
) -> KpiCard:
    return KpiCard(
        label=label,
        value=formatter(comparison.current),
        unit=unit,
        secondary=secondary,
        change_label=_change_label(comparison),
        change_direction=comparison.direction,
        comparison_label=comparison_label,
        is_available=comparison.is_available,
        is_new=comparison.is_new,
    )


def _trend_chart(current, previous, *, label: str, previous_label: str, offset_days: int):
    """Current and previous series on one pair of axes.

    The previous series is **shifted forward** onto the current window's dates.
    `build_trend_chart` positions a point by its date, so plotting the real
    previous dates would spread the drawing across both windows and put the two
    lines side by side rather than on top of each other. Aligning them is what
    makes the comparison readable; the line's own label carries the real
    previous interval so the tooltip's date cannot be mistaken for it.
    """
    sources = [TrendSource(label=label, style="solid", source="", series=current)]
    if previous:
        shifted = [(when + timedelta(days=offset_days), value) for when, value in previous]
        sources.append(
            TrendSource(
                label=f"Eelmine periood ({previous_label})",
                style="dashed",
                source="",
                series=shifted,
            )
        )
    return build_trend_chart(sources)


def build_overview(
    *,
    period_key: str | None,
    date_from: str | None,
    date_to: str | None,
    product_type: str,
    categories: tuple[int, ...],
    member_status: str,
    search: str,
    sort: str,
    page: int,
) -> ShopOverview:
    coverage = get_shop_coverage()
    if not coverage.has_data:
        return ShopOverview(
            has_source=False,
            as_of_label="",
            coverage_label="",
            window=ComparisonWindow(None, None, None, None),
            web_interval_label="",
            web_is_partial=False,
        )

    resolved = resolve_period(period_key, date_from, date_to, anchor=coverage.coverage_end)
    window = resolve_comparison(start=resolved.start, end=resolved.end, shop=coverage)

    # The member control is built but withheld: the source has not established
    # that its member flag describes the moment of the transaction rather than
    # the customer's standing today.
    member_gate_open = coverage.member_semantics_verified
    effective_member = member_status if member_gate_open else ""

    filters = {
        "product_types": (product_type,) if product_type else (),
        "category_term_ids": categories,
        "member_status": effective_member,
    }

    totals = get_totals(window, **filters)
    payments = get_payment_split(window, **filters)
    rows = build_product_rows(window, search=search, **filters)
    ordered = sorted(rows, key=_sort_key(sort))

    total_products = len(ordered)
    total_pages = max(1, -(-total_products // PAGE_SIZE))
    page = min(max(page, 1), total_pages)
    visible = ordered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

    category_rows = sorted(
        build_category_rows(rows), key=lambda row: (-row.units, row.category_name.casefold())
    )

    months = get_monthly_series(window, **filters)
    max_units = max((int(point.units) for point in months), default=0)

    # The period total's own product-page views, summed from the rows already
    # loaded rather than re-queried, so the headline and the table agree.
    known_views = [
        row.product_page_views.views
        for row in rows
        if row.product_page_views is not None and row.product_page_views.views is not None
    ]
    total_views = sum(known_views) if known_views else None
    conversion_units = sum((row.conversion_units for row in rows), Decimal(0))

    # --- the comparison, and everything derived from it -------------------
    pair = derive_period_pair(
        current_start=window.commerce_start,
        current_end=window.commerce_end,
        coverage_start=coverage.coverage_start,
    )
    previous_window = (
        ComparisonWindow(pair.previous_start, pair.previous_end, None, None)
        if pair.is_available
        else ComparisonWindow(None, None, None, None)
    )
    previous_totals = get_totals(previous_window, **filters) if pair.is_available else None

    # Distinct orders where the source can answer, order lines where it cannot.
    current_orders = get_distinct_orders(
        start=window.commerce_start, end=window.commerce_end, product_type=product_type
    )
    orders_are_distinct = current_orders is not None
    previous_orders = (
        get_distinct_orders(
            start=pair.previous_start, end=pair.previous_end, product_type=product_type
        )
        if pair.is_available and orders_are_distinct
        else (previous_totals.orders if previous_totals else None)
    )
    if not orders_are_distinct:
        current_orders = totals.orders

    units_cmp = MetricComparison.of(
        totals.units, previous_totals.units if previous_totals else None, period=pair
    )
    orders_cmp = MetricComparison.of(current_orders, previous_orders, period=pair)
    value_cmp = MetricComparison.of(
        totals.ordered_value_net,
        previous_totals.ordered_value_net if previous_totals else None,
        period=pair,
    )

    mix = get_free_paid_split(window, **filters)
    previous_mix = get_free_paid_split(previous_window, **filters) if pair.is_available else None

    kpis = (
        _kpi("Soetatud", units_cmp, formatter=lambda v: group_thousands(int(v)), unit="ühikut"),
        _kpi(
            "Tellimused" if orders_are_distinct else "Tellimusridu",
            orders_cmp,
            formatter=lambda v: group_thousands(int(v)),
            secondary=("" if orders_are_distinct else "eri tellimuste arv ei ole imporditud"),
        ),
        _kpi(
            "Tellitud väärtus",
            value_cmp,
            formatter=euros,
            unit="KM-ta",
            secondary=(
                f"{mix.free_share:.0f}% soetustest tasuta".replace(".", ",")
                if mix.free_share is not None
                else ""
            ),
        ),
    )

    risers, fallers = get_product_movers(
        current_start=window.commerce_start,
        current_end=window.commerce_end,
        previous_start=pair.previous_start,
        previous_end=pair.previous_end,
        **filters,
    )

    # Ranking: the products already loaded, largest first, bars proportioned here.
    ranked = sorted(rows, key=lambda r: (-r.units, r.title.casefold()))[:10]
    largest = max((r.units for r in ranked), default=Decimal(0))
    ranking = tuple(
        RankingBar(
            source_product_id=r.source_product_id,
            title=r.title,
            category_name=r.category_name,
            value=group_thousands(int(r.units)),
            width=float(r.units / largest * 100) if largest > 0 else 0.0,
        )
        for r in ranked
    )
    share = concentration_share(rows, top=10)

    weak, strong = get_web_opportunities(rows)

    trend = _trend_chart(
        [(point.month, float(point.units)) for point in months],
        (
            [
                (point.month, float(point.units))
                for point in get_monthly_series(previous_window, **filters)
            ]
            if pair.is_available
            else []
        ),
        label="Soetatud",
        previous_label=pair.previous_label,
        offset_days=pair.length_days,
    )

    state = {
        "product_type": product_type,
        "categories": categories,
        "search": search,
        "sort": sort if sort != SORT_UNITS else "",
        "member_status": effective_member,
    }

    return ShopOverview(
        has_source=True,
        as_of_label=long_date(coverage.source_as_of),
        coverage_label=(f"{long_date(coverage.coverage_start)}–{long_date(coverage.coverage_end)}"),
        window=window,
        web_interval_label=(
            f"{long_date(window.web_start)}–{long_date(window.web_end)}" if window.has_web else ""
        ),
        web_is_partial=window.web_is_partial,
        period=resolved,
        periods=period_options(resolved, **state),
        type_options=_type_options(product_type, resolved, state),
        category_options=_category_options(categories, resolved, state),
        sort_options=_sort_options(sort, resolved, state),
        orders=group_thousands(totals.orders),
        units=group_thousands(int(totals.units)),
        value=euros(totals.ordered_value_net),
        product_views=_figure(total_views),
        rate=_rate(acquisitions_per_hundred(conversion_units, total_views)),
        invoice_value=euros(
            payments.get(PaymentClass.INVOICE).ordered_value_net
            if payments.get(PaymentClass.INVOICE)
            else Decimal(0)
        ),
        settled_value=euros(
            payments.get(PaymentClass.BANK_OR_CARD).ordered_value_net
            if payments.get(PaymentClass.BANK_OR_CARD)
            else Decimal(0)
        ),
        member_gate_open=member_gate_open,
        listing_gate_open=coverage.public_listing_semantics_verified,
        kpis=kpis,
        web_kpis=(
            _kpi(
                "Tootelehe vaatamised",
                MetricComparison.of(total_views or 0, None),
                formatter=lambda v: _figure(total_views),
                comparison_label=(
                    f"{long_date(window.web_start)}–{long_date(window.web_end)}"
                    if window.has_web
                    else ""
                ),
            ),
            _kpi(
                "Soetusi / 100 vaatamist",
                MetricComparison.of(0, None),
                formatter=lambda v: _rate(acquisitions_per_hundred(conversion_units, total_views)),
            ),
        )
        if window.has_web
        else (),
        comparison_label=pair.previous_label,
        comparison_available=pair.is_available,
        orders_are_distinct=orders_are_distinct,
        trend=trend,
        risers=tuple(MoverPresenter(row) for row in risers),
        fallers=tuple(MoverPresenter(row) for row in fallers),
        ranking=ranking,
        ranking_note=(f"Top 10 moodustavad {share}% soetustest." if share is not None else ""),
        mix=_mix_presenter(mix, previous_mix),
        weak_acquisition=tuple(OpportunityPresenter(row) for row in weak),
        strong_acquisition=tuple(OpportunityPresenter(row) for row in strong),
        opportunity_threshold=MIN_VIEWS_FOR_OPPORTUNITY,
        period_options_primary=tuple(
            option for option in period_options(resolved, **state) if option.period.is_primary
        ),
        period_options_secondary=tuple(
            option for option in period_options(resolved, **state) if not option.period.is_primary
        ),
        months=months,
        max_month_units=max_units,
        categories=tuple(CategoryPresenter(row) for row in category_rows),
        products=tuple(ProductPresenter(row) for row in visible),
        search=search,
        sort=sort,
        page=page,
        total_pages=total_pages,
        total_products=total_products,
        result_summary=_result_summary(total_products, search),
        previous_query=build_query(
            period_key=resolved.key,
            start=resolved.start,
            end=resolved.end,
            page=page - 1,
            **state,
        ),
        next_query=build_query(
            period_key=resolved.key,
            start=resolved.start,
            end=resolved.end,
            page=page + 1,
            **state,
        ),
    )


def _result_summary(total: int, search: str) -> str:
    if search:
        return (
            f"Otsing „{search}“: {total} toodet."
            if total
            else f"Otsing „{search}“: vasteid ei ole."
        )
    return f"{total} toodet valitud perioodil." if total else "Valitud perioodil tooteid ei ole."


def _type_options(active: str, resolved, state: dict) -> tuple[TypeOption, ...]:
    options = [("", "Kõik e-poe tooted")] + [(value, label) for value, label in ProductType.choices]
    out = []
    for key, label in options:
        out.append(
            TypeOption(
                key=key,
                label=label,
                is_active=key == active,
                query=build_query(
                    period_key=resolved.key,
                    start=resolved.start,
                    end=resolved.end,
                    **{**state, "product_type": key},
                ),
            )
        )
    return tuple(out)


def _category_options(active: tuple[int, ...], resolved, state: dict) -> tuple[CategoryOption, ...]:
    out = []
    for term_id, name in get_categories():
        is_active = term_id in active
        chosen = tuple(t for t in active if t != term_id) if is_active else (*active, term_id)
        out.append(
            CategoryOption(
                term_id=term_id,
                label=name or f"#{term_id}",
                is_active=is_active,
                query=build_query(
                    period_key=resolved.key,
                    start=resolved.start,
                    end=resolved.end,
                    **{**state, "categories": chosen},
                ),
            )
        )
    return tuple(out)


def _sort_options(active: str, resolved, state: dict) -> tuple[SortOption, ...]:
    """One option per explorer column, in the order the columns appear.

    The sort control *is* the header row now, so the two must agree about order
    or a reader clicking "Väärtus" would reorder by something else.
    """
    out = []
    for key in SORT_COLUMNS:
        out.append(
            SortOption(
                key=key,
                label=SORT_LABELS[key],
                is_active=key == active,
                query=build_query(
                    period_key=resolved.key,
                    start=resolved.start,
                    end=resolved.end,
                    **{**state, "sort": key if key != SORT_UNITS else ""},
                ),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Product detail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductDetail:
    found: bool
    as_of_label: str = ""
    coverage_label: str = ""
    web_interval_label: str = ""
    web_is_partial: bool = False

    source_product_id: int = 0
    title: str = ""
    type_label: str = ""
    category_name: str = ""
    published: bool | None = None
    publicly_listed: bool | None = None
    listing_gate_open: bool = False
    member_gate_open: bool = False

    product_url: str = ""
    information_url: str = ""
    event_url: str = ""

    orders: str = DASH
    units: str = DASH
    value: str = DASH
    product_views: str = DASH
    information_views: str = DASH
    rate: str = DASH

    list_price: str = DASH
    member_price: str = DASH

    member_units: str = DASH
    non_member_units: str = DASH
    unknown_units: str = DASH

    months: tuple = ()
    max_month_units: int = 0

    period: object = None
    periods: tuple = ()

    @property
    def has_funnel(self) -> bool:
        """Only a document with both pages has an information → product step."""
        return bool(self.information_url and self.product_url)


def build_product_detail(
    source_product_id: int,
    *,
    period_key: str | None,
    date_from: str | None,
    date_to: str | None,
) -> ProductDetail:
    product = get_product(source_product_id)
    if product is None:
        return ProductDetail(found=False)

    coverage = get_shop_coverage()
    if not coverage.has_data:
        return ProductDetail(found=False)

    resolved = resolve_period(period_key, date_from, date_to, anchor=coverage.coverage_end)
    window = resolve_comparison(start=resolved.start, end=resolved.end, shop=coverage)
    snapshot = latest_snapshot_for(product)
    roles = paths_by_product((product.pk,)).get(product.pk, {})

    rows = build_product_rows(window)
    row = next((item for item in rows if item.source_product_id == source_product_id), None)

    filters = {"product_ids": (source_product_id,)}
    totals = get_totals(window, **filters)
    months = get_monthly_series(window, **filters)
    member_split = get_member_split(window, **filters)

    from .selectors import acquisitions_per_hundred

    product_views = row.product_page_views.views if row and row.product_page_views else None
    information_views = (
        row.information_page_views.views if row and row.information_page_views else None
    )

    def _units(status: str) -> str:
        totals_for = member_split.get(status)
        return group_thousands(int(totals_for.units)) if totals_for else DASH

    return ProductDetail(
        found=True,
        as_of_label=long_date(coverage.source_as_of),
        coverage_label=(f"{long_date(coverage.coverage_start)}–{long_date(coverage.coverage_end)}"),
        web_interval_label=(
            f"{long_date(window.web_start)}–{long_date(window.web_end)}" if window.has_web else ""
        ),
        web_is_partial=window.web_is_partial,
        source_product_id=source_product_id,
        title=snapshot.title if snapshot else f"#{source_product_id}",
        type_label=ProductType(product.product_type).label,
        category_name=(snapshot.category_name if snapshot else "") or DASH,
        published=snapshot.published if snapshot else None,
        publicly_listed=snapshot.publicly_listed if snapshot else None,
        listing_gate_open=coverage.public_listing_semantics_verified,
        member_gate_open=coverage.member_semantics_verified,
        product_url=(
            f"{PUBLIC_BASE}{roles[PageRole.PRODUCT]}" if roles.get(PageRole.PRODUCT) else ""
        ),
        information_url=(
            f"{PUBLIC_BASE}{roles[PageRole.INFORMATION]}" if roles.get(PageRole.INFORMATION) else ""
        ),
        event_url=(f"{PUBLIC_BASE}{roles[PageRole.EVENT]}" if roles.get(PageRole.EVENT) else ""),
        orders=group_thousands(totals.orders),
        units=group_thousands(int(totals.units)),
        value=euros(totals.ordered_value_net),
        product_views=_figure(product_views),
        information_views=_figure(information_views),
        rate=_rate(acquisitions_per_hundred(row.conversion_units if row else None, product_views)),
        list_price=euros(snapshot.list_price_net)
        if snapshot and snapshot.list_price_net is not None
        else DASH,
        member_price=(
            euros(snapshot.member_price_net)
            if snapshot and snapshot.member_price_net is not None
            else DASH
        ),
        member_units=_units(MemberStatus.MEMBER),
        non_member_units=_units(MemberStatus.NON_MEMBER),
        unknown_units=_units(MemberStatus.UNKNOWN),
        months=months,
        max_month_units=max((int(point.units) for point in months), default=0),
        period=resolved,
        periods=period_options(resolved),
    )


__all__ = [
    "PAGE_SIZE",
    "PARAM_CATEGORY",
    "PARAM_MEMBER",
    "PARAM_SEARCH",
    "PARAM_TYPE",
    "CategoryPresenter",
    "ProductDetail",
    "ProductPresenter",
    "ShopOverview",
    "build_overview",
    "build_product_detail",
    "parse_int_list",
    "parse_page",
    "parse_search",
    "parse_sort",
]
