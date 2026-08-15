"""The analytics behind Koduleht, and the arithmetic each one must refuse.

Almost every failure mode here produces a plausible figure rather than an error:
a partial numerator over a complete denominator, a rate averaged from daily
rates, a page ranked on five views against one, a share computed over the rows
that happened to be drawn.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.visibility.website_analytics import (
    OTHER_LANGUAGE_KEY,
    OTHER_SECTION_KEY,
    WEEKDAY_PATTERN_MIN_DAYS,
    get_channel_performance,
    get_concentration,
    get_content_mix,
    get_engagement_matrix,
    get_language_mix,
    get_page_detail,
    get_page_movement,
    get_peak_day,
    get_quality_signals,
    get_traffic_summary,
    get_weekday_pattern,
    min_page_views_for,
    rank_channel_movement,
)

pytestmark = pytest.mark.django_db

START = dt.date(2026, 3, 1)
END = dt.date(2026, 3, 30)
PREV_END = START - dt.timedelta(days=1)
PREV_START = PREV_END - dt.timedelta(days=29)


# ---------------------------------------------------------------------------
# Site-wide totals
# ---------------------------------------------------------------------------


def test_events_are_summed_across_a_period(ga4_day):
    ga4_day(START, sessions=100, page_views=300, engaged_sessions=60)
    ga4_day(START + dt.timedelta(days=1), sessions=120, page_views=340, engaged_sessions=80)

    summary = get_traffic_summary(start=START, end=START + dt.timedelta(days=1))

    assert summary.sessions == 220
    assert summary.page_views == 640
    assert summary.engaged_sessions == 140


def test_active_users_are_never_summed_into_a_period_total(ga4_day):
    """400 people on Monday and 380 on Tuesday are not 780 people, and no
    arithmetic over daily distinct counts can say how many there were."""
    ga4_day(START, sessions=1, active_users=400)
    ga4_day(START + dt.timedelta(days=1), sessions=1, active_users=380)

    summary = get_traffic_summary(start=START, end=START + dt.timedelta(days=1))

    assert summary.peak_active_users == 400
    assert summary.peak_active_users != 780
    assert summary.peak_active_users_on == START
    assert not hasattr(summary, "total_active_users")
    assert not hasattr(summary, "active_users")


def test_the_engagement_rate_is_a_ratio_of_sums_not_a_mean_of_rates(ga4_day):
    """A day with four sessions would otherwise weigh as much as one with four
    thousand."""
    ga4_day(START, sessions=1000, engaged_sessions=500)
    ga4_day(START + dt.timedelta(days=1), sessions=4, engaged_sessions=4)

    summary = get_traffic_summary(start=START, end=START + dt.timedelta(days=1))

    assert summary.engagement_rate == pytest.approx(504 / 1004)
    # The mean of the daily rates would be 75%.
    assert summary.engagement_rate < 0.6


def test_engagement_time_divides_by_the_sessions_of_the_days_that_measured_it(ga4_day):
    """The metric is nullable. Dividing a partial sum of seconds by every day's
    sessions under-reports the average with nothing looking wrong."""
    ga4_day(START, sessions=100, user_engagement_seconds=12000)
    ga4_day(START + dt.timedelta(days=1), sessions=100)

    summary = get_traffic_summary(start=START, end=START + dt.timedelta(days=1))

    assert summary.sessions == 200
    assert summary.sessions_with_seconds == 100
    assert summary.seconds_per_session == pytest.approx(120.0)


def test_a_missing_metric_is_absent_rather_than_zero(ga4_day):
    ga4_day(START, sessions=100)

    summary = get_traffic_summary(start=START, end=START)

    assert summary.page_views is None
    assert summary.engagement_rate is None
    assert summary.seconds_per_session is None
    assert summary.views_per_session is None


def test_a_zero_denominator_produces_no_ratio(ga4_day):
    ga4_day(START, sessions=0, page_views=0, engaged_sessions=0)

    summary = get_traffic_summary(start=START, end=START)

    assert summary.engagement_rate is None
    assert summary.views_per_session is None


def test_views_per_session_is_a_depth_metric_not_a_page_count(ga4_day):
    ga4_day(START, sessions=100, page_views=250)

    summary = get_traffic_summary(start=START, end=START)

    assert summary.views_per_session == pytest.approx(2.5)


def test_the_peak_day_is_named_rather_than_left_as_a_maximum(ga4_day):
    ga4_day(START, sessions=100, page_views=200)
    ga4_day(START + dt.timedelta(days=1), sessions=400, page_views=900)

    peak = get_peak_day(start=START, end=START + dt.timedelta(days=1))

    assert peak.day == START + dt.timedelta(days=1)
    assert peak.sessions == 400
    assert peak.page_views == 900


# ---------------------------------------------------------------------------
# Weekday pattern
# ---------------------------------------------------------------------------


def test_a_short_period_draws_no_weekday_pattern(ga4_day):
    for offset in range(20):
        ga4_day(START + dt.timedelta(days=offset), sessions=100)

    assert get_weekday_pattern(start=START, end=START + dt.timedelta(days=19)) == ()


def test_a_long_enough_period_averages_each_observed_weekday(ga4_day):
    span = WEEKDAY_PATTERN_MIN_DAYS
    for offset in range(span):
        ga4_day(START + dt.timedelta(days=offset), sessions=100 + offset % 7)

    pattern = get_weekday_pattern(start=START, end=START + dt.timedelta(days=span - 1))

    assert len(pattern) == 7
    assert {row.weekday for row in pattern} == set(range(1, 8))
    assert all(row.observed_days >= 8 for row in pattern)


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


def _two_channel_windows(ga4_day):
    for offset in range(30):
        ga4_day(
            START + dt.timedelta(days=offset),
            sessions=100,
            channels=(("Organic Search", 60, 40), ("Direct", 30, 12)),
        )
        ga4_day(
            PREV_START + dt.timedelta(days=offset),
            sessions=100,
            channels=(("Organic Search", 50, 30), ("Direct", 40, 16)),
        )


def test_channel_share_uses_the_whole_site_session_total(ga4_day):
    """A share over the drawn rows always adds to 100% whatever is left out, and
    would therefore be right however much traffic is missing from the list."""
    _two_channel_windows(ga4_day)

    channels = get_channel_performance(start=START, end=END, site_sessions=3000)
    by_name = {channel.channel: channel for channel in channels}

    assert by_name["Organic Search"].sessions == 1800
    assert by_name["Organic Search"].share == pytest.approx(0.6)
    # The two channels sum to 2700, not to the site's 3000. The remainder is
    # real traffic that simply has no row here.
    assert sum(channel.share for channel in channels) < 1.0


def test_channel_engagement_rate_is_per_channel(ga4_day):
    _two_channel_windows(ga4_day)

    channels = get_channel_performance(start=START, end=END, site_sessions=3000)
    by_name = {channel.channel: channel for channel in channels}

    assert by_name["Organic Search"].engagement_rate == pytest.approx(1200 / 1800)
    assert by_name["Direct"].engagement_rate == pytest.approx(360 / 900)


def test_channel_share_movement_is_in_percentage_points(ga4_day):
    _two_channel_windows(ga4_day)

    channels = get_channel_performance(
        start=START,
        end=END,
        previous_start=PREV_START,
        previous_end=PREV_END,
        site_sessions=3000,
        previous_site_sessions=3000,
    )
    organic = next(c for c in channels if c.channel == "Organic Search")

    assert organic.previous_sessions == 1500
    assert organic.session_change == 300
    assert organic.relative_change == pytest.approx(0.2)
    assert organic.share_change_points == pytest.approx(10.0)


def test_a_tiny_channel_cannot_top_the_movement_ranking(ga4_day):
    _two_channel_windows(ga4_day)
    # A channel that went from 1 session to 9 is +800% and is not news.
    for offset in range(30):
        ga4_day(
            START + dt.timedelta(days=offset) + dt.timedelta(days=60),
            sessions=1,
            channels=(("Affiliates", 1, 0),),
        )

    channels = get_channel_performance(
        start=START,
        end=END,
        previous_start=PREV_START,
        previous_end=PREV_END,
        site_sessions=3000,
        previous_site_sessions=3000,
    )
    movement = rank_channel_movement(channels, days=30)

    assert "Affiliates" not in {channel.channel for channel in movement.rising}
    assert movement.rising[0].channel == "Organic Search"
    assert movement.falling[0].channel == "Direct"
    assert movement.minimum_sessions >= 150


def test_no_per_channel_user_count_exists(ga4_day):
    _two_channel_windows(ga4_day)

    channel = get_channel_performance(start=START, end=END, site_sessions=3000)[0]

    assert not hasattr(channel, "active_users")
    assert not hasattr(channel, "users")


# ---------------------------------------------------------------------------
# Content mix
# ---------------------------------------------------------------------------


def test_the_content_mix_files_pages_by_whole_path_segment(ga4_day):
    ga4_day(
        START,
        pages=(
            ("/et/uudised/lugu", 100, 1000),
            # Eight characters of `/et/uudised` and a different section.
            ("/et/uudiseks", 40, 400),
            ("/et/teenused/eksport", 60, 600),
            ("/et/sundmused/foorum", 30, 300),
            ("/et/parkimine", 20, 200),
        ),
    )

    mix = get_content_mix(start=START, end=START)
    by_key = {row.key: row for row in mix.rows}

    assert by_key["uudised"].page_views == 100
    assert by_key["teenused"].page_views == 60
    assert by_key["sundmused"].page_views == 30
    # `/et/uudiseks` and `/et/parkimine` are ordinary pages in neither section.
    assert by_key[OTHER_SECTION_KEY].page_views == 60


def test_the_mix_denominator_excludes_what_is_not_content(ga4_day):
    ga4_day(
        START,
        pages=(
            ("/et/uudised/lugu", 100, 1000),
            ("/et", 5000, 20000),
            ("/et/cart", 400, 900),
            ("/404.html", 300, 100),
            ("/et/node/1173", 200, 500),
        ),
    )

    mix = get_content_mix(start=START, end=START)

    assert mix.total_page_views == 100


def test_a_language_variant_stays_in_its_own_section(ga4_day):
    ga4_day(
        START,
        pages=(("/et/uudised/lugu", 100, 1000), ("/en/news/story", 40, 400)),
    )

    mix = get_content_mix(start=START, end=START)
    by_key = {row.key: row for row in mix.rows}

    assert by_key["uudised"].page_views == 140


def test_section_share_movement_is_in_percentage_points(ga4_day):
    ga4_day(START, pages=(("/et/uudised/a", 60, 600), ("/et/teenused/b", 40, 400)))
    ga4_day(PREV_START, pages=(("/et/uudised/a", 20, 200), ("/et/teenused/b", 80, 800)))

    mix = get_content_mix(start=START, end=END, previous_start=PREV_START, previous_end=PREV_END)
    news = next(row for row in mix.rows if row.key == "uudised")

    assert news.share == pytest.approx(0.6)
    assert news.previous_share == pytest.approx(0.2)
    assert news.share_change_points == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# Page movement
# ---------------------------------------------------------------------------


def _movement_history(ga4_day, rows, *, spot=()):
    """Seed both windows.

    `rows` is `(path, current_views_per_day, previous_views_per_day)`, repeated
    on every day of the window it belongs to. A weight of zero means **no row at
    all** rather than a measured zero — the page had no traffic that window, and
    a stored zero would claim it was measured at none.

    `spot` is `(path, current_total, previous_total)` placed on the first day of
    each window only, for the small totals a per-day weight cannot express. It
    is merged into that day rather than published as a second snapshot: exactly
    one current revision may exist per date.
    """
    spot_current = {path: total for path, total, _ in spot}
    spot_previous = {path: total for path, _, total in spot}

    for offset in range(30):
        current_pages = [(path, current, current * 10) for path, current, _ in rows if current]
        previous_pages = [(path, previous, previous * 10) for path, _, previous in rows if previous]
        if offset == 0:
            current_pages += [(p, v, v * 10) for p, v in spot_current.items() if v]
            previous_pages += [(p, v, v * 10) for p, v in spot_previous.items() if v]
        ga4_day(START + dt.timedelta(days=offset), pages=tuple(current_pages))
        ga4_day(PREV_START + dt.timedelta(days=offset), pages=tuple(previous_pages))


def test_growth_is_measured_across_the_whole_population(ga4_day):
    """A page that rose from far outside the ranking is exactly the discovery
    this analysis exists to make."""
    rows = [(f"/et/teenused/big-{i}", 100, 100) for i in range(25)]
    rows.append(("/et/uudised/riser", 90, 2))
    _movement_history(ga4_day, rows)

    movement = get_page_movement(
        start=START, end=END, previous_start=PREV_START, previous_end=PREV_END, limit=10
    )

    assert movement.rising[0].path == "/et/uudised/riser"
    assert movement.rising[0].page_views == 2700
    assert movement.rising[0].previous_page_views == 60


def test_decline_is_reported_without_calling_it_a_failure(ga4_day):
    _movement_history(ga4_day, [("/et/sundmused/past", 2, 90), ("/et/uudised/flat", 50, 50)])

    movement = get_page_movement(
        start=START, end=END, previous_start=PREV_START, previous_end=PREV_END, limit=10
    )

    assert movement.falling[0].path == "/et/sundmused/past"
    assert movement.falling[0].change < 0


def test_a_page_with_no_previous_measurement_reports_no_relative_change(ga4_day):
    """Not +100%, not infinite: there was no base, and inventing one states a
    measurement nobody made."""
    for offset in range(30):
        ga4_day(START + dt.timedelta(days=offset), pages=(("/et/uudised/new", 20, 200),))
        ga4_day(PREV_START + dt.timedelta(days=offset), pages=(("/et/uudised/other", 20, 200),))

    movement = get_page_movement(
        start=START, end=END, previous_start=PREV_START, previous_end=PREV_END, limit=10
    )
    new = next(row for row in movement.rising if row.path == "/et/uudised/new")

    assert new.previous_page_views == 0
    assert new.relative_change is None
    assert new.is_new


def test_a_small_base_cannot_dominate_the_growth_ranking(ga4_day):
    """1 → 5 views is +400% and is not the site's biggest growth story."""
    _movement_history(
        ga4_day,
        [("/et/uudised/real", 60, 20)],
        spot=[("/et/uudised/tiny", 5, 1)],
    )

    movement = get_page_movement(
        start=START, end=END, previous_start=PREV_START, previous_end=PREV_END, limit=10
    )

    assert "/et/uudised/tiny" not in {row.path for row in movement.rising}
    assert movement.minimum_page_views == min_page_views_for(30)


def test_a_page_eligible_in_either_window_is_considered(ga4_day):
    """A page that fell out of relevance must still be visible as having done so.

    It clears the floor only in the window it has left, which is the whole point:
    a floor applied to the current window alone would hide every page that
    stopped being read.
    """
    _movement_history(
        ga4_day,
        [("/et/uudised/steadyish", 20, 20), ("/et/sundmused/gone", 0, 40)],
        spot=[("/et/sundmused/gone", 1, 0)],
    )

    movement = get_page_movement(
        start=START, end=END, previous_start=PREV_START, previous_end=PREV_END, limit=10
    )

    assert "/et/sundmused/gone" in {row.path for row in movement.falling}


def test_equal_values_appear_in_neither_direction(ga4_day):
    _movement_history(ga4_day, [("/et/uudised/steady", 50, 50)])

    movement = get_page_movement(
        start=START, end=END, previous_start=PREV_START, previous_end=PREV_END, limit=10
    )

    assert "/et/uudised/steady" not in {row.path for row in movement.rising}
    assert "/et/uudised/steady" not in {row.path for row in movement.falling}


def test_movement_is_one_query_over_the_whole_population(ga4_day, django_assert_num_queries):
    _movement_history(ga4_day, [(f"/et/teenused/p{i}", 40, 20) for i in range(30)])

    with django_assert_num_queries(2):
        result = get_page_movement(
            start=START, end=END, previous_start=PREV_START, previous_end=PREV_END, limit=10
        )
        assert result.rising


# ---------------------------------------------------------------------------
# Engagement
# ---------------------------------------------------------------------------


def test_engagement_per_view_divides_by_the_views_that_measured_it(ga4_day):
    for offset in range(30):
        # The last day measured views and no engagement seconds at all, which is
        # a state the metric's nullability genuinely produces.
        seconds = None if offset == 29 else 600
        ga4_day(START + dt.timedelta(days=offset), pages=(("/et/teenused/a", 10, seconds),))

    matrix = get_engagement_matrix(start=START, end=END)
    page = next(p for p in matrix.pages if p.path == "/et/teenused/a")

    assert page.page_views == 300
    assert page.views_with_seconds == 290
    # 29 days x 600 s over the 290 views that carried a reading, not over 300.
    assert page.seconds_per_view == pytest.approx(29 * 600 / 290)


def test_a_page_below_the_volume_floor_is_not_in_the_matrix(ga4_day):
    """One view and six hundred seconds must not dominate an engagement
    ranking."""
    ga4_day(START, pages=(("/et/teenused/rare", 1, 600), ("/et/teenused/busy", 200, 4000)))

    matrix = get_engagement_matrix(start=START, end=END)

    assert "/et/teenused/rare" not in {page.path for page in matrix.pages}
    assert "/et/teenused/busy" in {page.path for page in matrix.pages}


def test_the_quadrant_thresholds_are_the_medians_of_the_eligible_pages(ga4_day):
    ga4_day(
        START,
        pages=(
            ("/et/a", 100, 1000),
            ("/et/b", 200, 2000),
            ("/et/c", 300, 3000),
        ),
    )

    matrix = get_engagement_matrix(start=START, end=END)

    assert matrix.median_page_views == 200
    assert matrix.median_seconds_per_view == pytest.approx(10.0)


def test_every_quadrant_is_reachable_and_none_is_a_verdict(ga4_day):
    ga4_day(
        START,
        pages=(
            ("/et/many-deep", 400, 40000),
            ("/et/many-short", 400, 400),
            ("/et/few-deep", 60, 6000),
            ("/et/few-short", 60, 60),
        ),
    )

    matrix = get_engagement_matrix(start=START, end=END)
    quadrants = {page.path: matrix.quadrant_of(page) for page in matrix.pages}

    assert quadrants["/et/many-deep"] == "palju-sygav"
    assert quadrants["/et/many-short"] == "palju-lyhike"
    assert quadrants["/et/few-deep"] == "vahe-sygav"
    assert quadrants["/et/few-short"] == "vahe-lyhike"


def test_a_page_with_no_engagement_measurement_has_no_quadrant(ga4_day):
    ga4_day(START, pages=(("/et/unmeasured", 300, None), ("/et/measured", 300, 3000)))

    matrix = get_engagement_matrix(start=START, end=END)
    unmeasured = next(p for p in matrix.pages if p.path == "/et/unmeasured")

    assert unmeasured.seconds_per_view is None
    assert matrix.quadrant_of(unmeasured) == ""


# ---------------------------------------------------------------------------
# Concentration and language
# ---------------------------------------------------------------------------


def test_concentration_shares_the_content_denominator(ga4_day):
    ga4_day(
        START,
        pages=tuple((f"/et/uudised/p{i}", 100 - i, 500) for i in range(20))
        + (("/et", 9000, 20000),),
    )

    concentration = get_concentration(start=START, end=END)

    # The language root is not content and is out of both the list and its total.
    assert concentration.total_page_views == sum(100 - i for i in range(20))
    assert concentration.ranked_pages == 20
    assert 0 < concentration.top_5_share < concentration.top_10_share < 1


def test_language_is_read_from_the_path_prefix(ga4_day):
    ga4_day(
        START,
        pages=(
            ("/et/uudised/a", 100, 500),
            ("/en/news/b", 40, 200),
            ("/ru/novosti/c", 10, 50),
            ("/404.html", 7, 10),
            ("/", 3, 5),
        ),
    )

    mix = get_language_mix(start=START, end=END)
    by_key = {row.key: row for row in mix.rows}

    assert by_key["et"].page_views == 100
    assert by_key["en"].page_views == 40
    assert by_key["ru"].page_views == 10
    assert by_key[OTHER_LANGUAGE_KEY].page_views == 10


def test_language_uses_every_measured_page_view_as_its_denominator(ga4_day):
    """A language homepage is not a piece of content and is out of the ranking,
    but it is unambiguously an Estonian page and belongs in this count."""
    ga4_day(START, pages=(("/et", 900, 4000), ("/et/uudised/a", 100, 500)))

    mix = get_language_mix(start=START, end=END)

    assert mix.total_page_views == 1000
    assert mix.rows[0].key == "et"
    assert mix.rows[0].page_views == 1000


def test_a_language_root_is_not_mistaken_for_a_similar_prefix(ga4_day):
    ga4_day(START, pages=(("/etc/config", 50, 100), ("/et/uudised/a", 50, 100)))

    mix = get_language_mix(start=START, end=END)
    by_key = {row.key: row for row in mix.rows}

    assert by_key["et"].page_views == 50
    assert by_key[OTHER_LANGUAGE_KEY].page_views == 50


# ---------------------------------------------------------------------------
# Quality signals
# ---------------------------------------------------------------------------


def test_error_documents_are_counted_with_the_failed_address_appended(ga4_day):
    ga4_day(
        START,
        pages=(
            ("/404.html%3Fpage=/et/kadunud", 30, 60),
            ("/403.html", 10, 20),
            ("/et/uudised/a", 960, 5000),
        ),
    )

    signals = {s.key: s for s in get_quality_signals(start=START, end=END, total_page_views=1000)}

    assert signals["vead"].page_views == 40
    assert signals["vead"].share_of_page_views == pytest.approx(0.04)


def test_internal_search_is_counted_but_not_the_service_that_contains_the_word(ga4_day):
    """`/en/services/search-cooperation-partner` is a service the Chamber sells,
    and a substring rule would file it as internal search."""
    ga4_day(
        START,
        pages=(
            ("/et/search/node", 25, 40),
            ("/en/services/search-cooperation-partner", 300, 3000),
        ),
    )

    signals = {s.key: s for s in get_quality_signals(start=START, end=END, total_page_views=325)}

    assert signals["siseotsing"].page_views == 25


def test_the_cart_is_not_reported_as_a_website_fault(ga4_day):
    """Not every excluded route is an error. The cart is an ordinary working
    page that simply is not content, and counting it here would report a problem
    the site does not have."""
    ga4_day(START, pages=(("/et/cart", 500, 900),))

    signals = {s.key: s for s in get_quality_signals(start=START, end=END, total_page_views=500)}

    assert signals["vead"].page_views is None
    assert signals["siseotsing"].page_views is None


# ---------------------------------------------------------------------------
# One page
# ---------------------------------------------------------------------------


def test_page_detail_separates_the_window_from_everything_measured(ga4_day):
    ga4_day(dt.date(2025, 1, 1), pages=(("/et/liikmed/liikmemaks", 500, 5000),))
    for offset in range(30):
        ga4_day(START + dt.timedelta(days=offset), pages=(("/et/liikmed/liikmemaks", 10, 300),))

    detail = get_page_detail(path="/et/liikmed/liikmemaks", start=START, end=END)

    assert detail.page_views == 300
    assert detail.measured_total == 800
    assert detail.first_measured_on == dt.date(2025, 1, 1)
    assert detail.last_measured_on == END
    assert detail.days_seen == 30


def test_page_detail_accepts_a_pasted_url(ga4_day):
    ga4_day(START, pages=(("/et/liikmed/liikmemaks", 10, 300),))

    detail = get_page_detail(
        path="https://www.koda.ee/et/liikmed/liikmemaks/?utm_source=uudiskiri",
        start=START,
        end=END,
    )

    assert detail is not None
    assert detail.path == "/et/liikmed/liikmemaks"


def test_an_unmeasured_page_is_absent_rather_than_zero(ga4_day):
    ga4_day(START, pages=(("/et/uudised/a", 10, 100),))

    assert get_page_detail(path="/et/ei-ole-olemas", start=START, end=END) is None


def test_page_detail_never_sums_active_users(ga4_day):
    ga4_day(START, pages=(("/et/a", 10, 100, 8),))
    ga4_day(START + dt.timedelta(days=1), pages=(("/et/a", 10, 100, 9),))

    detail = get_page_detail(path="/et/a", start=START, end=END)

    assert not hasattr(detail, "active_users")
    assert not hasattr(detail, "peak_active_users")
