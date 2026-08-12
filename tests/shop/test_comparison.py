"""The one comparison rule. Pure arithmetic, no database."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from apps.shop.comparison import (
    DOWN,
    FLAT,
    NO_COVERAGE,
    TOO_SHORT,
    UP,
    MetricComparison,
    derive_period_pair,
)

COVERAGE_START = date(2020, 10, 22)


def pair(start, end, coverage=COVERAGE_START):
    return derive_period_pair(current_start=start, current_end=end, coverage_start=coverage)


# ---------------------------------------------------------------------------
# The previous window
# ---------------------------------------------------------------------------


def test_previous_window_is_exactly_as_long_as_the_current_one():
    p = pair(date(2025, 8, 12), date(2026, 8, 11))

    assert p.is_available
    assert p.length_days == 365
    assert p.previous_start == date(2024, 8, 12)
    assert p.previous_end == date(2025, 8, 11)
    assert (p.previous_end - p.previous_start).days + 1 == 365


def test_previous_window_ends_the_day_before_the_current_one_starts():
    p = pair(date(2026, 1, 1), date(2026, 1, 31))

    assert p.previous_end == date(2025, 12, 31)
    assert p.previous_start == date(2025, 12, 1)


def test_a_leap_day_does_not_change_the_length_rule():
    p = pair(date(2024, 2, 1), date(2024, 2, 29))

    assert p.length_days == 29
    assert (p.previous_end - p.previous_start).days + 1 == 29


def test_a_window_reaching_behind_coverage_is_refused_not_truncated():
    """A 365-day period against the 83 days that exist is not a comparison."""
    p = pair(date(2020, 11, 1), date(2021, 10, 31))

    assert p.is_available is False
    assert p.unavailable_reason == TOO_SHORT
    assert p.previous_start is None


def test_a_window_starting_exactly_at_coverage_plus_its_length_is_allowed():
    p = pair(date(2021, 10, 22), date(2022, 10, 21), coverage=COVERAGE_START)

    assert p.is_available
    assert p.previous_start == COVERAGE_START


def test_no_coverage_means_no_comparison():
    p = pair(date(2026, 1, 1), date(2026, 1, 31), coverage=None)

    assert p.is_available is False
    assert p.unavailable_reason == NO_COVERAGE


def test_an_open_period_yields_no_comparison():
    p = pair(None, None)

    assert p.is_available is False
    assert p.has_current is False


def test_the_previous_label_states_the_actual_window():
    p = pair(date(2026, 1, 1), date(2026, 1, 31))

    assert p.previous_label == "01.12.2025–31.12.2025"


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


def test_a_fall_reports_both_the_absolute_and_the_percentage():
    c = MetricComparison.of(750, 798)

    assert c.absolute_change == Decimal("-48")
    assert c.percentage_change == Decimal("-6.0")
    assert c.direction == DOWN


def test_a_rise_points_up():
    c = MetricComparison.of(120, 100)

    assert c.percentage_change == Decimal("20.0")
    assert c.direction == UP


def test_no_change_is_flat_rather_than_a_tiny_arrow():
    c = MetricComparison.of(100, 100)

    assert c.absolute_change == 0
    assert c.direction == FLAT


def test_something_from_nothing_is_new_and_has_no_percentage():
    """A newly introduced template must never render as +∞%."""
    c = MetricComparison.of(37, 0)

    assert c.is_new is True
    assert c.percentage_change is None
    assert c.absolute_change == Decimal("37")


def test_nothing_from_nothing_is_not_new():
    c = MetricComparison.of(0, 0)

    assert c.is_new is False
    assert c.percentage_change is None
    assert c.direction == FLAT


def test_an_unavailable_comparison_is_distinct_from_a_zero_one():
    absent = MetricComparison.of(750, None)
    zero = MetricComparison.of(750, 0)

    assert absent.is_available is False
    assert absent.absolute_change is None
    assert absent.is_new is False

    assert zero.is_available is True
    assert zero.is_new is True


def test_decimal_money_stays_exact():
    c = MetricComparison.of(Decimal("5250.00"), Decimal("9855.00"))

    assert c.absolute_change == Decimal("-4605.00")
    assert c.percentage_change == Decimal("-46.7")
