"""The impact and publishing views.

These cover the rules that are easiest to break while the page still renders: a
ranking sliced before it is ordered, and a partial period drawn as a finished
one.

The newsletter half of this suite moved to
`tests/visibility/test_mailings_page.py` with the builder it exercises, when the
Smaily material became `Otsepostitused`.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.news import analytics, page
from apps.news.categories import NewsCategory
from apps.news.measurement import resolve_reading
from apps.news.periods import resolve_period
from tests.news.conftest import COVERAGE_END, article

pytestmark = pytest.mark.django_db


def coverage():
    from apps.visibility.ga4_selectors import get_coverage

    return get_coverage()


# -- impact -------------------------------------------------------------------


def test_the_three_lenses_answer_three_different_questions(ga4):
    """One `Enim vaadatud` table cannot answer all three, which is why there are three.

    The old article wins `Loetakse praegu`; the new one wins the first-week
    ranking. A single ranking would have to pick one of these to be wrong about.
    """
    old = article("vana", published=dt.date(2026, 1, 5))
    recent_day = COVERAGE_END - dt.timedelta(days=20)
    fresh = article("varske", published=recent_day)
    ga4(
        views={
            old.path: {COVERAGE_END: 500},
            fresh.path: {recent_day: 300},
        }
    )
    reading = resolve_reading("30", coverage=coverage())

    now = page.build_impact(reading=reading, coverage=coverage(), lens=page.LENS_NOW)
    week = page.build_impact(reading=reading, coverage=coverage(), lens=page.LENS_WEEK)

    assert now["ranked"][0].title == old.title
    assert week["ranked"][0].title == fresh.title


def test_the_default_lens_is_the_age_normalised_one():
    """Comparing articles by total measured views compares their ages."""
    assert page.DEFAULT_LENS == page.LENS_MONTH
    assert page.parse_lens(None) == page.LENS_MONTH
    assert page.parse_lens("zzz") == page.LENS_MONTH


def test_the_first_month_ranking_orders_the_whole_population_before_slicing(ga4):
    """A ranking of a page is not a ranking.

    Thirty eligible articles, and the strongest is the one published earliest —
    so a ranking that took the newest thirty rows and sorted those in Python
    would put it nowhere near the top.
    """
    published_first = dt.date(2026, 2, 1)
    views = {}
    best = article("parim", published=published_first)
    views[best.path] = {published_first: 900}
    for index in range(30):
        item = article(f"muu-{index}", published=published_first + dt.timedelta(days=index + 1))
        views[item.path] = {published_first + dt.timedelta(days=index + 1): 10}
    ga4(views=views)

    built = page.build_impact(
        reading=resolve_reading("30", coverage=coverage()),
        coverage=coverage(),
        lens=page.LENS_MONTH,
    )

    assert built["ranked"][0].title == best.title


def test_the_distribution_describes_the_eligible_cohort(ga4):
    published = dt.date(2026, 2, 1)
    views = {}
    for index in range(20):
        item = article(f"lugu-{index}", published=published)
        views[item.path] = {published: index * 10}
    ga4(views=views)

    built = page.build_impact(
        reading=resolve_reading("30", coverage=coverage()),
        coverage=coverage(),
        lens=page.LENS_MONTH,
    )

    distribution = built["distribution"]
    assert distribution is not None
    assert distribution.has_data
    # Every eligible article lands in exactly one band.
    assert sum(int(row[1]) for row in distribution.table_rows) == 20


def test_category_performance_separates_output_from_fair_performance(ga4):
    """Koja out-publishes Sõprade, and that is not the same as out-performing it."""
    published = dt.date(2026, 2, 1)
    views = {}
    for index in range(12):
        chamber = article(f"koda-{index}", published=published, category=NewsCategory.CHAMBER)
        views[chamber.path] = {published: 20}
    for index in range(12):
        partner = article(f"sober-{index}", published=published, category=NewsCategory.PARTNER)
        views[partner.path] = {published: 20}
    # Three extra Chamber articles: more output, identical per-article result.
    for index in range(3):
        extra = article(f"koda-lisa-{index}", published=published, category=NewsCategory.CHAMBER)
        views[extra.path] = {published: 20}
    ga4(views=views)

    # `Koja ja Sõprade uudised` left the page on 2026-08-16 and `build_impact`
    # stopped composing it, so this asserts the selector that still exists and
    # still holds the rule. The figures are raw here rather than formatted,
    # which is the only difference from what the section used to render.
    reading = resolve_reading("30", coverage=coverage())
    cover = coverage()
    cohorts = analytics.benchmark_cohorts(coverage=cover)
    cohort_start = cover.latest - dt.timedelta(days=analytics.BENCHMARK_COHORT_DAYS - 1)
    rows = analytics.category_performance(
        cohorts=cohorts,
        cohort_start=cohort_start,
        cohort_end=cover.latest,
        reading_start=reading.start,
        reading_end=reading.end,
    )
    by_key = {row.key: row for row in rows}

    assert by_key[NewsCategory.CHAMBER].published == 15
    assert by_key[NewsCategory.PARTNER].published == 12
    # Same median: publishing more did not make each article better read.
    assert by_key[NewsCategory.CHAMBER].median_first_month == 20
    assert by_key[NewsCategory.PARTNER].median_first_month == 20


# -- publishing ---------------------------------------------------------------


def test_the_cadence_chart_never_draws_a_daily_grain(ga4):
    ga4()
    for index in range(40):
        article(f"lugu-{index}", published=COVERAGE_END - dt.timedelta(days=index * 4))

    weekly = page.build_publishing(
        period=resolve_period("90", today=COVERAGE_END), coverage=coverage()
    )
    monthly = page.build_publishing(
        period=resolve_period("1a", today=COVERAGE_END), coverage=coverage()
    )

    assert weekly["grain"] == "week"
    assert monthly["grain"] == "month"


def test_an_unfinished_period_is_labelled_partial(ga4):
    """A month that is a third over must not read as a collapse in output."""
    ga4()
    mid_month = dt.date(2026, 6, 10)
    for index in range(5):
        article(f"lugu-{index}", published=mid_month - dt.timedelta(days=index))

    built = page.build_publishing(
        period=resolve_period(
            "kohandatud",
            "2026-01-01",
            mid_month.isoformat(),
            today=mid_month,
        ),
        coverage=coverage(),
    )

    notes = [row[-1] for row in built["cadence"].table_rows]
    assert "osaline" in notes


def test_unclassified_articles_are_stacked_rather_than_dropped(ga4):
    ga4()
    day = dt.date(2026, 3, 1)
    article("koda", published=day, category=NewsCategory.CHAMBER)
    article("teadmata", published=day, category="")

    built = page.build_publishing(period=resolve_period("koik"), coverage=coverage())

    # The bar's height is the publication count, so the two must agree.
    assert built["counted"].total == 2
    assert sum(int(row[1]) for row in built["cadence"].table_rows) == 2
