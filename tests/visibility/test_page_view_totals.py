"""The shared total-views selector every module joins GA4 through.

One query, one answer. The reason this exists as one function rather than a
`SUM` in each app is that two surfaces printing different totals for one article
is a defect nobody notices until somebody compares them.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.visibility.bootstrap import ensure_ga4_source
from apps.visibility.ga4_selectors import get_page_view_totals
from apps.visibility.models import Ga4DailySnapshot, Ga4PageDaily

pytestmark = pytest.mark.django_db

START = dt.date(2026, 1, 1)
ARTICLE = "/et/uudised/example"


@pytest.fixture
def day():
    from apps.sources.services import build_import_run, register_external_reference

    source = ensure_ga4_source()
    artifact = register_external_reference(
        source=source,
        external_reference="synthetic:page-view-totals",
        original_name="synthetic.json",
        mime_type="application/json",
        sha256="d" * 64,
        size_bytes=10,
    )
    run = build_import_run(
        artifact=artifact,
        importer_name="synthetic_totals_test",
        schema_version="2.0",
        dry_run=False,
    )
    counter = {"n": 0}

    def _day(report_date, *, current=True, pages=()):
        counter["n"] += 1
        snapshot = Ga4DailySnapshot.objects.create(
            source=source,
            artifact=artifact,
            import_run=run,
            report_date=report_date,
            observed_at=timezone.now(),
            checksum=f"{counter['n']:064d}",
            is_current_for_date=current,
            has_page_detail=True,
            sessions=1,
        )
        for path, views in pages:
            Ga4PageDaily.objects.create(
                snapshot=snapshot, report_date=report_date, path=path, page_views=views
            )
        return snapshot

    return _day


# -- the total -----------------------------------------------------------


def test_one_path_totals_its_days(day):
    day(START, pages=((ARTICLE, 40),))
    day(START + dt.timedelta(days=1), pages=((ARTICLE, 60),))

    assert get_page_view_totals([ARTICLE])[ARTICLE].total == 100


def test_many_paths_come_back_in_one_query(day, django_assert_num_queries):
    """The property the whole design rests on: a list of items costs a fixed
    number of queries, not one per item."""
    pages = tuple((f"/et/uudised/{index}", index + 1) for index in range(30))
    day(START, pages=pages)

    # One coverage aggregate, one grouped total.
    with django_assert_num_queries(2):
        totals = get_page_view_totals([path for path, _ in pages])

    assert len(totals) == 30
    assert totals["/et/uudised/29"].total == 30


def test_each_path_keeps_its_own_views(day):
    """A wrong attribution here is invisible in the numbers afterwards."""
    day(START, pages=(("/et/uudised/a", 40), ("/et/uudised/b", 7)))

    totals = get_page_view_totals(["/et/uudised/a", "/et/uudised/b"])

    assert totals["/et/uudised/a"].total == 40
    assert totals["/et/uudised/b"].total == 7


# -- which revisions count -----------------------------------------------


def test_a_superseded_revision_is_not_added_to_the_current_one(day):
    """Both revisions summed would count that day twice."""
    day(START, current=False, pages=((ARTICLE, 50),))
    day(START, current=True, pages=((ARTICLE, 70),))

    assert get_page_view_totals([ARTICLE])[ARTICLE].total == 70


# -- absence -------------------------------------------------------------


def test_an_unmeasured_path_is_absent_rather_than_zero(day):
    """A page nobody measured has not been measured at zero visits, and the
    interface must be able to tell the difference."""
    day(START, pages=((ARTICLE, 40),))

    totals = get_page_view_totals([ARTICLE, "/et/uudised/never-measured"])

    assert "/et/uudised/never-measured" not in totals
    assert totals[ARTICLE].total == 40


def test_a_measured_zero_is_kept_as_a_reading(day):
    """GA4 reporting a row with no views is a measurement. Dropping it would
    turn "we looked and there was nothing" into "we never looked"."""
    day(START, pages=((ARTICLE, 0),))

    assert get_page_view_totals([ARTICLE])[ARTICLE].total == 0


def test_nothing_asked_for_is_nothing_queried(django_assert_num_queries):
    with django_assert_num_queries(0):
        assert get_page_view_totals([]) == {}
        assert get_page_view_totals(["", None]) == {}


# -- identity ------------------------------------------------------------


def test_a_url_and_a_path_reach_the_same_total(day):
    day(START, pages=((ARTICLE, 40),))

    for spelling in (
        ARTICLE,
        "https://www.koda.ee/et/uudised/example",
        "https://koda.ee/et/uudised/example/",
        "/et/uudised/example?utm_source=uudiskiri",
    ):
        totals = get_page_view_totals([spelling])
        assert totals[ARTICLE].total == 40, spelling


def test_several_spellings_of_one_page_are_asked_for_once(day, django_assert_num_queries):
    day(START, pages=((ARTICLE, 40),))

    with django_assert_num_queries(2):
        totals = get_page_view_totals(
            [ARTICLE, ARTICLE + "/", "https://www.koda.ee/et/uudised/example?fbclid=x"]
        )

    assert list(totals) == [ARTICLE]


# -- coverage ------------------------------------------------------------


def test_the_total_carries_the_coverage_it_was_measured_over(day):
    day(START, pages=((ARTICLE, 40),))
    day(START + dt.timedelta(days=5), pages=((ARTICLE, 10),))

    views = get_page_view_totals([ARTICLE])[ARTICLE]

    assert views.coverage_start == START
    assert views.coverage_end == START + dt.timedelta(days=5)


def test_coverage_says_whether_the_total_is_a_lifetime(day):
    """291 views on a page published in 2019 is 291 views *since measurement
    began*, which is a different claim from 291 views ever."""
    day(START, pages=((ARTICLE, 40),))
    views = get_page_view_totals([ARTICLE])[ARTICLE]

    assert views.covers(START + dt.timedelta(days=1)) is True
    assert views.covers(START - dt.timedelta(days=1)) is False
    assert views.covers(None) is False
