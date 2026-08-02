"""The workbook contract is enforced, not guessed at."""

import datetime as dt

import pytest

from apps.legal_work.workbook import (
    DATA_COLUMNS,
    DATA_COLUMNS_V12,
    SUPPORTED_SCHEMA_VERSIONS,
    WorkbookContractError,
    parse_workbook,
)

from .workbook_factory import (
    REPORTING_DATE,
    default_rows,
    synthetic_row,
    write_workbook,
)


def test_control_is_parsed_including_the_real_generator_metadata(make_workbook):
    parsed = parse_workbook(make_workbook())

    assert parsed.control.dataset_key == "oigusloome"
    assert parsed.control.schema_version == "1.1"
    assert parsed.control.reporting_date == REPORTING_DATE
    assert parsed.control.generator_version == "1.1.1"
    # The real generator reports this status; it must not be treated as failure.
    assert parsed.control.refresh_status == "completed_with_warnings"


def test_a_cloud_generator_may_leave_source_modified_at_empty(make_workbook):
    """A generator reading the operational file from the cloud has no mtime.

    The field is nullable in the parsed control record and on the snapshot, so a
    key written with an empty value is a legitimate workbook — distinct from the
    key being absent, which is still a contract break.
    """
    parsed = parse_workbook(make_workbook(control_overrides={"source_modified_at": None}))

    assert parsed.control.source_modified_at is None
    assert len(parsed.rows) == 3


def test_an_absent_source_modified_at_key_is_still_rejected(make_workbook):
    with pytest.raises(WorkbookContractError, match="puuduvad väljad"):
        parse_workbook(make_workbook(control_omit=("source_modified_at",)))


@pytest.mark.parametrize(
    "key",
    ["dataset_key", "schema_version", "refresh_status", "generator_version", "source_sha256"],
)
def test_a_mandatory_control_value_may_not_be_empty(make_workbook, key):
    with pytest.raises(WorkbookContractError, match="tühjad"):
        parse_workbook(make_workbook(control_overrides={key: None}))


@pytest.mark.parametrize("key", ["generated_at", "reporting_date", "total_record_count"])
def test_a_mandatory_control_value_may_not_be_blank_text(make_workbook, key):
    with pytest.raises(WorkbookContractError, match="tühjad"):
        parse_workbook(make_workbook(control_overrides={key: "   "}))


@pytest.mark.parametrize("version", SUPPORTED_SCHEMA_VERSIONS)
def test_every_supported_schema_version_is_accepted(make_workbook, version):
    """Each version is read in its own shape.

    1.2 appends two columns, so a workbook declaring it must carry them. That
    is the point of the version: it says what the DATA table looks like.
    """
    columns = DATA_COLUMNS_V12 if version == "1.2" else DATA_COLUMNS
    rows = None
    if version == "1.2":
        rows = [row + [None, None] for row in default_rows()]

    parsed = parse_workbook(make_workbook(schema_version=version, columns=columns, rows=rows))

    assert parsed.control.schema_version == version


def test_unsupported_schema_version_is_rejected(make_workbook):
    with pytest.raises(WorkbookContractError, match="skeemi versioon"):
        parse_workbook(make_workbook(schema_version="9.9"))


def test_wrong_dataset_key_is_rejected(make_workbook):
    with pytest.raises(WorkbookContractError, match="Vale andmestik"):
        parse_workbook(make_workbook(dataset_key="something-else"))


def test_missing_sheet_is_rejected(make_workbook):
    with pytest.raises(WorkbookContractError, match="puuduvad lehed"):
        parse_workbook(make_workbook(sheets=("CONTROL", "DATA", "WARNINGS")))


def test_missing_data_table_is_rejected(make_workbook):
    with pytest.raises(WorkbookContractError, match="puudub Exceli tabel"):
        parse_workbook(make_workbook(data_table_name=""))


def test_wrongly_named_data_table_is_rejected(make_workbook):
    with pytest.raises(WorkbookContractError, match="puudub Exceli tabel"):
        parse_workbook(make_workbook(data_table_name="tbl_something_else"))


def test_reordered_columns_are_rejected(make_workbook):
    swapped = list(DATA_COLUMNS)
    swapped[1], swapped[2] = swapped[2], swapped[1]

    with pytest.raises(WorkbookContractError, match="veerud ei vasta"):
        parse_workbook(make_workbook(columns=tuple(swapped)))


def test_renamed_column_is_rejected(make_workbook):
    renamed = ("teema",) + DATA_COLUMNS[1:]

    with pytest.raises(WorkbookContractError, match="veerud ei vasta"):
        parse_workbook(make_workbook(columns=renamed))


def test_duplicate_record_id_is_rejected(make_workbook):
    rows = [
        synthetic_row(record_id="SYN-DUP", source_row=2),
        synthetic_row(record_id="SYN-DUP", source_row=3),
    ]

    with pytest.raises(WorkbookContractError, match="Korduv 'record_id'"):
        parse_workbook(make_workbook(rows=rows))


def test_formula_inside_the_data_table_is_rejected(make_workbook):
    with pytest.raises(WorkbookContractError, match="valem"):
        parse_workbook(make_workbook(formula_in_data=True))


def test_real_dates_and_booleans_are_parsed_as_such(make_workbook):
    """Excel types survive as Python types, not as strings."""
    rows = [synthetic_row(record_id="SYN-1", received_date=dt.date(2026, 5, 29), is_open=True)]

    parsed = parse_workbook(make_workbook(rows=rows))
    first = parsed.rows[0]

    assert first.received_date == dt.date(2026, 5, 29)
    assert isinstance(first.received_date, dt.date)
    assert first.is_open is True
    assert isinstance(first.is_open, bool)


def test_text_in_a_date_column_is_rejected(make_workbook):
    rows = [synthetic_row(record_id="SYN-1", received_date="eile")]

    with pytest.raises(WorkbookContractError, match="received_date"):
        parse_workbook(make_workbook(rows=rows))


def test_non_boolean_is_open_is_rejected(make_workbook):
    rows = [synthetic_row(record_id="SYN-1", is_open="jah")]

    with pytest.raises(WorkbookContractError, match="is_open"):
        parse_workbook(make_workbook(rows=rows))


def test_unknown_sent_status_is_rejected(make_workbook):
    rows = [synthetic_row(record_id="SYN-1", sent_status="maybe")]

    with pytest.raises(WorkbookContractError, match="sent_status"):
        parse_workbook(make_workbook(rows=rows))


def test_sent_status_requires_a_sent_date(make_workbook):
    rows = [synthetic_row(record_id="SYN-1", sent_status="sent", sent_date=None)]

    with pytest.raises(WorkbookContractError, match="'sent_date' puudub"):
        parse_workbook(make_workbook(rows=rows))


def test_a_non_sent_record_may_not_carry_a_sent_date(make_workbook):
    rows = [synthetic_row(record_id="SYN-1", sent_status="not_sent", sent_date=dt.date(2099, 2, 2))]

    with pytest.raises(WorkbookContractError, match="olek on"):
        parse_workbook(make_workbook(rows=rows))


def test_missing_topic_is_a_structural_failure(make_workbook):
    rows = [synthetic_row(record_id="SYN-1", topic="")]

    with pytest.raises(WorkbookContractError, match="puudub 'topic'"):
        parse_workbook(make_workbook(rows=rows))


def test_warning_codes_are_preserved_and_split(make_workbook):
    rows = [
        synthetic_row(
            record_id="SYN-1",
            warning_codes="missing_stage;deadline_before_received",
        )
    ]

    parsed = parse_workbook(make_workbook(rows=rows))

    assert parsed.rows[0].warning_codes == ["missing_stage", "deadline_before_received"]
    assert parsed.warning_record_count == 1


def test_control_count_mismatch_is_detected(make_workbook):
    with pytest.raises(WorkbookContractError, match="ei ole DATA lehega kooskõlas"):
        parse_workbook(make_workbook(control_overrides={"total_record_count": 999}))


def test_warning_count_compares_records_not_warning_rows(make_workbook):
    """A record with two codes is one warning record, not two."""
    rows = [synthetic_row(record_id="SYN-1", warning_codes="missing_stage;sent_before_received")]

    parsed = parse_workbook(make_workbook(rows=rows))

    assert parsed.warning_record_count == 1
    assert len(parsed.warnings) == 2


def test_source_row_may_repeat_across_years(make_workbook):
    """The real workbook spans years and restarts row numbering in each."""
    rows = [
        synthetic_row(record_id="SYN-A", source_year=2098, source_row=2),
        synthetic_row(record_id="SYN-B", source_year=2099, source_row=2),
    ]

    parsed = parse_workbook(make_workbook(rows=rows))

    assert len(parsed.rows) == 2


def test_the_same_year_and_row_twice_is_rejected(make_workbook):
    rows = [
        synthetic_row(record_id="SYN-A", source_year=2099, source_row=2),
        synthetic_row(record_id="SYN-B", source_year=2099, source_row=2),
    ]

    with pytest.raises(WorkbookContractError, match="Korduv aasta ja lähterea"):
        parse_workbook(make_workbook(rows=rows))


def test_unknown_control_keys_do_not_break_the_import(make_workbook):
    """The generator adds metadata over time; that must stay non-breaking."""
    parsed = parse_workbook(
        make_workbook(control_overrides={"overview_year": 2099, "preview_limit": 15})
    )

    assert len(parsed.rows) == len(default_rows())


def test_warning_rows_never_carry_the_original_value(make_workbook):
    """Workbook content must not ride along into diagnostics."""
    parsed = parse_workbook(make_workbook())

    for warning in parsed.warnings:
        assert "original_value" not in warning
        assert "explanation" not in warning


# -- schema 1.2: member feedback counts ---------------------------------


def _row_with_counts(given, asked, **kwargs) -> list:
    """A 1.2 DATA row: the base row plus the two appended counts."""
    return synthetic_row(**kwargs) + [given, asked]


def test_a_12_workbook_carries_the_member_feedback_counts(tmp_path):
    path = write_workbook(
        tmp_path / "v12.xlsx",
        rows=[_row_with_counts(7, 12, record_id="OIG-2026-0001", source_row=2)],
        schema_version="1.2",
        columns=DATA_COLUMNS_V12,
    )

    parsed = parse_workbook(path)

    assert parsed.control.schema_version == "1.2"
    assert parsed.rows[0].feedback_member_count == 7
    assert parsed.rows[0].feedback_requested_member_count == 12


def test_a_blank_count_is_absent_rather_than_zero(tmp_path):
    """Nobody counted zero: the question simply was not tracked on that row.

    A reported 0 is a real answer — nobody responded — and has to stay
    distinguishable from it.
    """
    path = write_workbook(
        tmp_path / "blank.xlsx",
        rows=[
            _row_with_counts(None, 0, record_id="OIG-2026-0001", source_row=2),
        ],
        schema_version="1.2",
        columns=DATA_COLUMNS_V12,
    )

    parsed = parse_workbook(path)

    assert parsed.rows[0].feedback_member_count is None
    assert parsed.rows[0].feedback_requested_member_count == 0


def test_declaring_12_without_the_columns_is_a_contract_break(tmp_path):
    """A generator that bumps the version but forgets the columns must fail.

    Silently reading every count as absent would look exactly like a workbook
    where nobody tracked the question, which is a different claim entirely.
    """
    path = write_workbook(
        tmp_path / "liar.xlsx",
        schema_version="1.2",
        columns=DATA_COLUMNS,
    )

    with pytest.raises(WorkbookContractError, match="DATA veerud"):
        parse_workbook(path)


def test_an_11_workbook_still_parses_and_reports_no_counts(tmp_path):
    """The columns are appended, so the older shape is untouched."""
    path = write_workbook(tmp_path / "v11.xlsx", schema_version="1.1")

    parsed = parse_workbook(path)

    assert parsed.control.schema_version == "1.1"
    assert all(row.feedback_member_count is None for row in parsed.rows)
    assert all(row.feedback_requested_member_count is None for row in parsed.rows)
