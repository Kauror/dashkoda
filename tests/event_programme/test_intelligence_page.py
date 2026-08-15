"""The `/sundmused/` focus views, end to end through the real view.

The unit tests beside this file prove the arithmetic. These prove the two layers
nothing else touches — the view and the template — because a green selector
suite has shipped a dead page here before: a page can render every section and
load no chart JavaScript at all, and no value-inspecting test notices.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from apps.event_programme.intelligence import (
    FOCUS_ATTENTION,
    FOCUS_FORMATS,
    FOCUS_OVERVIEW,
    FOCUS_PLANNING,
    FOCUS_REGISTER,
    FOCUS_VALUES,
    FOCUS_VOLUME,
    parse_focus,
)

from .conftest import synthetic_programme

pytestmark = pytest.mark.django_db


@pytest.fixture
def programme(publish_programme):
    publish_programme(rows=synthetic_programme())


def _get(client, **params):
    return client.get(reverse("events"), params)


# ---------------------------------------------------------------------------
# Focus routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("focus", FOCUS_VALUES)
def test_every_focus_renders(viewer_client, programme, focus):
    response = _get(viewer_client, fookus=focus)
    assert response.status_code == 200
    assert response.context["intelligence"].focus == focus


def test_an_unknown_focus_falls_back_to_the_overview(viewer_client, programme):
    response = _get(viewer_client, fookus="ei-ole-olemas")
    assert response.status_code == 200
    assert response.context["intelligence"].focus == FOCUS_OVERVIEW


def test_no_focus_is_the_overview(viewer_client, programme):
    assert _get(viewer_client).context["intelligence"].focus == FOCUS_OVERVIEW


def test_parse_focus_never_raises():
    for raw in (None, "", "  ", "programm", "ULEVAADE", 5):
        assert parse_focus(raw) in FOCUS_VALUES


def test_only_the_register_builds_a_programme_page(viewer_client, programme):
    """Six analyses on one route only works if five of them cost nothing."""
    assert _get(viewer_client, fookus=FOCUS_OVERVIEW).context["page"] is None
    assert _get(viewer_client, fookus=FOCUS_REGISTER).context["page"] is not None


@pytest.mark.parametrize(
    ("focus", "expects_bundle"),
    [
        (FOCUS_OVERVIEW, False),
        (FOCUS_VOLUME, True),
        (FOCUS_FORMATS, True),
        (FOCUS_ATTENTION, True),
        (FOCUS_PLANNING, True),
        (FOCUS_REGISTER, False),
    ],
)
def test_the_chart_bundle_loads_exactly_where_a_chart_is_drawn(
    viewer_client, programme, focus, expects_bundle
):
    """The specific failure this guards: a page that renders every section and
    ships no chart JavaScript, which no value-inspecting assertion can see."""
    html = _get(viewer_client, fookus=focus).content.decode()
    assert ("build/charts.js" in html) is expects_bundle


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


def test_focus_links_carry_the_period(viewer_client, programme):
    page = _get(viewer_client, year="all", fookus=FOCUS_VOLUME).context["intelligence"]
    for link in page.focus_links:
        assert "year=all" in link.url


def test_year_links_carry_the_focus(viewer_client, programme):
    page = _get(viewer_client, fookus=FOCUS_PLANNING).context["intelligence"]
    for link in page.year_links:
        assert f"fookus={FOCUS_PLANNING}" in link.url


# ---------------------------------------------------------------------------
# Overview content
# ---------------------------------------------------------------------------


def test_the_overview_answers_the_five_second_questions(viewer_client, programme):
    overview = _get(viewer_client, fookus=FOCUS_OVERVIEW).context["intelligence"].overview
    labels = [figure.label for figure in overview.headline]
    assert "Sündmusi programmis" in labels
    assert "Algab lähiajal" in labels
    assert overview.types.has_data
    assert overview.delivery.has_data


def test_the_headline_names_the_grain(viewer_client, programme):
    """A reader must not have to guess whether a count is events or sessions."""
    html = _get(viewer_client, fookus=FOCUS_OVERVIEW).content.decode()
    assert "mitte toimumiskord" in html


def test_an_upcoming_event_without_a_page_is_surfaced(viewer_client, publish_programme):
    """The actionable signal: something starts soon and nothing links to it.

    Published here rather than taken from the shared fixture, whose upcoming
    event does carry a link — the notice has to be provoked to be tested.
    """
    from django.utils import timezone

    from .workbook_factory import synthetic_row

    today = timezone.localdate()
    publish_programme(
        rows=[
            synthetic_row(
                event_id="E-1",
                service_code="1",
                event_name="Sünteetiline lingita tulev sündmus",
                start_date=dt.datetime.combine(today + dt.timedelta(days=6), dt.time()),
                event_status="upcoming",
                source_row=2,
            )
        ]
    )
    overview = _get(viewer_client, fookus=FOCUS_OVERVIEW).context["intelligence"].overview
    texts = " ".join(notice.text for notice in overview.notices)
    assert "avaliku koda.ee lehega" in texts


def test_a_linked_upcoming_event_raises_no_notice(viewer_client, publish_programme):
    from django.utils import timezone

    from .conftest import SYNTHETIC_URL
    from .workbook_factory import synthetic_row

    today = timezone.localdate()
    publish_programme(
        rows=[
            synthetic_row(
                event_id="E-1",
                service_code="1",
                start_date=dt.datetime.combine(today + dt.timedelta(days=6), dt.time()),
                event_status="upcoming",
                public_url=SYNTHETIC_URL,
                public_link_status="linked_embedded_latest",
                source_row=2,
            )
        ]
    )
    overview = _get(viewer_client, fookus=FOCUS_OVERVIEW).context["intelligence"].overview
    texts = " ".join(notice.text for notice in overview.notices)
    assert "avaliku koda.ee lehega" not in texts


def test_undated_events_are_disclosed_on_the_overview(viewer_client, programme):
    overview = _get(viewer_client, fookus=FOCUS_OVERVIEW).context["intelligence"].overview
    texts = " ".join(notice.text for notice in overview.notices)
    assert "kuupäeva ei õnnestunud" in texts


# ---------------------------------------------------------------------------
# Forbidden vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("focus", FOCUS_VALUES)
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
def test_no_focus_claims_something_no_source_establishes(
    viewer_client, programme, focus, forbidden
):
    """Attendance, capacity, fill rate and satisfaction have no source at all.

    They are not merely absent from the selectors — they may not appear as a
    word on the page, because a label is what a reader takes away.
    """
    html = _get(viewer_client, fookus=focus).content.decode()
    assert forbidden not in html


@pytest.mark.parametrize("focus", FOCUS_VALUES)
def test_ordered_value_is_never_called_revenue(viewer_client, programme, focus):
    html = _get(viewer_client, fookus=focus).content.decode()
    for word in ("Käive", "Tulu ", "Laekunud"):
        assert word not in html


# ---------------------------------------------------------------------------
# Data quality block
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("focus", FOCUS_VALUES)
def test_provenance_is_on_every_focus(viewer_client, programme, focus):
    html = _get(viewer_client, fookus=focus).content.decode()
    assert "Andmete kohta" in html


def test_the_quality_block_states_its_denominators(viewer_client, programme):
    quality = _get(viewer_client, fookus=FOCUS_OVERVIEW).context["intelligence"].quality
    assert quality.canonical_events == quality.dated_events + quality.undated_events
    assert quality.type_coverage <= quality.canonical_events
    assert quality.effective_links <= quality.canonical_events


def test_the_public_calendar_is_secondary_and_not_subtracted(viewer_client, programme):
    """The two sources count different things over different periods."""
    html = _get(viewer_client, fookus=FOCUS_OVERVIEW).content.decode()
    assert "Koda.ee avalik kalender" in html
    assert "ei ole avaldamata sündmuste arv" in html


def test_the_page_states_that_occurrences_are_not_sessions(viewer_client, programme):
    html = _get(viewer_client, fookus=FOCUS_OVERVIEW).content.decode()
    assert "korduvad lähteread" in html


# ---------------------------------------------------------------------------
# Register focus
# ---------------------------------------------------------------------------


def test_the_register_still_searches_the_whole_population(viewer_client, programme):
    response = _get(viewer_client, fookus=FOCUS_REGISTER, year="all", q="konverents")
    assert response.status_code == 200
    assert response.context["page"].result_count >= 1


def test_the_register_exposes_type_and_delivery_filters(viewer_client, programme):
    response = _get(viewer_client, fookus=FOCUS_REGISTER, year="all", event_type="conference")
    page = response.context["page"]
    assert page.filters.event_type == "conference"
    assert page.refinement_count >= 1
    assert all(item.event_type_key == "conference" for item in page.items)


def test_delivery_filter_narrows_the_population(viewer_client, programme):
    page = _get(viewer_client, fookus=FOCUS_REGISTER, year="all", delivery_mode="hybrid").context[
        "page"
    ]
    assert page.filters.delivery_mode == "hybrid"
    assert all(item.delivery_mode == "hybrid" for item in page.items)


def test_register_links_keep_the_reader_on_the_register(viewer_client, programme):
    page = _get(viewer_client, fookus=FOCUS_REGISTER, year="all").context["page"]
    assert f"fookus={FOCUS_REGISTER}" in page.all_years_url
    for option in page.sort_options:
        assert f"fookus={FOCUS_REGISTER}" in option.url
    for link in page.quality:
        assert f"fookus={FOCUS_REGISTER}" in link.url


def test_the_registration_sort_is_not_offered_without_commerce_data(viewer_client, programme):
    """A control that cannot change the picture is worse than no control."""
    page = _get(viewer_client, fookus=FOCUS_REGISTER, year="all").context["page"]
    assert [option.label for option in page.sort_options] == ["Kuupäev", "Enim vaadatud"]
    assert page.shows_registrations is False
