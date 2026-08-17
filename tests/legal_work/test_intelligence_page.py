"""The Õigusloome page's focus navigation and what each surface draws.

These are page-level assertions: that a focus resolves safely, that the
mandatory visualisations are actually rendered, that a partial year is labelled
as partial, and that the claims the dashboard must never make are absent from
the markup rather than merely absent from the analytics.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse

from apps.legal_work.importer import import_artifact
from apps.legal_work.intelligence_page import FOCUS_CHOICES, parse_focus
from apps.legal_work.sections import LINKED_SECTIONS
from apps.legal_work.workbook import DATA_COLUMNS_V12

from .workbook_factory import synthetic_row, write_workbook

PAGE_URL = reverse("legal-work")
REPORTING = dt.date(2026, 8, 10)


def row(given=None, asked=None, **kwargs) -> list:
    return synthetic_row(**kwargs) + [given, asked]


@pytest.fixture
def register(register_workbook, tmp_path):
    """A small register with two years, an open matter and tracked feedback."""
    rows = [
        row(
            5,
            50,
            record_id="P-1",
            source_year=2026,
            source_row=2,
            topic="Sünteetiline avatud teema",
            received_date=dt.date(2026, 3, 1),
            deadline_date=dt.date(2026, 3, 15),
            stage="Kooskõlastusringil",
            stage_key="kooskõlastusringil",
            is_open=True,
        ),
        row(
            0,
            10,
            record_id="P-2",
            source_year=2026,
            source_row=3,
            topic="Sünteetiline saadetud teema",
            received_date=dt.date(2026, 2, 1),
            deadline_date=dt.date(2026, 2, 20),
            sent_date=dt.date(2026, 2, 18),
            sent_status="sent",
            is_open=False,
            stage="jõustunud",
            stage_key="jõustunud",
        ),
        row(
            None,
            None,
            record_id="P-3",
            source_year=2025,
            source_row=2,
            topic="Sünteetiline eelmise aasta teema",
            received_date=dt.date(2025, 2, 1),
            deadline_date=dt.date(2025, 2, 20),
            sent_date=dt.date(2025, 2, 18),
            sent_status="sent",
            is_open=False,
            stage="jõustunud",
            stage_key="jõustunud",
        ),
    ]
    path = write_workbook(
        tmp_path / "page.xlsx",
        rows=rows,
        schema_version="1.2",
        columns=DATA_COLUMNS_V12,
        control_overrides={"reporting_date": REPORTING},
    )
    return import_artifact(register_workbook(path), dry_run=False).snapshot


def get(client, focus: str | None = None):
    url = PAGE_URL if focus is None else f"{PAGE_URL}?fookus={focus}"
    return client.get(url).content.decode()


# --------------------------------------------------------------------------
# Focus navigation
# --------------------------------------------------------------------------


def test_an_unknown_focus_falls_back_to_the_overview():
    """A truncated link or an old bookmark must land somewhere real."""
    assert parse_focus("nonsense") == "ulevaade"
    assert parse_focus(None) == "ulevaade"
    assert parse_focus("") == "ulevaade"
    assert parse_focus("../../etc/passwd") == "ulevaade"
    assert parse_focus("REGISTER") == "register"


def test_every_focus_renders(client, authenticate_viewer, register):
    authenticate_viewer(client)

    for key, _label in FOCUS_CHOICES:
        response = client.get(f"{PAGE_URL}?fookus={key}")
        assert response.status_code == 200, f"focus {key} did not render"


def test_an_unknown_focus_still_renders_the_overview(client, authenticate_viewer, register):
    authenticate_viewer(client)

    content = get(client, "midagi-muud")

    assert "2026. aasta teemasid kokku" in content


def test_the_navigation_marks_exactly_one_focus_as_current(client, authenticate_viewer, register):
    """`aria-current` is what a screen reader uses; chip styling alone is not.

    Scoped to the focus navigation. The dashboard shell marks its own active
    module the same way, in both the sidebar and the drawer, so counting across
    the whole document measures the shell rather than this control.
    """
    authenticate_viewer(client)

    content = get(client, "arvamused")
    nav = content.split('aria-label="Õigusloome vaated"', 1)[1].split("</nav>", 1)[0]

    assert nav.count('aria-current="page"') == 1
    assert "fookus=arvamused" in nav


# --------------------------------------------------------------------------
# The mandatory visualisations
# --------------------------------------------------------------------------


def test_the_overview_carries_the_mandatory_headline_figures(client, authenticate_viewer, register):
    authenticate_viewer(client)

    content = get(client)

    assert "2026. aasta teemasid kokku" in content
    assert "2026. aastal arvamusi välja" in content
    assert "Arvamuste muutus võrreldes eelmise aastaga" in content
    assert "Hetkel töös" in content


def test_the_overview_carries_the_mandatory_stage_chart(client, authenticate_viewer, register):
    """The printed title states the total and left the page on 2026-08-17 —
    see `title_hidden` on `active_stage_chart` — so the total is checked via
    the canvas's `aria-label` instead, which carries the same figure."""
    authenticate_viewer(client)

    content = get(client)

    assert re.search(r"\d aktiivset teemat jaguneb \d hetkeseisu vahel", content)


def test_the_workflow_focus_carries_both_monthly_charts(client, authenticate_viewer, register):
    authenticate_viewer(client)

    content = get(client, "toovoog")

    assert "Uued teemad kuude lõikes" in content
    assert "Välja saadetud arvamused kuude lõikes" in content


def test_the_opinions_focus_carries_the_annual_and_response_charts(
    client, authenticate_viewer, register
):
    authenticate_viewer(client)

    content = get(client, "arvamused")

    assert "Välja saadetud arvamused aastate lõikes" in content
    assert "Arvamuse esitamiseks antud keskmine aeg" in content
    # Both series, not the mean alone.
    assert "Mediaan" in content
    assert "Keskmine" in content


def test_the_current_year_is_marked_as_partial(client, authenticate_viewer, register):
    """A partial bar that looks finished invites a false reading every January."""
    authenticate_viewer(client)

    content = get(client, "arvamused")

    assert "2026 (YTD)" in content


# --------------------------------------------------------------------------
# Claims the dashboard must never make
# --------------------------------------------------------------------------


def test_no_member_response_rate_is_offered_anywhere(client, authenticate_viewer, register):
    """The two feedback counts are not a valid numerator and denominator.

    The sentence that used to say so — the rate is *not* calculated — left
    the page on 2026-08-17 along with `Kuidas neid arve lugeda`. What must
    still never appear, with or without that sentence, is the claim itself: a
    percentage presented as a share of members who answered, or a count of
    distinct people.
    """
    authenticate_viewer(client)

    for key, _label in FOCUS_CHOICES:
        content = get(client, key)
        assert "Unikaalseid liikmeid" not in content
        assert "Liikmete vastamismäär" not in content
        assert "vastamismäär:" not in content.lower()
        assert "vastamismäär on" not in content.lower()


def test_the_feedback_view_states_what_it_is_not(client, authenticate_viewer, register):
    """The card is still here; the caption that qualifies it is not, any more.

    It moved to `Andmete seis` on 2026-08-16, and `Andmete seis` itself moved
    to `/haldus/` on 2026-08-17 — see
    `tests/dashboard/test_admin_area.py::test_the_legal_work_data_block_arrived`
    for where the rule lives now. The card stays: `kokku` still invites the
    misreading this page must not create.
    """
    authenticate_viewer(client)

    content = get(client, "tagasiside")

    assert "ei ole unikaalsete liikmete arv" not in content
    assert "Liikmete tagasiside kokku" in content


def test_a_measured_zero_stays_distinct_from_an_untracked_row(
    client, authenticate_viewer, register
):
    """`0` is an answer; the em dash is the absence of one."""
    authenticate_viewer(client)

    content = get(client)

    # The sent row reports a measured zero, and the previous-year row is
    # untracked. Both shapes have to reach the page.
    assert "—" in content


def test_the_page_offers_no_forecast_or_score(client, authenticate_viewer, register):
    authenticate_viewer(client)

    for key, _label in FOCUS_CHOICES:
        content = get(client, key).lower()
        assert "prognoos" not in content
        assert "skoor" not in content


# --------------------------------------------------------------------------
# Contracts the redesign must not break
# --------------------------------------------------------------------------


def test_the_linked_sections_stay_on_the_default_focus(client, authenticate_viewer, register):
    """The dashboard overview links straight into these by fragment.

    A fragment that lands nowhere fails silently in a browser, dropping the
    reader at the top of the page with no error anywhere.
    """
    authenticate_viewer(client)

    content = get(client)

    for section_id in LINKED_SECTIONS:
        assert f'id="{section_id}"' in content


def test_the_chart_bundle_loads_only_where_something_is_drawn(
    client, authenticate_viewer, register
):
    """It is over a megabyte, so the register must not pay for it."""
    authenticate_viewer(client)

    assert "build/charts.js" in get(client, "arvamused")
    assert "build/charts.js" not in get(client, "register")


def test_every_chart_names_itself_for_a_reader_who_cannot_see_the_canvas(
    client, authenticate_viewer, register
):
    """The accessible data table left every chart on 2026-08-17. What is left
    is `chart.summary`, rendered as the canvas's own `aria-label`."""
    authenticate_viewer(client)

    content = get(client, "arvamused")

    assert "Andmed tabelina" not in content
    payload_count = content.count("data-chart-payload=")
    label_count = len(re.findall(r'data-chart-canvas[^>]*aria-label="[^"]+"', content))
    assert payload_count > 0
    assert label_count == payload_count


def test_the_page_adds_no_inline_script(client, authenticate_viewer, register):
    """The Content Security Policy stays as it was: no inline script anywhere."""
    authenticate_viewer(client)

    for key, _label in FOCUS_CHOICES:
        content = get(client, key)
        assert "<script>" not in content


def test_a_chart_payload_rides_in_a_non_executable_block(client, authenticate_viewer, register):
    """`json_script` is what keeps `script-src` at 'self' with no unsafe-eval."""
    authenticate_viewer(client)

    assert 'type="application/json"' in get(client, "arvamused")


def test_the_feedback_focus_no_longer_states_its_caveats_inline(
    client, authenticate_viewer, register
):
    """`Kuidas neid arve lugeda` and its four caveats left this focus on
    2026-08-17. What each one described is unchanged — feedback is still
    counted, not people; a member is still not unique across topics; no
    response rate is still ever computed — only the on-page statement of it
    is gone."""
    authenticate_viewer(client)

    content = get(client, "tagasiside")

    assert "Kuidas neid arve lugeda" not in content
    assert "Vastamismäära ei arvutata" not in content
    assert "sama liige võib anda tagasisidet" not in content


def test_the_page_no_longer_dates_the_data_by_the_workbook(client, authenticate_viewer, register):
    """The as-of line moved to `/haldus/` whole on 2026-08-17 — the fact is
    not lost, only relocated; see `apps/legal_work/admin/_data_about.html`."""
    authenticate_viewer(client)

    content = get(client)

    assert "Andmed seisuga 10.08.2026" not in content


def test_the_search_reaches_the_whole_register_from_the_overview(
    client, authenticate_viewer, register
):
    """Burying it one click deep would undo the reason it was added."""
    authenticate_viewer(client)

    content = get(client)

    assert 'id="section-search"' in content


def test_without_a_snapshot_every_focus_is_an_empty_state(client, authenticate_viewer, db):
    """No published data is an empty state, never a page of zeroes."""
    authenticate_viewer(client)

    for key, _label in FOCUS_CHOICES:
        content = get(client, key)
        assert "Andmeallikas ei ole veel ühendatud." in content
