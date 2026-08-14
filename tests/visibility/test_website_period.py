"""The measurement window, its coverage, and when a comparison may be drawn.

The defects this guards against all produce a plausible number rather than an
error: a previous period quietly shortened to fit, a delta computed across two
windows with different amounts of collection behind them, a custom range that
manufactures days before GA4 was measuring anything.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.visibility.ga4_selectors import get_coverage
from apps.visibility.website_period import (
    CUSTOM_KEY,
    DEFAULT_PRESET,
    MIN_CUSTOM_DAYS,
    build_comparison,
    get_period_coverage,
    parse_period,
)

pytestmark = pytest.mark.django_db

START = dt.date(2026, 1, 1)
END = dt.date(2026, 3, 31)


@pytest.fixture
def history(ga4_day):
    """Ninety days of complete collection, 01.01–31.03.2026."""
    for offset in range((END - START).days + 1):
        ga4_day(
            START + dt.timedelta(days=offset),
            sessions=100,
            page_views=300,
            engaged_sessions=60,
            user_engagement_seconds=6000,
            pages=(("/et/uudised/a", 10, 400),),
            channels=(("Organic Search", 60, 40),),
        )
    return get_coverage()


# -- resolving a window --------------------------------------------------


def test_a_preset_ends_at_the_newest_collected_day_not_today(history):
    """A chart that ran to today would end in a flat gap the width of however
    late the collector is."""
    period = parse_period("30", history)

    assert period.end == END
    assert period.start == END - dt.timedelta(days=29)
    assert period.days == 30


def test_a_preset_is_clamped_to_the_start_of_measurement(history):
    """Nothing pads the chart with zeros for the period before collection."""
    period = parse_period("5a", history)

    assert period.start == START
    assert period.is_all is False


def test_an_unknown_period_key_resolves_rather_than_raising(history):
    assert parse_period("kolm-nadalat", history).key == DEFAULT_PRESET.key
    assert parse_period(None, history).key == DEFAULT_PRESET.key
    assert parse_period("", history).key == DEFAULT_PRESET.key


def test_koik_spans_the_whole_of_coverage(history):
    period = parse_period("koik", history)

    assert (period.start, period.end) == (START, END)
    assert period.is_all


# -- the custom range ----------------------------------------------------


def test_a_custom_range_is_honoured(history):
    period = parse_period(CUSTOM_KEY, history, raw_from="2026-02-01", raw_to="2026-02-28")

    assert (period.start, period.end) == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))
    assert period.is_custom
    assert period.custom_note == ""


def test_reversed_dates_are_swapped_rather_than_rejected(history):
    period = parse_period(CUSTOM_KEY, history, raw_from="2026-02-28", raw_to="2026-02-01")

    assert (period.start, period.end) == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))
    assert "vastupidi" in period.custom_note


def test_a_custom_range_is_clamped_to_measured_coverage(history):
    """Never manufacture data before GA4 started measuring."""
    period = parse_period(CUSTOM_KEY, history, raw_from="2020-01-01", raw_to="2026-02-15")

    assert period.start == START
    assert period.end == dt.date(2026, 2, 15)
    assert "piiratud" in period.custom_note


def test_a_range_entirely_outside_coverage_falls_back_and_says_so(history):
    period = parse_period(CUSTOM_KEY, history, raw_from="2019-01-01", raw_to="2019-06-30")

    assert period.start >= START
    assert "mõõtmisandmed puuduvad" in period.custom_note


def test_a_single_day_is_widened_because_one_day_is_not_a_period(history):
    period = parse_period(CUSTOM_KEY, history, raw_from="2026-02-10", raw_to="2026-02-10")

    assert period.days >= MIN_CUSTOM_DAYS
    assert "kaks päeva" in period.custom_note


def test_a_half_filled_range_runs_to_the_edge_of_coverage(history):
    from_only = parse_period(CUSTOM_KEY, history, raw_from="2026-03-01", raw_to=None)
    to_only = parse_period(CUSTOM_KEY, history, raw_from=None, raw_to="2026-01-31")

    assert (from_only.start, from_only.end) == (dt.date(2026, 3, 1), END)
    assert (to_only.start, to_only.end) == (START, dt.date(2026, 1, 31))


def test_a_hand_typed_custom_range_never_raises(history):
    period = parse_period(CUSTOM_KEY, history, raw_from="not-a-date", raw_to="<script>")

    assert period.has_window
    assert period.custom_note


# -- coverage ------------------------------------------------------------


def test_coverage_counts_each_grain_separately(ga4_day):
    """A collected day can carry the site figures and neither detail set."""
    ga4_day(START, sessions=10, pages=(("/et/a", 1, 10),), channels=(("Direct", 5, 3),))
    ga4_day(START + dt.timedelta(days=1), sessions=10)

    coverage = get_period_coverage(START, START + dt.timedelta(days=1))

    assert coverage.expected_days == 2
    assert coverage.snapshot_days == 2
    assert coverage.days_with_page_detail == 1
    assert coverage.days_with_channel_detail == 1
    assert coverage.is_site_complete
    assert not coverage.is_page_complete
    assert not coverage.is_channel_complete


def test_uncollected_days_are_named_not_merely_counted(ga4_day):
    ga4_day(START, sessions=10)
    ga4_day(START + dt.timedelta(days=2), sessions=10)

    coverage = get_period_coverage(START, START + dt.timedelta(days=2))

    assert coverage.missing_count == 1
    assert coverage.missing_dates == (START + dt.timedelta(days=1),)


def test_a_superseded_revision_does_not_count_as_coverage(ga4_day):
    ga4_day(START, sessions=10, current=False)

    coverage = get_period_coverage(START, START)

    assert coverage.snapshot_days == 0
    assert coverage.missing_dates == (START,)


def test_a_nullable_metric_absent_on_a_collected_day_is_tracked(ga4_day):
    """A day may exist while one figure is absent, and summing must not read
    that absence as a zero."""
    ga4_day(START, sessions=10, page_views=30)

    coverage = get_period_coverage(START, START)

    assert coverage.days_with_sessions == 1
    assert coverage.days_with_engagement_seconds == 0
    assert not coverage.is_engagement_complete


# -- comparison ----------------------------------------------------------


def test_the_previous_window_is_equal_length_and_does_not_overlap(history):
    period = parse_period("30", history)
    comparison = build_comparison(period, history, get_period_coverage(period.start, period.end))

    assert comparison.is_available
    assert comparison.end == period.start - dt.timedelta(days=1)
    assert (comparison.end - comparison.start).days == (period.end - period.start).days


def test_koik_has_no_previous_period_and_none_is_invented(history):
    period = parse_period("koik", history)
    comparison = build_comparison(period, history, get_period_coverage(period.start, period.end))

    assert not comparison.is_available
    assert comparison.start is None
    assert not comparison.can_compare_site
    assert comparison.unavailable_reason


def test_a_previous_window_before_measurement_is_refused_not_shortened(history):
    """A previous period trimmed at the start of collection and still labelled a
    full one is the exact comparison this refuses: fewer days measured fewer
    sessions, and the fall would be the collector's history."""
    period = parse_period("90", history)
    comparison = build_comparison(period, history, get_period_coverage(period.start, period.end))

    assert not comparison.is_available
    assert "varasemaks" in comparison.unavailable_reason


def test_a_complete_period_is_not_compared_with_a_patchy_one(ga4_day):
    """Thirty intended days against twenty measured ones is not −19%."""
    current_start = dt.date(2026, 2, 1)
    for offset in range(30):
        ga4_day(current_start + dt.timedelta(days=offset), sessions=100)
    # The previous window is complete at its edges — so a comparison is
    # *available* — but missing a third of its days in the middle, which is what
    # must stop the delta being drawn.
    previous_start = current_start - dt.timedelta(days=30)
    for offset in range(30):
        if offset and offset % 3 == 0:
            continue
        ga4_day(previous_start + dt.timedelta(days=offset), sessions=100)

    coverage = get_coverage()
    period = parse_period("30", coverage)
    comparison = build_comparison(period, coverage, get_period_coverage(period.start, period.end))

    assert comparison.is_available
    assert not comparison.can_compare_site


def test_two_equally_patchy_periods_remain_comparable(ga4_day):
    """Two windows each missing their own one day describe the same amount of
    time; refusing that comparison would leave the dashboard silent about a
    perfectly ordinary month."""
    current_start = dt.date(2026, 2, 1)
    previous_start = current_start - dt.timedelta(days=30)
    for offset in range(30):
        if offset != 5:
            ga4_day(current_start + dt.timedelta(days=offset), sessions=100)
        if offset != 9:
            ga4_day(previous_start + dt.timedelta(days=offset), sessions=100)

    coverage = get_coverage()
    period = parse_period("30", coverage)
    comparison = build_comparison(period, coverage, get_period_coverage(period.start, period.end))

    assert comparison.can_compare_site


def test_page_and_channel_comparison_are_judged_separately(ga4_day):
    """A day carrying site figures says nothing about page-level completeness,
    and a content delta drawn over days with no page rows compares the days that
    happen to have them."""
    current_start = dt.date(2026, 2, 1)
    previous_start = current_start - dt.timedelta(days=30)
    for offset in range(30):
        ga4_day(
            current_start + dt.timedelta(days=offset),
            sessions=100,
            pages=(("/et/a", 5, 50),),
            channels=(("Direct", 50, 30),),
        )
        # The previous window was collected, but page detail was not requested.
        ga4_day(
            previous_start + dt.timedelta(days=offset),
            sessions=100,
            channels=(("Direct", 50, 30),),
        )

    coverage = get_coverage()
    period = parse_period("30", coverage)
    comparison = build_comparison(period, coverage, get_period_coverage(period.start, period.end))

    assert comparison.can_compare_site
    assert comparison.can_compare_channels
    assert not comparison.can_compare_pages
