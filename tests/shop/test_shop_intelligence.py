"""The deterministic intelligence layer, checked without a database.

Every rule here is a threshold on a measured figure. The tests are mostly about
what the dashboard **refuses** to say: no per-order figure under a filter the
daily summary cannot answer, no rate on a denominator too small to mean
anything, and no claim that a product is new because its previous window was
empty.
"""

from __future__ import annotations

from decimal import Decimal

from apps.shop.intelligence import (
    build_attention_matrix,
    build_order_structure,
    build_signals,
)
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
    MIN_VIEWS_FOR_OPPORTUNITY,
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
# Derived order metrics
# ---------------------------------------------------------------------------


def test_per_order_figures_are_withheld_when_the_grain_cannot_answer():
    """Withheld rather than approximated: order lines run 39% above orders."""
    structure = build_order_structure(
        units=Decimal("300"),
        ordered_value_net=Decimal("6000"),
        distinct_orders=200,
        supports_distinct=False,
    )

    assert structure.units_per_order is None
    assert structure.value_per_order is None
    assert structure.has_per_order is False
    # Both sides of this one come from the same filtered facts, so it survives.
    assert structure.value_per_unit == Decimal("20.00")


def test_per_order_figures_appear_when_the_grain_supports_them():
    structure = build_order_structure(
        units=Decimal("300"),
        ordered_value_net=Decimal("6000"),
        distinct_orders=200,
        supports_distinct=True,
    )

    assert structure.is_distinct is True
    assert structure.units_per_order == Decimal("1.50")
    assert structure.value_per_order == Decimal("30.00")


def test_no_units_means_no_value_per_unit_rather_than_a_division_by_zero():
    structure = build_order_structure(
        units=Decimal("0"),
        ordered_value_net=Decimal("0"),
        distinct_orders=0,
        supports_distinct=True,
    )

    assert structure.value_per_unit is None
    assert structure.has_any is False


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
    assert presenter.context_title == "Eelmisel perioodil soetusi ei olnud."


# ---------------------------------------------------------------------------
# The attention matrix
# ---------------------------------------------------------------------------


def test_the_matrix_excludes_products_below_the_view_floor():
    """A product with three views and one acquisition is not a 33-per-100 star."""
    rows = [
        product(1, units="1", product_path="/a", product_views=3),
        product(2, units="1", product_path="/b", product_views=4),
    ]

    matrix = build_attention_matrix(rows, minimum_views=MIN_VIEWS_FOR_OPPORTUNITY)

    assert matrix.is_available is False
    assert matrix.population == 0


def test_the_matrix_splits_measured_products_on_the_median():
    rows = [
        product(1, units="40", product_path="/a", product_views=1000),  # busy, converting
        product(2, units="1", product_path="/b", product_views=900),  # busy, weak
        product(3, units="30", product_path="/c", product_views=200),  # quiet, converting
        product(4, units="1", product_path="/d", product_views=150),  # quiet, weak
    ]

    matrix = build_attention_matrix(rows, minimum_views=MIN_VIEWS_FOR_OPPORTUNITY)
    counts = {cell.key: cell.count for cell in matrix.cells}

    assert matrix.population == 4
    assert counts == {"high_high": 1, "high_low": 1, "low_high": 1, "low_low": 1}


def test_an_unmeasured_product_is_not_a_low_traffic_product():
    rows = [
        product(1, units="40", product_path="/a", product_views=1000),
        product(2, units="5"),  # no page at all
    ]

    matrix = build_attention_matrix(rows, minimum_views=MIN_VIEWS_FOR_OPPORTUNITY)

    assert matrix.population == 1


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def _signals(**overrides):
    defaults = dict(
        units_change=None,
        units_percentage=None,
        weak_acquisition=(),
        strong_acquisition=(),
        product_fallers=(),
        category_fallers=(),
        free_share=None,
        previous_free_share=None,
        concentration=None,
        focus_query=lambda key: f"?fookus={key}",
        minimum_views=MIN_VIEWS_FOR_OPPORTUNITY,
    )
    defaults.update(overrides)
    return build_signals(**defaults)


def test_a_small_move_in_the_free_share_is_not_worth_stating():
    signals = _signals(free_share=Decimal("50"), previous_free_share=Decimal("48"))

    assert [s.kind for s in signals if s.kind == "free_share"] == []


def test_a_material_move_in_the_free_share_is_stated_in_percentage_points():
    signals = _signals(free_share=Decimal("60"), previous_free_share=Decimal("42"))
    (signal,) = [s for s in signals if s.kind == "free_share"]

    assert "protsendipunkti" in signal.text
    assert "18" in signal.text


def test_signals_never_explain_why():
    """§105/§106: evidence, not causes, and no opaque score."""
    signals = _signals(
        free_share=Decimal("60"),
        previous_free_share=Decimal("42"),
        product_fallers=(
            MoverRow(
                source_product_id=1,
                title="Tööleping",
                category_name="Töösuhted",
                current_units=Decimal("10"),
                previous_units=Decimal("40"),
            ),
        ),
    )
    text = " ".join(f"{s.text} {s.detail}" for s in signals).casefold()

    for forbidden in ("sest", "põhjus", "huvi kadu", "liiga kallis", "skoor", "hinnang"):
        assert forbidden not in text, f"a signal explained rather than measured: {forbidden}"


def test_the_period_direction_is_only_stated_when_nothing_specific_was_found():
    """It restates the headline, so it earns a place only as a fallback."""
    quiet = _signals(units_change=Decimal("12"), units_percentage=Decimal("24"))
    assert [s.kind for s in quiet] == ["units_trend"]

    busy = _signals(
        units_change=Decimal("12"),
        units_percentage=Decimal("24"),
        product_fallers=(
            MoverRow(
                source_product_id=1,
                title="Tööleping",
                category_name="Töösuhted",
                current_units=Decimal("10"),
                previous_units=Decimal("40"),
            ),
        ),
    )
    assert "units_trend" not in [s.kind for s in busy]


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
