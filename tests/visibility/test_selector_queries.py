"""What the visibility selectors cost, and that batching changed no answer.

`get_visibility_summary` asked two queries per metric — the newest current
observation, then the one before it — for seven metrics. Fourteen queries, every
one the same statement with a different constant, on a page that draws them all
at once.

They are one query now. These tests pin the count so it cannot drift back, and
pin the values the readings carry so the batching cannot have changed an answer:
a metric nobody entered stays absent rather than becoming zero, a change is
still measured against the actual previous observation whatever its date, and a
superseded row stays invisible.

Everything is published through `submit`, the same service the staff form uses,
so the rows are ones the application could actually have written.
"""

from __future__ import annotations

import pytest

from apps.visibility.models import VisibilityMetric
from apps.visibility.registry import NEWSLETTER_METRICS, SOCIAL_METRICS
from apps.visibility.selectors import (
    get_latest_visibility_observation,
    get_newsletter_summary,
    get_previous_visibility_observation,
    get_visibility_summary,
)

pytestmark = pytest.mark.django_db

ALL_METRICS = (*NEWSLETTER_METRICS, *SOCIAL_METRICS)

FACEBOOK = VisibilityMetric.FACEBOOK_FOLLOWERS
LINKEDIN = VisibilityMetric.LINKEDIN_FOLLOWERS
INSTAGRAM = VisibilityMetric.INSTAGRAM_FOLLOWERS
YOUTUBE = VisibilityMetric.YOUTUBE_SUBSCRIBERS


def every_metric(base: int) -> dict:
    """One value per metric, keyed the way `submit` expects."""
    return {str(metric): base + index for index, metric in enumerate(ALL_METRICS)}


def force(summary) -> list:
    """Touch every derived value, so a lazy attribute cannot hide a query."""
    return [(r.value, r.previous_value, r.change, r.as_of, r.previous_date) for r in summary]


class TestTheQueryCount:
    """Was two per metric — fourteen. One window query answers all seven."""

    def test_the_whole_summary_costs_one_query(
        self, django_assert_num_queries, submit, today, days_ago
    ):
        submit(observation_date=days_ago(7), **every_metric(90))
        submit(observation_date=today, **every_metric(100))

        with django_assert_num_queries(1):
            force(get_visibility_summary(today=today).readings)

    def test_it_stays_one_query_with_no_data_at_all(self, django_assert_num_queries, today):
        with django_assert_num_queries(1):
            force(get_visibility_summary(today=today).readings)

    def test_it_stays_one_query_when_only_some_metrics_have_data(
        self, django_assert_num_queries, submit, today
    ):
        submit(observation_date=today, **{str(FACEBOOK): 10})

        with django_assert_num_queries(1):
            force(get_visibility_summary(today=today).readings)

    def test_the_newsletter_summary_alone_costs_one_query(
        self, django_assert_num_queries, submit, today
    ):
        submit(observation_date=today, **{str(m): 500 for m in NEWSLETTER_METRICS})

        with django_assert_num_queries(1):
            force(get_newsletter_summary(today=today).readings)

    def test_the_count_does_not_grow_with_history(
        self, django_assert_num_queries, submit, today, days_ago
    ):
        """Ten dated submissions per metric still cost one query."""
        for age in range(10):
            submit(observation_date=days_ago(age), **every_metric(100 + age))

        with django_assert_num_queries(1):
            force(get_visibility_summary(today=today).readings)


class TestTheAnswersAreUnchanged:
    """Every value the per-metric selectors returned, still returned."""

    def test_it_agrees_with_the_single_metric_selectors(self, submit, today, days_ago):
        submit(observation_date=days_ago(30), **every_metric(150))
        submit(observation_date=today, **every_metric(200))

        summary = get_visibility_summary(today=today)

        for metric in ALL_METRICS:
            reading = summary.reading(metric)
            latest = get_latest_visibility_observation(metric)
            previous = get_previous_visibility_observation(metric)

            assert reading.value == latest.value
            assert reading.as_of == latest.observation_date
            assert reading.previous_value == previous.value
            assert reading.previous_date == previous.observation_date

    def test_a_metric_nobody_entered_is_absent_not_zero(self, submit, today):
        submit(observation_date=today, **{str(FACEBOOK): 10})

        reading = get_visibility_summary(today=today).reading(LINKEDIN)

        assert reading.value is None
        assert reading.has_data is False

    def test_a_reported_zero_is_a_real_reading(self, submit, today):
        submit(observation_date=today, **{str(YOUTUBE): 0})

        reading = get_visibility_summary(today=today).reading(YOUTUBE)

        assert reading.value == 0
        assert reading.has_data is True

    def test_a_single_observation_has_no_previous_and_no_change(self, submit, today):
        submit(observation_date=today, **{str(FACEBOOK): 42})

        reading = get_visibility_summary(today=today).reading(FACEBOOK)

        assert reading.value == 42
        assert reading.previous_value is None
        assert reading.change is None, "a difference without its baseline is not shown"

    def test_the_change_is_measured_against_the_actual_previous_observation(
        self, submit, today, days_ago
    ):
        """Whatever its date — not against a fixed window."""
        submit(observation_date=days_ago(400), **{str(FACEBOOK): 250})
        submit(observation_date=today, **{str(FACEBOOK): 300})

        reading = get_visibility_summary(today=today).reading(FACEBOOK)

        assert reading.change == 50
        assert reading.previous_date == days_ago(400)

    def test_a_same_date_correction_replaces_rather_than_becoming_the_previous(
        self, submit, today, days_ago
    ):
        """The retired row shares its date with the one that replaced it.

        Ranking must skip it, or the change would be measured against a value
        the Chamber has already withdrawn — and would read as no movement.
        """
        submit(observation_date=days_ago(7), **{str(FACEBOOK): 100})
        submit(observation_date=today, **{str(FACEBOOK): 999})
        submit(observation_date=today, **{str(FACEBOOK): 111})

        reading = get_visibility_summary(today=today).reading(FACEBOOK)

        assert reading.value == 111, "the correction is what is current"
        assert reading.previous_value == 100, "the superseded same-date row was used"
        assert reading.change == 11

    def test_metrics_do_not_borrow_each_others_rows(self, submit, today, days_ago):
        """The window partitions by metric; a leak would be silent and wrong."""
        submit(observation_date=days_ago(1), **{str(LINKEDIN): 15})
        submit(observation_date=today, **{str(FACEBOOK): 10, str(LINKEDIN): 20})

        summary = get_visibility_summary(today=today)

        assert summary.reading(FACEBOOK).previous_value is None
        assert summary.reading(LINKEDIN).previous_value == 15

    def test_mixed_dates_across_metrics_each_get_their_own_previous(self, submit, today, days_ago):
        submit(observation_date=days_ago(90), **{str(INSTAGRAM): 25})
        submit(observation_date=days_ago(5), **{str(INSTAGRAM): 30})
        submit(observation_date=days_ago(2), **{str(FACEBOOK): 8})
        submit(observation_date=today, **{str(FACEBOOK): 10})

        summary = get_visibility_summary(today=today)

        assert summary.reading(FACEBOOK).previous_date == days_ago(2)
        assert summary.reading(INSTAGRAM).previous_date == days_ago(90)

    def test_the_summary_reports_every_metric_in_registry_order(self, today):
        summary = get_visibility_summary(today=today)

        assert tuple(r.spec.key for r in summary.readings) == ALL_METRICS

    def test_the_newsletter_lists_are_still_reported_one_by_one(self, submit, today):
        """Three lists, never summed: nobody has measured the overlap."""
        submit(observation_date=today, **{str(m): 100 for m in NEWSLETTER_METRICS})

        newsletter = get_newsletter_summary(today=today)

        assert len(newsletter.readings) == len(NEWSLETTER_METRICS)
        assert not hasattr(newsletter, "total")


class TestTheStaffEntryFormCostsTheSame:
    """The form shows every metric at once, so it had the same fourteen queries.

    Found by the post-remediation audit: the first pass batched the viewer path
    and left this one asking per metric.
    """

    def test_the_entry_defaults_cost_one_query(self, django_assert_num_queries, submit, today):
        from apps.visibility.selectors import get_manual_entry_defaults

        submit(observation_date=today, **every_metric(100))

        with django_assert_num_queries(1):
            defaults = get_manual_entry_defaults(today=today)
            [(r.value, r.previous_value) for r in defaults.values()]

    def test_it_still_answers_for_every_metric(self, submit, today):
        from apps.visibility.registry import METRICS
        from apps.visibility.selectors import get_manual_entry_defaults

        submit(observation_date=today, **every_metric(100))

        defaults = get_manual_entry_defaults(today=today)

        assert set(defaults) == {spec.key for spec in METRICS}

    def test_it_agrees_with_the_summary(self, submit, today, days_ago):
        from apps.visibility.selectors import get_manual_entry_defaults

        submit(observation_date=days_ago(3), **every_metric(50))
        submit(observation_date=today, **every_metric(100))

        defaults = get_manual_entry_defaults(today=today)
        summary = get_visibility_summary(today=today)

        for metric in ALL_METRICS:
            assert defaults[metric].value == summary.reading(metric).value
            assert defaults[metric].previous_value == summary.reading(metric).previous_value
