"""Looking a page up, rather than reading a ranking.

Two modes answering different questions. The ranking answers "what was most
read"; search answers "I know which page I want — show me its analytics". The
whole point of the second is that it reaches pages the first cannot: a page
sitting at #347 is exactly the kind of page somebody searches for.

Every figure here comes from stored `Ga4PageDaily` rows. Nothing in this file
contacts Google, and neither does the page.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.visibility.content_sections import SECTION_EVENTS, SECTION_NEWS
from apps.visibility.ga4_selectors import search_pages
from apps.visibility.models import Ga4DailySnapshot, Ga4PageDaily
from apps.visibility.traffic_page import SEARCH_PER_PAGE, build_traffic_section

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 7, 1)
START = dt.date(2026, 6, 1)


@pytest.fixture
def day():
    """One published GA4 reporting day carrying the page rows it is given."""
    from apps.sources.services import build_import_run, register_external_reference
    from apps.visibility.bootstrap import ensure_ga4_source

    source = ensure_ga4_source()
    artifact = register_external_reference(
        source=source,
        external_reference="synthetic:page-search",
        original_name="synthetic.json",
        mime_type="application/json",
        sha256="d" * 64,
        size_bytes=10,
    )
    run = build_import_run(
        artifact=artifact,
        importer_name="synthetic_search_test",
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
            # The site-level figure GA4 reports for the day, which is what the
            # traffic series reads. Set from the page rows so a test can show
            # that excluding a path from a *ranking* leaves the site total alone.
            page_views=sum(views for _path, views in pages),
        )
        for path, views in pages:
            Ga4PageDaily.objects.create(
                snapshot=snapshot, report_date=report_date, path=path, page_views=views
            )
        return snapshot

    return _day


def news(url, title):
    from apps.news.public_models import NewsResource
    from apps.visibility.ga4_paths import canonical_path

    return NewsResource.objects.create(
        canonical_url=url,
        path=canonical_path(url),
        title=title,
        title_origin="feed",
        last_seen_at=timezone.now(),
    )


def event(url, title, *, starts_on=dt.date(2026, 5, 1)):
    from apps.events.public_models import PublicEventResource

    return PublicEventResource.objects.create(
        canonical_url=url,
        stable_key=url.rsplit("/", 1)[-1],
        title=title,
        starts_on=starts_on,
        discovered_from="listing",
        content_checksum="a" * 64,
        last_seen_at=timezone.now(),
    )


# -- the regression this exists for -----------------------------------------


def test_a_page_below_the_top_twenty_is_still_findable(day):
    """The key test.

    Twenty-five measured pages, and the one being looked for is quieter than
    every other. It is absent from the ranking by design and present in search,
    which is what proves search runs over the whole population rather than over
    the slice the ranking returned.
    """
    pages = [(f"/et/teenused/populaarne-{index:02d}", 500 + index) for index in range(24)]
    pages.append(("/et/teenused/vaikne-leht", 3))
    day(START, pages=pages)

    ranking = build_traffic_section(period_key="koik", today=TODAY).ranking
    assert len(ranking) == 20
    assert "/et/teenused/vaikne-leht" not in {row.path for row in ranking}

    found = build_traffic_section(period_key="koik", search="vaikne", today=TODAY)
    assert [row.path for row in found.results] == ["/et/teenused/vaikne-leht"]
    assert found.results[0].page_views == 3


# -- the ranking is cleaned --------------------------------------------------


def test_utility_paths_no_longer_occupy_ranking_positions(day):
    """The screenshot that started this: `/et`, `/en`, `/ru`, `/et/search/node`
    above the content they dwarf."""
    day(
        START,
        pages=(
            ("/et", 133_588),
            ("/en", 27_336),
            ("/ru", 11_817),
            ("/et/search/node", 10_633),
            ("/403.html", 5_359),
            ("/et/node/1173", 1_348),
            ("/et/pood", 900),
            ("/et/liikmed/liikmemaks", 120),
        ),
    )

    ranking = build_traffic_section(period_key="koik", today=TODAY).ranking

    assert [row.path for row in ranking] == ["/et/pood", "/et/liikmed/liikmemaks"]


def test_the_excluded_traffic_still_counts_towards_the_site_total(day):
    """They are dropped from a ranking of content, never from the website's
    own figures. `/et` is 133 588 real page views."""
    from apps.visibility.ga4_selectors import get_traffic_series

    day(START, pages=(("/et", 133_588), ("/et/pood", 900)))

    series = get_traffic_series(start=START, end=START)

    assert series.total_page_views == 134_488


# -- searching by path -------------------------------------------------------


@pytest.mark.parametrize(
    "term",
    [
        "liikmemaks",
        "LIIKMEMAKS",
        "  liikmemaks  ",
        "/et/liikmed/liikmemaks",
        "https://www.koda.ee/et/liikmed/liikmemaks",
        "et/liikmed",
    ],
)
def test_a_path_is_found_however_it_was_typed(day, term):
    day(START, pages=(("/et/liikmed/liikmemaks", 120), ("/et/pood", 900)))

    section = build_traffic_section(period_key="koik", search=term, today=TODAY)

    assert "/et/liikmed/liikmemaks" in {row.path for row in section.results}


def test_an_empty_search_is_the_ordinary_ranking(day):
    day(START, pages=(("/et/pood", 900),))

    for blank in ("", "   ", None):
        section = build_traffic_section(period_key="koik", search=blank, today=TODAY)
        assert not section.is_searching
        assert section.results == ()
        assert [row.path for row in section.ranking] == ["/et/pood"]


def test_a_very_long_or_odd_term_is_bounded_and_harmless(day):
    day(START, pages=(("/et/pood", 900),))

    for hostile in ("x" * 5000, "'; drop table--", "%%%", "../../etc/passwd", "üõäö"):
        section = build_traffic_section(period_key="koik", search=hostile, today=TODAY)
        assert len(section.search) <= 120
        assert section.result_count == 0


def test_a_percent_encoded_path_is_searchable(day):
    day(START, pages=(("/et/uudised/t%C3%B6%C3%B6turg", 40),))

    section = build_traffic_section(period_key="koik", search="t%C3%B6", today=TODAY)

    assert [row.path for row in section.results] == ["/et/uudised/t%C3%B6%C3%B6turg"]


def test_a_utility_path_is_not_a_search_result(day):
    """Searching "search" must not return the internal search page, and must
    still return the service whose name contains the word."""
    day(
        START,
        pages=(
            ("/et/search/node", 10_000),
            ("/en/services/search-cooperation-partner", 50),
        ),
    )

    section = build_traffic_section(period_key="koik", search="search", today=TODAY)
    paths = {row.path for row in section.results}

    assert "/et/search/node" not in paths
    assert "/en/services/search-cooperation-partner" in paths


# -- searching by trusted title ----------------------------------------------


def test_an_event_is_found_by_its_catalogued_title(day):
    """ "islandi" is in the title. The slug is what GA4 measured."""
    event("https://www.koda.ee/et/sundmused/arifoorum-2026", "Eesti–Islandi ärifoorum")
    day(START, pages=(("/et/sundmused/arifoorum-2026", 118),))

    section = build_traffic_section(period_key="koik", search="islandi", today=TODAY)

    assert [row.path for row in section.results] == ["/et/sundmused/arifoorum-2026"]
    assert section.results[0].title == "Eesti–Islandi ärifoorum"


def test_a_news_article_is_found_by_its_catalogued_title(day):
    news("https://www.koda.ee/et/uudised/ettepanek-2019", "Koja ettepanek maamaksu muutmiseks")
    day(START, pages=(("/et/uudised/ettepanek-2019", 379),))

    section = build_traffic_section(period_key="koik", search="maamaksu", today=TODAY)

    assert [row.path for row in section.results] == ["/et/uudised/ettepanek-2019"]


def test_an_article_long_out_of_the_feed_is_still_findable(day):
    """The durable catalogue is why. The ten-item feed forgot this years ago."""
    news("https://www.koda.ee/et/uudised/vana-lugu", "Ammune lugu tööturust")
    day(dt.date(2024, 3, 3), pages=(("/et/uudised/vana-lugu", 12),))

    section = build_traffic_section(period_key="koik", search="ammune", today=TODAY)

    assert [row.path for row in section.results] == ["/et/uudised/vana-lugu"]


def test_a_page_with_no_known_title_shows_its_path(day):
    """No slug-to-title invention. Services have no title catalogue, and the
    row says only what it honestly knows."""
    day(START, pages=(("/et/teenused/paritolusertifikaadid", 200),))

    section = build_traffic_section(period_key="koik", search="paritolu", today=TODAY)
    row = section.results[0]

    assert row.title == ""
    assert row.label == "/et/teenused/paritolusertifikaadid"


# -- section and period ------------------------------------------------------


def test_a_section_filter_narrows_the_search(day):
    news("https://www.koda.ee/et/uudised/islandi-kaubandus", "Islandi kaubandus kasvas")
    event("https://www.koda.ee/et/sundmused/islandi-foorum", "Eesti–Islandi ärifoorum")
    day(
        START,
        pages=(("/et/uudised/islandi-kaubandus", 50), ("/et/sundmused/islandi-foorum", 60)),
    )

    everything = build_traffic_section(period_key="koik", search="islandi", today=TODAY)
    assert len(everything.results) == 2

    only_news = build_traffic_section(
        period_key="koik", section_key=SECTION_NEWS.key, search="islandi", today=TODAY
    )
    assert [row.path for row in only_news.results] == ["/et/uudised/islandi-kaubandus"]

    only_events = build_traffic_section(
        period_key="koik", section_key=SECTION_EVENTS.key, search="islandi", today=TODAY
    )
    assert [row.path for row in only_events.results] == ["/et/sundmused/islandi-foorum"]


def test_the_two_figures_answer_different_questions(day):
    """100 views in all, 10 of them recently. Both are shown, both are true."""
    day(dt.date(2025, 1, 5), pages=(("/et/pood", 90),))
    day(TODAY - dt.timedelta(days=3), pages=(("/et/pood", 10),))

    recent = build_traffic_section(period_key="30", search="pood", today=TODAY)
    assert recent.results[0].page_views == 10
    assert recent.results[0].total_views == 100

    everything = build_traffic_section(period_key="koik", search="pood", today=TODAY)
    assert everything.results[0].page_views == 100
    assert everything.results[0].total_views == 100


def test_results_are_ordered_by_period_views(day):
    day(START, pages=(("/et/pood/a", 5), ("/et/pood/b", 90), ("/et/pood/c", 40)))

    section = build_traffic_section(period_key="koik", search="/et/pood", today=TODAY)

    assert [row.page_views for row in section.results] == [90, 40, 5]


# -- query state -------------------------------------------------------------


def test_every_control_carries_the_whole_state(day):
    day(START, pages=(("/et/pood", 900),))
    section = build_traffic_section(
        period_key="1a", section_key=SECTION_NEWS.key, search="maamaks", today=TODAY
    )

    for option in section.options:
        assert "sisu=uudised" in option.query
        assert "otsing=maamaks" in option.query
    for option in section.section_options:
        assert "periood=1a" in option.query
        assert "otsing=maamaks" in option.query


def test_clearing_a_search_keeps_the_period_and_the_section(day):
    day(START, pages=(("/et/pood", 900),))
    section = build_traffic_section(
        period_key="1a", section_key=SECTION_NEWS.key, search="maamaks", today=TODAY
    )

    assert section.clear_query == "periood=1a&sisu=uudised"
    assert "otsing" not in section.clear_query


def test_an_invalid_period_or_section_falls_back_without_losing_the_search(day):
    day(START, pages=(("/et/pood", 900),))
    section = build_traffic_section(
        period_key="ei-ole", section_key="ei-ole", search="pood", today=TODAY
    )

    assert section.period.key == "30"
    assert section.section.key == "koik"
    assert section.search == "pood"


# -- pagination --------------------------------------------------------------


def test_search_results_paginate_and_keep_their_state(day):
    day(
        START,
        pages=[
            (f"/et/pood/toode-{index:02d}", 100 - index) for index in range(SEARCH_PER_PAGE + 5)
        ],
    )

    first = build_traffic_section(period_key="koik", search="toode", today=TODAY)
    assert first.result_count == SEARCH_PER_PAGE + 5
    assert len(first.results) == SEARCH_PER_PAGE
    assert first.has_next and not first.has_previous
    assert "lk=2" in first.next_query
    assert "otsing=toode" in first.next_query

    second = build_traffic_section(period_key="koik", search="toode", page=2, today=TODAY)
    assert len(second.results) == 5
    assert second.has_previous and not second.has_next


def test_a_rotten_page_number_falls_back(day):
    day(START, pages=(("/et/pood", 900),))

    for bad in ("0", "-3", "banana", "9999", None):
        section = build_traffic_section(period_key="koik", search="pood", page=bad, today=TODAY)
        assert section.page_number >= 1


# -- the view actually carries the parameters --------------------------------


def test_the_rendered_page_searches_when_asked(viewer_client, day):
    """The whole feature was reachable in Python and unreachable in a browser.

    `build_traffic_section` took `search` and `page`, `build_visibility_page`
    passed them on, the template rendered both modes — and the view read
    neither, so `?otsing=…` rendered the ordinary Top 20 and looked like a
    search that had found everything. Every test above passed throughout,
    because every one of them called the builder directly.

    So this asserts through the view: the term must survive the request, and
    the response must be in the other mode.
    """
    day(START, pages=(("/et/pood", 900), ("/et/liikmed/liikmemaks", 5)))

    page = viewer_client.get(
        reverse("visibility"), {"periood": "koik", "otsing": "liikmemaks"}
    ).content.decode()

    assert "Otsingu tulemused" in page
    assert "/et/liikmed/liikmemaks" in page
    # The ranking's own heading is gone: this is not a Top 20 with a filter.
    assert "Enim vaadatud sisu valitud perioodil" not in page
    # And the box still holds what was typed, so the term is visible.
    assert 'value="liikmemaks"' in page


def test_the_rendered_page_carries_the_result_page_number(viewer_client, day):
    """`lk` reaches the traffic section, not only the campaign archive.

    Both pages paginate under `lk`. The overview view has to read it for the
    traffic section, and `views.py` imports the two modules' parameter names
    under aliases precisely so the archive's cannot be used here by accident.
    """
    day(START, pages=[(f"/et/pood/toode-{index:02d}", 100 - index) for index in range(30)])

    second = viewer_client.get(
        reverse("visibility"), {"periood": "koik", "otsing": "toode", "lk": "2"}
    ).content.decode()

    assert "Lehekülg 2 / 2" in second
    # Page two holds the tail of the ordering, not the head.
    assert "/et/pood/toode-29" in second
    assert "/et/pood/toode-00" not in second


def test_a_search_that_finds_nothing_still_offers_the_way_back(viewer_client, day):
    """The trap that hid the first bug behind a second one.

    Search empties the ranking deliberately, so the block guarding the whole
    content area on `traffic.ranking` removed the search box the moment a
    search ran — including the "Tühjenda otsing" link, leaving a reader who
    mistyped with no control to correct it and no explanation.
    """
    day(START, pages=(("/et/pood", 900),))

    page = viewer_client.get(
        reverse("visibility"), {"periood": "koik", "otsing": "ei-ole-olemas"}
    ).content.decode()

    assert "Ühtegi lehte ei leitud." in page
    assert 'name="otsing"' in page
    assert "Tühjenda otsing" in page


# -- the selector's own contract ---------------------------------------------


def test_the_selector_reports_the_total_match_count_not_the_slice(day):
    day(START, pages=[(f"/et/pood/toode-{index:02d}", 10) for index in range(30)])

    matches, total = search_pages(
        term="toode", start=dt.date(2020, 1, 1), end=TODAY, limit=5, offset=0
    )

    assert len(matches) == 5
    assert total == 30
