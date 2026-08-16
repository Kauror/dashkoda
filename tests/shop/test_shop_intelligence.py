"""The deterministic intelligence layer, checked without a database.

Every rule here is a threshold on a measured figure. The tests are mostly about
what the dashboard **refuses** to say: no per-order figure under a filter the
daily summary cannot answer, no rate on a denominator too small to mean
anything, and no claim that a product is new because its previous window was
empty.
"""

from __future__ import annotations

from decimal import Decimal

from apps.shop.periods import (
    DEFAULT_FOCUS,
    FOCUS_PRODUCTS,
    METRIC_UNITS,
    METRIC_VALUE,
    build_query,
    parse_focus,
    parse_metric,
)
from apps.shop.selectors import (
    MoverRow,
    distinct_orders_supported,
    get_concentration,
    long_tail_count,
)

from .test_shop_web_denominator import product

# ---------------------------------------------------------------------------
# The distinct-order grain guard
# ---------------------------------------------------------------------------


def test_an_unfiltered_population_may_use_distinct_orders():
    assert distinct_orders_supported() is True


def test_a_category_filter_may_not_reuse_the_type_wide_order_count():
    """§24/§134: the defect this guard exists for.

    `ShopDailySummary` is keyed by day and product type. Under a category
    filter its total describes a wider population than the units and value
    beside it, and nothing on screen would say so.
    """
    assert distinct_orders_supported(category_term_ids=(159,)) is False


def test_a_member_filter_may_not_reuse_the_type_wide_order_count():
    assert distinct_orders_supported(member_status="member") is False


def test_a_search_may_not_reuse_the_type_wide_order_count():
    """Search narrows the product population exactly as a category does."""
    assert distinct_orders_supported(search="tööleping") is False


# ---------------------------------------------------------------------------
# Concentration
# ---------------------------------------------------------------------------


def test_the_long_tail_counts_the_fewest_products_reaching_eighty_percent():
    rows = [
        product(1, units="80"),
        product(2, units="10"),
        product(3, units="5"),
        product(4, units="5"),
    ]

    assert long_tail_count(rows) == 1


def test_an_even_population_needs_most_of_itself_to_reach_eighty_percent():
    rows = [product(pk, units="10") for pk in range(1, 11)]

    assert long_tail_count(rows) == 8


def test_there_is_no_eighty_percent_of_nothing():
    rows = [product(1, units="0"), product(2, units="0")]

    assert long_tail_count(rows) is None
    assert get_concentration(rows).top_share is None


def test_concentration_can_be_measured_on_ordered_value_separately():
    """§70: unit concentration and value concentration are different questions."""
    rows = [
        product(1, units="1"),  # ordered_value_net mirrors units in the factory
        product(2, units="99"),
    ]

    assert long_tail_count(rows, by_value=True) == 1


# ---------------------------------------------------------------------------
# "New" means new to the comparison
# ---------------------------------------------------------------------------


def test_an_empty_previous_window_is_not_a_product_launch():
    """§72: purchase activity does not establish when a product launched."""
    mover = MoverRow(
        source_product_id=1,
        title="Tööleping",
        category_name="Töösuhted",
        current_units=Decimal("5"),
        previous_units=Decimal("0"),
    )

    assert mover.is_new is True
    assert mover.percentage_change is None

    from apps.shop.page import MoverPresenter

    presenter = MoverPresenter(mover)
    assert presenter.context == "uus perioodil"
    assert "toode" not in presenter.context
    assert presenter.context_title == "Eelmisel perioodil oste ei olnud."


# ---------------------------------------------------------------------------
# Focus and metric state
# ---------------------------------------------------------------------------


def test_an_unknown_focus_lands_on_the_overview():
    """A rotted bookmark must render a page, not raise."""
    assert parse_focus("ei-ole-olemas") is DEFAULT_FOCUS
    assert parse_focus(None) is DEFAULT_FOCUS
    assert parse_focus("") is DEFAULT_FOCUS


def test_a_known_focus_resolves_to_itself():
    assert parse_focus(FOCUS_PRODUCTS).key == FOCUS_PRODUCTS


def test_an_unknown_metric_falls_back_to_units():
    assert parse_metric("nonsense") == METRIC_UNITS
    assert parse_metric(METRIC_VALUE) == METRIC_VALUE


def test_the_default_focus_is_omitted_from_a_url():
    """`/epood/` stays the canonical address for the default view."""
    query = build_query(period_key="1a", focus=DEFAULT_FOCUS.key)

    assert "fookus" not in query


def test_a_focus_link_carries_the_rest_of_the_state():
    query = build_query(
        period_key="90",
        focus=FOCUS_PRODUCTS,
        product_type="document",
        categories=(159,),
        search="tööleping",
    )

    assert "fookus=tooted" in query
    assert "periood=90" in query
    assert "liik=document" in query
    assert "kategooria=159" in query
    assert "otsing=t" in query
