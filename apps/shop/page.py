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

from apps.core.formatting import euros, group_thousands, long_date, month_and_year
from apps.dashboard.sparkline import TrendSource, build_trend_chart

from .comparison import FLAT, MetricComparison, derive_period_pair
from .intelligence import (
    build_attention_matrix,
    build_order_structure,
    build_signals,
)
from .models import MemberStatus, PageRole, PaymentClass, ProductType
from .periods import (
    FOCUSES,
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
    MIN_VIEWS_FOR_OPPORTUNITY,
    CategoryRow,
    ComparisonWindow,
    ProductRow,
    acquisitions_per_hundred,
    aggregate_web,
    build_category_rows,
    build_product_rows,
    denominator_path,
    distinct_orders_supported,
    get_catalogue_summary,
    get_categories,
    get_category_movers,
    get_concentration,
    get_distinct_orders,
    get_free_paid_series,
    get_free_paid_split,
    get_member_split,
    get_monthly_distinct_orders,
    get_monthly_series,
    get_payment_split,
    get_product,
    get_product_movers,
    get_shop_coverage,
    get_totals,
    get_web_opportunities,
    get_yearly_series,
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
class CategoryMoverPresenter:
    """One category's movement, formatted the same way a product's is."""

    row: object

    @property
    def name(self) -> str:
        return self.row.name

    @property
    def change_label(self) -> str:
        change = int(self.row.change)
        return f"{change:+d}".replace("-", "−")

    @property
    def context(self) -> str:
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
class RankingBar:
    """One row of the ranking: which product, in which category, how many.

    No `width` any more. It was a 0–100 geometry value for a bar under every
    row, and the bar came off when the ranking went from three lines a product
    to one — it encoded the quantity the count states exactly, on rows already
    ordered by it. A field computed on every render and printed nowhere is the
    thing that later gets read as though it were still on the page.
    """

    source_product_id: int
    title: str
    category_name: str
    value: str


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
class FocusOption:
    """One entry in the focus navigation."""

    key: str
    label: str
    question: str
    is_active: bool
    query: str


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
class YearPresenter:
    """One calendar year, with its incompleteness stated rather than implied."""

    year: str
    units: str
    value: str
    orders: str
    width: float
    is_partial: bool
    covered_label: str = ""

    @property
    def partial_note(self) -> str:
        """What a partial year actually covers, in words a chart cannot show."""
        if not self.is_partial:
            return ""
        return f"osaline aasta · {self.covered_label}"


@dataclass(frozen=True)
class PaymentMixPresenter:
    """Ordered value by payment mode. Never a statement about settlement."""

    is_known: bool = False
    rows: tuple[BarRow, ...] = ()
    invoice_value: str = DASH
    settled_value: str = DASH
    unknown_value: str = DASH


@dataclass(frozen=True)
class OrderStructurePresenter:
    is_distinct: bool = False
    units_per_order: str = DASH
    value_per_order: str = DASH
    value_per_unit: str = DASH
    has_per_order: bool = False
    withheld_note: str = ""


@dataclass(frozen=True)
class WebCoveragePresenter:
    """How much of the selected population the web figures cover."""

    measured: int = 0
    without_path: int = 0
    without_measurement: int = 0
    total: int = 0
    summary: str = ""
    has_population: bool = False


@dataclass(frozen=True)
class CataloguePresenter:
    """The current catalogue snapshot, as present-tense facts."""

    has_products: bool = False
    products: str = DASH
    by_type: tuple[BarRow, ...] = ()
    with_list_price: str = DASH
    with_member_price: str = DASH
    free_listed: str = DASH
    published: str = DASH
    publicly_listed: str = DASH
    listing_gate_open: bool = False
    with_acquisition_path: str = DASH


@dataclass(frozen=True)
class MemberSplitPresenter:
    """The member dimension, shown only when the gate is open."""

    is_open: bool = False
    rows: tuple[BarRow, ...] = ()
    value_rows: tuple[BarRow, ...] = ()


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

    # --- the intelligence layer ------------------------------------------
    #: Which analytical view is on screen, and the links to the others.
    focus: str = ""
    focus_label: str = ""
    focus_question: str = ""
    focus_options: tuple[FocusOption, ...] = ()
    #: Ready-made links to each view by key, so a template can point at one
    #: without indexing into `focus_options` by position.
    focus_links: dict = field(default_factory=dict)
    #: Type-aware wording for every acquisition figure on the page.
    units_label: str = "Ostetud"
    units_noun: str = "ühikut"
    views_label: str = "Ostulehe vaatamised"
    rate_label: str = "Oste / 100 vaatamist"
    #: Deterministic things worth a second look.
    signals: tuple = ()
    #: Long-term history.
    years: tuple[YearPresenter, ...] = ()
    months_bars: tuple[BarRow, ...] = ()
    free_paid_series: tuple[BarRow, ...] = ()
    free_paid_series_known: bool = False
    #: Structure and composition.
    payment_mix: PaymentMixPresenter = field(default_factory=PaymentMixPresenter)
    order_structure: OrderStructurePresenter = field(default_factory=OrderStructurePresenter)
    member_split: MemberSplitPresenter = field(default_factory=MemberSplitPresenter)
    concentration: object = None
    concentration_note: str = ""
    long_tail_note: str = ""
    value_concentration_note: str = ""
    #: Rankings for the deeper views.
    category_bars: tuple[BarRow, ...] = ()
    value_bars: tuple[BarRow, ...] = ()
    value_category_bars: tuple[BarRow, ...] = ()
    value_type_bars: tuple[BarRow, ...] = ()
    type_bars: tuple[BarRow, ...] = ()
    category_risers: tuple = ()
    category_fallers: tuple = ()
    #: Web.
    web_coverage: WebCoveragePresenter = field(default_factory=WebCoveragePresenter)
    matrix: object = None
    #: Catalogue, present tense.
    catalogue: CataloguePresenter = field(default_factory=CataloguePresenter)
    #: Data quality.
    schema_version: str = ""
    distinct_orders_available: bool = False
    free_paid_available: bool = False
    page_detail_complete: bool = False

    @property
    def is_overview(self) -> bool:
        return self.focus == "ulevaade"

    @property
    def is_purchases(self) -> bool:
        return self.focus == "ostud"

    @property
    def is_products(self) -> bool:
        return self.focus == "tooted"

    @property
    def is_visibility(self) -> bool:
        return self.focus == "nahtavus"

    @property
    def is_value(self) -> bool:
        return self.focus == "vaartus"

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
    focus=None,
    metric: str = METRIC_UNITS,
) -> ShopOverview:
    coverage = get_shop_coverage()
    focus = focus if focus is not None else FOCUSES[0]
    if not coverage.has_data:
        return ShopOverview(
            has_source=False,
            as_of_label="",
            coverage_label="",
            window=ComparisonWindow(None, None, None, None),
            web_interval_label="",
            web_is_partial=False,
            focus=focus.key,
            focus_label=focus.label,
            focus_question=focus.question,
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

    # The period's own acquisition-page views, from the rows already loaded
    # rather than re-queried, so the headline and the table agree — and over
    # **unique** paths, so two event products sharing one event page contribute
    # that page's traffic once rather than twice.
    web = aggregate_web(rows)
    total_views = web.views
    conversion_units = web.units

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

    # Distinct orders where the source can answer the population **now on
    # screen**, order lines where it cannot.
    #
    # `ShopDailySummary` is keyed by day and product type only. A category,
    # member or search filter narrows past that grain, and reusing the stored
    # total would put the whole type's order count beside one category's units
    # and value — two populations, one row, nothing saying so. So the guard is
    # checked before the query, not after it, and the label follows the metric.
    supports_distinct = distinct_orders_supported(
        category_term_ids=categories, member_status=effective_member, search=search
    )
    current_orders = (
        get_distinct_orders(
            start=window.commerce_start, end=window.commerce_end, product_type=product_type
        )
        if supports_distinct
        else None
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

    words = vocabulary_for(product_type)

    # Why the order label moves: `Tellimused` is only true when the stored
    # distinct-order summary answers this exact population. Otherwise the figure
    # counts order *lines*, is a different and larger number, and says so.
    if orders_are_distinct:
        orders_label = "Tellimused"
        orders_note = ""
    else:
        orders_label = "Tellimusridu"
        orders_note = (
            "kitsendatud valikul loetakse tooteridu"
            if supports_distinct is False
            else "eri tellimuste arv ei ole imporditud"
        )

    kpis = (
        _kpi(
            words.units_label,
            units_cmp,
            formatter=lambda v: group_thousands(int(v)),
            unit=words.units_noun,
        ),
        _kpi(
            orders_label,
            orders_cmp,
            formatter=lambda v: group_thousands(int(v)),
            secondary=orders_note,
        ),
        _kpi(
            "Tellitud väärtus",
            value_cmp,
            formatter=euros,
            unit="KM-ta",
            secondary=(
                f"{mix.free_share:.0f}% ostudest tasuta".replace(".", ",")
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

    # Ranking: the products already loaded, largest first. The order is the
    # comparison now that the bars are gone; `ranking_note` below carries the
    # one thing the order does not say, which is what share of everything these
    # ten are.
    ranked = sorted(rows, key=lambda r: (-r.units, r.title.casefold()))[:10]
    ranking = tuple(
        RankingBar(
            source_product_id=r.source_product_id,
            title=r.title,
            category_name=r.category_name,
            value=group_thousands(int(r.units)),
        )
        for r in ranked
    )
    concentration = get_concentration(rows, top=10)
    value_concentration = get_concentration(rows, top=10, by_value=True)
    share = concentration.top_share

    weak, strong = get_web_opportunities(rows)
    category_risers, category_fallers = get_category_movers(
        current_start=window.commerce_start,
        current_end=window.commerce_end,
        previous_start=pair.previous_start,
        previous_end=pair.previous_end,
        **filters,
    )

    # --- the trend, on whichever metric was asked for ---------------------
    #
    # The order series draws distinct orders when the summary can answer this
    # population and order lines when it cannot, matching the KPI above it. Two
    # series on one page that disagree about what an order is would be worse
    # than offering only one.
    monthly_orders = (
        dict(
            get_monthly_distinct_orders(
                start=window.commerce_start,
                end=window.commerce_end,
                product_type=product_type,
            )
        )
        if orders_are_distinct
        else {}
    )
    previous_months = get_monthly_series(previous_window, **filters) if pair.is_available else ()
    previous_monthly_orders = (
        dict(
            get_monthly_distinct_orders(
                start=pair.previous_start,
                end=pair.previous_end,
                product_type=product_type,
            )
        )
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

    trend_label = {
        METRIC_VALUE: "Tellitud väärtus",
        METRIC_ORDERS: orders_label,
    }.get(metric, words.trend_label)

    trend = _trend_chart(
        _series(months, monthly_orders),
        _series(previous_months, previous_monthly_orders) if pair.is_available else [],
        label=trend_label,
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

    def _focus_query(key: str) -> str:
        """A link to another view, carrying everything already chosen."""
        query = build_query(
            period_key=resolved.key,
            start=resolved.start,
            end=resolved.end,
            focus=key,
            metric=metric,
            **state,
        )
        return f"?{query}"

    # --- long-term history, structure and composition ---------------------
    years = get_yearly_series(window, **filters)
    max_year_units = max((int(point.units) for point in years), default=0)
    year_rows = tuple(
        YearPresenter(
            year=str(point.year),
            units=group_thousands(int(point.units)),
            value=euros(point.ordered_value_net),
            orders=group_thousands(point.orders),
            width=_width(int(point.units), max_year_units),
            is_partial=point.is_partial,
            covered_label=(
                f"{long_date(point.covered_from)}–{long_date(point.covered_to)}"
                if point.is_partial
                else ""
            ),
        )
        for point in years
    )

    mix_series = get_free_paid_series(window, **filters)
    free_paid_bars = tuple(
        BarRow(
            label=month_and_year(point.month),
            value=f"{point.free_share:.0f}%".replace(".", ","),
            width=float(point.free_share),
            note=f"{group_thousands(int(point.free or 0))} tasuta, "
            f"{group_thousands(int(point.paid or 0))} tasulist",
        )
        for point in mix_series
        if point.free_share is not None
    )

    order_structure = build_order_structure(
        units=totals.units,
        ordered_value_net=totals.ordered_value_net,
        distinct_orders=current_orders if orders_are_distinct else None,
        supports_distinct=orders_are_distinct,
    )

    matrix = build_attention_matrix(rows, minimum_views=MIN_VIEWS_FOR_OPPORTUNITY)

    signals = build_signals(
        units_change=units_cmp.absolute_change if units_cmp.is_available else None,
        units_percentage=units_cmp.percentage_change if units_cmp.is_available else None,
        weak_acquisition=weak,
        strong_acquisition=strong,
        product_fallers=fallers,
        category_fallers=category_fallers,
        free_share=mix.free_share,
        previous_free_share=previous_mix.free_share if previous_mix else None,
        concentration=concentration,
        focus_query=_focus_query,
        minimum_views=MIN_VIEWS_FOR_OPPORTUNITY,
    )

    # --- composition breakdowns ------------------------------------------
    #
    # Units and ordered value only. Distinct orders are deliberately absent from
    # every per-type breakdown: one Commerce order may carry a document and a
    # physical product, so it belongs to two type rows and adding them would
    # count it twice — the exact error `ShopDailySummary`'s blank-type row
    # exists to avoid.
    type_totals: dict[str, list] = {}
    for row in rows:
        bucket = type_totals.setdefault(row.product_type, [Decimal(0), Decimal(0)])
        bucket[0] += row.units
        bucket[1] += row.ordered_value_net
    type_bars = _bars(
        sorted(
            (
                (
                    ProductType(key).label,
                    group_thousands(int(amounts[0])),
                    int(amounts[0]),
                    euros(amounts[1]),
                )
                for key, amounts in type_totals.items()
            ),
            key=lambda item: -item[2],
        )
    )
    value_type_bars = _bars(
        sorted(
            (
                (
                    ProductType(key).label,
                    euros(amounts[1]),
                    int(amounts[1]),
                    f"{group_thousands(int(amounts[0]))} {words.units_noun}",
                )
                for key, amounts in type_totals.items()
            ),
            key=lambda item: -item[2],
        )
    )

    category_bars = _bars(
        (
            row.category_name or "Kategooriata",
            group_thousands(int(row.units)),
            int(row.units),
            f"{row.product_count} toodet",
        )
        for row in category_rows[:12]
    )
    value_category_bars = _bars(
        sorted(
            (
                (
                    row.category_name or "Kategooriata",
                    euros(row.ordered_value_net),
                    int(row.ordered_value_net),
                    f"{group_thousands(int(row.units))} {words.units_noun}",
                )
                for row in category_rows
            ),
            key=lambda item: -item[2],
        )[:12]
    )
    value_ranked = sorted(rows, key=lambda r: (-r.ordered_value_net, r.title.casefold()))[:10]
    value_bars = _bars(
        (
            row.title,
            euros(row.ordered_value_net),
            int(row.ordered_value_net),
            f"{group_thousands(int(row.units))} {words.units_noun}",
        )
        for row in value_ranked
    )

    payment_rows = [
        (PaymentClass.INVOICE, "Arve"),
        (PaymentClass.BANK_OR_CARD, "Pangalink või kaart"),
        (PaymentClass.UNKNOWN, "Teadmata"),
    ]
    payment_present = [
        (label, payments[key].ordered_value_net)
        for key, label in payment_rows
        if payments.get(key) and payments[key].ordered_value_net > 0
    ]
    payment_mix = PaymentMixPresenter(
        is_known=bool(payment_present),
        rows=_bars(((label, euros(amount), int(amount), "") for label, amount in payment_present)),
        invoice_value=_payment_value(payments, PaymentClass.INVOICE),
        settled_value=_payment_value(payments, PaymentClass.BANK_OR_CARD),
        unknown_value=_payment_value(payments, PaymentClass.UNKNOWN),
    )

    member_totals = get_member_split(window, **filters) if member_gate_open else {}
    member_labels = [
        (MemberStatus.MEMBER, "Liige"),
        (MemberStatus.NON_MEMBER, "Mitteliige"),
        (MemberStatus.UNKNOWN, "Teadmata"),
    ]
    member_presenter = MemberSplitPresenter(
        is_open=member_gate_open,
        rows=_bars(
            (
                (
                    label,
                    group_thousands(int(member_totals[key].units)),
                    int(member_totals[key].units),
                    "",
                )
                for key, label in member_labels
                if member_totals.get(key)
            )
        ),
        value_rows=_bars(
            (
                (
                    label,
                    euros(member_totals[key].ordered_value_net),
                    int(member_totals[key].ordered_value_net),
                    "",
                )
                for key, label in member_labels
                if member_totals.get(key)
            )
        ),
    )

    coverage_summary = web.coverage
    web_coverage = WebCoveragePresenter(
        measured=coverage_summary.measured,
        without_path=coverage_summary.without_path,
        without_measurement=coverage_summary.without_measurement,
        total=coverage_summary.total,
        has_population=coverage_summary.has_population,
        summary=(
            f"{group_thousands(coverage_summary.measured)} / "
            f"{group_thousands(coverage_summary.total)} valitud tootest on "
            "sellel vahemikul mõõdetud ostuleht."
            if coverage_summary.has_population
            else ""
        ),
    )

    catalogue = get_catalogue_summary(listing_gate_open=coverage.public_listing_semantics_verified)
    catalogue_presenter = CataloguePresenter(
        has_products=catalogue.has_products,
        products=group_thousands(catalogue.products),
        by_type=_bars(
            sorted(
                (
                    (ProductType(key).label, group_thousands(count), count, "")
                    for key, count in catalogue.by_type.items()
                ),
                key=lambda item: -item[2],
            )
        ),
        with_list_price=group_thousands(catalogue.with_list_price),
        with_member_price=group_thousands(catalogue.with_member_price),
        free_listed=group_thousands(catalogue.free_listed),
        published=group_thousands(catalogue.published),
        publicly_listed=(
            group_thousands(catalogue.publicly_listed)
            if catalogue.publicly_listed is not None
            else DASH
        ),
        listing_gate_open=coverage.public_listing_semantics_verified,
        with_acquisition_path=group_thousands(catalogue.with_acquisition_path),
    )

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
                words.views_label,
                MetricComparison.of(total_views or 0, None),
                formatter=lambda v: _figure(total_views),
                comparison_label=(
                    f"{long_date(window.web_start)}–{long_date(window.web_end)}"
                    if window.has_web
                    else ""
                ),
            ),
            _kpi(
                words.rate_label,
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
        ranking_note=(f"Top 10 moodustavad {share}% ostudest." if share is not None else ""),
        focus=focus.key,
        focus_label=focus.label,
        focus_question=focus.question,
        focus_options=_focus_options(focus.key, resolved, state, metric),
        focus_links={item.key: _focus_query(item.key) for item in FOCUSES},
        units_label=words.units_label,
        units_noun=words.units_noun,
        views_label=words.views_label,
        rate_label=words.rate_label,
        trend_metric=metric,
        trend_options=_metric_options(metric, resolved, state, focus.key, orders_label),
        signals=signals,
        years=year_rows,
        free_paid_series=free_paid_bars,
        free_paid_series_known=bool(free_paid_bars),
        payment_mix=payment_mix,
        order_structure=OrderStructurePresenter(
            is_distinct=order_structure.is_distinct,
            units_per_order=_decimal(order_structure.units_per_order),
            value_per_order=euros(order_structure.value_per_order)
            if order_structure.value_per_order is not None
            else DASH,
            value_per_unit=euros(order_structure.value_per_unit)
            if order_structure.value_per_unit is not None
            else DASH,
            has_per_order=order_structure.has_per_order,
            withheld_note=(
                ""
                if order_structure.is_distinct
                else "Tellimuse kohta arvutatavad näitajad vajavad eri tellimuste arvu, "
                "mida see valik ei toeta."
            ),
        ),
        member_split=member_presenter,
        concentration=concentration,
        concentration_note=(
            f"Top 10 toodet moodustavad {share}% ostetud ühikutest." if share is not None else ""
        ),
        long_tail_note=(
            f"80% ostudest tuleb {concentration.long_tail_count} tootest "
            f"({concentration.population} seast)."
            if concentration.long_tail_count is not None
            else ""
        ),
        value_concentration_note=(
            f"Top 10 toodet moodustavad {value_concentration.top_share}% tellitud väärtusest."
            if value_concentration.top_share is not None
            else ""
        ),
        category_bars=category_bars,
        value_bars=value_bars,
        value_category_bars=value_category_bars,
        value_type_bars=value_type_bars,
        type_bars=type_bars,
        category_risers=tuple(CategoryMoverPresenter(row) for row in category_risers),
        category_fallers=tuple(CategoryMoverPresenter(row) for row in category_fallers),
        web_coverage=web_coverage,
        matrix=matrix,
        catalogue=catalogue_presenter,
        schema_version=coverage.schema_version,
        distinct_orders_available=orders_are_distinct,
        free_paid_available=mix.is_known,
        page_detail_complete=window.page_detail_complete,
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


def _decimal(value: Decimal | None) -> str:
    """A derived average, to two places, in Estonian decimal notation."""
    if value is None:
        return DASH
    return f"{value:.2f}".replace(".", ",")


def _focus_options(active: str, resolved, state: dict, metric: str) -> tuple[FocusOption, ...]:
    """The five views, each carrying the period and filters already chosen.

    Every link goes through `build_query`, so moving between views preserves the
    reader's whole state — and the URL stays bookmarkable, shareable and
    reload-safe because the state lives in it rather than in a session.
    """
    return tuple(
        FocusOption(
            key=focus.key,
            label=focus.label,
            question=focus.question,
            is_active=focus.key == active,
            query=build_query(
                period_key=resolved.key,
                start=resolved.start,
                end=resolved.end,
                focus=focus.key,
                metric=metric,
                **state,
            ),
        )
        for focus in FOCUSES
    )


def _metric_options(
    active: str, resolved, state: dict, focus_key: str, orders_label: str
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
                focus=focus_key,
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
