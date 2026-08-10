"""The content ranking on Nähtavus: what it counts, and what it refuses to.

Two independent controls — a period saying *when*, a section saying *what* —
that have to survive each other, plus the section-boundary rule that keeps one
part of the site's traffic out of another's report.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.visibility.content_performance import describe_pages
from apps.visibility.content_sections import (
    CONTENT_SECTIONS,
    DEFAULT_SECTION,
    SECTION_ALL,
    SECTION_EVENTS,
    SECTION_NEWS,
    SECTION_SERVICES,
    parse_section,
    section_of,
)
from apps.visibility.ga4_selectors import get_top_pages
from apps.visibility.models import Ga4DailySnapshot, Ga4PageDaily
from apps.visibility.traffic_page import build_traffic_section, parse_period

pytestmark = pytest.mark.django_db

START = dt.date(2026, 1, 1)


@pytest.fixture
def day():
    from apps.sources.services import build_import_run, register_external_reference
    from apps.visibility.bootstrap import ensure_ga4_source

    source = ensure_ga4_source()
    artifact = register_external_reference(
        source=source,
        external_reference="synthetic:content-ranking",
        original_name="synthetic.json",
        mime_type="application/json",
        sha256="e" * 64,
        size_bytes=10,
    )
    run = build_import_run(
        artifact=artifact,
        importer_name="synthetic_ranking_test",
        schema_version="2.0",
        dry_run=False,
    )
    counter = {"n": 0}

    def _day(report_date, *, pages=()):
        counter["n"] += 1
        snapshot = Ga4DailySnapshot.objects.create(
            source=source,
            artifact=artifact,
            import_run=run,
            report_date=report_date,
            observed_at=timezone.now(),
            checksum=f"{counter['n']:064d}",
            is_current_for_date=True,
            has_page_detail=True,
            sessions=1,
        )
        for path, views in pages:
            Ga4PageDaily.objects.create(
                snapshot=snapshot, report_date=report_date, path=path, page_views=views
            )
        return snapshot

    return _day


# -- section boundaries ---------------------------------------------------


def test_a_section_matches_whole_segments_only(day):
    """`/et/uudiseks` shares eight characters with `/et/uudised` and is another
    section. Filing one under the other moves real traffic into the wrong
    report, and nothing in the figures looks wrong afterwards."""
    day(
        START,
        pages=(
            ("/et/uudised", 10),
            ("/et/uudised/artikkel", 20),
            ("/et/uudiseks", 999),
        ),
    )

    ranked = get_top_pages(start=START, end=START, prefix=SECTION_NEWS.prefixes)

    assert {row.path for row in ranked} == {"/et/uudised", "/et/uudised/artikkel"}


def test_a_section_covers_its_translations(day):
    """A translated article is the same content; dropping `/en/news` would
    undercount it."""
    day(START, pages=(("/et/uudised/a", 10), ("/en/news/a", 5), ("/et/sundmused/x", 99)))

    ranked = get_top_pages(start=START, end=START, prefix=SECTION_NEWS.prefixes)

    assert {row.path for row in ranked} == {"/et/uudised/a", "/en/news/a"}


def test_everything_filters_nothing(day):
    day(START, pages=(("/et/uudised/a", 10), ("/et/liikmeks-astumine", 40)))

    ranked = get_top_pages(start=START, end=START, prefix=SECTION_ALL.prefixes)

    assert len(ranked) == 2


@pytest.mark.parametrize(
    ("path", "section"),
    [
        ("/et/uudised/artikkel", SECTION_NEWS),
        ("/en/news/article", SECTION_NEWS),
        ("/et/sundmused/foorum", SECTION_EVENTS),
        ("/et/teenused/eksport", SECTION_SERVICES),
        ("/et/uudiseks", None),
        ("/et/liikmeks-astumine", None),
    ],
)
def test_a_path_is_classified_by_its_section_root(path, section):
    assert section_of(path) is section


# -- the ranking itself ---------------------------------------------------


def test_the_ranking_orders_by_views_and_breaks_ties_stably(day):
    day(START, pages=(("/b", 50), ("/a", 50), ("/c", 90)))

    ranked = get_top_pages(start=START, end=START)

    assert [row.path for row in ranked] == ["/c", "/a", "/b"]


def test_the_ranking_counts_only_the_selected_period(day):
    """The number beside an item is its whole history; the number in the
    ranking is the period asked for. They are different questions."""
    day(START, pages=(("/et/uudised/a", 100),))
    day(START + dt.timedelta(days=40), pages=(("/et/uudised/a", 7),))

    recent = get_top_pages(start=START + dt.timedelta(days=30), end=START + dt.timedelta(days=40))

    assert [(row.path, row.page_views) for row in recent] == [("/et/uudised/a", 7)]


# -- query state -----------------------------------------------------------


def test_an_unknown_section_falls_back_rather_than_reaching_the_database():
    for raw in (None, "", "   ", "'; DROP TABLE", "/et/salajane"):
        assert parse_section(raw) is DEFAULT_SECTION


def test_changing_the_period_keeps_the_section(day):
    day(START, pages=(("/et/uudised/a", 10),))

    section = build_traffic_section(period_key="1a", section_key="uudised")

    assert section.section is SECTION_NEWS
    for option in section.options:
        assert "sisu=uudised" in option.query


def test_changing_the_section_keeps_the_period(day):
    day(START, pages=(("/et/uudised/a", 10),))

    section = build_traffic_section(period_key="1a", section_key="uudised")

    assert section.period is parse_period("1a")
    for option in section.section_options:
        assert "periood=1a" in option.query


def test_every_section_is_offered(day):
    day(START, pages=(("/et/uudised/a", 10),))

    section = build_traffic_section()

    assert [option.label for option in section.section_options] == [
        s.label for s in CONTENT_SECTIONS
    ]
    assert sum(option.is_active for option in section.section_options) == 1


# -- titles ----------------------------------------------------------------


def test_an_event_page_is_named_from_the_durable_catalogue(day):
    """`PublicEventResource` keeps a public page after its event has passed, so
    a 2023 path still resolves to a title."""
    from apps.events.public_models import PublicEventResource

    PublicEventResource.objects.create(
        canonical_url="https://www.koda.ee/et/sundmused/arifoorum",
        stable_key="arifoorum",
        title="Eesti–Islandi ärifoorum",
        starts_on=dt.date(2026, 8, 12),
        discovered_from="listing",
        content_checksum="f" * 64,
        last_seen_at=timezone.now(),
    )
    day(START, pages=(("/et/sundmused/arifoorum", 40),))

    row = describe_pages(get_top_pages(start=START, end=START), section=SECTION_ALL)[0]

    assert row.title == "Eesti–Islandi ärifoorum"
    assert row.type_label == "Sündmus"
    assert row.url == "https://www.koda.ee/et/sundmused/arifoorum"


def test_an_unknown_path_shows_its_path_rather_than_an_invented_title(day):
    """`/et/teenused/ekspordi-arendamine` must not become "Ekspordi
    arendamine" — a sentence nobody wrote, looking exactly as authoritative as
    one somebody did."""
    day(START, pages=(("/et/teenused/ekspordi-arendamine", 40),))

    row = describe_pages(get_top_pages(start=START, end=START), section=SECTION_ALL)[0]

    assert row.title == ""
    assert row.label == "/et/teenused/ekspordi-arendamine"
    assert row.has_known_identity is False


def test_the_type_badge_is_only_for_the_all_pages_view(day):
    """Inside `Uudised` every row is a news item; repeating the word on each
    line says nothing."""
    day(START, pages=(("/et/uudised/a", 40),))

    inside = describe_pages(get_top_pages(start=START, end=START), section=SECTION_NEWS)

    assert inside[0].type_label == ""
