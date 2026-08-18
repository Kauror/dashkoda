"""What the archive costs, and that the cost does not grow with the list.

The old page rendered ten items and bought their analytics in one bulk lookup,
which was the right shape. This page renders thirty rows out of a catalogue of
twelve hundred and has to rank the whole population before slicing it — so the
tempting implementation, a dictionary of every path's views pulled into Python
and sorted there, would be correct and would get slower every month.

It is a subquery annotation instead. These tests are what stops that becoming a
per-row lookup again: the same query count for one row and for a full page.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.news.archive import PER_PAGE, build_news_archive
from apps.news.periods import SORT_VIEWS
from apps.news.public_models import NewsResource, TitleOrigin

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 8, 11)

#: Count, page slice, coverage, catalogue facts, and the three benchmark
#: cohorts (`""`, `meie_uudised`, `soprade_uudised`) the new `vs tüüpiline`
#: column reads against every row on the page. Stated as a number rather than
#: a range so that adding another query is a decision somebody makes on
#: purpose.
#:
#: It was four until 2026-08-18, when the archive table grew a `vs tüüpiline`
#: column: each row now needs a cohort to compare against, and
#: `analytics.benchmark_cohorts` is one query per cohort rather than one query
#: for all three — three articles, three cohorts, one query each, whatever the
#: page size. #106 already established the pattern this budget follows: a
#: catalogue-wide question is one query regardless of how many rows are shown,
#: never one per row.
EXPECTED_QUERIES = 7


def catalogue(count: int) -> None:
    NewsResource.objects.bulk_create(
        NewsResource(
            canonical_url=f"https://www.koda.ee/et/uudised/lugu-{index:03d}",
            path=f"/et/uudised/lugu-{index:03d}",
            title=f"Uudis {index}",
            published_at=timezone.make_aware(
                dt.datetime.combine(TODAY - dt.timedelta(days=index), dt.time(9, 0))
            ),
            title_origin=TitleOrigin.FEED,
            last_seen_at=timezone.now(),
        )
        for index in range(count)
    )


@pytest.mark.parametrize("rows", [1, PER_PAGE])
def test_the_query_count_does_not_depend_on_how_many_rows_are_shown(
    django_assert_num_queries, rows
):
    catalogue(rows)

    with django_assert_num_queries(EXPECTED_QUERIES):
        archive = build_news_archive(period_key="koik", today=TODAY)
        assert len(archive.rows) == rows


def test_ranking_by_views_costs_no_more_than_ranking_by_date(django_assert_num_queries):
    """The ordering happens in PostgreSQL. If it ever moves into Python, the
    view totals have to be fetched for the whole population and this changes."""
    catalogue(PER_PAGE * 3)

    with django_assert_num_queries(EXPECTED_QUERIES):
        build_news_archive(period_key="koik", sort=SORT_VIEWS, today=TODAY)


def test_a_larger_catalogue_does_not_cost_more_queries(django_assert_num_queries):
    catalogue(PER_PAGE * 5)

    with django_assert_num_queries(EXPECTED_QUERIES):
        archive = build_news_archive(period_key="koik", today=TODAY)

    assert archive.total == PER_PAGE * 5
    assert len(archive.rows) == PER_PAGE
