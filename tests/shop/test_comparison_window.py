"""The two-window clamping rule, checked without a database.

This is the arithmetic that decides whether a conversion rate is honest, and it
is deliberately pure so it can be read and tested on its own.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from apps.shop.selectors import (
    ShopCoverage,
    acquisitions_per_hundred,
    clamp_windows,
)
from apps.visibility.ga4_selectors import Coverage as Ga4Coverage

# The real spans, used as the default fixture because the interesting cases are
# all about how these two disagree.
SHOP = ShopCoverage(
    source_as_of=date(2026, 8, 11),
    coverage_start=date(2020, 10, 22),
    coverage_end=date(2026, 8, 11),
)
GA4 = Ga4Coverage(
    earliest=date(2023, 6, 16),
    latest=date(2026, 9, 30),  # GA4 keeps collecting after the export froze
    days_covered=1200,
    days_with_pages=1200,
)


def window(start=None, end=None, *, shop=SHOP, ga4=GA4):
    return clamp_windows(start=start, end=end, shop=shop, ga4=ga4)


def test_all_time_uses_commerce_coverage_not_today():
    result = window()
    assert result.commerce_start == date(2020, 10, 22)
    assert result.commerce_end == date(2026, 8, 11)


def test_web_window_starts_at_ga4_coverage():
    result = window()
    assert result.web_start == date(2023, 6, 16)


def test_web_window_never_runs_past_commerce_coverage():
    """The rule this module exists for.

    GA4 has data to 30 September; the export stops on 11 August. Traffic after
    that date has no orders to compare against — not zero orders.
    """
    result = window()
    assert result.web_end == date(2026, 8, 11)
    assert result.web_end == SHOP.coverage_end


def test_period_entirely_after_commerce_coverage_has_no_web_window():
    """A September period against an August export compares nothing."""
    result = window(date(2026, 9, 1), date(2026, 9, 30))
    assert result.has_commerce is False
    assert result.has_web is False


def test_period_entirely_before_ga4_has_commerce_but_no_web():
    result = window(date(2021, 1, 1), date(2021, 12, 31))
    assert result.has_commerce is True
    assert result.commerce_start == date(2021, 1, 1)
    assert result.has_web is False


def test_period_inside_both_is_not_partial():
    result = window(date(2024, 1, 1), date(2024, 6, 30))
    assert result.has_web is True
    assert result.web_start == date(2024, 1, 1)
    assert result.web_end == date(2024, 6, 30)
    assert result.web_is_partial is False


def test_period_straddling_ga4_start_is_partial():
    result = window(date(2022, 1, 1), date(2024, 1, 1))
    assert result.web_is_partial is True
    assert result.web_start == date(2023, 6, 16)


def test_selected_end_beyond_coverage_is_clamped():
    result = window(date(2026, 1, 1), date(2027, 1, 1))
    assert result.commerce_end == date(2026, 8, 11)


def test_no_shop_data_yields_nothing():
    result = window(shop=ShopCoverage())
    assert result.has_commerce is False
    assert result.has_web is False


def test_no_ga4_data_still_yields_commerce():
    result = window(ga4=Ga4Coverage())
    assert result.has_commerce is True
    assert result.has_web is False


def test_ga4_ending_before_commerce_uses_the_earlier_end():
    """When GA4 is the shorter of the two, it is the one that clamps."""
    ga4 = Ga4Coverage(
        earliest=date(2023, 6, 16),
        latest=date(2025, 12, 31),
        days_covered=900,
        days_with_pages=900,
    )
    result = window(ga4=ga4)
    assert result.web_end == date(2025, 12, 31)


# ---------------------------------------------------------------------------
# The rate
# ---------------------------------------------------------------------------


def test_rate_is_per_hundred_views():
    assert acquisitions_per_hundred(Decimal("12"), 400) == Decimal("3.0")


def test_rate_is_unknown_without_views():
    assert acquisitions_per_hundred(Decimal("12"), None) is None


def test_rate_is_unknown_rather_than_infinite_at_zero_views():
    """A product with acquisitions and no measured views has no rate."""
    assert acquisitions_per_hundred(Decimal("12"), 0) is None


def test_rate_of_zero_acquisitions_is_zero_not_unknown():
    assert acquisitions_per_hundred(Decimal("0"), 400) == Decimal("0.0")
