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

from dataclasses import dataclass
from decimal import Decimal

from apps.core.formatting import euros, group_thousands, short_date

from .models import MemberStatus, PageRole, PaymentClass, ProductType
from .periods import (
    PARAM_CATEGORY,
    PARAM_MEMBER,
    PARAM_SEARCH,
    PARAM_TYPE,
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
    CategoryRow,
    ComparisonWindow,
    ProductRow,
    acquisitions_per_hundred,
    build_category_rows,
    build_product_rows,
    get_categories,
    get_member_split,
    get_monthly_series,
    get_payment_split,
    get_product,
    get_shop_coverage,
    get_totals,
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

    @property
    def has_rows(self) -> bool:
        return bool(self.products)

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages


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

    state = {
        "product_type": product_type,
        "categories": categories,
        "search": search,
        "sort": sort if sort != SORT_UNITS else "",
        "member_status": effective_member,
    }

    return ShopOverview(
        has_source=True,
        as_of_label=short_date(coverage.source_as_of),
        coverage_label=(
            f"{short_date(coverage.coverage_start)}–{short_date(coverage.coverage_end)}"
        ),
        window=window,
        web_interval_label=(
            f"{short_date(window.web_start)}–{short_date(window.web_end)}" if window.has_web else ""
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
    out = []
    for key in (SORT_UNITS, SORT_VALUE, SORT_VIEWS, SORT_CONVERSION, SORT_TITLE):
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
        as_of_label=short_date(coverage.source_as_of),
        coverage_label=(
            f"{short_date(coverage.coverage_start)}–{short_date(coverage.coverage_end)}"
        ),
        web_interval_label=(
            f"{short_date(window.web_start)}–{short_date(window.web_end)}" if window.has_web else ""
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
