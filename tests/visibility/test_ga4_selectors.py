"""What the stored history answers, and the aggregation it must refuse.

The arithmetic here is the part of this feature most likely to be wrong in a way
nobody notices: a sum that should not be a sum produces a plausible number.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.visibility.bootstrap import ensure_ga4_source
from apps.visibility.ga4_selectors import (
    GRAIN_DAY,
    GRAIN_MONTH,
    GRAIN_WEEK,
    get_article_views,
    get_channel_totals,
    get_coverage,
    get_top_pages,
    get_traffic_series,
    grain_for,
    missing_dates,
)
from apps.visibility.models import Ga4ChannelDaily, Ga4DailySnapshot, Ga4PageDaily

pytestmark = pytest.mark.django_db

START = dt.date(2026, 1, 1)


@pytest.fixture
def provenance():
    from apps.sources.services import build_import_run, register_external_reference

    source = ensure_ga4_source()
    artifact = register_external_reference(
        source=source,
        external_reference="synthetic:ga4-selectors",
        original_name="synthetic.json",
        mime_type="application/json",
        sha256="c" * 64,
        size_bytes=10,
    )
    run = build_import_run(
        artifact=artifact,
        importer_name="synthetic_selector_test",
        schema_version="2.0",
        dry_run=False,
    )
    return source, artifact, run


@pytest.fixture
def day(provenance):
    source, artifact, run = provenance
    counter = {"n": 0}

    def _day(report_date, *, current=True, pages=(), channels=(), **figures):
        counter["n"] += 1
        snapshot = Ga4DailySnapshot.objects.create(
            source=source,
            artifact=artifact,
            import_run=run,
            report_date=report_date,
            observed_at=timezone.now(),
            checksum=f"{counter['n']:064d}",
            is_current_for_date=current,
            has_page_detail=bool(pages),
            **figures,
        )
        for path, views, *rest in pages:
            Ga4PageDaily.objects.create(
                snapshot=snapshot,
                report_date=report_date,
                path=path,
                page_views=views,
                active_users=rest[0] if rest else None,
            )
        for name, sessions in channels:
            Ga4ChannelDaily.objects.create(
                snapshot=snapshot, report_date=report_date, channel=name, sessions=sessions
            )
        return snapshot

    return _day


# -- the rule that matters most ------------------------------------------


def test_sessions_and_page_views_are_summed_across_a_period(day):
    day(START, sessions=100, page_views=300)
    day(START + dt.timedelta(days=1), sessions=120, page_views=340)

    series = get_traffic_series(start=START, end=START + dt.timedelta(days=1))

    assert series.total_sessions == 220
    assert series.total_page_views == 640


def test_daily_users_are_never_added_into_a_period_total(day):
    """400 people on Monday and 380 on Tuesday are not 780 people. Most of them
    are the same people, and no arithmetic over daily distinct counts can say
    how many. The only figure offered is the busiest day, named as one."""
    day(START, sessions=1, active_users=400)
    day(START + dt.timedelta(days=1), sessions=1, active_users=380)

    series = get_traffic_series(start=START, end=START + dt.timedelta(days=1))

    assert series.peak_active_users == 400
    assert series.peak_active_users != 780
    assert not hasattr(series, "total_active_users"), (
        "a summable users total must not exist: a column that can be SUM()-ed will be"
    )


def test_a_monthly_bucket_peaks_its_users_and_sums_its_events(day):
    for offset in range(40):
        day(
            START + dt.timedelta(days=offset),
            sessions=10,
            page_views=20,
            active_users=100 + offset,
        )

    series = get_traffic_series(start=START, end=START + dt.timedelta(days=39), grain=GRAIN_MONTH)

    january = series.points[0]
    assert january.sessions == 310, "31 days of ten sessions"
    assert january.peak_active_users == 130, "the busiest day in January, not a sum"


# -- only current revisions count ----------------------------------------


def test_a_superseded_revision_is_never_counted(day):
    """Both revisions of one day summed would count that Tuesday twice."""
    day(START, current=False, sessions=100)
    day(START, current=True, sessions=140)

    series = get_traffic_series(start=START, end=START)

    assert series.total_sessions == 140


def test_page_rows_of_a_superseded_revision_are_not_counted(day):
    day(START, current=False, sessions=1, pages=(("/et/uudised/a", 50),))
    day(START, current=True, sessions=1, pages=(("/et/uudised/a", 70),))

    pages = get_top_pages(start=START, end=START)

    assert [(page.path, page.page_views) for page in pages] == [("/et/uudised/a", 70)]


# -- grain ---------------------------------------------------------------


def test_the_grain_follows_the_span():
    assert grain_for(30) == GRAIN_DAY
    assert grain_for(90) == GRAIN_DAY
    assert grain_for(200) == GRAIN_WEEK
    assert grain_for(365 * 3) == GRAIN_MONTH


# -- coverage ------------------------------------------------------------


def test_coverage_reports_what_exists_and_what_is_missing(day):
    day(START, sessions=1)
    day(START + dt.timedelta(days=3), sessions=1)

    coverage = get_coverage()

    assert coverage.earliest == START
    assert coverage.latest == START + dt.timedelta(days=3)
    assert coverage.days_covered == 2
    assert coverage.span_days == 4
    assert coverage.missing_days == 2


def test_missing_dates_names_the_gaps(day):
    day(START, sessions=1)
    day(START + dt.timedelta(days=2), sessions=1)

    assert missing_dates(START, START + dt.timedelta(days=2)) == (START + dt.timedelta(days=1),)


def test_an_empty_history_has_no_coverage_and_no_span():
    coverage = get_coverage()

    assert coverage.has_data is False
    assert coverage.span_days == 0
    assert coverage.missing_days == 0


# -- pages ---------------------------------------------------------------


def test_top_pages_are_summed_over_the_period_and_ordered(day):
    day(START, sessions=1, pages=(("/a", 10), ("/b", 30)))
    day(START + dt.timedelta(days=1), sessions=1, pages=(("/a", 50), ("/b", 5)))

    pages = get_top_pages(start=START, end=START + dt.timedelta(days=1))

    assert [(page.path, page.page_views) for page in pages] == [("/a", 60), ("/b", 35)]


def test_a_section_prefix_matches_whole_segments(day):
    """`/et/uudiseks` is a different section and must not be filed under news."""
    day(
        START,
        sessions=1,
        pages=(("/et/uudised", 10), ("/et/uudised/a", 20), ("/et/uudiseks", 99)),
    )

    pages = get_top_pages(start=START, end=START, prefix="/et/uudised")

    assert {page.path for page in pages} == {"/et/uudised", "/et/uudised/a"}


# -- channels ------------------------------------------------------------


def test_channel_sessions_are_additive(day):
    day(START, sessions=1, channels=(("Organic Search", 70), ("Direct", 30)))
    day(START + dt.timedelta(days=1), sessions=1, channels=(("Organic Search", 40),))

    totals = get_channel_totals(start=START, end=START + dt.timedelta(days=1))

    assert [(t.channel, t.sessions) for t in totals] == [("Organic Search", 110), ("Direct", 30)]


# -- article performance -------------------------------------------------


class Item:
    """The shape `get_article_views` reads: a URL and a publication moment."""

    def __init__(self, url, published):
        self.canonical_url = url
        self.published_at = published


def test_an_article_is_matched_by_exact_canonical_path(day):
    day(START, sessions=1, pages=(("/et/uudised/a", 40),))

    views = get_article_views([Item("https://www.koda.ee/et/uudised/a?utm_source=x", START)])

    assert views["/et/uudised/a"].total == 40


def test_a_similar_path_is_not_matched(day):
    """No fuzzy matching anywhere: a wrong match attributes one article's
    readership to another and nothing in the figures would ever reveal it."""
    day(START, sessions=1, pages=(("/et/uudised/a-pikem", 40),))

    views = get_article_views([Item("https://www.koda.ee/et/uudised/a", START)])

    assert views["/et/uudised/a"].total is None


def test_the_first_windows_count_only_the_article_s_own_first_days(day):
    published = START
    for offset in range(40):
        day(
            START + dt.timedelta(days=offset),
            sessions=1,
            pages=(("/et/uudised/a", 10),),
        )

    views = get_article_views(
        [Item("https://www.koda.ee/et/uudised/a", published)],
        today=START + dt.timedelta(days=39),
    )["/et/uudised/a"]

    assert views.first_7_days == 70
    assert views.first_30_days == 300
    assert views.total == 400
    assert views.last_30_days == 300


def test_an_article_older_than_the_measurement_does_not_claim_a_lifetime_total(day):
    """The figure is views within GA4's coverage. Printing it as a lifetime
    count would be a number about a period nobody measured."""
    day(START, sessions=1, pages=(("/et/uudised/vana", 40),))

    views = get_article_views(
        [Item("https://www.koda.ee/et/uudised/vana", START - dt.timedelta(days=365))]
    )["/et/uudised/vana"]

    assert views.total == 40
    assert views.covers_publication is False
    assert views.first_7_days is None or views.first_7_days == 0


def test_an_article_published_inside_the_coverage_may_say_lifetime(day):
    day(START, sessions=1, pages=(("/et/uudised/uus", 40),))
    day(START + dt.timedelta(days=1), sessions=1, pages=(("/et/uudised/uus", 10),))

    views = get_article_views(
        [Item("https://www.koda.ee/et/uudised/uus", START + dt.timedelta(days=1))]
    )["/et/uudised/uus"]

    assert views.covers_publication is True


def test_an_unmatchable_url_is_absent_rather_than_zero(day):
    day(START, sessions=1, pages=(("/et/uudised/a", 40),))

    views = get_article_views([Item("", START), Item("https://www.koda.ee/et/uudised/a", START)])

    assert set(views) == {"/et/uudised/a"}


def test_many_articles_cost_a_fixed_number_of_queries(day, django_assert_num_queries):
    """The N+1 this design exists to avoid: a news list of fifty items must not
    be fifty queries."""
    pages = tuple((f"/et/uudised/{index}", index + 1) for index in range(50))
    day(START, sessions=1, pages=pages)
    items = [Item(f"https://www.koda.ee/et/uudised/{index}", START) for index in range(50)]

    # One coverage aggregate and one query per window.
    with django_assert_num_queries(5):
        views = get_article_views(items)

    assert len(views) == 50


# -- how the channel breakdown is presented ----------------------------------


def test_the_channel_breakdown_is_its_own_view_and_still_lists_every_channel(viewer_client, day):
    """Twelve channel rows are an answer few readers arrive with.

    On the Nähtavus page they were a `<details>` that started shut, because open
    they sat between the traffic chart and `Enim vaadatud sisu` and pushed the
    content ranking — which readers do arrive for — below the fold.

    Koduleht gives acquisition a focus view of its own, so nothing has to be
    collapsed to make room for it. What has not changed is that the rows are
    rendered into the page rather than fetched, and that the heading is a real
    heading rather than a summary line.
    """
    day(
        START,
        sessions=100,
        page_views=300,
        channels=(("Organic Search", 70), ("Direct", 30)),
    )

    page = viewer_client.get(
        reverse("visibility"), {"fookus": "kanalid", "periood": "koik"}
    ).content.decode()

    assert "Külastused kanalite kaupa" in page
    assert "Organic Search" in page
    assert "Direct" in page
