"""The event-programme workbook contract.

These tests need no database: the parser knows nothing about Django models.
Each one states a way a workbook can be wrong and asserts that it is rejected
rather than repaired, because a defective export is the generator's problem and
silently importing part of one would publish numbers nobody verified.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.event_programme.workbook import (
    EVENTS_COLUMNS,
    EVENTS_SHEET,
    OCCURRENCES_SHEET,
    STORED_COLUMNS,
    WorkbookContractError,
    parse_workbook,
)

from .workbook_factory import build_workbook, default_control, default_rows, synthetic_row


def test_parses_a_well_formed_workbook(tmp_path):
    parsed = parse_workbook(build_workbook(tmp_path / "ok.xlsx"))

    assert len(parsed.rows) == 3
    assert parsed.control.dataset_key == "events"
    assert parsed.control.schema_version == "1.0"
    # CONTROL counts arrive as text and come back as integers.
    assert parsed.control.canonical_event_count == 3
    assert isinstance(parsed.control.canonical_event_count, int)


def test_reads_the_header_below_the_banner(tmp_path):
    """The table starts on row 3, not row 1."""
    parsed = parse_workbook(build_workbook(tmp_path / "banner.xlsx"))

    assert [row.event_id for row in parsed.rows] == ["EVENT-9001", "EVENT-9002", "EVENT-9003"]


def test_undated_event_keeps_no_invented_calendar_fields(tmp_path):
    parsed = parse_workbook(build_workbook(tmp_path / "undated.xlsx"))

    undated = next(row for row in parsed.rows if row.event_id == "EVENT-9003")
    assert undated.start_date is None
    assert undated.end_date is None
    assert undated.event_year is None
    assert undated.event_month_key == ""
    assert undated.date_parse_status == "unparsed"
    # It is still a real event, and it still counts.
    assert len(parsed.rows) == 3
    assert parsed.dated_event_count == 2


def test_counts_derive_from_rows_not_from_control(tmp_path):
    parsed = parse_workbook(build_workbook(tmp_path / "counts.xlsx"))

    assert parsed.linked_public_url_count == 1
    assert parsed.review_required_count == 1
    assert parsed.dated_event_count == 2


def test_warning_codes_become_a_list(tmp_path):
    rows = default_rows()
    rows[0]["warning_codes"] = "price_unparsed; date_shifted"
    parsed = parse_workbook(build_workbook(tmp_path / "warnings.xlsx", rows=rows))

    first = next(row for row in parsed.rows if row.event_id == "EVENT-9001")
    assert first.warning_codes == ["price_unparsed", "date_shifted"]


def test_no_price_column_survives_parsing():
    """Pricing is verified in the header and then discarded.

    A field that does not exist cannot reach a model, a template or an export.
    """
    assert "member_price_eur" in EVENTS_COLUMNS
    assert not any("price" in name for name in STORED_COLUMNS)
    assert not any("discount" in name for name in STORED_COLUMNS)
    assert not any(name.endswith("_raw") for name in STORED_COLUMNS)


@pytest.mark.parametrize("missing_sheet", [EVENTS_SHEET, OCCURRENCES_SHEET])
def test_missing_sheet_is_rejected(tmp_path, missing_sheet):
    """Even a sheet that is never read as data must be present.

    Its absence means the file is a truncated export rather than a complete one.
    """
    path = build_workbook(tmp_path / "missing.xlsx", omit_sheets=(missing_sheet,))

    with pytest.raises(WorkbookContractError, match="puuduvad lehed"):
        parse_workbook(path)


def test_reordered_columns_are_rejected(tmp_path):
    swapped = (EVENTS_COLUMNS[1], EVENTS_COLUMNS[0]) + EVENTS_COLUMNS[2:]
    path = build_workbook(tmp_path / "reordered.xlsx", events_columns=swapped)

    with pytest.raises(WorkbookContractError, match="veerud ei vasta"):
        parse_workbook(path)


def test_wrong_dataset_key_is_rejected(tmp_path):
    control = default_control(default_rows())
    control["dataset_key"] = "oigusloome"
    path = build_workbook(tmp_path / "dataset.xlsx", control=control)

    with pytest.raises(WorkbookContractError, match="Vale andmestik"):
        parse_workbook(path)


def test_unsupported_schema_version_is_rejected(tmp_path):
    control = default_control(default_rows())
    control["schema_version"] = "2.0"
    path = build_workbook(tmp_path / "schema.xlsx", control=control)

    with pytest.raises(WorkbookContractError, match="Toetamata skeemi versioon"):
        parse_workbook(path)


def test_blocked_refresh_status_is_rejected(tmp_path):
    control = default_control(default_rows())
    control["refresh_status"] = "blocked"
    path = build_workbook(tmp_path / "blocked.xlsx", control=control)

    with pytest.raises(WorkbookContractError, match="ei ole imporditav"):
        parse_workbook(path)


def test_blocking_errors_are_rejected(tmp_path):
    """The generator's own gate. A file that admits to a blocking error is not
    repaired here, and it is not imported."""
    control = default_control(default_rows())
    control["blocking_error_count"] = "3"
    path = build_workbook(tmp_path / "blocking.xlsx", control=control)

    with pytest.raises(WorkbookContractError, match="blokeerivast veast"):
        parse_workbook(path)


def test_control_disagreeing_with_the_table_is_rejected(tmp_path):
    control = default_control(default_rows())
    control["canonical_event_count"] = "99"
    path = build_workbook(tmp_path / "disagree.xlsx", control=control)

    with pytest.raises(WorkbookContractError, match="lubab 99 sündmust"):
        parse_workbook(path)


def test_control_link_count_disagreeing_is_rejected(tmp_path):
    control = default_control(default_rows())
    control["linked_public_url_count"] = "7"
    path = build_workbook(tmp_path / "links.xlsx", control=control)

    with pytest.raises(WorkbookContractError, match="avalikku linki"):
        parse_workbook(path)


def test_non_numeric_control_count_is_rejected(tmp_path):
    control = default_control(default_rows())
    control["canonical_event_count"] = "3.0"
    path = build_workbook(tmp_path / "float.xlsx", control=control)

    with pytest.raises(WorkbookContractError, match="peab olema täisarv"):
        parse_workbook(path)


def test_duplicate_service_code_is_rejected(tmp_path):
    rows = [
        synthetic_row(event_id="EVENT-1", service_code="1", source_row=2),
        synthetic_row(event_id="EVENT-2", service_code="1", source_row=3),
    ]
    path = build_workbook(tmp_path / "dupe.xlsx", rows=rows)

    with pytest.raises(WorkbookContractError, match="kordub"):
        parse_workbook(path)


def test_duplicate_event_id_is_rejected(tmp_path):
    rows = [
        synthetic_row(event_id="EVENT-1", service_code="1", source_row=2),
        synthetic_row(event_id="EVENT-1", service_code="2", source_row=3),
    ]
    path = build_workbook(tmp_path / "dupe-id.xlsx", rows=rows)

    with pytest.raises(WorkbookContractError, match="'event_id'"):
        parse_workbook(path)


def test_end_before_start_is_rejected(tmp_path):
    rows = [
        synthetic_row(
            event_id="EVENT-1",
            service_code="1",
            start_date=dt.datetime(2099, 3, 4),
            end_date=dt.datetime(2099, 3, 1),
        )
    ]
    control = default_control(rows)
    path = build_workbook(tmp_path / "backwards.xlsx", rows=rows, control=control)

    with pytest.raises(WorkbookContractError, match="varasem kui"):
        parse_workbook(path)


def test_end_without_start_is_rejected(tmp_path):
    rows = [synthetic_row(event_id="EVENT-1", service_code="1", start_date=None)]
    rows[0]["end_date"] = dt.datetime(2099, 3, 4)
    control = default_control(rows)
    path = build_workbook(tmp_path / "orphan-end.xlsx", rows=rows, control=control)

    with pytest.raises(WorkbookContractError, match="ilma 'start_date'"):
        parse_workbook(path)


def test_missing_event_name_is_rejected(tmp_path):
    rows = [synthetic_row(event_id="EVENT-1", service_code="1", event_name="")]
    control = default_control(rows)
    path = build_workbook(tmp_path / "nameless.xlsx", rows=rows, control=control)

    with pytest.raises(WorkbookContractError, match="puudub 'event_name'"):
        parse_workbook(path)


def test_non_boolean_review_flag_is_rejected(tmp_path):
    rows = [synthetic_row(event_id="EVENT-1", service_code="1")]
    rows[0]["review_required"] = "JAH"
    control = default_control(rows)
    path = build_workbook(tmp_path / "flag.xlsx", rows=rows, control=control)

    with pytest.raises(WorkbookContractError, match="tõeväärtus"):
        parse_workbook(path)
