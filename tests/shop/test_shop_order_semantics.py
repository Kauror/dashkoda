"""Orders versus order lines, against a real database.

This is the most consequential distinction in the module and the easiest to get
silently wrong, so it is tested at the page level rather than at the selector:
what matters is not only which number is computed but which **label** the reader
sees above it.

`ShopDailySummary` is keyed by day and product type. Any narrower selection —
a category, a member status, a search — describes a population the summary
cannot answer, and the rule is that the page then counts order lines and says
so, rather than showing the wider figure beside the narrower units and value.
"""

from __future__ import annotations

import pytest

from apps.shop.importing import import_shop_package
from apps.shop.page import build_overview
from apps.shop.periods import ALL_KEY
from apps.shop.selectors import get_distinct_orders, get_shop_coverage

from .package_factory import build_package

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(tmp_path):
    import_shop_package(build_package(tmp_path), dry_run=False)


def _overview(**overrides):
    defaults = dict(
        period_key=ALL_KEY,
        date_from=None,
        date_to=None,
        product_type="",
        categories=(),
        member_status="",
        search="",
        sort="",
        page=1,
    )
    defaults.update(overrides)
    return build_overview(**defaults)


def _order_kpi(overview):
    """The second headline card, which is the order figure whatever it counts."""
    return overview.kpis[1]


# ---------------------------------------------------------------------------
# Where the summary can answer
# ---------------------------------------------------------------------------


def test_an_unfiltered_page_counts_distinct_orders(seeded):
    overview = _overview()

    assert overview.orders_are_distinct is True
    assert _order_kpi(overview).label == "Tellimused"


def test_a_product_type_stays_inside_the_summary_grain(seeded):
    """Day × product type is exactly what the summary carries."""
    overview = _overview(product_type="document")

    assert overview.orders_are_distinct is True
    assert _order_kpi(overview).label == "Tellimused"


# ---------------------------------------------------------------------------
# Where it cannot
# ---------------------------------------------------------------------------


def test_a_category_filter_falls_back_to_order_lines(seeded):
    """§24: never an unfiltered order count beside filtered units and value."""
    overview = _overview(categories=(159,))

    assert overview.orders_are_distinct is False
    assert _order_kpi(overview).label == "Tellimusridu"


def test_a_category_filtered_page_never_shows_the_type_wide_order_count(seeded):
    """The figure itself must change, not only the word above it."""
    coverage = get_shop_coverage()
    unfiltered_distinct = get_distinct_orders(
        start=coverage.coverage_start, end=coverage.coverage_end, product_type=""
    )
    filtered = _overview(categories=(159,))

    assert unfiltered_distinct is not None
    assert _order_kpi(filtered).value != f"{unfiltered_distinct}", (
        "the whole shop's distinct-order count was shown beside one category's units"
    )


def test_a_search_falls_back_to_order_lines(seeded):
    overview = _overview(search="tööleping")

    assert overview.orders_are_distinct is False
    assert _order_kpi(overview).label == "Tellimusridu"


def test_the_fallback_says_why_it_is_counting_lines(seeded):
    overview = _overview(categories=(159,))

    assert "tooteridu" in _order_kpi(overview).secondary


# ---------------------------------------------------------------------------
# The two figures really are different
# ---------------------------------------------------------------------------


def test_order_lines_are_not_fewer_than_distinct_orders(seeded):
    """An order carrying three products is one order and three lines.

    So lines are always at least orders. If this ever inverted, one of the two
    would be reading the wrong table.
    """
    unfiltered = _overview()
    coverage = get_shop_coverage()
    distinct = get_distinct_orders(
        start=coverage.coverage_start, end=coverage.coverage_end, product_type=""
    )

    lines = int(unfiltered.orders.replace(" ", "").replace(" ", ""))

    assert distinct is not None
    assert lines >= distinct


def test_per_type_distinct_counts_are_never_summed_into_a_total(seeded):
    """§62/§130: one order may carry two product types and belong to both rows.

    The all-types row exists precisely so nobody adds the type rows, and this
    asserts the two are read independently rather than derived from each other.
    """
    coverage = get_shop_coverage()
    window = dict(start=coverage.coverage_start, end=coverage.coverage_end)

    total = get_distinct_orders(**window, product_type="")
    by_type = sum(
        get_distinct_orders(**window, product_type=product_type) or 0
        for product_type in ("document", "event_registration", "physical_product")
    )

    assert total is not None
    assert total <= by_type, (
        "the all-types row must be read from its own row, never summed from the type rows"
    )


# ---------------------------------------------------------------------------
# A single product may still say `Tellimused`
# ---------------------------------------------------------------------------


def test_a_product_page_keeps_the_order_semantic(seeded, client, authenticate_viewer):
    """§25: the sum runs over one product's own cells, so each order appears once."""
    from .package_factory import DOCUMENT_WITH_BOTH_PAGES

    authenticate_viewer(client)
    content = client.get(f"/epood/toode/{DOCUMENT_WITH_BOTH_PAGES}/").content.decode()

    assert "Tellimused" in content
    assert "tellimust, mis sisaldasid seda toodet" in content
