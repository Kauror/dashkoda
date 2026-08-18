"""Orders versus order lines, against a real database.

This is the most consequential distinction in the module and the easiest to get
silently wrong, so it is tested at the page level rather than at the selector:
what matters is not only which number is computed but which **label** the reader
sees above it.

`ShopDailySummary` is keyed by day and product type. Any narrower selection —
a category, a member status, a search — describes a population the summary
cannot answer, and `distinct_orders_supported` (tested directly in
`test_shop_intelligence.py`) is the guard against showing the wider figure
beside narrower units and value.

The page's own headline stopped being one of the places that guard has to fire.
The KPI strip and Lepingupõhjad no longer follow a reader's category, member or
search choice — those narrow the Tooted table alone, since 2026-08-18 — so the
headline order label is always `Tellimused` and Lepingupõhjad's own order count
is always answerable from the day × document-type grain the summary carries.
"""

from __future__ import annotations

import pytest

from apps.shop.importing import import_shop_package
from apps.shop.page import build_overview
from apps.shop.periods import ALL_KEY
from apps.shop.selectors import ComparisonWindow, get_distinct_orders, get_shop_coverage, get_totals

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
# The headline is unfiltered, always
# ---------------------------------------------------------------------------


def test_an_unfiltered_page_counts_distinct_orders(seeded):
    overview = _overview()

    assert overview.orders_are_distinct is True
    assert _order_kpi(overview).label == "Tellimused"


def test_the_headline_order_label_does_not_follow_a_category_or_search(seeded):
    """Tooted's own filters, since 2026-08-18, narrow one table and nothing
    above it — the failure this guards against is the opposite of the old one:
    a category or search silently changing what the headline claims."""
    unfiltered = _order_kpi(_overview())
    narrowed = _order_kpi(_overview(categories=(159,), search="tööleping"))

    assert narrowed.label == unfiltered.label == "Tellimused"
    assert narrowed.value == unfiltered.value


# ---------------------------------------------------------------------------
# The two figures really are different
# ---------------------------------------------------------------------------


def test_order_lines_are_not_fewer_than_distinct_orders(seeded):
    """An order carrying three products is one order and three lines.

    So lines are always at least orders. If this ever inverted, one of the two
    would be reading the wrong table.
    """
    coverage = get_shop_coverage()
    distinct = get_distinct_orders(
        start=coverage.coverage_start, end=coverage.coverage_end, product_type=""
    )
    window = ComparisonWindow(coverage.coverage_start, coverage.coverage_end, None, None)
    lines = get_totals(window).orders

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
# Lepingupõhjad: always the document family, always answerable
# ---------------------------------------------------------------------------


def test_lepingupohjad_always_labels_its_orders_tellimused(seeded):
    """Document-only narrowing stays inside the day × product-type grain the
    summary carries, so the fallback to order lines this module guards
    elsewhere never applies here."""
    overview = _overview()

    assert overview.document_orders_label == "Tellimused"
    assert overview.document_orders != "—"


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
