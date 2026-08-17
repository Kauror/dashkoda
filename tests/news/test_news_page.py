"""The focus navigation and the overview's four measures.

The overview is the screen somebody opens with a question, so these tests are
about whether it answers one — and about the two ways it could look like it did
when it had not: a placeholder standing in for a figure that does not exist, and
a control that silently governs the wrong question.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.news import analytics, page
from apps.news.categories import NewsCategory
from apps.news.focus import (
    FOCUS_OVERVIEW,
    FOCUSES,
    parse_focus,
)
from apps.news.measurement import resolve_reading
from tests.news.conftest import COVERAGE_END, COVERAGE_START, article

pytestmark = pytest.mark.django_db


def coverage():
    from apps.visibility.ga4_selectors import get_coverage

    return get_coverage()


# -- every view renders against nothing at all --------------------------------


@pytest.mark.parametrize("focus", [one.key for one in FOCUSES])
def test_every_focus_renders_with_no_data_whatsoever(viewer_client, focus):
    """A dashboard that 500s on an unconnected source is worse than no dashboard.

    The regression this exists for: `eligible_cohort` short-circuited to a bare
    `none()` when GA4 held nothing, so the correlated subquery went looking for
    window-bound columns that had never been annotated and `fookus=moju` raised
    `FieldError`. Every test seeded some GA4, so every test passed — only an
    empty database could find it, and this is that database.

    Driven through the real view rather than the builders, so a template that
    reaches for a figure the builder did not produce fails here too.
    """
    from django.urls import reverse

    response = viewer_client.get(reverse("news"), {"fookus": focus})

    assert response.status_code == 200
    assert "Uudised" in response.content.decode()


@pytest.mark.parametrize("focus", [one.key for one in FOCUSES])
def test_every_focus_renders_with_articles_but_no_analytics(viewer_client, focus):
    """The other half-connected state: a catalogue and no measurement.

    News collection and GA4 are separate sources with separate schedules, so
    "articles but nothing measured" is a state production really passes through
    — and the one where a first-window figure has nothing to be computed from.
    """
    from django.urls import reverse

    article("lugu", published=dt.date(2026, 3, 1))

    response = viewer_client.get(reverse("news"), {"fookus": focus})

    assert response.status_code == 200


# -- focus navigation ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, FOCUS_OVERVIEW),
        ("", FOCUS_OVERVIEW),
        ("ulevaade", FOCUS_OVERVIEW),
        # Retired 2026-08-16: the publishing material lives on the overview.
        ("avaldamine", FOCUS_OVERVIEW),
        # Retired 2026-08-17: `moju`'s one remaining chart and `arhiiv`'s
        # whole table both merged onto the same one view.
        ("moju", FOCUS_OVERVIEW),
        ("arhiiv", FOCUS_OVERVIEW),
        ("zzz", FOCUS_OVERVIEW),
        ("../../etc", FOCUS_OVERVIEW),
    ],
)
def test_an_unreadable_focus_resolves_to_the_overview(raw, expected):
    assert parse_focus(raw).key == expected


def test_the_default_focus_is_not_written_into_the_url():
    """`/uudised/` and `/uudised/?fookus=ulevaade` are one address, not two."""
    options = page.focus_options(parse_focus(None), state="periood=90")

    assert len(options) == 1
    assert options[0].focus.key == FOCUS_OVERVIEW
    assert options[0].query == "periood=90"


def test_every_focus_link_carries_the_pages_state():
    options = page.focus_options(parse_focus("moju"), state="periood=1a&kategooria=meie_uudised")

    for option in options:
        assert "periood=1a" in option.query
        assert "kategooria=meie_uudised" in option.query


# -- the measurement window is anchored to the data, not to today -------------


def test_the_measurement_window_ends_at_the_last_collected_day(ga4):
    """Today is never collected, so a window ending today ends in nothing."""
    ga4()

    reading = resolve_reading("30", coverage=coverage())

    assert reading.end == COVERAGE_END
    assert reading.start == COVERAGE_END - dt.timedelta(days=29)
    assert not reading.is_truncated


def test_a_window_longer_than_the_history_is_clipped_and_says_so(ga4):
    ga4()

    reading = resolve_reading("1a", coverage=coverage())

    assert reading.start == COVERAGE_START
    assert reading.is_truncated
    assert reading.days < 365


def test_an_unreadable_measurement_window_is_the_default(ga4):
    ga4()

    assert resolve_reading("zzz", coverage=coverage()).key == "30"


# -- the four measures --------------------------------------------------------


def headline(built, key):
    return next((one for one in built["headlines"] if one.key == key), None)


def test_the_overview_answers_the_four_questions(ga4):
    published = dt.date(2026, 3, 1)
    views = {}
    for index in range(12):
        item = article(f"lugu-{index}", published=published)
        views[item.path] = {published: 40 + index}
    ga4(views=views, site_views_per_day=1000)

    built = page.build_overview(
        reading=resolve_reading("30", coverage=coverage()),
        period=page.resolve_period("koik"),
        coverage=coverage(),
    )

    assert headline(built, "published").value == "12"
    # `Tüüpiline esimene kuu` left the strip on 2026-08-16 and the cohort behind
    # it is no longer walked for this view. Asserted as an absence rather than
    # by pinning the whole set: which of the readership measures exist depends
    # on what this fixture happens to seed, and that is a different test's
    # subject.
    assert headline(built, "typical_month") is None
    assert "typical_month" not in {h.key for h in built["headlines"]}


def test_a_measure_with_no_data_is_absent_rather_than_zero(ga4):
    """Three honest figures beat four where the fourth is invented.

    With no GA4 rows at all there is no readership figure, no share and no
    median — and a card reading `0` would look exactly like a measurement.
    """
    article("lugu", published=dt.date(2026, 3, 1))

    built = page.build_overview(
        reading=resolve_reading("30", coverage=coverage()),
        period=page.resolve_period("koik"),
        coverage=coverage(),
    )

    assert headline(built, "published") is not None
    assert headline(built, "news_views") is None
    assert headline(built, "news_share") is None


def test_the_share_change_is_in_percentage_points(ga4):
    """A share that moved from 20% to 10% did not fall by 10%."""
    current_day = COVERAGE_END
    previous_day = COVERAGE_END - dt.timedelta(days=30)
    item = article("lugu", published=dt.date(2026, 2, 1))
    ga4(views={item.path: {current_day: 100, previous_day: 200}}, site_views_per_day=1000)

    built = page.build_overview(
        reading=resolve_reading("30", coverage=coverage()),
        period=page.resolve_period("koik"),
        coverage=coverage(),
    )

    share = headline(built, "news_share")
    assert share is not None
    assert "pp" in share.change


def test_the_publication_measure_never_annualises(ga4):
    """Eleven articles in thirty days is eleven articles, not a rate."""
    ga4()
    for index in range(11):
        article(f"lugu-{index}", published=COVERAGE_END - dt.timedelta(days=index))

    built = page.build_overview(
        reading=resolve_reading("30", coverage=coverage()),
        period=page.resolve_period("30", today=COVERAGE_END),
        coverage=coverage(),
    )

    published = headline(built, "published")
    assert published.value == "11"
    for forbidden in ("aastas", "kuus", "nädalas"):
        assert forbidden not in published.detail


def test_the_publication_split_shows_unknown_rather_than_hiding_it(ga4):
    ga4()
    article("koda", published=COVERAGE_END, category=NewsCategory.CHAMBER)
    article("sober", published=COVERAGE_END, category=NewsCategory.PARTNER)
    article("teadmata", published=COVERAGE_END, category="")

    built = page.build_overview(
        reading=resolve_reading("30", coverage=coverage()),
        period=page.resolve_period("30", today=COVERAGE_END),
        coverage=coverage(),
    )

    # The split left the KPI caption on 2026-08-16. What must not happen is the
    # unclassified articles becoming invisible, so the assertion follows them to
    # `Andmete kohta`, where `Kataloogi ulatus` counts them.
    assert not headline(built, "published").parts
    assert analytics.catalogue_facts(coverage())["unclassified"] == 1


# -- the two time questions stay apart ----------------------------------------


def test_publication_and_measurement_windows_are_separate_controls(ga4):
    """The rule the whole page is built around.

    An article published long ago and read today is invisible to the publication
    window and top of the measurement one. If one control governed both, one of
    these two assertions would have to fail.
    """
    old = article("2026-jaanuar", published=dt.date(2026, 1, 5))
    ga4(views={old.path: {COVERAGE_END: 500}})

    reading = resolve_reading("30", coverage=coverage())
    recent_publication = page.resolve_period("30", today=COVERAGE_END)

    # Published months ago: absent from a thirty-day publication window.
    assert analytics.published_between(recent_publication.start, recent_publication.end).total == 0
    # Read this month: top of a thirty-day measurement window.
    ranked = list(analytics.most_read(start=reading.start, end=reading.end, limit=5))
    assert [row.path for row in ranked] == [old.path]


def test_signals_state_evidence_and_never_a_cause(ga4):
    """Evidence, never explanation — now asserted where the signals still are.

    `Tähelepanu` left the overview on 2026-08-16 and `build_overview` stopped
    composing it. `Alla tavapärase` on `Uudiste mõju` is the same kind of
    statement from the same cohorts, and it is the one that still reaches a
    reader, so the rule is asserted against it.
    """
    published = dt.date(2026, 2, 1)
    views = {}
    for index in range(12):
        item = article(f"tavaline-{index}", published=published)
        views[item.path] = {published: 100}
    weak = article("vaikne", published=published)
    views[weak.path] = {published: 1}
    ga4(views=views)

    built = page.build_impact(
        reading=resolve_reading("30", coverage=coverage()),
        coverage=coverage(),
        lens=page.parse_lens(None),
    )

    text = " ".join(signal.evidence + signal.label for signal in built["below_normal"])
    for forbidden in ("pealkiri", "peaks", "sest", "halb", "vale"):
        assert forbidden not in text.lower()


def test_the_overview_no_longer_pays_for_what_it_stopped_showing(ga4):
    """Three sections left the overview; three builders had to stop running.

    `changes`, `first_week` and `signals` each cost at least one query, and this
    module's rule is that a focus builds only what it renders. Absent keys, not
    empty ones — an empty tuple would mean the work ran and found nothing.
    """
    article("lugu", published=dt.date(2026, 3, 1))

    built = page.build_overview(
        reading=resolve_reading("30", coverage=coverage()),
        period=page.resolve_period("koik"),
        coverage=coverage(),
    )

    assert set(built) == {"headlines", "most_read"}


def test_the_impact_view_no_longer_pays_for_its_removed_sections(ga4):
    """Same rule, other focus: two sections went, two selectors stopped running."""
    article("lugu", published=dt.date(2026, 3, 1))

    built = page.build_impact(
        reading=resolve_reading("30", coverage=coverage()),
        coverage=coverage(),
        lens=page.parse_lens(None),
    )

    for absent in ("concentration", "categories", "lens_question"):
        assert absent not in built
