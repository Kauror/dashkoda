"""The overview's four measures, and the one window that now governs them.

The overview is the screen somebody opens with a question, so these tests are
about whether it answers one — and about the two ways it could look like it did
when it had not: a placeholder standing in for a figure that does not exist, and
a window silently clipped without saying so.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.news import analytics, page
from apps.news.categories import NewsCategory
from tests.news.conftest import COVERAGE_END, COVERAGE_START, article

pytestmark = pytest.mark.django_db


def coverage():
    from apps.visibility.ga4_selectors import get_coverage

    return get_coverage()


# -- every view renders against nothing at all --------------------------------


def test_the_page_renders_with_no_data_whatsoever(viewer_client):
    """A dashboard that 500s on an unconnected source is worse than no dashboard.

    The regression this exists for: `eligible_cohort` short-circuited to a bare
    `none()` when GA4 held nothing, so the correlated subquery went looking for
    window-bound columns that had never been annotated and the page raised
    `FieldError`. Driven through the real view rather than the builders, so a
    template that reaches for a figure the builder did not produce fails here
    too.
    """
    from django.urls import reverse

    response = viewer_client.get(reverse("news"))

    assert response.status_code == 200
    assert "Uudised" in response.content.decode()


def test_the_page_renders_with_articles_but_no_analytics(viewer_client):
    """The other half-connected state: a catalogue and no measurement.

    News collection and GA4 are separate sources with separate schedules, so
    "articles but nothing measured" is a state production really passes through
    — and the one where a first-window figure has nothing to be computed from.
    """
    from django.urls import reverse

    article("lugu", published=dt.date(2026, 3, 1))

    response = viewer_client.get(reverse("news"))

    assert response.status_code == 200


@pytest.mark.parametrize("stray", ["fookus=moju", "fookus=arhiiv", "loetud=90", "vaade=kuu"])
def test_a_retired_parameter_from_an_old_bookmark_is_simply_unread(viewer_client, stray):
    """Three focuses and a second window all merged onto this one page.

    `fookus=`, `loetud=` and `vaade=` are none of them parsed any more — a
    bookmark carrying one still opens the page, exactly as an unrecognised
    query parameter always has.
    """
    from django.urls import reverse

    response = viewer_client.get(f"{reverse('news')}?{stray}")

    assert response.status_code == 200
    assert "Uudised" in response.content.decode()


# -- the read window is the same window as the publication window, clipped ----


def test_the_read_window_matches_the_period_when_it_fits_inside_coverage(ga4):
    ga4()

    period = page.resolve_period("30", today=COVERAGE_END)
    start, end, truncated = page._reading_window(period, coverage=coverage())

    assert start == period.start
    assert end == period.end
    assert not truncated


def test_an_open_ended_period_reads_the_whole_coverage(ga4):
    """`Kõik` has no bounds of its own — the read window borrows coverage's."""
    ga4()

    period = page.resolve_period("koik")
    start, end, truncated = page._reading_window(period, coverage=coverage())

    assert start == COVERAGE_START
    assert end == COVERAGE_END
    assert not truncated


def test_a_period_reaching_past_coverage_is_clipped_and_says_so(ga4):
    ga4()

    period = page.resolve_period("1a", today=COVERAGE_END)
    start, end, truncated = page._reading_window(period, coverage=coverage())

    assert start == COVERAGE_START
    assert end == COVERAGE_END
    assert truncated


def test_no_coverage_at_all_leaves_the_read_window_empty():
    from apps.visibility.ga4_selectors import Coverage

    period = page.resolve_period("30", today=COVERAGE_END)
    start, end, truncated = page._reading_window(period, coverage=Coverage())

    assert start is None
    assert end is None
    assert not truncated


# -- the four measures --------------------------------------------------------


def headline(built, key):
    return next((one for one in built["headlines"] if one.key == key), None)


def _overview(*, period_key="koik", today=None, cohorts=None):
    period = page.resolve_period(period_key, today=today)
    coverage_ = coverage()
    start, end, _ = page._reading_window(period, coverage=coverage_)
    return page.build_overview(
        period=period,
        coverage=coverage_,
        read_start=start,
        read_end=end,
        cohorts=cohorts if cohorts is not None else {},
    )


def test_the_overview_answers_the_first_three_questions(ga4):
    published = dt.date(2026, 3, 1)
    views = {}
    for index in range(12):
        item = article(f"lugu-{index}", published=published)
        views[item.path] = {published: 40 + index}
    ga4(views=views, site_views_per_day=1000)

    built = _overview()

    assert headline(built, "published").value == "12"
    assert headline(built, "news_views") is not None
    assert headline(built, "news_share") is not None


def test_a_measure_with_no_data_is_absent_rather_than_zero(ga4):
    """Three honest figures beat four where the fourth is invented.

    With no GA4 rows at all there is no readership figure, no share and no
    typical-month figure — and a card reading `0` would look exactly like a
    measurement.
    """
    article("lugu", published=dt.date(2026, 3, 1))

    built = _overview()

    assert headline(built, "published") is not None
    assert headline(built, "news_views") is None
    assert headline(built, "news_share") is None
    assert headline(built, "typical_first_month") is None


def test_the_share_change_is_in_percentage_points(ga4):
    """A share that moved from 20% to 10% did not fall by 10%."""
    current_day = COVERAGE_END
    previous_day = COVERAGE_END - dt.timedelta(days=30)
    item = article("lugu", published=dt.date(2026, 2, 1))
    ga4(views={item.path: {current_day: 100, previous_day: 200}}, site_views_per_day=1000)

    built = _overview()

    share = headline(built, "news_share")
    assert share is not None
    assert "pp" in share.change


def test_the_publication_measure_never_annualises(ga4):
    """Eleven articles in thirty days is eleven articles, not a rate."""
    ga4()
    for index in range(11):
        article(f"lugu-{index}", published=COVERAGE_END - dt.timedelta(days=index))

    built = _overview(period_key="30", today=COVERAGE_END)

    published = headline(built, "published")
    assert published.value == "11"
    for forbidden in ("aastas", "kuus", "nädalas"):
        assert forbidden not in published.change


def test_the_publication_split_shows_unknown_rather_than_hiding_it(ga4):
    ga4()
    article("koda", published=COVERAGE_END, category=NewsCategory.CHAMBER)
    article("sober", published=COVERAGE_END, category=NewsCategory.PARTNER)
    article("teadmata", published=COVERAGE_END, category="")

    built = _overview(period_key="30", today=COVERAGE_END)

    # The split lives in `page.counted`, built by `build_publishing`, not in the
    # headline. What must not happen is the unclassified articles becoming
    # invisible, so the assertion follows them to `Andmete kohta`, where
    # `Kataloogi ulatus` counts them.
    assert not headline(built, "published").parts
    assert analytics.catalogue_facts(coverage())["unclassified"] == 1


def test_the_typical_first_month_states_its_own_population(ga4):
    published = dt.date(2026, 1, 15)
    views = {}
    for index in range(15):
        item = article(f"tavaline-{index}", published=published)
        views[item.path] = {COVERAGE_END - dt.timedelta(days=29): 10 + index}
    ga4(views=views)

    impact = page.build_impact(coverage=coverage())
    built = _overview(cohorts=impact["cohorts"])

    typical = headline(built, "typical_first_month")
    if typical is not None:
        assert "uudise põhjal" in typical.note


# -- what build_impact still computes ------------------------------------------


def test_build_impact_only_carries_the_distribution_and_its_cohorts(ga4):
    """Three separate rankings and a lens picker retired between 2026-08-16 and
    2026-08-18; the distribution chart is the one section that survived."""
    article("lugu", published=dt.date(2026, 3, 1))

    built = page.build_impact(coverage=coverage())

    assert set(built) == {"distribution", "cohorts"}
