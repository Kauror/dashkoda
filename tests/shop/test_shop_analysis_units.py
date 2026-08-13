"""The analytical pieces that need no database, checked on their own."""

from __future__ import annotations

from decimal import Decimal

from apps.shop.selectors import (
    MIN_VIEWS_FOR_OPPORTUNITY,
    MixBreakdown,
    MoverRow,
    PageViewFigure,
    ProductRow,
    concentration_share,
    get_web_opportunities,
)


def product(pk: int, *, units="0", views=None, conversion_units=None, title=None) -> ProductRow:
    figure = None if views is None else PageViewFigure(path=f"/p/{pk}", views=views)
    return ProductRow(
        source_product_id=pk,
        product_type="document",
        title=title or f"Toode {pk}",
        category_term_id=159,
        category_name="Töösuhted",
        published=True,
        publicly_listed=None,
        product_path=f"/p/{pk}",
        units=Decimal(units),
        product_page_views=figure,
        conversion_units=Decimal(conversion_units if conversion_units is not None else units),
    )


# ---------------------------------------------------------------------------
# Free / paid
# ---------------------------------------------------------------------------


def test_the_free_share_ignores_the_unclassified_remainder():
    """A share of a total that includes "we do not know" is a share of nothing."""
    mix = MixBreakdown(free=Decimal("74"), paid=Decimal("26"), unknown=Decimal("50"))

    assert mix.free_share == Decimal("74.0")
    assert mix.total == Decimal("150")


def test_an_unclassified_mix_has_no_share():
    mix = MixBreakdown()

    assert mix.is_known is False
    assert mix.free_share is None


def test_a_classified_mix_of_nothing_has_no_share():
    mix = MixBreakdown(free=Decimal("0"), paid=Decimal("0"), unknown=Decimal("0"))

    assert mix.is_known is True
    assert mix.free_share is None


# ---------------------------------------------------------------------------
# Movers
# ---------------------------------------------------------------------------


def test_a_mover_with_no_previous_activity_is_new_rather_than_infinite():
    row = MoverRow(
        source_product_id=1,
        title="Uus põhi",
        category_name="Töösuhted",
        current_units=Decimal("37"),
        previous_units=Decimal("0"),
    )

    assert row.is_new is True
    assert row.percentage_change is None
    assert row.change == Decimal("37")


def test_a_mover_percentage_is_offered_beside_the_absolute_change():
    row = MoverRow(
        source_product_id=1,
        title="Koondamisteade",
        category_name="Töösuhted",
        current_units=Decimal("68"),
        previous_units=Decimal("100"),
    )

    assert row.change == Decimal("-32")
    assert row.percentage_change == Decimal("-32")


# ---------------------------------------------------------------------------
# Concentration
# ---------------------------------------------------------------------------


def test_concentration_is_withheld_when_the_population_is_too_small():
    """ "The top 10 of 9 products are 100%" is arithmetic, not insight."""
    rows = [product(i, units="5") for i in range(9)]

    assert concentration_share(rows, top=10) is None


def test_concentration_reports_the_share_of_the_largest_products():
    rows = [product(i, units="10") for i in range(10)] + [product(i, units="1") for i in range(20)]

    # 100 units in the top ten, 120 overall.
    assert concentration_share(rows, top=10) == Decimal("83")


# ---------------------------------------------------------------------------
# Web opportunities
# ---------------------------------------------------------------------------


def test_a_tiny_denominator_is_never_classified():
    """One acquisition on four views is not a 25-per-100 success story."""
    rows = [product(1, units="1", views=4)]

    weak, strong = get_web_opportunities(rows)

    assert weak == ()
    assert strong == ()


def test_an_unmeasured_product_is_never_classified():
    rows = [product(1, units="50", views=None)]

    weak, strong = get_web_opportunities(rows)

    assert weak == ()
    assert strong == ()


def test_attention_without_acquisition_and_its_opposite_are_separated():
    busy_but_quiet = product(1, units="5", views=5000, title="Palju vaatamisi")
    quiet_but_busy = product(2, units="60", views=MIN_VIEWS_FOR_OPPORTUNITY, title="Tugev ost")
    rows = [busy_but_quiet, quiet_but_busy]

    weak, strong = get_web_opportunities(rows)

    assert [r.title for r in weak][0] == "Palju vaatamisi"
    assert [r.title for r in strong][0] == "Tugev ost"


def test_the_threshold_is_inclusive_at_its_boundary():
    rows = [product(1, units="10", views=MIN_VIEWS_FOR_OPPORTUNITY)]

    weak, _ = get_web_opportunities(rows)

    assert len(weak) == 1
