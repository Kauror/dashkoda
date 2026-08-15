"""How a roster row becomes a bucket, and what refuses to become one.

The classification rules are where composition analytics can quietly go wrong:
a status folded into a neighbouring one, a nought counted as a small company, a
tenure measured from today instead of from the snapshot. Each of those produces
a plausible chart that is not true, so each has a test.

Runs without PostgreSQL — `composition.py` touches neither Django nor a model.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from apps.membership.composition import (
    LONG_TENURE_YEARS,
    MIN_OVERALL_FOR_INDEX,
    MIN_RECENT_FOR_INDEX,
    RECENT_JOINER_WINDOW_DAYS,
    SIZE_1_9,
    SIZE_10_49,
    SIZE_50_249,
    SIZE_250_PLUS,
    SIZE_ZERO,
    TENURE_1_2,
    TENURE_3_5,
    TENURE_6_10,
    TENURE_11_20,
    TENURE_20_PLUS,
    TENURE_UNDER_1,
    UNKNOWN,
    CompositionTally,
    Dimension,
    Population,
    build_member_row,
    category_label,
    classify_employee_size,
    classify_join_cohort,
    classify_legal_form,
    classify_region,
    classify_sector,
    classify_status,
    classify_tenure,
    completed_years,
    growth_index,
    ordered_keys,
)

SNAPSHOT = dt.date(2026, 6, 9)


# ---------------------------------------------------------------------------
# Status and legal form
# ---------------------------------------------------------------------------


def test_the_three_roster_statuses_map_explicitly():
    assert classify_status("Koja liige") == "regular"
    assert classify_status("Peatatud liige") == "suspended"
    assert classify_status("Toetaja liige") == "supporter"


def test_status_matching_survives_case_and_padding():
    assert classify_status("  KOJA LIIGE ") == "regular"


def test_an_unrecognised_status_is_unknown_not_the_nearest_category():
    """A status the Chamber adds next year must surface, not be absorbed."""
    for raw in ("Auliige", "", None, "liige", 7):
        assert classify_status(raw) == UNKNOWN


def test_legal_forms_map_and_an_unlisted_one_is_unknown():
    assert classify_legal_form("OÜ") == "ou"
    assert classify_legal_form("as") == "as"
    assert classify_legal_form("UÜ") == UNKNOWN
    assert classify_legal_form(None) == UNKNOWN


# ---------------------------------------------------------------------------
# Company size
# ---------------------------------------------------------------------------


def test_employee_bands_use_the_canonical_boundaries():
    assert classify_employee_size(1) == SIZE_1_9
    assert classify_employee_size(9) == SIZE_1_9
    assert classify_employee_size(10) == SIZE_10_49
    assert classify_employee_size(49) == SIZE_10_49
    assert classify_employee_size(50) == SIZE_50_249
    assert classify_employee_size(249) == SIZE_50_249
    assert classify_employee_size(250) == SIZE_250_PLUS
    assert classify_employee_size(5000) == SIZE_250_PLUS


def test_zero_employees_is_its_own_band_and_never_joins_one_to_nine():
    """Fifteen rows of the real roster report it; folding them in would inflate
    the smallest class with organisations that do not belong in it."""
    assert classify_employee_size(0) == SIZE_ZERO
    assert classify_employee_size(0) != SIZE_1_9


def test_an_impossible_or_unreadable_employee_count_is_unknown_not_clamped():
    for raw in (-1, -1000, None, "", "palju", True):
        assert classify_employee_size(raw) == UNKNOWN


# ---------------------------------------------------------------------------
# Region and sector
# ---------------------------------------------------------------------------


def test_counties_fold_to_one_category_however_they_are_spelled():
    assert classify_region("HARJUMAA") == "harjumaa"
    assert classify_region("VÕRUMAA") == classify_region("Vorumaa") == "vorumaa"
    assert classify_region("Lääne-Virumaa") == "laane-virumaa"


def test_an_unknown_region_does_not_become_a_new_county():
    for raw in (None, "", "Riga", "MAAKOND"):
        assert classify_region(raw) == UNKNOWN


def test_nace_divisions_map_to_their_published_section():
    assert classify_sector(46) == "G"  # wholesale
    assert classify_sector(47) == "G"  # retail
    assert classify_sector(25) == "C"  # manufacturing
    assert classify_sector(62) == "J"  # information and communication
    assert classify_sector(85) == "P"  # education
    assert classify_sector("4612") == "G"  # longer codes read their division


def test_a_code_outside_every_published_range_is_unknown():
    """34 and 44 belong to no NACE section, and neither does a blank."""
    for raw in (34, 44, None, "", "x", 4):
        assert classify_sector(raw) == UNKNOWN


# ---------------------------------------------------------------------------
# Tenure and cohorts
# ---------------------------------------------------------------------------


def test_tenure_is_measured_from_the_snapshot_not_from_today():
    """The same snapshot must yield the same bands however long afterwards it
    is read, or a June export would gain six months of tenure by December."""
    start = dt.date(2020, 6, 9)

    assert completed_years(start, dt.date(2026, 6, 9)) == 6
    assert completed_years(start, dt.date(2030, 6, 9)) == 10


def test_a_day_before_the_anniversary_is_still_the_previous_year():
    start = dt.date(2020, 6, 10)
    assert completed_years(start, dt.date(2026, 6, 9)) == 5


def test_tenure_bands_cover_every_year_with_no_gap():
    """A member of exactly two-and-a-half years must land somewhere."""
    bands = [classify_tenure(dt.date(SNAPSHOT.year - n, 6, 9), SNAPSHOT) for n in range(0, 40)]
    assert UNKNOWN not in bands

    assert classify_tenure(dt.date(2026, 1, 1), SNAPSHOT) == TENURE_UNDER_1
    assert classify_tenure(dt.date(2024, 6, 9), SNAPSHOT) == TENURE_1_2
    assert classify_tenure(dt.date(2021, 6, 9), SNAPSHOT) == TENURE_3_5
    assert classify_tenure(dt.date(2018, 6, 9), SNAPSHOT) == TENURE_6_10
    assert classify_tenure(dt.date(2010, 6, 9), SNAPSHOT) == TENURE_11_20
    assert classify_tenure(dt.date(1995, 6, 9), SNAPSHOT) == TENURE_20_PLUS


def test_a_start_date_after_the_snapshot_is_not_a_tenure():
    assert completed_years(dt.date(2026, 12, 1), SNAPSHOT) is None
    assert classify_tenure(dt.date(2026, 12, 1), SNAPSHOT) == UNKNOWN
    assert classify_tenure(None, SNAPSHOT) == UNKNOWN


def test_the_long_tenure_threshold_matches_the_band_it_reads():
    assert LONG_TENURE_YEARS == 11
    assert classify_tenure(dt.date(SNAPSHOT.year - 11, 6, 9), SNAPSHOT) == TENURE_11_20


def test_a_join_cohort_is_the_start_year_and_a_future_date_has_none():
    assert classify_join_cohort(dt.date(2019, 3, 1), SNAPSHOT) == "2019"
    assert classify_join_cohort(dt.date(2027, 3, 1), SNAPSHOT) == UNKNOWN


# ---------------------------------------------------------------------------
# Row building and tallying
# ---------------------------------------------------------------------------


def row(**overrides):
    base = {
        "status": "Koja liige",
        "legal_form": "OÜ",
        "employees": 12,
        "region": "HARJUMAA",
        "sector_code": 46,
        "membership_start": dt.date(2015, 3, 1),
        "snapshot_date": SNAPSHOT,
    }
    return build_member_row(**{**base, **overrides})


def test_a_member_row_carries_no_field_that_could_hold_an_identity():
    """The structural half of the privacy guarantee: there is nowhere to put one."""
    fields = set(row().__dataclass_fields__)

    assert fields == {
        "status",
        "legal_form",
        "employee_size",
        "region",
        "sector",
        "tenure_band",
        "join_cohort",
        "tenure_days",
        "is_recent_joiner",
    }


def test_recent_joiners_are_defined_against_the_snapshot_window():
    inside = SNAPSHOT - dt.timedelta(days=RECENT_JOINER_WINDOW_DAYS)
    outside = SNAPSHOT - dt.timedelta(days=RECENT_JOINER_WINDOW_DAYS + 1)

    assert row(membership_start=inside).is_recent_joiner is True
    assert row(membership_start=outside).is_recent_joiner is False


def test_a_recent_joiner_is_counted_in_both_populations():
    tally = CompositionTally(snapshot_date=SNAPSHOT)
    tally.add(row(membership_start=SNAPSHOT - dt.timedelta(days=30)))
    tally.add(row(membership_start=dt.date(2001, 1, 1)))

    assert tally.total(Population.ALL_CURRENT) == 2
    assert tally.total(Population.RECENT_JOINERS) == 1


def test_every_dimension_counts_every_row_including_the_unclassified_ones():
    """A dimension that dropped a row would give itself a quietly different
    denominator from the one beside it."""
    tally = CompositionTally(snapshot_date=SNAPSHOT)
    tally.add(row())
    tally.add(row(status="Auliige", region=None, sector_code=None, employees=-5))

    for dimension in (
        Dimension.STATUS,
        Dimension.LEGAL_FORM,
        Dimension.EMPLOYEE_SIZE,
        Dimension.REGION,
        Dimension.SECTOR,
        Dimension.TENURE_BAND,
        Dimension.JOIN_COHORT,
    ):
        counted = sum(tally.category_counts(Population.ALL_CURRENT, dimension).values())
        assert counted == 2, dimension


def test_coverage_reports_what_a_dimension_could_actually_classify():
    tally = CompositionTally(snapshot_date=SNAPSHOT)
    for _ in range(9):
        tally.add(row())
    tally.add(row(sector_code=None))

    assert tally.coverage_pct(Dimension.SECTOR) == Decimal("90.0")
    assert tally.coverage_pct(Dimension.STATUS) == Decimal("100.0")


def test_the_median_tenure_is_absent_rather_than_zero_without_usable_dates():
    empty = CompositionTally(snapshot_date=SNAPSHOT)
    empty.add(row(membership_start=None))

    assert empty.median_tenure_days is None


def test_the_median_tenure_is_the_middle_value():
    tally = CompositionTally(snapshot_date=SNAPSHOT)
    for days in (10, 20, 300):
        tally.add(row(membership_start=SNAPSHOT - dt.timedelta(days=days)))

    assert tally.median_tenure_days == 20


# ---------------------------------------------------------------------------
# Ordering and labels
# ---------------------------------------------------------------------------


def test_an_ordinal_dimension_keeps_its_scale_order():
    """Size and tenure are scales; sorting them by count would destroy the axis."""
    keys = ordered_keys(Dimension.EMPLOYEE_SIZE, {SIZE_250_PLUS, SIZE_ZERO, SIZE_10_49})
    assert keys == [SIZE_ZERO, SIZE_10_49, SIZE_250_PLUS]

    tenure = ordered_keys(Dimension.TENURE_BAND, {TENURE_20_PLUS, TENURE_UNDER_1, TENURE_6_10})
    assert tenure == [TENURE_UNDER_1, TENURE_6_10, TENURE_20_PLUS]


def test_join_cohorts_are_chronological_with_unknown_last():
    keys = ordered_keys(Dimension.JOIN_COHORT, {"2019", "1998", UNKNOWN, "2026"})
    assert keys == ["1998", "2019", "2026", UNKNOWN]


def test_a_joining_year_is_its_own_label():
    assert category_label(Dimension.JOIN_COHORT, "2019") == "2019"
    assert category_label(Dimension.EMPLOYEE_SIZE, SIZE_1_9) == "1–9 töötajat"


# ---------------------------------------------------------------------------
# Growth index
# ---------------------------------------------------------------------------


def test_equal_representation_is_an_index_of_one_hundred():
    rows, _ = growth_index(
        overall={"a": 500, "b": 500},
        recent={"a": 50, "b": 50},
        dimension=Dimension.SECTOR,
    )
    assert {row.key: row.index for row in rows} == {"a": 100, "b": 100}


def test_over_and_under_representation_land_either_side_of_one_hundred():
    rows, _ = growth_index(
        overall={"a": 800, "b": 200},
        recent={"a": 50, "b": 50},
        dimension=Dimension.SECTOR,
    )
    index = {row.key: row.index for row in rows}

    assert index["a"] < 100 < index["b"]


def test_a_thin_category_is_suppressed_rather_than_ranked_on_noise():
    rows, suppressed = growth_index(
        overall={"big": 900, "thin": MIN_OVERALL_FOR_INDEX - 1},
        recent={"big": 90, "thin": MIN_RECENT_FOR_INDEX},
        dimension=Dimension.SECTOR,
    )

    assert [row.key for row in rows] == ["big"]
    assert suppressed == ("thin",)


def test_a_category_with_too_few_recent_joiners_is_also_suppressed():
    rows, suppressed = growth_index(
        overall={"big": 900, "quiet": 400},
        recent={"big": 90, "quiet": MIN_RECENT_FOR_INDEX - 1},
        dimension=Dimension.SECTOR,
    )

    assert [row.key for row in rows] == ["big"]
    assert "quiet" in suppressed


def test_a_suppressed_category_is_never_returned_as_a_zero_or_as_average():
    """Withheld and exactly-average are different statements about the data."""
    rows, suppressed = growth_index(
        overall={"thin": 3}, recent={"thin": 1}, dimension=Dimension.SECTOR
    )

    assert rows == ()
    assert suppressed == ("thin",)


def test_an_empty_population_yields_no_index_at_all():
    rows, suppressed = growth_index(overall={}, recent={}, dimension=Dimension.SECTOR)
    assert rows == ()
    assert suppressed == ()


def test_the_index_is_ranked_most_over_represented_first():
    rows, _ = growth_index(
        overall={"a": 400, "b": 400, "c": 400},
        recent={"a": 20, "b": 60, "c": 40},
        dimension=Dimension.SECTOR,
    )

    assert [row.key for row in rows] == ["b", "c", "a"]
