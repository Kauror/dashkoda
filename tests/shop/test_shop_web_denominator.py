"""Which page a rate divides by, and how shared pages are counted.

These are the rules that decide whether a number is a rate of anything, and all
of them are pure functions — so they are checked here without a database, where
a failure names the rule rather than a page.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.shop.models import PageRole, ProductType
from apps.shop.selectors import PageViewFigure, ProductRow, build_category_rows
from apps.shop.web_effectiveness import (
    aggregate_web,
    denominator_path,
    denominator_role,
)


def product(
    pk: int,
    *,
    product_type=ProductType.DOCUMENT,
    units="0",
    conversion_units=None,
    product_path="",
    information_path="",
    event_path="",
    product_views=None,
    information_views=None,
    event_views=None,
    category="Töösuhted",
    term_id=159,
) -> ProductRow:
    def figure(path, views):
        if not path or views is None:
            return None
        return PageViewFigure(path=path, views=views)

    return ProductRow(
        source_product_id=pk,
        product_type=product_type,
        title=f"Toode {pk}",
        category_term_id=term_id,
        category_name=category,
        published=True,
        publicly_listed=None,
        product_path=product_path,
        information_path=information_path,
        event_path=event_path,
        units=Decimal(units),
        ordered_value_net=Decimal(units),
        product_page_views=figure(product_path, product_views),
        information_page_views=figure(information_path, information_views),
        event_page_views=figure(event_path, event_views),
        conversion_units=Decimal(conversion_units if conversion_units is not None else units),
    )


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("product_type", "expected"),
    [
        (ProductType.DOCUMENT, PageRole.PRODUCT),
        (ProductType.EVENT_REGISTRATION, PageRole.EVENT),
        (ProductType.PHYSICAL_PRODUCT, PageRole.PRODUCT),
    ],
)
def test_each_family_divides_by_its_own_acquisition_page(product_type, expected):
    assert denominator_role(product_type) == expected


def test_an_event_registration_uses_its_event_page():
    """The regression this module exists for.

    An event product has no `/et/pood/` page, so a policy that always reached
    for `PageRole.PRODUCT` gave every event a rate of `None` — which looked like
    missing data rather than like the wrong question being asked.
    """
    row = product(
        1,
        product_type=ProductType.EVENT_REGISTRATION,
        units="8",
        event_path="/et/sundmused/seminar",
        event_views=200,
    )

    assert row.denominator_path == "/et/sundmused/seminar"
    assert row.acquisitions_per_hundred == Decimal("4.0")


def test_a_document_divides_by_its_product_page_not_its_information_page():
    """Both pages exist and only one carries the buy action.

    The information page carries roughly a hundred times less traffic on the
    real dataset, so substituting it would not blur the rate — it would invert
    the ranking.
    """
    row = product(
        1,
        units="10",
        product_path="/et/pood/leping",
        information_path="/et/tooriistad/leping",
        product_views=500,
        information_views=5,
    )

    assert row.denominator_path == "/et/pood/leping"
    assert row.acquisitions_per_hundred == Decimal("2.0")


def test_a_missing_denominator_page_yields_no_rate_and_no_substitute():
    """§137: never silently fall back to a different page role."""
    row = product(
        1,
        units="10",
        information_path="/et/tooriistad/leping",
        information_views=900,
    )

    assert denominator_path(ProductType.DOCUMENT, {PageRole.INFORMATION: "/x"}) == ""
    assert row.denominator_path == ""
    assert row.denominator_page_views is None
    assert row.acquisitions_per_hundred is None


def test_a_measured_page_with_no_views_is_a_rate_of_nothing_not_zero():
    row = product(1, units="4", product_path="/et/pood/a", product_views=0)

    assert row.acquisitions_per_hundred is None


# ---------------------------------------------------------------------------
# Shared paths
# ---------------------------------------------------------------------------


def test_two_products_on_one_event_page_count_its_views_once():
    """§33/§138: the aggregate deduplicates paths before summing views.

    An early-bird and a full-price registration for one seminar legitimately map
    to one event page. Summing each row's own figure would double that page's
    traffic — and would do it for exactly the products most likely to share.
    """
    shared = "/et/sundmused/seminar"
    rows = [
        product(
            1,
            product_type=ProductType.EVENT_REGISTRATION,
            units="6",
            event_path=shared,
            event_views=400,
        ),
        product(
            2,
            product_type=ProductType.EVENT_REGISTRATION,
            units="2",
            event_path=shared,
            event_views=400,
        ),
    ]

    web = aggregate_web(rows)

    assert web.views == 400, "the shared page's traffic was counted twice"
    # Both products' acquisitions belong over that page: it sold both.
    assert web.units == Decimal("8")
    assert web.acquisitions_per_hundred == Decimal("2.0")


def test_each_product_still_keeps_the_shared_page_as_its_own_denominator():
    """Documented behaviour: dedup is an aggregate rule, not a per-product one."""
    shared = "/et/sundmused/seminar"
    row = product(
        1,
        product_type=ProductType.EVENT_REGISTRATION,
        units="6",
        event_path=shared,
        event_views=400,
    )

    assert row.acquisitions_per_hundred == Decimal("1.5")


def test_distinct_paths_are_added_together():
    rows = [
        product(1, units="1", product_path="/a", product_views=100),
        product(2, units="1", product_path="/b", product_views=50),
    ]

    assert aggregate_web(rows).views == 150


def test_a_category_rollup_does_not_double_count_a_shared_page():
    """The rollup runs through the same helper, so the rule holds there too."""
    shared = "/et/sundmused/seminar"
    rows = [
        product(
            1,
            product_type=ProductType.EVENT_REGISTRATION,
            units="6",
            event_path=shared,
            event_views=400,
            category="Sündmused",
            term_id=42,
        ),
        product(
            2,
            product_type=ProductType.EVENT_REGISTRATION,
            units="2",
            event_path=shared,
            event_views=400,
            category="Sündmused",
            term_id=42,
        ),
    ]

    (row,) = build_category_rows(rows)

    assert row.product_count == 2
    assert row.units == Decimal("8")
    assert row.product_page_views == 400


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_coverage_separates_no_page_from_an_unmeasured_page():
    """§80: three states, because only one of them might change next month."""
    rows = [
        product(1, units="1", product_path="/a", product_views=100),
        product(2, units="1", product_path="/b"),  # page exists, never measured
        product(3, units="1"),  # no acquisition page at all
    ]

    coverage = aggregate_web(rows).coverage

    assert coverage.measured == 1
    assert coverage.without_measurement == 1
    assert coverage.without_path == 1
    assert coverage.total == 3
    assert coverage.is_complete is False


def test_an_entirely_unmeasured_population_has_no_view_total():
    """`None`, not `0`: nobody measured it, which is not "nobody visited"."""
    rows = [product(1, units="1"), product(2, units="2")]

    web = aggregate_web(rows)

    assert web.views is None
    assert web.has_views is False
    assert web.acquisitions_per_hundred is None
