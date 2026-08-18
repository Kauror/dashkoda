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
    METRIC_ORDERS,
    METRIC_UNITS,
    METRIC_VALUE,
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
    ComparisonWindow,
    ProductRow,
    acquisitions_per_hundred,
    build_product_rows,
    denominator_path,
    get_concentration,
    get_distinct_orders,
    get_free_paid_split,
    get_member_split,
    get_monthly_distinct_orders,
    get_monthly_series,
    get_payment_split,
    get_product,
    get_product_movers,
    get_shop_coverage,
    get_totals,
    latest_snapshot_for,
    page_views_in_window,
    paths_by_product,
    resolve_comparison,
)
from .vocabulary import vocabulary_for

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


def _views_for(figures: dict, path: str) -> int | None:
    """Views for one path, or `None` when it was never measured.

    `None` and `0` are different answers and stay different: a page with no
    stored GA4 rows was not measured, while a measured zero is a page nobody
    visited. `page_views_in_window` decides which, and this only unwraps it.
    """
    if not path:
        return None
    figure = figures.get(path)
    return figure.views if figure is not None else None


def _payment_value(payments: dict, key: str) -> str:
    """One payment class's ordered value.

    Zero is a real answer here — the class exists and carried nothing — so this
    formats zero rather than dashing it. What it must never do is imply
    settlement: an invoice figure is value that was *ordered* on an invoice, not
    money received, and no caller may label it otherwise.
    """
    totals = payments.get(key)
    return euros(totals.ordered_value_net if totals else Decimal(0))


def _width(value, largest) -> float:
    """A bar's width as a percentage of the largest row in its group.

    Zero when there is nothing to compare against, so an empty group draws no
    bars rather than a row of full-width ones.
    """
    if not largest or largest <= 0:
        return 0.0
    return round(float(value) / float(largest) * 100, 1)


def _bars(items, *, largest=None) -> tuple:
    """`(label, value_text, width_basis, note)` tuples as `BarRow`s."""
    rows = list(items)
    basis = largest if largest is not None else max((row[2] for row in rows), default=0)
    return tuple(
        BarRow(
            label=row[0],
            value=row[1],
            width=_width(row[2], basis),
            note=row[3] if len(row) > 3 else "",
        )
        for row in rows
    )


@dataclass(frozen=True)
class TypeOption:
    key: str
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
    #: This row's position in the whole ordered result set, not just the page
    #: it landed on — so page two starts at 26, not back at 1.
    rank: int = 0
    #: This row's units as a share of every row in the same result set —
    #: `None` when the caller has not supplied one, which a template reads as
    #: no `Osa` figure rather than a division by zero.
    total_units: Decimal | None = None
    #: Whether a previous equal-length window was joined at all. Without this,
    #: a row whose `previous_units` defaults to zero because no window was
    #: requested is indistinguishable from one that was compared and is
    #: genuinely new — the same distinction `PeriodPair.is_available` makes
    #: for every other comparison on this page.
    has_comparison: bool = False

    @property
    def source_product_id(self) -> int:
        return self.row.source_product_id

    @property
    def share(self) -> str:
        if not self.total_units or self.total_units <= 0:
            return DASH
        return f"{(self.row.units / self.total_units * 100):.0f}%".replace(".", ",")

    @property
    def change_label(self) -> str:
        """The movement since the equal-length previous window, worded exactly
        as the movers list already words it — `uus`, `±N%`, or `—` when there
        is no previous window to compare against at all.
        """
        if not self.has_comparison:
            return DASH
        if self.row.is_new:
            return "uus"
        percentage = self.row.percentage_change
        if percentage is None:
            return DASH
        return f"{int(percentage):+d}%".replace("-", "−")

    @property
    def change_direction(self) -> str:
        if not self.has_comparison:
            return "flat"
        if self.row.is_new or (self.row.percentage_change or 0) > 0:
            return "up"
        if self.row.percentage_change is not None and self.row.percentage_change < 0:
            return "down"
        return "flat"

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
        """The percentage, or a statement about the *comparison* being empty.

        `uus perioodil` rather than `uus toode`, and the difference is not
        pedantry: a product with no acquisitions in the previous window may have
        been in the catalogue for five years. Purchase activity does not
        establish when a product launched, and only catalogue history could.
        `title` spells the same thing out for a reader who hovers.
        """
        if self.row.is_new:
            return "uus perioodil"
        percentage = self.row.percentage_change
        return "" if percentage is None else f"{int(percentage):+d}%".replace("-", "−")

    @property
    def context_title(self) -> str:
        if self.row.is_new:
            return "Eelmisel perioodil oste ei olnud."
        return ""


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
class MetricOption:
    key: str
    label: str
    is_active: bool
    query: str


@dataclass(frozen=True)
class BarRow:
    """One row of a compact horizontal comparison.

    `width` is a percentage of the largest row, used for a bar the reader can
    scan. The exact figure is always printed beside it, so the bar is a visual
    aid rather than the only carrier of the quantity.
    """

    label: str
    value: str
    width: float = 0.0
    note: str = ""
    href: str = ""

    @property
    def has_note(self) -> bool:
        return bool(self.note)


@dataclass(frozen=True)
class PaymentMixPresenter:
    """Ordered value by payment mode. Never a statement about settlement."""

    is_known: bool = False
    rows: tuple[BarRow, ...] = ()
    invoice_value: str = DASH
    settled_value: str = DASH
    unknown_value: str = DASH


@dataclass(frozen=True)
class ShopOverview:
    """Everything the E-pood overview renders.

    One page since 2026-08-18, not four tabs. `Ülevaade`, `Tooted`, the trend
    and the top movers are what is left; `Ostud` (payment and member split),
    `Nähtavus` (web-effectiveness) and Tooted's own category, concentration and
    catalogue sections came off with the tab strip they lived in. Nothing here
    computes a figure those views used that this one does not also need — a
    member split and a catalogue count are still built, but only for one
    product's own detail page, in `build_product_detail` below.
    """

    has_source: bool
    as_of_label: str
    coverage_label: str
    window: ComparisonWindow
    web_interval_label: str
    web_is_partial: bool

    period: object = None
    periods: tuple = ()
    product_type: str = ""
    type_options: tuple[TypeOption, ...] = ()
    sort_options: tuple[SortOption, ...] = ()

    member_gate_open: bool = False
    listing_gate_open: bool = False

    products: tuple[ProductPresenter, ...] = ()

    search: str = ""
    sort: str = SORT_UNITS
    page: int = 1
    total_pages: int = 1
    total_products: int = 0
    result_summary: str = ""
    previous_query: str = ""
    next_query: str = ""

    #: The three headline figures, plus the free/paid card, already compared.
    kpis: tuple[KpiCard, ...] = ()
    comparison_label: str = ""
    comparison_available: bool = False
    #: Whether the order figure counts distinct orders or order lines.
    orders_are_distinct: bool = False
    trend: object = None
    trend_metric: str = ""
    trend_options: tuple = ()
    mix: MixPresenter = field(default_factory=MixPresenter)
    period_options_primary: tuple = ()
    period_options_secondary: tuple = ()

    #: Type-aware wording for every acquisition figure on the page.
    units_label: str = "Ostetud"
    units_noun: str = "ühikut"

    # --- Lepingupõhjad: always the document family, whatever a reader has
    # chosen in Tooted below it. Contract templates carry the shop, and this
    # section states that rather than following the reader's own filter. ----
    document_risers: tuple[MoverPresenter, ...] = ()
    document_fallers: tuple[MoverPresenter, ...] = ()
    document_note: str = ""
    document_orders: str = DASH
    document_orders_label: str = "Tellimused"
    document_value: str = DASH

    # --- Tooted: the whole catalogue, one table -----------------------------
    concentration_note: str = ""
    physical_note: str = ""

    #: Data quality, read by `/haldus/`.
    schema_version: str = ""
    distinct_orders_available: bool = False
    free_paid_available: bool = False
    page_detail_complete: bool = False

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
        note = f"Eelmisel perioodil oli tasuta ostude osakaal {previous.free_share:.0f}%.".replace(
            ".", ","
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
        # The previous window measured nothing, so there is no percentage. This
        # says the comparison is empty, never that the product is new.
        return "uus perioodil"
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
    metric: str = METRIC_UNITS,
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

    # The member control is built but withheld: the source has not established
    # that its member flag describes the moment of the transaction rather than
    # the customer's standing today.
    member_gate_open = coverage.member_semantics_verified
    effective_member = member_status if member_gate_open else ""

    # --- the headline: every product, every family. The KPI strip, the trend
    # and the free/paid card no longer follow the Tooted type filter below them
    # — that filter narrows one table, not the whole page. --------------------
    totals = get_totals(window)
    previous_totals = get_totals(previous_window) if pair.is_available else None
    mix = get_free_paid_split(window)
    previous_mix = get_free_paid_split(previous_window) if pair.is_available else None

    # `ShopDailySummary` is keyed by day and product type only, so an unfiltered
    # headline always qualifies for the stored distinct-order total — there is
    # no narrower population here for it to misrepresent.
    current_orders = get_distinct_orders(start=window.commerce_start, end=window.commerce_end)
    orders_are_distinct = current_orders is not None
    previous_orders = (
        get_distinct_orders(start=pair.previous_start, end=pair.previous_end)
        if pair.is_available and orders_are_distinct
        else (previous_totals.orders if previous_totals else None)
    )
    if not orders_are_distinct:
        current_orders = totals.orders

    units_cmp = MetricComparison.of(
        totals.units, previous_totals.units if previous_totals else None, period=pair
    )
    orders_label = "Tellimused" if orders_are_distinct else "Tellimusridu"
    orders_cmp = MetricComparison.of(current_orders, previous_orders, period=pair)
    value_cmp = MetricComparison.of(
        totals.ordered_value_net,
        previous_totals.ordered_value_net if previous_totals else None,
        period=pair,
    )

    kpis = (
        _kpi(
            "Ostetud ühikud", units_cmp, formatter=lambda v: group_thousands(int(v)), unit="ühikut"
        ),
        _kpi(orders_label, orders_cmp, formatter=lambda v: group_thousands(int(v))),
        _kpi("Tellitud väärtus", value_cmp, formatter=euros, unit="KM-ta"),
    )

    # --- the trend, on whichever metric was asked for ----------------------
    months = get_monthly_series(window)
    previous_months = get_monthly_series(previous_window) if pair.is_available else ()
    monthly_orders = (
        dict(get_monthly_distinct_orders(start=window.commerce_start, end=window.commerce_end))
        if orders_are_distinct
        else {}
    )
    previous_monthly_orders = (
        dict(get_monthly_distinct_orders(start=pair.previous_start, end=pair.previous_end))
        if orders_are_distinct and pair.is_available
        else {}
    )

    def _series(points, distinct_by_month):
        if metric == METRIC_VALUE:
            return [(point.month, float(point.ordered_value_net)) for point in points]
        if metric == METRIC_ORDERS:
            if distinct_by_month:
                return [
                    (point.month, float(distinct_by_month.get(point.month, 0))) for point in points
                ]
            return [(point.month, float(point.orders)) for point in points]
        return [(point.month, float(point.units)) for point in points]

    trend_label = {METRIC_VALUE: "Tellitud väärtus", METRIC_ORDERS: orders_label}.get(
        metric, "Ostetud"
    )

    trend = _trend_chart(
        _series(months, monthly_orders),
        _series(previous_months, previous_monthly_orders) if pair.is_available else [],
        label=trend_label,
        previous_label=pair.previous_label,
        offset_days=pair.length_days,
    )

    # --- Lepingupõhjad: always the document family, whatever a reader has
    # chosen for Tooted below. Contract templates carry the shop, and this
    # section states that rather than following the reader's own filter. ----
    document_types = (ProductType.DOCUMENT,)
    document_totals = get_totals(window, product_types=document_types)
    document_mix = get_free_paid_split(window, product_types=document_types)
    document_orders = get_distinct_orders(
        start=window.commerce_start, end=window.commerce_end, product_type=ProductType.DOCUMENT
    )
    document_risers, document_fallers = get_product_movers(
        current_start=window.commerce_start,
        current_end=window.commerce_end,
        previous_start=pair.previous_start,
        previous_end=pair.previous_end,
        product_types=document_types,
    )
    document_words = vocabulary_for(ProductType.DOCUMENT)
    document_note_parts = [
        f"{group_thousands(int(document_totals.units))} {document_words.units_noun} ostetud"
    ]
    if document_mix.free_share is not None:
        document_note_parts.append(
            f"{document_mix.free_share:.0f}% ostudest tasuta".replace(".", ",")
        )
    document_note = " · ".join(document_note_parts)
    if pair.is_available:
        document_note = f"{document_note} — muutus: {pair.current_label} vs {pair.previous_label}"

    # --- Tooted: the whole catalogue, one table, filtered by the reader's own
    # type chip and search term. ---------------------------------------------
    filters = {
        "product_types": (product_type,) if product_type else (),
        "category_term_ids": categories,
        "member_status": effective_member,
    }
    rows = build_product_rows(
        window,
        search=search,
        previous_start=pair.previous_start if pair.is_available else None,
        previous_end=pair.previous_end if pair.is_available else None,
        **filters,
    )
    ordered = sorted(rows, key=_sort_key(sort))
    total_products = len(ordered)
    total_pages = max(1, -(-total_products // PAGE_SIZE))
    page = min(max(page, 1), total_pages)
    visible = ordered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

    total_current_units = sum((row.units for row in rows), Decimal(0))
    rank_offset = (page - 1) * PAGE_SIZE
    products = tuple(
        ProductPresenter(
            row=row,
            rank=rank_offset + index,
            total_units=total_current_units,
            has_comparison=pair.is_available,
        )
        for index, row in enumerate(visible, start=1)
    )

    concentration = get_concentration(rows, top=10)
    share = concentration.top_share
    concentration_note = (
        f"Top 10 toodet moodustavad {share}% kõigist ostudest." if share is not None else ""
    )

    physical_totals = get_totals(window, product_types=(ProductType.PHYSICAL_PRODUCT,))
    physical_note = (
        f"Füüsilistest toodetest osteti perioodil "
        f"{group_thousands(int(physical_totals.units))} ühikut "
        f"({euros(physical_totals.ordered_value_net)} KM-ta)."
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
        product_type=product_type,
        type_options=_type_options(product_type, resolved, state),
        sort_options=_sort_options(sort, resolved, state),
        member_gate_open=member_gate_open,
        listing_gate_open=coverage.public_listing_semantics_verified,
        products=products,
        search=search,
        sort=sort,
        page=page,
        total_pages=total_pages,
        total_products=total_products,
        result_summary=_result_summary(total_products, search),
        previous_query=build_query(
            period_key=resolved.key, start=resolved.start, end=resolved.end, page=page - 1, **state
        ),
        next_query=build_query(
            period_key=resolved.key, start=resolved.start, end=resolved.end, page=page + 1, **state
        ),
        kpis=kpis,
        comparison_label=pair.previous_label,
        comparison_available=pair.is_available,
        orders_are_distinct=orders_are_distinct,
        trend=trend,
        mix=_mix_presenter(mix, previous_mix),
        period_options_primary=tuple(
            option for option in period_options(resolved, **state) if option.period.is_primary
        ),
        period_options_secondary=tuple(
            option for option in period_options(resolved, **state) if not option.period.is_primary
        ),
        trend_metric=metric,
        trend_options=_metric_options(metric, resolved, state, orders_label),
        document_risers=tuple(MoverPresenter(row) for row in document_risers),
        document_fallers=tuple(MoverPresenter(row) for row in document_fallers),
        document_note=document_note,
        document_orders=(group_thousands(document_orders) if document_orders is not None else DASH),
        document_orders_label="Tellimused",
        document_value=euros(document_totals.ordered_value_net),
        concentration_note=concentration_note,
        physical_note=physical_note,
        schema_version=coverage.schema_version,
        distinct_orders_available=orders_are_distinct,
        free_paid_available=mix.is_known,
        page_detail_complete=window.page_detail_complete,
    )


def _result_summary(total: int, search: str) -> str:
    if search:
        return (
            f"Otsing „{search}“: {total} toodet."
            if total
            else f"Otsing „{search}“: vasteid ei ole."
        )
    return f"{total} toodet valitud perioodil." if total else "Valitud perioodil tooteid ei ole."


def _decimal(value: Decimal | None) -> str:
    """A derived average, to two places, in Estonian decimal notation."""
    if value is None:
        return DASH
    return f"{value:.2f}".replace(".", ",")


def _metric_options(
    active: str, resolved, state: dict, orders_label: str
) -> tuple[MetricOption, ...]:
    """Which series the trend may draw.

    The order option is labelled with whatever the order figure actually is for
    this population — `Tellimused` where the summary answers it, `Tellimusridu`
    where it does not — so the switch cannot promise a metric the page will not
    deliver.
    """
    labels = (
        (METRIC_UNITS, "Ostetud"),
        (METRIC_ORDERS, orders_label),
        (METRIC_VALUE, "Tellitud väärtus"),
    )
    return tuple(
        MetricOption(
            key=key,
            label=label,
            is_active=key == active,
            query=build_query(
                period_key=resolved.key,
                start=resolved.start,
                end=resolved.end,
                metric=key,
                **state,
            ),
        )
        for key, label in labels
    )


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

    # --- the intelligence layer ------------------------------------------
    #: Type-aware wording, so an event says `Registreerimised` and a template
    #: says `Ostetud` without either page hard-coding the other's word.
    units_label: str = "Ostetud"
    units_noun: str = "ühikut"
    views_label: str = "Tootelehe vaatamised"
    rate_label: str = "Oste / 100 vaatamist"
    #: Which page role this product's rate divides by, named for the reader.
    denominator_label: str = ""
    #: Views for that page. For a template this is the product page and for an
    #: event registration the event page, so the heading and the number always
    #: describe the same page as the rate below them.
    denominator_views: str = DASH
    #: Period-over-period movement, which the page previously lacked entirely.
    kpis: tuple[KpiCard, ...] = ()
    comparison_label: str = ""
    comparison_available: bool = False
    trend: object = None
    #: Composition, for the deep read.
    mix: MixPresenter = field(default_factory=MixPresenter)
    payment_mix: PaymentMixPresenter = field(default_factory=PaymentMixPresenter)
    month_rows: tuple = ()

    @property
    def has_funnel(self) -> bool:
        """Only a document with both pages has an information → product step."""
        return bool(self.information_url and self.product_url)

    @property
    def is_event(self) -> bool:
        return bool(self.event_url)


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

    # This product's own paths and figures, rather than the whole catalogue's.
    # `build_product_rows` builds every product in scope; asking it for one was
    # a full-catalogue aggregation to answer a single-product question.
    words = vocabulary_for(product.product_type)
    denominator = denominator_path(product.product_type, roles)
    figures = page_views_in_window([path for path in roles.values() if path], window=window)
    product_views = _views_for(figures, roles.get(PageRole.PRODUCT, ""))
    information_views = _views_for(figures, roles.get(PageRole.INFORMATION, ""))
    denominator_views = _views_for(figures, denominator)

    filters = {"product_ids": (source_product_id,)}
    totals = get_totals(window, **filters)
    months = get_monthly_series(window, **filters)
    member_split = get_member_split(window, **filters)
    mix = get_free_paid_split(window, **filters)
    payments = get_payment_split(window, **filters)

    # The rate's numerator must read the **web** window, exactly as the ranking
    # does: six years of acquisitions over three years of views is not a rate.
    conversion_units = (
        get_totals(
            ComparisonWindow(window.web_start, window.web_end, window.web_start, window.web_end),
            **filters,
        ).units
        if window.has_web
        else Decimal(0)
    )

    # --- comparison, which this page previously had none of ---------------
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

    detail_kpis = (
        _kpi(
            words.units_label,
            MetricComparison.of(
                totals.units, previous_totals.units if previous_totals else None, period=pair
            ),
            formatter=lambda v: group_thousands(int(v)),
            unit=words.units_noun,
        ),
        # `Tellimused` is accurate here and only here: the sum runs over one
        # product's own cells, so each order appears once.
        _kpi(
            "Tellimused",
            MetricComparison.of(
                totals.orders, previous_totals.orders if previous_totals else None, period=pair
            ),
            formatter=lambda v: group_thousands(int(v)),
            secondary="tellimust, mis sisaldasid seda toodet",
        ),
        _kpi(
            "Tellitud väärtus",
            MetricComparison.of(
                totals.ordered_value_net,
                previous_totals.ordered_value_net if previous_totals else None,
                period=pair,
            ),
            formatter=euros,
            unit="KM-ta",
        ),
    )

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
        label=words.trend_label,
        previous_label=pair.previous_label,
        offset_days=pair.length_days,
    )

    payment_present = [
        (label, payments[key].ordered_value_net)
        for key, label in (
            (PaymentClass.INVOICE, "Arve"),
            (PaymentClass.BANK_OR_CARD, "Pangalink või kaart"),
            (PaymentClass.UNKNOWN, "Teadmata"),
        )
        if payments.get(key) and payments[key].ordered_value_net > 0
    ]

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
        # Divided by this family's acquisition page — the event page for a
        # registration, the product page for a template — and `—` when that
        # page is missing, never another role's views standing in for it.
        rate=_rate(acquisitions_per_hundred(conversion_units, denominator_views)),
        units_label=words.units_label,
        units_noun=words.units_noun,
        views_label=words.views_label,
        rate_label=words.rate_label,
        denominator_label=words.views_label,
        denominator_views=_figure(denominator_views),
        kpis=detail_kpis,
        comparison_label=pair.previous_label,
        comparison_available=pair.is_available,
        trend=trend,
        mix=_mix_presenter(mix, None),
        payment_mix=PaymentMixPresenter(
            is_known=bool(payment_present),
            rows=_bars(
                ((label, euros(amount), int(amount), "") for label, amount in payment_present)
            ),
            invoice_value=_payment_value(payments, PaymentClass.INVOICE),
            settled_value=_payment_value(payments, PaymentClass.BANK_OR_CARD),
            unknown_value=_payment_value(payments, PaymentClass.UNKNOWN),
        ),
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
