"""The register explorer: filters, applied state, and record drill-down.

The filters narrow a register a lawyer is trying to find one matter in, so the
tests here are mostly about what must *not* happen: a filter that silently does
not apply, a filter that applies invisibly, a deadline band that moves with the
wall clock, and the three member-feedback states collapsing into two.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from apps.legal_work import register as reg
from apps.legal_work.importer import import_artifact
from apps.legal_work.workbook import DATA_COLUMNS_V12

from .workbook_factory import synthetic_row, write_workbook

PAGE_URL = reverse("legal-work")
REPORTING = dt.date(2026, 8, 10)


def row(given=None, asked=None, **kwargs) -> list:
    return synthetic_row(**kwargs) + [given, asked]


@pytest.fixture
def register(register_workbook, tmp_path):
    """A register varied enough that every filter has something to select."""
    rows = [
        # Open, stage A, ministry A, deadline five days out, feedback measured.
        row(
            4,
            40,
            record_id="R-1",
            source_year=2026,
            source_row=2,
            topic="Sünteetiline maksuteema",
            act_type="seadus",
            recipient="Ministeerium A",
            stage="Kooskõlastusringil",
            stage_key="kooskõlastusringil",
            received_date=dt.date(2026, 3, 1),
            deadline_date=REPORTING + dt.timedelta(days=5),
            is_open=True,
        ),
        # Open, stage B, ministry B, deadline passed, opinion still pending.
        row(
            0,
            10,
            record_id="R-2",
            source_year=2026,
            source_row=3,
            topic="Sünteetiline keskkonnateema",
            act_type="määrus",
            recipient="Ministeerium B",
            stage="Riigikogus",
            stage_key="riigikogus",
            received_date=dt.date(2026, 2, 1),
            deadline_date=REPORTING - dt.timedelta(days=5),
            is_open=True,
        ),
        # Open, deadline passed, but the opinion already went out: not overdue.
        row(
            None,
            None,
            record_id="R-3",
            source_year=2026,
            source_row=4,
            topic="Sünteetiline saadetud kuid avatud teema",
            act_type="seadus",
            recipient="Ministeerium A",
            stage="Ootan jõustumist",
            stage_key="ootan jõustumist",
            received_date=dt.date(2026, 1, 5),
            deadline_date=REPORTING - dt.timedelta(days=8),
            sent_date=REPORTING - dt.timedelta(days=12),
            sent_status="sent",
            is_open=True,
        ),
        # Closed, sent, previous year, no deadline at all.
        row(
            7,
            70,
            record_id="R-4",
            source_year=2025,
            source_row=2,
            topic="Sünteetiline eelmise aasta teema",
            act_type="direktiiv",
            recipient="Ministeerium B",
            stage="jõustunud",
            stage_key="jõustunud",
            received_date=dt.date(2025, 2, 1),
            deadline_date=None,
            sent_date=dt.date(2025, 3, 1),
            sent_status="sent",
            is_open=False,
        ),
    ]
    path = write_workbook(
        tmp_path / "register.xlsx",
        rows=rows,
        schema_version="1.2",
        columns=DATA_COLUMNS_V12,
        control_overrides={"reporting_date": REPORTING},
    )
    return import_artifact(register_workbook(path), dry_run=False).snapshot


def view(snapshot, **params):
    return reg.build_register(snapshot, params)


def ids(result) -> set[str]:
    return {record.item.record_id for record in result.records}


# --------------------------------------------------------------------------
# Facets come from the register itself
# --------------------------------------------------------------------------


def test_the_facets_are_read_from_the_snapshot(register):
    """Hard-coded menus would hide a stage that appeared this month."""
    facets = reg.build_facets(register)

    assert facets.years == (2026, 2025)
    assert "kooskõlastusringil" in facets.stage_keys
    assert "Ministeerium A" in facets.recipients
    assert "seadus" in facets.act_types


def test_a_stage_the_register_does_not_have_simply_does_not_apply(register):
    """An old bookmark narrows to nothing rather than returning nothing."""
    result = view(register, etapp="etapp-mida-ei-ole")

    assert result.state.stage_key == ""
    assert len(result.records) == 4


# --------------------------------------------------------------------------
# The filters
# --------------------------------------------------------------------------


def test_each_facet_narrows_the_register(register):
    assert ids(view(register, aasta="2025")) == {"R-4"}
    assert ids(view(register, etapp="riigikogus")) == {"R-2"}
    assert ids(view(register, saaja="Ministeerium A")) == {"R-1", "R-3"}
    assert ids(view(register, liik="seadus")) == {"R-1", "R-3"}


def test_filters_compose_rather_than_replace_each_other(register):
    result = view(register, saaja="Ministeerium A", liik="seadus", aasta="2026")

    assert ids(result) == {"R-1", "R-3"}
    assert result.state.active_filter_count == 3


def test_the_search_term_still_reaches_the_whole_register(register):
    assert ids(view(register, otsing="keskkonna")) == {"R-2"}
    assert ids(view(register, otsing="Ministeerium A")) == {"R-1", "R-3"}


# --------------------------------------------------------------------------
# Deadline states
# --------------------------------------------------------------------------


def test_overdue_means_passed_and_still_unanswered(register):
    """A matter whose opinion already went out is not late, however old its
    deadline. Sweeping it up would manufacture a backlog out of ordinary
    process: a topic legitimately stays open after Koda has answered."""
    result = view(register, tahtaeg="moodas")

    assert ids(result) == {"R-2"}
    assert "R-3" not in ids(result), "the already-sent matter is not overdue"


def test_a_deadline_band_is_measured_from_the_reporting_date(register):
    """Not from today: the same data must land in the same band on any day."""
    assert ids(view(register, tahtaeg="7")) == {"R-1"}
    assert ids(view(register, tahtaeg="21")) == {"R-1"}


def test_records_without_a_deadline_are_their_own_state(register):
    assert ids(view(register, tahtaeg="puudub")) == {"R-4"}


# --------------------------------------------------------------------------
# Feedback states -- three, not two
# --------------------------------------------------------------------------


def test_the_three_feedback_states_are_three_distinct_populations(register):
    """Measured-at-zero and never-measured are different facts about a matter,
    and collapsing them is the same error as writing 0 into an empty cell."""
    present = ids(view(register, tagasiside="on"))
    zero = ids(view(register, tagasiside="null"))
    untracked = ids(view(register, tagasiside="puudub"))

    assert present == {"R-1", "R-4"}
    assert zero == {"R-2"}
    assert untracked == {"R-3"}
    assert not (present & zero) and not (zero & untracked) and not (present & untracked)
    assert present | zero | untracked == {"R-1", "R-2", "R-3", "R-4"}


# --------------------------------------------------------------------------
# Applied state must never be invisible
# --------------------------------------------------------------------------


def test_every_active_filter_is_reported(register):
    """A narrowed register that looks unnarrowed makes the reader conclude the
    Chamber has one record."""
    result = view(register, saaja="Ministeerium A", tahtaeg="7", tagasiside="on")

    assert result.state.active_filter_count == 3
    assert result.state.has_filters is True
    assert {applied.label for applied in result.applied} == {"Saaja", "Tähtaeg", "Tagasiside"}


def test_removing_one_filter_keeps_the_others(register):
    result = view(register, saaja="Ministeerium A", liik="seadus")
    by_label = {applied.label: applied for applied in result.applied}

    remove_recipient = by_label["Saaja"].remove_query

    assert "saaja=" not in remove_recipient
    assert "liik=seadus" in remove_recipient


def test_narrowing_resets_to_the_first_page(register):
    """Page three of a wider question is usually empty once it is narrowed."""
    result = view(register, lk="3")

    assert "lk=" not in reg.build_query(result.state, recipient="Ministeerium A")
    assert "lk=2" in reg.build_query(result.state, page=2)


# --------------------------------------------------------------------------
# Record drill-down
# --------------------------------------------------------------------------


def test_the_timeline_carries_only_the_steps_that_happened(register):
    """A missing date is left out, never drawn as an empty slot."""
    records = {record.item.record_id: record for record in view(register).records}

    # Received and a deadline, but never sent.
    assert [label for label, _day in records["R-1"].timeline] == ["Sisse", "Tähtaeg"]
    # All three.
    assert [label for label, _day in records["R-3"].timeline] == ["Sisse", "Tähtaeg", "Välja"]
    # No deadline at all.
    assert [label for label, _day in records["R-4"].timeline] == ["Sisse", "Välja"]


def test_a_record_with_no_trustworthy_match_carries_no_link(register):
    """Stale or absent matching is plain text, never a guess."""
    for record in view(register).records:
        if not record.has_resource:
            assert record.resource_url == ""
            assert record.resource_is_opinion is False


def test_the_drill_down_exposes_no_internal_field(register, client, authenticate_viewer):
    """`source_row`, snapshot ids and matcher internals belong to the admin."""
    authenticate_viewer(client)

    content = client.get(f"{PAGE_URL}?fookus=register").content.decode()

    assert "source_row" not in content
    assert "stage_key" not in content
    assert "matcher" not in content.lower()


# --------------------------------------------------------------------------
# The rendered surface
# --------------------------------------------------------------------------


def test_the_register_focus_renders_its_filters_and_rows(register, client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get(f"{PAGE_URL}?fookus=register").content.decode()

    assert 'id="section-register"' in content
    assert "Täpsem valik" in content
    assert "Sünteetiline maksuteema" in content
    # The three feedback states are offered by name.
    assert "Mõõdetud, tagasisidet ei antud" in content
    assert "Tagasisidet ei ole mõõdetud" in content


def test_the_applied_filter_count_is_visible_on_the_page(register, client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get(
        f"{PAGE_URL}?fookus=register&saaja=Ministeerium+A&liik=seadus"
    ).content.decode()

    assert "2 aktiivset filtrit" in content


def test_the_live_fragment_keeps_the_registers_filters(register, client, authenticate_viewer):
    """Typing into a box with six filters beside it must not widen the answer."""
    authenticate_viewer(client)

    response = client.get(
        reverse("legal-work-search"),
        {"fookus": "register", "saaja": "Ministeerium A", "otsing": "maksu"},
        headers={"hx-request": "true"},
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "Sünteetiline maksuteema" in content
    assert "Sünteetiline keskkonnateema" not in content, "the recipient filter still applies"


def test_the_register_works_without_javascript(register, client, authenticate_viewer):
    """Every control is a plain GET; htmx is an enhancement, not the mechanism."""
    authenticate_viewer(client)

    content = client.get(f"{PAGE_URL}?fookus=register").content.decode()

    assert 'method="get"' in content
    assert 'type="submit"' in content
