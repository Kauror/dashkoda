"""The age-normalised article metrics, and the rules that keep them honest.

Every test here is about a way the numbers could be wrong while still looking
right: a window that sums the wrong days, a listing page counted as an article,
an unmeasured article printed as a zero, a benchmark taken over three rows.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.news import analytics
from apps.news.categories import NewsCategory
from apps.news.public_models import NewsResource
from tests.news.conftest import COVERAGE_END, article, listing

pytestmark = pytest.mark.django_db


def coverage():
    from apps.visibility.ga4_selectors import get_coverage

    return get_coverage()


def window_for(resource, days: int) -> analytics.ArticleWindow:
    """Resolve one article's first window, the way a page does."""
    rows = analytics.annotate_first_window(
        analytics.with_window_bounds(NewsResource.objects.filter(pk=resource.pk), days=days),
        name=analytics.FIRST_WINDOW_ANNOTATION,
    )
    row = rows.first()
    published = row.published_at.astimezone(timezone.get_current_timezone()).date()
    return analytics.resolve_window(
        published_on=published,
        raw_views=getattr(row, analytics.FIRST_WINDOW_ANNOTATION),
        days=days,
        coverage=coverage(),
    )


# -- the window sums the article's own days, and only those -------------------


def test_first_month_counts_only_the_articles_own_first_thirty_days(ga4):
    """The property the whole module rests on.

    Views before publication and views on day 31 both belong to other questions.
    A window that leaked either would make every comparison between articles
    quietly wrong in a way no total would reveal.
    """
    published = dt.date(2026, 3, 1)
    item = article("lugu", published=published)
    ga4(
        views={
            item.path: {
                # before publication — impossible in life, deliberate here
                dt.date(2026, 2, 20): 500,
                published: 10,
                published + dt.timedelta(days=6): 5,
                published + dt.timedelta(days=29): 3,
                # day 31, one past the window
                published + dt.timedelta(days=30): 900,
            }
        }
    )

    assert window_for(item, analytics.FIRST_MONTH_DAYS).views == 18
    assert window_for(item, analytics.FIRST_WEEK_DAYS).views == 15


# -- eligibility: three different ways to have no figure ----------------------


def test_article_published_before_coverage_has_no_first_window(ga4):
    ga4()
    item = article("vana", published=dt.date(2025, 11, 1))

    result = window_for(item, analytics.FIRST_MONTH_DAYS)

    assert result.views is None
    assert result.reason == analytics.REASON_BEFORE_COVERAGE


def test_article_whose_window_has_not_elapsed_has_no_first_month(ga4):
    """A six-day-old article does not have a thirty-day result yet.

    It has part of one, and printing that as a thirty-day figure would put a
    number that is small because it is young beside numbers that are small
    because nobody read them.
    """
    ga4()
    item = article("varske", published=COVERAGE_END - dt.timedelta(days=5))

    result = window_for(item, analytics.FIRST_MONTH_DAYS)

    assert result.views is None
    assert result.reason == analytics.REASON_NOT_ELAPSED
    # Its first *week* has not elapsed either.
    assert window_for(item, analytics.FIRST_WEEK_DAYS).views is None


def test_a_gap_in_collection_withholds_the_window(ga4):
    """An uncollected day inside the window is missing, not zero.

    The sum would be short by an unknown amount, and there is no honest way to
    say by how much.
    """
    published = dt.date(2026, 3, 1)
    item = article("lunk", published=published)
    ga4(
        views={item.path: {published: 40}},
        skip={published + dt.timedelta(days=10)},
    )

    result = window_for(item, analytics.FIRST_MONTH_DAYS)

    assert result.views is None
    assert result.reason == analytics.REASON_COVERAGE_GAP


def test_a_fully_covered_window_with_no_rows_is_a_measured_zero(ga4):
    """The one place absence *is* a number, and why.

    Every day of the window was collected and the page appears nowhere in it.
    That is not "we did not measure this article" — it is "we measured it and
    nobody opened it", which is a finding the Chamber should see rather than an
    em dash that hides it.
    """
    published = dt.date(2026, 3, 1)
    item = article("keegi-ei-lugenud", published=published)
    ga4(views={"/et/uudised/keegi-teine": {published: 5}})

    result = window_for(item, analytics.FIRST_MONTH_DAYS)

    assert result.views == 0
    assert result.is_eligible


# -- listing pages are not articles -------------------------------------------


def test_listing_pages_are_excluded_from_articles_and_from_news_traffic(ga4):
    """The 21% inflation this registry exists to stop.

    `/et/uudised` is a catalogued `NewsResource` row carrying more measured
    traffic than most articles ever will, because everybody passes through it.
    Counting it as news *reading* overstates the Chamber's reach and hides the
    overstatement inside a plausible total.
    """
    day = dt.date(2026, 3, 1)
    index = listing("/et/uudised")
    item = article("pisike", published=day)
    ga4(views={index.path: {day: 247}, item.path: {day: 12}})

    assert index.path not in set(analytics.article_resources().values_list("path", flat=True))
    assert item.path in set(analytics.article_resources().values_list("path", flat=True))

    traffic = analytics.news_traffic(start=day, end=day)
    assert traffic.news_views == 12
    assert traffic.articles_read == 1


def test_news_share_uses_page_views_on_both_sides(ga4):
    day = dt.date(2026, 3, 1)
    item = article("lugu", published=day)
    ga4(views={item.path: {day: 25}}, site_views_per_day=100)

    traffic = analytics.news_traffic(start=day, end=day)

    assert traffic.news_views == 25
    assert traffic.site_views == 100
    assert traffic.share == pytest.approx(0.25)


def test_news_traffic_reports_no_data_rather_than_zero_when_nothing_is_measured():
    traffic = analytics.news_traffic(start=dt.date(2026, 3, 1), end=dt.date(2026, 3, 30))

    assert traffic.news_views is None
    assert traffic.share is None
    assert not traffic.has_data


# -- cohorts, medians and benchmarks ------------------------------------------


def test_percentiles_match_postgres_linear_interpolation():
    values = [1, 2, 3, 4]

    assert analytics.percentile(values, 0.5) == 2  # 2.5 rounds to even → 2
    assert analytics.percentile([1, 2, 3], 0.5) == 2
    assert analytics.percentile([10, 20], 0.25) == 12
    assert analytics.percentile([], 0.5) is None


def test_cohort_counts_a_measured_zero_as_an_observation(ga4):
    """An article nobody read is part of what normal looks like.

    Dropping unread articles from the median would describe only the ones that
    worked, and the median would climb every time the Chamber published
    something nobody opened.
    """
    published = dt.date(2026, 2, 1)
    read = article("loetud", published=published)
    article("lugemata", published=published)
    ga4(views={read.path: {published: 40}})

    values = analytics.cohort_values(days=analytics.FIRST_MONTH_DAYS, coverage=coverage())

    assert sorted(values) == [0, 40]


def test_a_cohort_below_the_minimum_is_not_quoted_as_a_benchmark(ga4):
    """Three articles are a coincidence, not a normal."""
    published = dt.date(2026, 2, 1)
    for index in range(3):
        article(f"vahe-{index}", published=published)
    ga4()

    cohorts = analytics.benchmark_cohorts(coverage=coverage())

    assert cohorts[""].count == 3
    assert not cohorts[""].is_usable
    assert analytics.benchmark_for(cohorts, NewsCategory.CHAMBER) is None


def test_a_small_category_falls_back_to_the_all_news_cohort(ga4):
    """A category with too few articles borrows the wider benchmark.

    Better a comparison against all news, stated, than no comparison — and far
    better than a median of two partner articles presented as normal.
    """
    published = dt.date(2026, 2, 1)
    for index in range(10):
        article(f"koda-{index}", published=published, category=NewsCategory.CHAMBER)
    article("sober", published=published, category=NewsCategory.PARTNER)
    ga4()

    cohorts = analytics.benchmark_cohorts(coverage=coverage())
    chosen = analytics.benchmark_for(cohorts, NewsCategory.PARTNER)

    assert cohorts[NewsCategory.PARTNER].count == 1
    assert chosen is not None
    assert chosen.label == "Kõik uudised"


def test_each_category_is_benchmarked_against_its_own_kind(ga4):
    """The finding that made this necessary.

    Koja and Sõprade news perform very differently — a median of 36 against 10
    in the real catalogue. One shared benchmark would file most partner news
    under "below normal", which describes the cohort it was compared with rather
    than the article.
    """
    published = dt.date(2026, 2, 1)
    views: dict[str, dict[dt.date, int]] = {}
    for index in range(10):
        strong = article(f"koda-{index}", published=published, category=NewsCategory.CHAMBER)
        weak = article(f"sober-{index}", published=published, category=NewsCategory.PARTNER)
        views[strong.path] = {published: 100}
        views[weak.path] = {published: 10}
    ga4(views=views)

    cohorts = analytics.benchmark_cohorts(coverage=coverage())

    assert cohorts[NewsCategory.CHAMBER].median == 100
    assert cohorts[NewsCategory.PARTNER].median == 10

    partner_cohort = analytics.benchmark_for(cohorts, NewsCategory.PARTNER)
    result = analytics.benchmark(10, partner_cohort)
    # Ten views is exactly normal for a partner article, and would have been
    # filed as a failure against the combined median.
    assert not result.is_below_normal
    assert result.ratio == pytest.approx(1.0)


def test_below_normal_is_the_cohorts_own_lowest_quartile(ga4):
    cohort = analytics.CohortStats(label="Kõik", count=20, p25=10, median=30, p75=60)

    assert analytics.benchmark(5, cohort).is_below_normal
    assert not analytics.benchmark(10, cohort).is_below_normal
    assert analytics.benchmark(70, cohort).is_above_normal


# -- measurement-window questions ---------------------------------------------


def test_most_read_ignores_publication_date(ga4):
    """An old article being read now belongs at the top of `Loetakse praegu`.

    This is the distinction the whole page is built around: the ranking is about
    when something was *read*, not when it was written.
    """
    old = article("2026-jaanuar", published=dt.date(2026, 1, 5))
    fresh = article("juuni", published=dt.date(2026, 6, 1))
    window_start, window_end = dt.date(2026, 6, 1), dt.date(2026, 6, 30)
    ga4(
        views={
            old.path: {window_start: 400},
            fresh.path: {window_start: 50},
        }
    )

    ranked = list(analytics.most_read(start=window_start, end=window_end, limit=5))

    assert [row.path for row in ranked] == [old.path, fresh.path]
    assert getattr(ranked[0], analytics.WINDOW_ANNOTATION) == 400


def test_evergreen_excludes_articles_still_in_their_launch(ga4):
    published_recently = dt.date(2026, 6, 10)
    old = article("vana-aga-loetav", published=dt.date(2026, 1, 2))
    fresh = article("uus", published=published_recently)
    start, end = dt.date(2026, 6, 15), dt.date(2026, 6, 30)
    ga4(views={old.path: {start: 30}, fresh.path: {start: 80}})

    rows = list(analytics.evergreen(start=start, end=end, today=end))

    assert [row.path for row in rows] == [old.path]


def test_first_week_leaders_only_rank_complete_weeks(ga4):
    """A two-day-old article is not ranked against seven-day-complete ones."""
    complete = article("valmis", published=COVERAGE_END - dt.timedelta(days=20))
    immature = article("poolik", published=COVERAGE_END - dt.timedelta(days=2))
    ga4(
        views={
            complete.path: {COVERAGE_END - dt.timedelta(days=20): 30},
            immature.path: {COVERAGE_END - dt.timedelta(days=2): 900},
        }
    )

    rows = list(analytics.first_week_leaders(coverage=coverage(), today=COVERAGE_END))

    assert [row.path for row in rows] == [complete.path]


def test_concentration_ranks_the_whole_population_before_slicing(ga4):
    day = dt.date(2026, 3, 1)
    views = {}
    for index in range(12):
        item = article(f"lugu-{index}", published=day)
        views[item.path] = {day: (12 - index) * 10}
    ga4(views=views)

    result = analytics.concentration(start=day, end=day)

    assert result.articles_read == 12
    # 120+110+100+90+80 = 500 of 780
    assert result.top_5 == 500
    assert result.total_views == 780
    assert result.top_5_share == pytest.approx(500 / 780)


# -- publication windows ------------------------------------------------------


def test_previous_window_is_equal_length_and_does_not_overlap():
    start, end = analytics.previous_window(dt.date(2026, 7, 16), dt.date(2026, 8, 14))

    assert (start, end) == (dt.date(2026, 6, 16), dt.date(2026, 7, 15))
    assert (end - start).days == (dt.date(2026, 8, 14) - dt.date(2026, 7, 16)).days


def test_published_between_reports_unknown_rather_than_folding_it_in(ga4):
    day = dt.date(2026, 3, 1)
    article("koda", published=day, category=NewsCategory.CHAMBER)
    article("sober", published=day, category=NewsCategory.PARTNER)
    article("teadmata", published=day, category="")
    listing("/et/uudised")

    counted = analytics.published_between(day, day)

    assert counted.total == 3
    assert (counted.chamber, counted.partner, counted.unknown) == (1, 1, 1)
    # The share names classified articles as its denominator, not all three.
    assert counted.classified == 2
    assert counted.chamber_share == pytest.approx(0.5)
