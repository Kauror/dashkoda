"""Schema 2.0 package contract.

These tests need no database: `apps.membership.package` holds no Django import,
which is what lets the whole contract be exercised on a machine without
PostgreSQL.

Everything here is synthetic. No Chamber document, member name or real figure
appears — the contract is what is under test, and a contract can be checked with
invented numbers.
"""

from __future__ import annotations

import pytest

from apps.membership.package import (
    PACKAGE_SCHEMA_VERSION,
    REQUIRED_PATHS_BY_VERSION,
    SUPPORTED_MANIFEST_SCHEMA_VERSIONS,
    V2_ONLY_PATHS,
    PackageContractError,
    read_package,
)

from .package_factory import (
    BATCH_SUSPENSION,
    BATCH_TERMINATION,
    PERIOD_SUMMER,
    SOURCE_A,
    SOURCE_B,
    build_package,
)


def v2(tmp_path, **overrides):
    return build_package(tmp_path / "package.zip", schema_version="2.0", **overrides)


# --------------------------------------------------------------------------
# Version handling and backward compatibility
# --------------------------------------------------------------------------


def test_importer_declares_two_dot_zero_and_still_accepts_one_dot_zero():
    assert PACKAGE_SCHEMA_VERSION == "2.0"
    assert SUPPORTED_MANIFEST_SCHEMA_VERSIONS == {"1.0", "2.0"}


def test_a_one_dot_zero_package_still_parses_exactly_as_before(tmp_path):
    parsed = read_package(build_package(tmp_path / "v1.zip", schema_version="1.0"))

    assert parsed.manifest_schema_version == "1.0"
    assert parsed.snapshots
    assert parsed.monthly_values
    # The 2.0 tables are absent, which is not the same as being empty.
    assert parsed.decision_batches == ()
    assert parsed.new_member_periods == ()
    # And the counts say nothing about them rather than claiming a zero.
    assert "decision_batches" not in parsed.row_counts


def test_an_unknown_schema_version_is_refused_not_guessed_at(tmp_path):
    with pytest.raises(PackageContractError):
        read_package(build_package(tmp_path / "v9.zip", schema_version="9.9"))


def test_a_one_dot_zero_package_carrying_a_two_dot_zero_table_is_refused(tmp_path):
    """The manifest declares the contract; a stray table means they disagree."""

    def add_v2_file(payloads):
        payloads["data/decision_batches.csv"] = b"batch_id\n"
        return payloads

    with pytest.raises(PackageContractError):
        read_package(
            build_package(tmp_path / "mixed.zip", schema_version="1.0", mutate_payloads=add_v2_file)
        )


@pytest.mark.parametrize("missing", V2_ONLY_PATHS)
def test_a_two_dot_zero_package_missing_any_new_table_is_refused(tmp_path, missing):
    def drop(payloads):
        payloads.pop(missing)
        return payloads

    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, mutate_payloads=drop))


def test_required_paths_grow_by_exactly_the_new_tables():
    v1 = set(REQUIRED_PATHS_BY_VERSION["1.0"])
    two = set(REQUIRED_PATHS_BY_VERSION["2.0"])
    assert two - v1 == set(V2_ONLY_PATHS)


# --------------------------------------------------------------------------
# Decision batches
# --------------------------------------------------------------------------


def test_a_valid_two_dot_zero_package_parses_every_new_table(tmp_path):
    parsed = read_package(v2(tmp_path))

    assert parsed.manifest_schema_version == "2.0"
    assert parsed.row_counts["decision_batches"] == 2
    assert parsed.row_counts["decision_batch_sizes"] == 4
    assert parsed.row_counts["decision_batch_reasons"] == 3
    assert parsed.row_counts["new_member_periods"] == 1
    assert parsed.row_counts["new_member_sizes"] == 3


def test_the_as_of_date_and_the_decision_date_are_kept_apart(tmp_path):
    """The appendix is compiled before the board signs; both dates are facts."""
    parsed = read_package(v2(tmp_path))
    batch = next(b for b in parsed.decision_batches if b.batch_id == BATCH_TERMINATION)

    assert batch.as_of_date.isoformat() == "2024-01-04"
    assert batch.decision_date.isoformat() == "2024-01-11"
    assert batch.as_of_date != batch.decision_date


def test_a_batch_without_a_decision_date_does_not_borrow_the_as_of_date(tmp_path):
    def undated(rows):
        rows[0] = {**rows[0], "decision_date": ""}
        return rows

    parsed = read_package(v2(tmp_path, decision_batches=undated(_batches())))
    batch = next(b for b in parsed.decision_batches if b.batch_id == BATCH_TERMINATION)

    assert batch.decision_date is None
    assert batch.as_of_date is not None


def test_termination_and_suspension_are_separate_batches(tmp_path):
    parsed = read_package(v2(tmp_path))
    kinds = {b.batch_id: b.batch_kind for b in parsed.decision_batches}

    assert kinds[BATCH_TERMINATION] == "termination"
    assert kinds[BATCH_SUSPENSION] == "suspension"


def test_a_duplicate_batch_id_is_refused(tmp_path):
    rows = _batches()
    rows[1] = {**rows[1], "batch_id": BATCH_TERMINATION}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, decision_batches=rows))


def test_a_batch_pointing_at_an_unknown_document_is_refused(tmp_path):
    rows = _batches()
    rows[0] = {**rows[0], "source_id": "src_does_not_exist"}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, decision_batches=rows))


def test_an_unknown_corroborating_document_is_refused(tmp_path):
    rows = _batches()
    rows[0] = {**rows[0], "corroborating_source_id": "src_nope"}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, decision_batches=rows))


def test_a_corroborating_document_may_be_absent(tmp_path):
    """Not every appendix has a matching formal decision in the corpus."""
    parsed = read_package(v2(tmp_path))
    batch = next(b for b in parsed.decision_batches if b.batch_id == BATCH_SUSPENSION)

    assert batch.corroborating_source_id == ""
    other = next(b for b in parsed.decision_batches if b.batch_id == BATCH_TERMINATION)
    assert other.corroborating_source_id == SOURCE_B


def test_a_size_row_for_an_unknown_batch_is_refused(tmp_path):
    rows = _sizes()
    rows[0] = {**rows[0], "batch_id": "batch_nope"}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, decision_batch_sizes=rows))


def test_a_reason_row_for_an_unknown_batch_is_refused(tmp_path):
    rows = _reasons()
    rows[0] = {**rows[0], "batch_id": "batch_nope"}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, decision_batch_reasons=rows))


def test_the_new_size_bands_survive_the_contract(tmp_path):
    """`group_company` and `unknown` are real bands, not parse failures."""
    parsed = read_package(v2(tmp_path))
    bands = {
        row.size_band_key
        for row in parsed.decision_batch_sizes
        if row.batch_id == BATCH_TERMINATION
    }

    assert "group_company" in bands
    assert "unknown" in bands


def test_the_reason_table_cannot_carry_raw_source_text(tmp_path):
    """Free reason text can name another company, so no column may hold it."""
    from apps.membership.package import REQUIRED_HEADERS

    header = REQUIRED_HEADERS["data/decision_batch_reasons.csv"]

    assert header == ("batch_id", "reason_key", "member_count", "warning_codes")
    assert not [name for name in header if "label" in name or "raw" in name]


# --------------------------------------------------------------------------
# New-member periods
# --------------------------------------------------------------------------


def test_a_multi_month_span_is_kept_whole(tmp_path):
    parsed = read_package(v2(tmp_path))
    period = parsed.new_member_periods[0]

    assert period.period_scope == "multi_month_period"
    assert period.period_start.isoformat() == "2024-06-01"
    assert period.period_end.isoformat() == "2024-07-31"
    assert period.new_members == 9
    # It must not have been split across its two months.
    assert not [
        m for m in parsed.monthly_values if (m.calendar_year, m.calendar_month) == (2024, 6)
    ]


def test_a_period_ending_before_it_starts_is_refused(tmp_path):
    rows = _periods()
    rows[0] = {**rows[0], "period_start": "2024-07-31", "period_end": "2024-06-01"}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, new_member_periods=rows))


def test_a_period_pointing_at_an_unknown_document_is_refused(tmp_path):
    rows = _periods()
    rows[0] = {**rows[0], "source_id": "src_nope"}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, new_member_periods=rows))


# --------------------------------------------------------------------------
# The shared size distribution
# --------------------------------------------------------------------------


def test_a_size_row_names_exactly_one_parent(tmp_path):
    parsed = read_package(v2(tmp_path))
    for row in parsed.new_member_sizes:
        has_period = bool(row.period_id)
        has_month = row.calendar_year is not None and row.calendar_month is not None
        assert has_period != has_month


def test_a_size_row_naming_both_parents_is_refused(tmp_path):
    rows = _new_sizes()
    rows[0] = {**rows[0], "period_id": PERIOD_SUMMER}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, new_member_sizes=rows))


def test_a_size_row_naming_no_parent_is_refused(tmp_path):
    rows = _new_sizes()
    rows[0] = {**rows[0], "period_id": "", "calendar_year": "", "calendar_month": ""}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, new_member_sizes=rows))


def test_a_size_row_for_a_month_no_monthly_value_reports_is_refused(tmp_path):
    rows = _new_sizes()
    rows[0] = {**rows[0], "calendar_year": "1999", "calendar_month": "5"}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, new_member_sizes=rows))


def test_a_size_row_for_an_unknown_period_is_refused(tmp_path):
    rows = _new_sizes()
    rows[-1] = {**rows[-1], "period_id": "period_nope"}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, new_member_sizes=rows))


def test_an_impossible_calendar_month_is_refused(tmp_path):
    rows = _new_sizes()
    rows[0] = {**rows[0], "calendar_month": "13"}
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, new_member_sizes=rows))


def test_a_missing_member_count_stays_missing_and_never_becomes_zero(tmp_path):
    rows = _new_sizes()
    rows[0] = {**rows[0], "member_count": ""}
    parsed = read_package(v2(tmp_path, new_member_sizes=rows))
    row = parsed.new_member_sizes[0]

    assert row.member_count is None
    assert row.member_count != 0


def test_an_explicit_zero_stays_zero(tmp_path):
    rows = _new_sizes()
    rows[0] = {**rows[0], "member_count": "0"}
    parsed = read_package(v2(tmp_path, new_member_sizes=rows))

    assert parsed.new_member_sizes[0].member_count == 0


def test_a_duplicate_size_row_is_refused(tmp_path):
    rows = _new_sizes()
    rows.append(dict(rows[0]))
    with pytest.raises(PackageContractError):
        read_package(v2(tmp_path, new_member_sizes=rows))


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_two_reads_of_one_package_agree(tmp_path):
    path = v2(tmp_path)
    first = read_package(path)
    second = read_package(path)

    assert first == second
    assert first.package_sha256 == second.package_sha256


def test_row_counts_expose_aggregates_only(tmp_path):
    parsed = read_package(v2(tmp_path))
    for key, value in parsed.row_counts.items():
        assert isinstance(value, int), key
    assert SOURCE_A not in str(parsed.row_counts)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _batches():
    from .package_factory import default_decision_batches

    return default_decision_batches()


def _sizes():
    from .package_factory import default_decision_batch_sizes

    return default_decision_batch_sizes()


def _reasons():
    from .package_factory import default_decision_batch_reasons

    return default_decision_batch_reasons()


def _periods():
    from .package_factory import default_new_member_periods

    return default_new_member_periods()


def _new_sizes():
    from .package_factory import default_new_member_sizes

    return default_new_member_sizes()
