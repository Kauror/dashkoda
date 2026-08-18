"""The `/sundmused/` page, end to end through the real view.

The unit tests beside this file prove the arithmetic. These prove the two layers
nothing else touches — the view and the template — because a green selector
suite has shipped a dead page here before: a page can render every section and
load no chart JavaScript at all, and no value-inspecting test notices.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from .conftest import synthetic_programme

pytestmark = pytest.mark.django_db


@pytest.fixture
def programme(publish_programme):
    publish_programme(rows=synthetic_programme())


def _get(client, **params):
    return client.get(reverse("events"), params)


# ---------------------------------------------------------------------------
# One page, always
# ---------------------------------------------------------------------------


def test_the_page_renders(viewer_client, programme):
    response = _get(viewer_client)
    assert response.status_code == 200
    assert response.context["intelligence"].overview is not None


def test_a_stray_focus_parameter_from_an_old_bookmark_is_simply_unread(viewer_client, programme):
    """`Maht ja kalender`, `Formaadid ja teemad` and the register's own retired
    focus all folded into the one page on 2026-08-18. `fookus` is not parsed
    any more, so every value — current, retired, or invented — renders the
    same page."""
    for focus in ("ulevaade", "maht", "formaadid", "programm", "ei-ole-olemas"):
        response = _get(viewer_client, fookus=focus)
        assert response.status_code == 200
        assert response.context["intelligence"].overview is not None


def test_the_register_is_always_built(viewer_client, programme):
    """It was one click away, behind its own focus, until 2026-08-17, then
    became unconditional the day after."""
    assert _get(viewer_client).context["page"] is not None


def test_the_chart_bundle_loads_when_the_programme_has_dated_events(viewer_client, programme):
    """The specific failure this guards: a page that renders every section and
    ships no chart JavaScript, which no value-inspecting assertion can see."""
    html = _get(viewer_client).content.decode()
    assert "build/charts.js" in html


# ---------------------------------------------------------------------------
# Period control
# ---------------------------------------------------------------------------


def test_the_year_defaults_to_the_current_one_when_present(viewer_client, publish_programme):
    from django.utils import timezone

    from .workbook_factory import synthetic_row

    today = timezone.localdate()
    publish_programme(
        rows=[
            synthetic_row(
                event_id="E-1",
                service_code="1",
                start_date=dt.datetime.combine(today, dt.time()),
                source_row=2,
            ),
            synthetic_row(
                event_id="E-2",
                service_code="2",
                start_date=dt.datetime.combine(today.replace(year=today.year - 1), dt.time()),
                source_row=3,
            ),
        ]
    )
    assert _get(viewer_client).context["intelligence"].year == today.year


def test_all_years_is_an_explicit_choice(viewer_client, programme):
    page = _get(viewer_client, year="all").context["intelligence"]
    assert page.year is None
    assert page.period_label == "Kõik aastad"


def test_a_year_the_snapshot_lacks_falls_back(viewer_client, programme):
    page = _get(viewer_client, year="1999").context["intelligence"]
    assert page.year != 1999


def test_year_links_carry_no_focus_marker(viewer_client, programme):
    """`fookus` is not parsed any more, so no link needs to carry one."""
    page = _get(viewer_client).context["intelligence"]
    for link in page.year_links:
        assert "fookus" not in link.url


# ---------------------------------------------------------------------------
# Overview content
# ---------------------------------------------------------------------------


def test_the_overview_answers_the_headline_questions(viewer_client, programme):
    """Four cards since 2026-08-18, each titled and each stating its own
    scope — the two comparisons that used to render as a separate `Mis
    muutus?` list are folded into two of the four cards' own notes now."""
    overview = _get(viewer_client).context["intelligence"].overview

    assert len(overview.headline) >= 3
    labels = [card.label for card in overview.headline]
    assert any("Sündmusi programmis" in label for label in labels)
    assert any("Järgmise kuu jooksul" in label for label in labels)
    assert overview.types.has_data
    assert overview.delivery.has_data


def test_the_struck_headline_figures_stay_struck(viewer_client, programme):
    html = _get(viewer_client).content.decode()

    for struck in ("Mediaan planeerimisvaru", "Algab lähiajal"):
        assert struck not in html


def test_the_overview_carries_hinnastruktuur(viewer_client, programme):
    """The one section kept out of `Planeerimine` when that focus came off."""
    response = _get(viewer_client)
    overview = response.context["intelligence"].overview

    assert overview.price_chart is not None
    assert "Hinnastruktuur" in response.content.decode()


def test_the_overview_carries_the_volume_and_delivery_charts(viewer_client, programme):
    """`Maht ja kalender` and `Formaadid ja teemad`'s survivors, folded onto
    the one page on 2026-08-18."""
    response = _get(viewer_client)
    overview = response.context["intelligence"].overview
    html = response.content.decode()

    assert overview.year_chart is not None
    assert overview.month_chart is not None
    assert overview.delivery_over_time is not None
    assert "Maht aastate lõikes" in html
    assert "Toimumisviis aastate lõikes" in html


def test_the_theme_only_charts_and_duration_ranking_did_not_fold_in(viewer_client, programme):
    """Neither is in the mockup this round rebuilt the page to."""
    html = _get(viewer_client).content.decode()

    for retired in ("Teemade muutus", "Sündmuste kestus"):
        assert retired not in html


# ---------------------------------------------------------------------------
# Forbidden vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    [
        "Osalejaid",
        "Kohal käinud",
        "Täitumus",
        "Vabu kohti",
        "No-show",
        "Rahulolu",
    ],
)
def test_the_page_claims_nothing_no_source_establishes(viewer_client, programme, forbidden):
    """Attendance, capacity, fill rate and satisfaction have no source at all.

    They are not merely absent from the selectors — they may not appear as a
    word on the page, because a label is what a reader takes away.
    """
    html = _get(viewer_client).content.decode()
    assert forbidden not in html


def test_ordered_value_is_never_called_revenue(viewer_client, programme):
    html = _get(viewer_client).content.decode()
    for word in ("Käive", "Tulu ", "Laekunud"):
        assert word not in html


# ---------------------------------------------------------------------------
# Data quality block — on /haldus/ now
# ---------------------------------------------------------------------------


def test_provenance_is_not_on_the_page(viewer_client, programme):
    """It was on this page until 2026-08-15.

    The board moved it to `/haldus/`: it is pipeline diagnostics, and a manager
    opening this dashboard is not its reader. `tests/dashboard/test_admin_area.py`
    proves it arrived, which is the half that matters — a block deleted from one
    page and never rendered on the other would pass this assertion too.
    """
    html = _get(viewer_client).content.decode()

    assert "Andmete kohta" not in html
    assert "Mida need andmed ei tõesta" not in html


def test_the_quality_block_states_its_denominators(viewer_client, programme):
    quality = _get(viewer_client).context["intelligence"].quality
    assert quality.canonical_events == quality.dated_events + quality.undated_events
    assert quality.type_coverage <= quality.canonical_events
    assert quality.effective_links <= quality.canonical_events


def test_the_public_calendar_is_secondary_and_not_subtracted(viewer_client, programme):
    """The two sources count different things over different periods.

    The rule did not change on 2026-08-15; the page it is stated on did. The
    events dashboard no longer names the calendar at all, and `/haldus/` carries
    the whole statement — including that the gap between the two counts is not a
    count of unpublished events, which is the reading this exists to forbid.
    """
    assert "Koda.ee avalik kalender" not in _get(viewer_client).content.decode()

    admin = viewer_client.get(reverse("dashboard-admin")).content.decode()
    assert "Koda.ee avalik kalender" in admin
    assert "ei ole avaldamata sündmuste arv" in admin


def test_the_page_states_that_occurrences_are_not_sessions(viewer_client, programme):
    """Also moved with the provenance block, and still stated in full."""
    assert "korduvad lähteread" in viewer_client.get(reverse("dashboard-admin")).content.decode()


# ---------------------------------------------------------------------------
# Register, unconditional on the page since 2026-08-18
# ---------------------------------------------------------------------------


def test_the_register_still_searches_the_whole_population(viewer_client, programme):
    response = _get(viewer_client, year="all", q="konverents")
    assert response.status_code == 200
    assert response.context["page"].result_count >= 1


def test_the_register_exposes_type_and_delivery_filters(viewer_client, programme):
    response = _get(viewer_client, year="all", event_type="conference")
    page = response.context["page"]
    assert page.filters.event_type == "conference"
    assert page.refinement_count >= 1
    assert all(item.event_type_key == "conference" for item in page.items)


def test_delivery_filter_narrows_the_population(viewer_client, programme):
    page = _get(viewer_client, year="all", delivery_mode="hybrid").context["page"]
    assert page.filters.delivery_mode == "hybrid"
    assert all(item.delivery_mode == "hybrid" for item in page.items)


def test_register_links_carry_no_focus_marker(viewer_client, programme):
    """The register was the only content on the overview focus since
    2026-08-17, and `fookus` is not parsed at all since 2026-08-18 — so none
    of its own links need to carry one."""
    page = _get(viewer_client, year="all").context["page"]
    assert "fookus=" not in page.all_years_url
    for option in page.sort_options:
        assert "fookus=" not in option.url
    for link in page.quality:
        assert "fookus=" not in link.url


def test_the_registration_sort_is_not_offered_without_commerce_data(viewer_client, programme):
    """A control that cannot change the picture is worse than no control."""
    page = _get(viewer_client, year="all").context["page"]
    assert [option.label for option in page.sort_options] == ["Kuupäev", "Enim vaadatud"]
    assert page.shows_registrations is False
