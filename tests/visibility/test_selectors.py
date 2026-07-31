"""What the selectors read, and what they refuse to invent."""

from __future__ import annotations

import pytest

from apps.visibility.models import VisibilityMetric
from apps.visibility.registry import (
    NEWSLETTER_STALE_AFTER_DAYS,
    SOCIAL_STALE_AFTER_DAYS,
    spec_for,
)
from apps.visibility.selectors import (
    ReadingState,
    get_latest_visibility_observation,
    get_previous_visibility_observation,
    get_visibility_entry_history,
    get_visibility_history,
    get_visibility_series,
    get_visibility_summary,
)

pytestmark = pytest.mark.django_db

FACEBOOK = VisibilityMetric.FACEBOOK_FOLLOWERS


# -- latest and previous ------------------------------------------------


def test_the_latest_observation_is_the_newest_dated_one(submit, today, days_ago):
    submit(observation_date=days_ago(60), facebook_followers=3900)
    submit(observation_date=today, facebook_followers=4200)

    latest = get_latest_visibility_observation(FACEBOOK)
    assert latest.value == 4200
    assert latest.observation_date == today


def test_the_previous_observation_is_the_one_before_the_latest(submit, today, days_ago):
    submit(observation_date=days_ago(60), facebook_followers=3900)
    submit(observation_date=days_ago(30), facebook_followers=4100)
    submit(observation_date=today, facebook_followers=4200)

    previous = get_previous_visibility_observation(FACEBOOK)
    assert previous.value == 4100
    assert previous.observation_date == days_ago(30)


def test_a_superseded_row_is_never_the_latest(submit, today):
    submit(observation_date=today, facebook_followers=4200)
    submit(observation_date=today, facebook_followers=4250)

    assert get_latest_visibility_observation(FACEBOOK).value == 4250


def test_a_superseded_row_is_excluded_from_the_series(submit, today, days_ago):
    submit(observation_date=days_ago(30), facebook_followers=4100)
    submit(observation_date=today, facebook_followers=4200)
    submit(observation_date=today, facebook_followers=4250)

    assert get_visibility_series(FACEBOOK) == ((days_ago(30), 4100), (today, 4250))


def test_an_absent_metric_returns_none_rather_than_zero(submit):
    submit(facebook_followers=4200)

    assert get_latest_visibility_observation(VisibilityMetric.INSTAGRAM_FOLLOWERS) is None
    summary = get_visibility_summary()
    instagram = summary.reading(VisibilityMetric.INSTAGRAM_FOLLOWERS)
    assert instagram.value is None
    assert instagram.has_data is False


def test_a_stored_zero_is_returned_as_zero(submit):
    submit(instagram_followers=0)

    reading = get_visibility_summary().reading(VisibilityMetric.INSTAGRAM_FOLLOWERS)
    assert reading.value == 0
    assert reading.has_data is True


# -- history ------------------------------------------------------------


def test_history_is_returned_oldest_first(submit, today, days_ago):
    submit(observation_date=days_ago(60), facebook_followers=3900)
    submit(observation_date=days_ago(30), facebook_followers=4100)
    submit(observation_date=today, facebook_followers=4200)

    values = [row.value for row in get_visibility_history(FACEBOOK)]
    assert values == [3900, 4100, 4200]


def test_a_limited_history_keeps_the_most_recent_points(submit, today, days_ago):
    submit(observation_date=days_ago(60), facebook_followers=3900)
    submit(observation_date=days_ago(30), facebook_followers=4100)
    submit(observation_date=today, facebook_followers=4200)

    values = [row.value for row in get_visibility_history(FACEBOOK, limit=2)]
    assert values == [4100, 4200]


def test_history_can_be_bounded_by_date(submit, today, days_ago):
    submit(observation_date=days_ago(60), facebook_followers=3900)
    submit(observation_date=today, facebook_followers=4200)

    values = [row.value for row in get_visibility_history(FACEBOOK, date_from=days_ago(30))]
    assert values == [4200]


# -- change -------------------------------------------------------------


def test_change_is_measured_against_the_actual_previous_observation(submit, today, days_ago):
    submit(observation_date=days_ago(30), facebook_followers=4100)
    submit(observation_date=today, facebook_followers=4200)

    reading = get_visibility_summary().reading(FACEBOOK)
    assert reading.change == 100
    assert reading.change_direction == "up"
    assert reading.previous_date == days_ago(30)
    assert days_ago(30).strftime("%d.%m.%Y") in reading.comparison_period


def test_a_first_observation_has_no_change_at_all(submit):
    submit(facebook_followers=4200)

    reading = get_visibility_summary().reading(FACEBOOK)
    assert reading.change is None
    assert reading.change_label == ""
    assert reading.comparison_period == ""


def test_a_percentage_change_is_not_computed_from_a_zero_baseline(submit, today, days_ago):
    """Growth from nothing has no meaningful percentage."""
    submit(observation_date=days_ago(30), instagram_followers=0)
    submit(observation_date=today, instagram_followers=50)

    reading = get_visibility_summary().reading(VisibilityMetric.INSTAGRAM_FOLLOWERS)
    assert reading.change == 50
    assert reading.change_pct is None


# -- staleness ----------------------------------------------------------


def test_a_recent_social_reading_is_not_stale(submit, days_ago):
    submit(observation_date=days_ago(SOCIAL_STALE_AFTER_DAYS - 1), facebook_followers=4200)

    reading = get_visibility_summary().reading(FACEBOOK)
    assert reading.is_stale is False
    assert reading.state is ReadingState.OBSERVED
    assert reading.state_label == "Käsitsi sisestatud"


def test_an_old_social_reading_is_marked_stale_but_still_shown(submit, days_ago):
    submit(observation_date=days_ago(SOCIAL_STALE_AFTER_DAYS + 1), facebook_followers=4200)

    reading = get_visibility_summary().reading(FACEBOOK)
    assert reading.is_stale is True
    assert reading.state is ReadingState.STALE
    assert reading.state_label == "Vajab uuendamist"
    # Stale labels a figure; it never hides one.
    assert reading.value == 4200


def test_the_newsletter_threshold_is_longer_than_the_social_one(submit, days_ago):
    older_than_social = SOCIAL_STALE_AFTER_DAYS + 1
    submit(
        observation_date=days_ago(older_than_social),
        newsletter_member_recipients=1200,
    )

    reading = get_visibility_summary().reading(VisibilityMetric.NEWSLETTER_MEMBER_RECIPIENTS)
    assert NEWSLETTER_STALE_AFTER_DAYS > SOCIAL_STALE_AFTER_DAYS
    assert reading.is_stale is False


def test_a_newsletter_reading_past_its_own_threshold_is_stale(submit, days_ago):
    submit(
        observation_date=days_ago(NEWSLETTER_STALE_AFTER_DAYS + 1),
        newsletter_member_recipients=1200,
    )

    reading = get_visibility_summary().reading(VisibilityMetric.NEWSLETTER_MEMBER_RECIPIENTS)
    assert reading.is_stale is True


def test_a_metric_with_no_reading_is_never_stale(submit):
    submit(facebook_followers=4200)

    reading = get_visibility_summary().reading(VisibilityMetric.INSTAGRAM_FOLLOWERS)
    assert reading.is_stale is False
    assert reading.state is ReadingState.MISSING
    assert reading.state_label == "Andmed puuduvad"


def test_the_thresholds_come_from_the_registry():
    assert spec_for(FACEBOOK).stale_after_days == SOCIAL_STALE_AFTER_DAYS
    assert (
        spec_for(VisibilityMetric.NEWSLETTER_OVERLAP_RECIPIENTS).stale_after_days
        == NEWSLETTER_STALE_AFTER_DAYS
    )


# -- provenance ---------------------------------------------------------


def test_a_reading_carries_its_source_date_and_collection_method(submit, today):
    submit(facebook_followers=4200)

    reading = get_visibility_summary().reading(FACEBOOK)
    assert reading.source_label == "Facebooki jälgijad"
    assert reading.as_of == today
    assert reading.method_label == "Käsitsi sisestatud"
    assert reading.is_manual is True


def test_a_reading_carries_its_fixed_public_profile_link(submit):
    submit(linkedin_followers=2500)

    reading = get_visibility_summary().reading(VisibilityMetric.LINKEDIN_FOLLOWERS)
    assert reading.profile_url == "https://www.linkedin.com/company/ecci/"


# -- entry history ------------------------------------------------------


def test_entry_history_is_newest_first_and_counts_corrections(submit, today, days_ago):
    submit(observation_date=days_ago(30), facebook_followers=4100)
    submit(observation_date=today, facebook_followers=4200)
    submit(observation_date=today, facebook_followers=4250)

    rows = get_visibility_entry_history()
    assert [row.observation_date for row in rows] == [today, today, days_ago(30)]
    assert rows[0].correction_count == 1
    assert rows[-1].correction_count == 0
