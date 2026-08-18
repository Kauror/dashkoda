"""The Koduleht page itself: its views, its state, and what each one refuses.

A green suite proves the parts work, not that anything reaches them. These tests
go through the **view** — `build_website_page` and the rendered HTML — because
the two layers a unit test never touches are exactly where this feature's
predecessor shipped a page search that returned the ordinary ranking.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.visibility.website_page import (
    FOCUS_CHANNELS,
    FOCUS_CONTENT,
    FOCUS_OVERVIEW,
    FOCUS_PAGES,
    FOCUS_TRAFFIC,
    build_website_page,
    duration_label,
    parse_focus,
)

from .conftest import PAGE_URL, PREV_START, START

pytestmark = pytest.mark.django_db


def body(response) -> str:
    return response.content.decode()


# ---------------------------------------------------------------------------
# Focus navigation
# ---------------------------------------------------------------------------


def test_the_default_view_is_the_overview():
    assert parse_focus(None).key == FOCUS_OVERVIEW
    assert parse_focus("").key == FOCUS_OVERVIEW


@pytest.mark.parametrize("key", [FOCUS_OVERVIEW, FOCUS_CONTENT, FOCUS_CHANNELS])
def test_every_focus_resolves_to_itself(key):
    assert parse_focus(key).key == key


def test_a_retired_focus_resolves_to_the_view_that_inherited_it():
    """`liiklus` and `lehed` retired on 2026-08-16, their bookmarks did not.

    Each lands on the view that actually holds its content — the overview took
    the traffic material and `Sisu ja lehed` took the page explorer. Falling
    through to the default would answer a saved traffic link correctly by
    accident and a saved explorer link wrongly on purpose.
    """
    assert parse_focus(FOCUS_TRAFFIC).key == FOCUS_OVERVIEW
    assert parse_focus(FOCUS_PAGES).key == FOCUS_CONTENT


def test_an_unknown_focus_falls_back_rather_than_erroring():
    """A rotted bookmark renders a page, never a 500."""
    assert parse_focus("kanalidx").key == FOCUS_OVERVIEW
    assert parse_focus("<script>").key == FOCUS_OVERVIEW


@pytest.mark.parametrize(
    "key", [FOCUS_OVERVIEW, FOCUS_TRAFFIC, FOCUS_CONTENT, FOCUS_CHANNELS, FOCUS_PAGES]
)
def test_every_focus_renders_through_the_view(history, viewer_client, key):
    response = viewer_client.get(PAGE_URL, {"fookus": key})

    assert response.status_code == 200
    assert "Koduleht" in body(response)


def test_an_unknown_focus_renders_the_overview_through_the_view(history, viewer_client):
    response = viewer_client.get(PAGE_URL, {"fookus": "ei-ole"})

    assert response.status_code == 200
    # Every key lands on the one page, which always draws its primary figures.
    assert "Peamised näitajad" in body(response)


def test_changing_focus_keeps_the_measurement_period(history, viewer_client):
    page = build_website_page(focus_key=FOCUS_CONTENT, period_key="90")

    assert page.period.key == "90"
    for option in page.focuses:
        assert "periood=90" in option.query


def test_the_focus_links_are_real_urls_the_page_answers(history, viewer_client):
    page = build_website_page(focus_key=FOCUS_OVERVIEW)

    for option in page.focuses:
        response = viewer_client.get(f"{PAGE_URL}?{option.query}")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Headline figures
# ---------------------------------------------------------------------------


def test_the_overview_answers_the_five_second_questions(history):
    page = build_website_page(focus_key=FOCUS_OVERVIEW, period_key="30")
    labels = [headline.label for headline in page.headlines]

    # Users first, the order Google Analytics' own dashboard uses.
    assert labels[0] == "Kasutajad"
    assert "Külastused" in labels
    assert "Lehevaatamised" in labels
    assert "Keskmine kaasatuse aeg / külastus" in labels


def test_the_headline_totals_are_the_period_sums(history):
    page = build_website_page(focus_key=FOCUS_OVERVIEW, period_key="30")

    assert page.summary.sessions == 30_000
    assert page.summary.page_views == 78_000
    assert page.summary.engagement_rate == pytest.approx(0.65)


def test_a_rate_moves_in_percentage_points_and_a_count_in_percent(history):
    """Two kinds of movement, spelled two ways, and never interchangeably.

    A count moves by a percentage of itself; a rate moves by percentage points,
    because the difference between two percentages is not a percentage. The
    engagement-time card is the strip's own rate and states its movement the
    same way — it was `Perioodi muutus` that carried the engagement *share*
    until that strip left on 2026-08-18.
    """
    page = build_website_page(focus_key=FOCUS_OVERVIEW, period_key="30")
    by_key = {headline.key: headline for headline in page.headlines}

    assert by_key["seansid"].change.endswith("%")

    mix = page.content_mix_table
    # The share movement of a content band, which is the page's remaining
    # percentage-point figure.
    assert any(row.values[2].endswith("pp") for row in mix.rows if row.values[2])


def test_no_headline_carries_a_delta_when_the_comparison_is_refused(ga4_day):
    """Twenty measured days against thirty is not a fall in traffic."""
    for offset in range(30):
        ga4_day(START + dt.timedelta(days=offset), sessions=1000, page_views=2000)
    for offset in range(30):
        if offset % 3 == 0:
            continue
        ga4_day(PREV_START + dt.timedelta(days=offset), sessions=1000, page_views=2000)

    page = build_website_page(focus_key=FOCUS_OVERVIEW, period_key="30")

    assert not any(headline.has_change for headline in page.headlines)
    assert all(headline.note for headline in page.headlines if headline.has_value)


def test_the_engagement_time_card_is_absent_when_nothing_measured_it(ga4_day):
    """The layout tolerates three primary figures; an empty box claiming a
    measurement exists does not."""
    for offset in range(30):
        ga4_day(
            START + dt.timedelta(days=offset),
            sessions=1000,
            page_views=2000,
            engaged_sessions=600,
        )
        ga4_day(
            PREV_START + dt.timedelta(days=offset),
            sessions=900,
            page_views=1800,
            engaged_sessions=500,
        )

    page = build_website_page(focus_key=FOCUS_OVERVIEW, period_key="30")

    assert len(page.headlines) == 3
    assert "Keskmine kaasatuse aeg / külastus" not in {h.label for h in page.headlines}


def test_the_peak_day_readout_says_it_is_not_a_period_total(history):
    """Still the point of that readout, and more so now.

    A period user count sits in the strip above it, so the day figure has to
    keep saying which of the two it is.
    """
    page = build_website_page(focus_key=FOCUS_OVERVIEW, period_key="30")
    peak = next(r for r in page.secondary if r.label == "Kõige aktiivsem päev")

    assert "ei ole päevade summa" in peak.note
    assert peak.value.endswith("kasutajat")


def test_new_users_are_collected_but_not_published_as_a_period_total(ga4_day):
    """Verified before published, not argued from the field's name."""
    for offset in range(30):
        ga4_day(START + dt.timedelta(days=offset), sessions=100, new_users=40)

    page = build_website_page(focus_key=FOCUS_TRAFFIC, period_key="30")
    labels = {h.label for h in page.headlines} | {r.label for r in page.secondary}

    assert "Uued kasutajad" not in labels


def test_engagement_time_reads_as_a_duration():
    assert duration_label(102) == "1 min 42 s"
    assert duration_label(48) == "48 s"
    assert duration_label(None) == ""


# ---------------------------------------------------------------------------
# Mis muutus? and Võimalused
# ---------------------------------------------------------------------------


def test_no_composite_score_is_invented(history, viewer_client):
    rendered = body(viewer_client.get(PAGE_URL))

    for forbidden in ("Health Score", "Digital Score", "Engagement Score", "/100"):
        assert forbidden not in rendered


def test_opportunities_carry_evidence_and_no_instruction(history):
    page = build_website_page(focus_key=FOCUS_OVERVIEW, period_key="30")

    assert page.opportunities
    for opportunity in page.opportunities:
        assert opportunity.evidence
        assert opportunity.subject


def test_a_growing_page_is_found_and_named_by_its_measurements(history):
    page = build_website_page(focus_key=FOCUS_CONTENT, period_key="30")
    rising = {row.label for row in page.rising_table.rows}

    assert any("kasvab" in label for label in rising)


def test_a_page_with_deep_engagement_and_little_traffic_is_surfaced(history):
    page = build_website_page(focus_key=FOCUS_CONTENT, period_key="30")
    kinds = {opportunity.kind for opportunity in page.opportunities}

    assert "vahem-leitud" in kinds or "palju-liiklust" in kinds


# ---------------------------------------------------------------------------
# Each focus view builds only its own analysis
# ---------------------------------------------------------------------------


def test_the_overview_does_not_run_the_movement_query_when_it_cannot_compare(ga4_day):
    for offset in range(30):
        ga4_day(START + dt.timedelta(days=offset), sessions=100, pages=(("/et/a", 5, 50),))

    page = build_website_page(focus_key=FOCUS_OVERVIEW, period_key="30")

    assert page.movement is None


def test_the_page_builds_every_section_once(history):
    """One page, one read, every section.

    The three tabbed views each built a subset, and the two tests that stood
    here pinned that isolation: the explorer built no channel analysis, the
    channel view built no content analysis. Both are meaningless now and their
    successor is the opposite claim — whichever key a bookmark carries, the
    reader gets the whole page.
    """
    for key in (FOCUS_OVERVIEW, FOCUS_CONTENT, FOCUS_CHANNELS, FOCUS_PAGES):
        page = build_website_page(focus_key=key, period_key="30")

        assert page.channels, f"{key} lost the channel analysis"
        assert page.channel_table.has_rows
        assert page.content_mix is not None, f"{key} lost the content analysis"
        assert page.language is not None
        assert page.top_pages_table.has_rows
        # Read by the tiles at the foot of the page rather than drawn.
        assert page.matrix is not None


# ---------------------------------------------------------------------------
# The page explorer
# ---------------------------------------------------------------------------


def test_search_runs_over_the_whole_population_not_the_ranking(ga4_day):
    """A page ranked far outside the ranking is exactly what somebody looks up,
    and searching the ranking would answer only for pages already visible.

    Seeded without the shared `history` fixture: one busy page that dominates
    every ranking, and one quiet page that no ranking would ever show.
    """
    for offset in range(30):
        ga4_day(
            START + dt.timedelta(days=offset),
            sessions=100,
            pages=(
                ("/et/uudised/vali", 500, 10000),
                ("/et/liikmed/liikmemaks", 2, 40),
            ),
        )

    page = build_website_page(focus_key=FOCUS_PAGES, period_key="30", search="liikmemaks")

    assert page.search.total == 1
    assert page.search.rows[0].path == "/et/liikmed/liikmemaks"
    # 60 views over the window — far below the busy page, and found anyway.
    assert page.search.rows[0].page_views == 60


def test_search_matches_a_pasted_koda_ee_url(history):
    page = build_website_page(
        focus_key=FOCUS_PAGES,
        period_key="30",
        search="https://www.koda.ee/et/uudised/kasvab?utm_source=uudiskiri",
    )

    assert page.search.total == 1
    assert page.search.rows[0].path == "/et/uudised/kasvab"


def test_search_reaches_the_view_and_does_not_return_the_ranking(history, viewer_client):
    """The defect this exists for: `?otsing=…` returning the ordinary ranking
    looks exactly like a search that matched everything.

    Scoped to the results region since the explorer moved onto `Sisu ja
    lehed`: the view above it legitimately lists falling pages, so the page as
    a whole may name `vaheneb` — the search results must not.
    """
    response = viewer_client.get(PAGE_URL, {"fookus": "lehed", "otsing": "kasvab"})
    rendered = body(response)

    assert response.status_code == 200
    results = rendered.split("koduleht-otsingutulemused", 1)[1]
    assert "kasvab" in results
    assert "vaheneb" not in results


def test_a_search_that_matches_nothing_says_so(history, viewer_client):
    response = viewer_client.get(PAGE_URL, {"fookus": "lehed", "otsing": "ei-ole-olemas"})

    assert "Ühtegi lehte ei leitud." in body(response)


def test_an_invalid_page_number_renders_rather_than_raising(history, viewer_client):
    response = viewer_client.get(PAGE_URL, {"fookus": "lehed", "otsing": "et", "lk": "kaheksa"})

    assert response.status_code == 200


def test_selecting_one_page_shows_both_of_its_figures(history):
    page = build_website_page(
        focus_key=FOCUS_PAGES, period_key="30", detail_path="/et/uudised/kasvab"
    )

    assert page.detail.page_views == 1800
    assert page.detail.previous_page_views == 300
    assert page.detail.measured_total == 2100
    assert page.detail_chart is not None


def test_an_unmeasured_address_is_reported_as_unmeasured(history, viewer_client):
    response = viewer_client.get(PAGE_URL, {"fookus": "lehed", "leht": "/et/ei-kunagi-moodetud"})

    assert "ei ole mõõtmisandmeid" in body(response)


def test_a_page_dashkoda_cannot_name_shows_its_path(history):
    """Nothing is invented from a slug: turning
    `/et/teenused/ekspordi-arendamine` into a title would put a sentence nobody
    wrote next to a number somebody measured."""
    page = build_website_page(
        focus_key=FOCUS_PAGES, period_key="30", detail_path="/et/teenused/kiire"
    )

    assert page.detail_title == "/et/teenused/kiire"


# ---------------------------------------------------------------------------
# Denominators and wording, through the rendered page
# ---------------------------------------------------------------------------


def test_the_content_mix_no_longer_states_its_denominator_inline(history, viewer_client):
    """The footnote and the chart's own question left the page on 2026-08-17,
    with the ranking's own question alongside them. `Enim vaadatud sisu` became
    `Vaadatud sisu jaotus` and the ranking `Enim vaadatud lehed` on 2026-08-18;
    neither carries a question line."""
    rendered = body(viewer_client.get(PAGE_URL, {"fookus": "sisu"}))

    assert "järjestatavaks sisuks" not in rendered
    assert "Millised kodulehe osad tähelepanu saavad" not in rendered
    assert "Mida perioodil kõige rohkem loeti" not in rendered


def test_the_language_split_disclaims_visitor_identity(history, viewer_client):
    rendered = body(viewer_client.get(PAGE_URL, {"fookus": "sisu"}))

    assert "mitte külastaja rahvust" in rendered


def test_the_declining_pages_table_left_the_page(history, viewer_client):
    """`Vähenenud tähelepanu`, the declining half of `Mis kasvas ja mis
    vähenes`, left the page on 2026-08-17 and its builder on 2026-08-18 —
    a falling ranking on a dashboard is read as a list of failures when it is
    mostly a list of pages whose campaign ended. Nothing
    renders it. `Kasvavad lehed` stayed and is worded neutrally regardless."""
    rendered = body(viewer_client.get(PAGE_URL, {"fookus": "sisu"}))

    assert "Vähenenud tähelepanu" not in rendered
    assert "Kasvavad lehed" in rendered
    for forbidden in ("Halvimad lehed", "halb", "ebaõnnestunud"):
        assert forbidden not in rendered


def test_the_channel_share_denominator_is_stated(history, viewer_client):
    """Still stated — on `/haldus/`, with the rest of the definitions.

    The footnote under the chart went with the 2026-08-16 declutter. What must
    not happen is the denominator going unstated anywhere, so this follows it
    rather than being deleted.
    """
    koduleht = body(viewer_client.get(PAGE_URL, {"fookus": "kanalid"}))
    assert "kogu kodulehe külastuste suhtes" not in koduleht

    admin = body(viewer_client.get("/haldus/"))
    assert "kanali külastused jagatud kogu kodulehe külastustega" in admin


def test_no_source_or_campaign_detail_is_invented(history, viewer_client):
    """Acquisition is stored at channel-group level only."""
    rendered = body(viewer_client.get(PAGE_URL, {"fookus": "kanalid"}))

    for forbidden in ("google / organic", "utm_", "Google organic", "Facebook referral"):
        assert forbidden not in rendered


def test_the_methodology_left_every_view(history, viewer_client):
    """`Andmete kohta` moved to `/haldus/` on 2026-08-16.

    Every focus view, because it used to render outside the focus branch and so
    appeared on all five. The header's jump link went with it — a `#section-andmed`
    left behind would be a link to nothing.
    """
    for focus in (FOCUS_OVERVIEW, FOCUS_TRAFFIC, FOCUS_CONTENT, FOCUS_CHANNELS, FOCUS_PAGES):
        rendered = body(viewer_client.get(PAGE_URL, {"fookus": focus}))
        assert "Andmete kohta" not in rendered, focus
        assert "section-andmed" not in rendered, focus


def test_the_arithmetic_rule_left_with_it(history, viewer_client):
    """The disclosure moved whole; it was not partly left behind.

    `tests/dashboard/test_admin_area.py` asserts the same sentence arrived.
    """
    rendered = body(viewer_client.get(PAGE_URL))

    assert "ei ole 780" not in rendered


# ---------------------------------------------------------------------------
# E-pood stays out
# ---------------------------------------------------------------------------


def test_no_commercial_analytics_reach_koduleht(history, viewer_client):
    for focus in (FOCUS_OVERVIEW, FOCUS_TRAFFIC, FOCUS_CONTENT, FOCUS_CHANNELS, FOCUS_PAGES):
        rendered = body(viewer_client.get(PAGE_URL, {"fookus": focus}))
        for forbidden in ("Tellimused", "Käive", "Tooted", "Tellimuste väärtus", "€"):
            assert forbidden not in rendered, f"{forbidden} on the {focus} view"


def test_shop_page_traffic_is_not_subtracted_from_site_totals(ga4_day):
    """A visitor who browsed a shop page still had a session on Koda.ee. The
    boundary is which dashboard studies commerce, not which traffic counts."""
    for offset in range(30):
        ga4_day(
            START + dt.timedelta(days=offset),
            sessions=100,
            page_views=250,
            pages=(("/et/pood/toode", 40, 800), ("/et/uudised/a", 10, 200)),
        )

    page = build_website_page(focus_key=FOCUS_TRAFFIC, period_key="30")

    assert page.summary.sessions == 3000
    assert page.summary.page_views == 7500


def test_an_ordinary_shop_page_may_still_be_looked_up(ga4_day):
    """It is still a web page. What it does not get is order or product data."""
    for offset in range(30):
        ga4_day(START + dt.timedelta(days=offset), pages=(("/et/pood/toode", 40, 800),))

    page = build_website_page(focus_key=FOCUS_PAGES, period_key="30", search="pood")

    assert page.search.total == 1
    assert not hasattr(page.search.rows[0], "orders")


def test_the_epood_dashboard_still_answers(history, viewer_client):
    response = viewer_client.get("/epood/")

    assert response.status_code == 200
