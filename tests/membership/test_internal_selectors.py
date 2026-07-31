"""Selectors: preferred rows only, per-metric omission, and never a zero."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.membership.internal_selectors import (
    get_fee_collection_trend,
    get_internal_membership_latest,
    get_internal_membership_observations,
    get_internal_membership_quality_summary,
    get_internal_membership_trend,
    get_manual_entry_defaults,
    get_membership_size_movement,
    get_monthly_new_members,
    get_removal_reasons,
)
from apps.membership.models import SIZE_BAND_ORDER, MembershipMetricConflict

pytestmark = pytest.mark.django_db


def test_latest_returns_the_newest_preferred_observation(imported_package):
    latest = get_internal_membership_latest()

    assert latest is not None
    assert latest.observation_date == date(2025, 1, 15)
    assert latest.observation.is_preferred_for_date is True


def test_latest_is_none_before_anything_is_imported(internal_source):
    assert get_internal_membership_latest() is None


def test_only_preferred_observations_are_returned(imported_package):
    points = get_internal_membership_observations()

    assert len(points) == 2
    assert all(point.observation.is_preferred_for_date for point in points)
    assert [point.observation_date for point in points] == [
        date(2024, 1, 10),
        date(2025, 1, 15),
    ]


def test_a_conflicted_metric_is_withheld_but_the_rest_of_the_row_is_not(imported_package):
    """The 2024 conflict is on total_members only."""
    point = get_internal_membership_observations(
        date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
    )[0]

    assert point.value("total_members") is None
    assert point.value("paid_members") == 3000
    assert point.value("new_members_ytd") == 40


def test_a_withheld_metric_is_none_and_never_zero(imported_package):
    """The distinction the whole quality policy rests on."""
    point = get_internal_membership_observations(
        date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
    )[0]
    value = point.value("total_members")

    assert value is None
    # `0 == None` is already False, but a future refactor that returned a
    # default would satisfy `is None` checks elsewhere and quietly chart a zero.
    assert value != 0


def test_resolving_a_conflict_restores_the_metric(imported_package):
    conflict = MembershipMetricConflict.objects.get()
    conflict.resolved = True
    conflict.save(update_fields=["resolved"])

    point = get_internal_membership_observations(
        date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
    )[0]

    assert point.value("total_members") == 3200


def test_trend_series_skips_absent_values_without_a_gap_marker(imported_package):
    trend = get_internal_membership_trend()
    series = trend.series("total_members")

    # 2024 is conflicted, so only 2025 has a drawable total.
    assert [day for day, _value in series] == [date(2025, 1, 15)]
    assert all(value is not None for _day, value in series)
    assert trend.withheld_metric_points >= 1


def test_trend_respects_a_bounded_range(imported_package):
    trend = get_internal_membership_trend(date_from=date(2025, 1, 1))

    assert [point.observation_date for point in trend.points] == [date(2025, 1, 15)]


def test_metric_filter_drops_observations_with_nothing_to_draw(imported_package):
    points = get_internal_membership_observations(metric="total_members")

    assert [point.observation_date for point in points] == [date(2025, 1, 15)]


def test_paid_share_is_derived_not_stored(imported_package):
    latest = get_internal_membership_latest()

    # 3100 of 3300, kept exact rather than turned into a float on the way out.
    assert latest.paid_member_share_pct == Decimal("93.94")


def test_fee_trend_reports_both_percentages(imported_package):
    rows = get_fee_collection_trend()
    latest = rows[-1]

    assert latest["reported_pct"] == 105
    assert latest["computed_pct"] == 105


def test_monthly_values_keep_their_three_states_apart(imported_package):
    by_year = get_monthly_new_members([2024, 2025])
    months = {value.calendar_month: value for value in by_year[2024]}

    assert months[1].new_members == 12
    assert months[2].new_members == 0
    assert months[2].is_chartable is True
    assert months[3].new_members is None
    assert months[3].is_conflict is True
    assert months[3].is_chartable is False
    # A month nobody reported has no entry at all.
    assert 7 not in months
    assert by_year[2025][0].is_provisional is True


def test_provisional_values_can_be_excluded(imported_package):
    by_year = get_monthly_new_members([2025], include_provisional=False)

    assert by_year[2025] == ()


def test_size_movements_come_back_in_canonical_band_order(imported_package):
    latest = get_internal_membership_latest()
    rows = get_membership_size_movement(latest.observation.pk)
    order = [row["band"] for row in rows]

    # Smallest employee band first, supporter last — the canonical chart order,
    # not whatever order the database returned.
    assert order == ["employees_1_4", "supporter"]
    assert SIZE_BAND_ORDER.index("employees_1_4") < SIZE_BAND_ORDER.index("supporter")
    assert rows[0]["joined"] == 30
    assert rows[0]["removed"] == 15


def test_removal_reasons_carry_counts_and_shares(imported_package):
    observation = get_internal_membership_observations(
        date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
    )[0].observation
    rows = get_removal_reasons(observation.pk)

    assert {row["count"] for row in rows} == {12, 8}
    assert sum(row["share_pct"] for row in rows) == 100


def test_quality_summary_reports_counts_only(imported_package):
    summary = get_internal_membership_quality_summary()

    assert summary.observation_count == 3
    assert summary.preferred_count == 2
    assert summary.conflicted_metric_count == 1
    assert summary.unresolved_error_count == 1
    assert summary.conflict_month_count == 1
    assert summary.provisional_month_count == 1
    assert summary.earliest_observation_date == date(2024, 1, 10)
    assert summary.latest_observation_date == date(2025, 1, 15)
    # Nothing in the summary can carry a path, a code or a value.
    assert ".docx" not in str(summary)


def test_manual_entry_defaults_expose_existing_months(imported_package):
    defaults = get_manual_entry_defaults(2024)

    assert defaults["reporting_year"] == 2024
    assert len(defaults["existing_monthly"]) == 3
    assert defaults["latest_observation_date"] == date(2025, 1, 15)


def test_selectors_are_bounded_in_query_count(imported_package, django_assert_max_num_queries):
    """A page render must not scale its queries with the number of rows.

    A ceiling rather than an exact count: the point is that nothing here runs a
    query per observation, not that the number never changes.
    """
    with django_assert_max_num_queries(6):
        trend = get_internal_membership_trend()
        trend.series("total_members")
        get_internal_membership_latest()
