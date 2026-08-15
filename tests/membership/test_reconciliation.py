"""The stock-and-flow check, and the periods it refuses to check.

The refusals are the important half. An identity computed across a gap in the
history would produce a confident residual describing a period nobody measured,
and it would look exactly like a real finding.

Runs without PostgreSQL — every observation is an unsaved model instance.
"""

from __future__ import annotations

import datetime as dt

from apps.membership.internal_selectors import ObservationPoint
from apps.membership.models import InternalMembershipObservation
from apps.membership.reconciliation import (
    YEAR_BOUNDARY_TOLERANCE_DAYS,
    Unreconcilable,
    reconcile_history,
    reconcile_year,
)


def point(
    day: dt.date,
    *,
    total: int | None = None,
    joined: int | None = None,
    removed: int | None = None,
    withheld: frozenset[str] = frozenset(),
) -> ObservationPoint:
    return ObservationPoint(
        observation=InternalMembershipObservation(
            observation_date=day,
            total_members=total,
            new_members_ytd=joined,
            removed_members_ytd=removed,
        ),
        withheld=withheld,
    )


def year_end(year: int, **kwargs) -> ObservationPoint:
    return point(dt.date(year, 12, 31), **kwargs)


# ---------------------------------------------------------------------------
# The identity
# ---------------------------------------------------------------------------


def test_a_year_whose_flows_explain_the_change_reconciles_exactly():
    points = (
        year_end(2024, total=3300),
        year_end(2025, total=3420, joined=300, removed=180),
    )

    result = reconcile_year(2025, points)

    assert result.is_available
    assert result.expected_total == 3420
    assert result.residual == 0
    assert result.reconciles


def test_a_residual_is_reported_and_nothing_is_corrected():
    """The residual is a question about four reported figures, not an answer."""
    points = (
        year_end(2024, total=3300),
        year_end(2025, total=3415, joined=300, removed=180),
    )

    result = reconcile_year(2025, points)

    assert result.expected_total == 3420
    assert result.residual == -5
    assert result.reconciles is False
    # Every reported figure survives untouched.
    assert result.opening_total == 3300
    assert result.closing_total == 3415
    assert result.joined == 300
    assert result.removed == 180


def test_a_negative_and_a_positive_residual_are_both_kept_as_they_are():
    over = reconcile_year(
        2025, (year_end(2024, total=3300), year_end(2025, total=3430, joined=300, removed=180))
    )
    assert over.residual == 10


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


def test_a_year_with_no_opening_observation_is_unavailable_not_zero():
    result = reconcile_year(2025, (year_end(2025, total=3420, joined=300, removed=180),))

    assert result.is_available is False
    assert result.unavailable_reason == Unreconcilable.NO_OPENING
    assert result.residual is None


def test_an_opening_observation_far_from_the_year_boundary_is_refused():
    """`new_members_ytd` counts from 1 January.

    Anchoring on an October reading would leave November and December in
    neither the opening stock nor the flow counters, and the identity would
    quietly measure a different period than it claims.
    """
    points = (
        point(dt.date(2024, 10, 1), total=3300),
        year_end(2025, total=3420, joined=300, removed=180),
    )

    result = reconcile_year(2025, points)

    assert result.is_available is False
    assert result.unavailable_reason == Unreconcilable.OPENING_TOO_FAR


def test_an_opening_observation_just_inside_the_tolerance_is_accepted():
    inside = dt.date(2025, 1, 1) - dt.timedelta(days=YEAR_BOUNDARY_TOLERANCE_DAYS)
    points = (point(inside, total=3300), year_end(2025, total=3420, joined=300, removed=180))

    assert reconcile_year(2025, points).is_available


def test_a_year_with_no_flow_figures_is_named_as_such():
    points = (year_end(2024, total=3300), year_end(2025, total=3420))

    result = reconcile_year(2025, points)

    assert result.is_available is False
    assert result.unavailable_reason == Unreconcilable.NO_FLOWS


def test_a_year_with_no_closing_observation_at_all_is_named_differently():
    points = (year_end(2024, total=3300),)

    result = reconcile_year(2025, points)

    assert result.unavailable_reason == Unreconcilable.NO_CLOSING


def test_a_withheld_metric_cannot_anchor_a_reconciliation():
    """A conflicted total is not a total, so the period is simply not checked."""
    points = (
        year_end(2024, total=3300, withheld=frozenset({"total_members"})),
        year_end(2025, total=3420, joined=300, removed=180),
    )

    assert reconcile_year(2025, points).is_available is False


def test_the_flows_must_come_from_the_same_report_as_the_closing_total():
    """Otherwise the counters cover a different stretch than the stock does."""
    points = (
        year_end(2024, total=3300),
        point(dt.date(2025, 6, 30), joined=150, removed=90),  # flows, no total
        year_end(2025, total=3420),  # total, no flows
    )

    result = reconcile_year(2025, points)

    assert result.is_available is False
    assert result.unavailable_reason == Unreconcilable.NO_FLOWS


# ---------------------------------------------------------------------------
# Partial periods
# ---------------------------------------------------------------------------


def test_a_partial_year_reconciles_the_stretch_it_actually_covers():
    """Valid arithmetic; it is only wrong to call it the year."""
    points = (
        year_end(2025, total=3300),
        point(dt.date(2026, 7, 31), total=3380, joined=200, removed=120),
    )

    result = reconcile_year(2026, points)

    assert result.is_available
    assert result.expected_total == 3380
    assert result.is_partial_year is True
    assert result.closing_date == dt.date(2026, 7, 31)


def test_a_full_year_is_not_labelled_partial():
    points = (
        year_end(2024, total=3300),
        year_end(2025, total=3420, joined=300, removed=180),
    )

    assert reconcile_year(2025, points).is_partial_year is False


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_only_checkable_periods_are_listed():
    """A dozen refusals under a data-quality heading would bury the two years
    that genuinely do not add up."""
    points = (
        year_end(2020, total=3100),  # no flows anywhere near it
        year_end(2023, total=3300),
        year_end(2024, total=3420, joined=300, removed=180),
        year_end(2025, total=3500, joined=250, removed=170),
    )

    results = reconcile_history(points)

    assert [row.year for row in results] == [2025, 2024]
    assert all(row.is_available for row in results)


def test_the_history_is_newest_first_and_bounded():
    points = []
    total = 3000
    for year in range(2014, 2027):
        points.append(year_end(year, total=total, joined=200, removed=150))
        total += 50
    results = reconcile_history(tuple(points), limit=3)

    assert len(results) == 3
    assert [row.year for row in results] == [2026, 2025, 2024]


def test_an_empty_history_reconciles_nothing():
    assert reconcile_history(()) == ()
