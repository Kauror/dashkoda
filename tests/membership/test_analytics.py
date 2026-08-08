"""The comparison rules, tested as decisions rather than as arithmetic.

Each test here names a way the page could lie and pins the refusal that stops
it. None of them touches PostgreSQL: `apps.membership.analytics` is handed the
points a caller already holds, which is what makes the rules testable at all.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.membership.analytics import (
    YOY_TOLERANCE_DAYS,
    Unavailable,
    anniversary,
    change,
    compare_with,
    cumulative,
    elapsed_total,
    mean_of_complete_years,
    net_movement,
    pick_comparable,
    share_change,
    value_domain,
)

JULY = dt.date(2026, 7, 31)


def day(year: int, month: int, number: int) -> dt.date:
    return dt.date(year, month, number)


# -- picking the baseline ------------------------------------------------


def test_the_year_ago_baseline_is_the_observation_nearest_the_anniversary():
    history = (
        (day(2025, 6, 30), 3500),
        (day(2025, 7, 28), 3547),
        (day(2025, 9, 30), 3510),
    )

    found = pick_comparable(history, anniversary(JULY))

    assert found == (day(2025, 7, 28), 3547)


def test_nothing_inside_the_tolerance_means_no_comparison():
    """The nearest report is five months out. That is not "a year ago"."""
    history = ((day(2025, 2, 28), 3500),)

    comparison = compare_with(3412, JULY, history)

    assert not comparison.is_available
    assert comparison.unavailable_reason == Unavailable.OUT_OF_TOLERANCE
    # The current value is still known and still offered; only the comparison
    # is missing, and the page can show the figure without it.
    assert comparison.current == 3412


def test_the_tolerance_boundary_is_inclusive():
    inside = ((JULY - dt.timedelta(days=365 + YOY_TOLERANCE_DAYS), 3500),)
    outside = ((JULY - dt.timedelta(days=365 + YOY_TOLERANCE_DAYS + 1), 3500),)

    assert compare_with(3412, JULY, inside).is_available
    assert not compare_with(3412, JULY, outside).is_available


def test_a_tie_resolves_to_the_earlier_observation():
    """Deterministic, so one page does not render two baselines on two loads."""
    target = anniversary(JULY)
    history = (
        (target - dt.timedelta(days=10), 3400),
        (target + dt.timedelta(days=10), 3450),
    )

    assert pick_comparable(history, target)[1] == 3400


def test_a_history_shorter_than_a_year_has_no_year_ago_figure():
    history = ((day(2026, 3, 31), 3390),)

    comparison = compare_with(3412, JULY, history)

    assert not comparison.is_available


def test_the_current_observation_is_never_its_own_baseline():
    history = ((JULY, 3412),)

    assert not compare_with(3412, JULY, history).is_available


def test_february_29_compares_against_february_28():
    assert anniversary(dt.date(2024, 2, 29)) == dt.date(2023, 2, 28)


# -- the arithmetic, and where it refuses --------------------------------


def test_absolute_and_relative_change():
    absolute, relative = change(3412, 3547)

    assert absolute == -135
    assert relative == Decimal("-3.81")


def test_growth_from_zero_has_no_percentage():
    """Not 100%, not infinite. The absolute change is real; the rate is not."""
    absolute, relative = change(27, 0)

    assert absolute == 27
    assert relative is None


def test_a_missing_value_never_becomes_a_zero():
    assert change(None, 3500) == (None, None)
    assert change(3500, None) == (None, None)


def test_an_explicit_zero_is_compared_like_any_other_number():
    absolute, relative = change(0, 40)

    assert absolute == -40
    assert relative == Decimal("-100.00")


def test_a_zero_baseline_still_reports_the_absolute_change():
    comparison = compare_with(27, JULY, ((day(2025, 7, 31), 0),))

    assert comparison.is_available
    assert comparison.absolute == 27
    assert not comparison.has_relative


def test_a_share_moves_in_percentage_points_not_percent():
    """92,6 → 96,0 is 3,4 points. Calling it 3,7% would also be true and is a
    different number, which is exactly why the unit is stated."""
    assert share_change(Decimal("96.0"), Decimal("92.6")) == Decimal("3.4")


def test_a_share_change_with_a_missing_side_is_nothing():
    assert share_change(None, Decimal("92.6")) is None


def test_a_provisional_baseline_is_used_but_flagged():
    comparison = compare_with(
        3412,
        JULY,
        ((day(2025, 7, 31), 3547),),
        provisional_dates=frozenset({day(2025, 7, 31)}),
    )

    assert comparison.is_available
    assert comparison.baseline_is_provisional


# -- net movement --------------------------------------------------------


@pytest.mark.parametrize(
    ("joined", "removed", "expected"),
    [(21, 38, -17), (27, 22, 5), (14, 14, 0), (8, 0, 8)],
)
def test_net_movement_is_joined_minus_removed(joined, removed, expected):
    assert net_movement(joined, removed) == expected


def test_a_band_missing_one_side_has_no_net():
    """Arrivals alone under a "net" heading would read as a measured gain."""
    assert net_movement(21, None) is None
    assert net_movement(None, 38) is None


# -- cumulative recruitment ----------------------------------------------


def test_a_complete_year_accumulates_through():
    series = cumulative(((1, 20), (2, 15), (3, 25)))

    assert series.values == ((1, 20), (2, 35), (3, 60))
    assert series.is_complete


def test_an_explicit_zero_month_accumulates_as_zero():
    series = cumulative(((1, 20), (2, 0), (3, 25)))

    assert series.values == ((1, 20), (2, 20), (3, 45))
    assert series.is_complete


def test_the_cumulative_line_stops_at_the_first_unknown_month():
    """Carrying on past a missing month would draw a flatter slope and read as
    "recruitment slowed" — a claim about the year that came from a missing row."""
    series = cumulative(((1, 20), (2, None), (3, 25)))

    assert series.values == ((1, 20),)
    assert series.stopped_at == 2
    assert not series.is_complete


def test_a_missing_month_is_never_treated_as_zero_in_the_total():
    stopped = cumulative(((1, 20), (2, None), (3, 25)))
    zeroed = cumulative(((1, 20), (2, 0), (3, 25)))

    assert stopped.values[-1][1] == 20
    assert zeroed.values[-1][1] == 45


# -- same period last year ------------------------------------------------


def test_the_elapsed_total_sums_only_the_months_that_have_passed():
    months = tuple((m, 10 + m) for m in range(1, 13))

    assert elapsed_total(months, through=7) == sum(10 + m for m in range(1, 8))


def test_an_elapsed_total_with_a_missing_month_is_unavailable():
    """A partial sum compared against a complete one is the July-year-to-date
    against last year's twelve months, which is a collapse that never happened."""
    months = ((1, 20), (2, None), (3, 25))

    assert elapsed_total(months, through=3) is None


def test_an_elapsed_total_needs_every_month_up_to_the_cutoff():
    assert elapsed_total(((1, 20), (3, 25)), through=3) is None


# -- historical benchmark -------------------------------------------------


def test_the_three_year_average_needs_every_named_year():
    by_year = {
        2023: ((7, 20),),
        2024: ((7, 24),),
        2025: ((7, 25),),
    }

    assert mean_of_complete_years(by_year, period=7, years=(2023, 2024, 2025)) == Decimal("23.0")


def test_a_year_missing_that_month_withdraws_the_average():
    """An average over "the years that happened to report" changes meaning from
    point to point, so the benchmark would be several series wearing one name."""
    by_year = {2023: ((7, 20),), 2024: (), 2025: ((7, 25),)}

    assert mean_of_complete_years(by_year, period=7, years=(2023, 2024, 2025)) is None


def test_an_average_over_no_years_is_nothing_rather_than_zero():
    assert mean_of_complete_years({}, period=7, years=()) is None


# -- the y axis, where a truthful series most easily becomes a lie ---------


def test_the_axis_is_never_anchored_at_zero():
    """A membership lives in a narrow band far from the origin. A 0–3 412 axis
    draws every real change as a flat line near the top of the frame."""
    domain = value_domain((3380, 3400, 3412))

    assert domain.minimum > 3000


def test_a_nearly_flat_series_is_drawn_nearly_flat():
    """32 members of movement on a base of 3 412 is one percent. Fitted tightly
    it reads as a cliff, and the reader takes away a collapse."""
    tight = value_domain((3380, 3412))

    assert tight.height >= Decimal("3412") * Decimal("0.05")


def test_a_genuinely_large_movement_is_not_compressed():
    """The floor is a minimum, not a target: real variation keeps its scale."""
    domain = value_domain((1000, 5000))

    assert domain.minimum < 1000
    assert domain.maximum > 5000
    assert domain.height > 4000


def test_the_domain_leaves_room_around_the_extremes():
    domain = value_domain((1000, 5000))

    assert domain.minimum < 1000, "the lowest point is not welded to the frame"
    assert domain.maximum > 5000


def test_a_single_observation_gets_a_domain_around_itself():
    """Not a zero-height axis, which has no drawable range at all."""
    domain = value_domain((3412,))

    assert domain.height > 0
    assert domain.minimum < 3412 < domain.maximum


def test_no_values_means_no_domain_rather_than_a_zero_axis():
    assert value_domain(()) is None
