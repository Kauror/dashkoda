"""The new-member chart's visualisation contract.

The chart this replaces drew one equally weighted line per year across months
numbered I–XII. These pin what replaced it: one subject year in front of one
benchmark, months a reader does not have to translate, and — the rule the whole
thing rests on — a month nobody reported never behaving as a zero.

`MonthlyValue` is a plain frozen dataclass, so the module runs without
PostgreSQL.
"""

from __future__ import annotations

import pytest

from apps.membership.charts import (
    BENCHMARK_AVERAGE,
    BENCHMARK_PREVIOUS,
    VIEW_CUMULATIVE,
    available_benchmarks,
    monthly_new_members_chart,
)
from apps.membership.internal_selectors import MonthlyValue
from apps.membership.models.internal import MonthlyValueStatus


def month(year: int, number: int, value: int | None, status: str = MonthlyValueStatus.VERIFIED):
    return MonthlyValue(
        calendar_year=year, calendar_month=number, new_members=value, value_status=status
    )


def full_year(year: int, *, base: int = 20) -> tuple[MonthlyValue, ...]:
    return tuple(month(year, number, base + number) for number in range(1, 13))


def series_named(option: dict, name: str) -> dict:
    return next(item for item in option["series"] if name in item["name"])


def drawn(option: dict, name: str) -> list:
    return [item["value"] if item else None for item in series_named(option, name)["data"]]


@pytest.fixture
def part_year() -> dict:
    """This year reported January, an explicit zero in February and March, then
    a conflicted April and a May that follows the gap."""
    return {
        2025: full_year(2025),
        2026: (
            month(2026, 1, 24),
            month(2026, 2, 0),
            month(2026, 3, 31),
            month(2026, 4, None, MonthlyValueStatus.CONFLICT),
            month(2026, 5, 29),
        ),
    }


# -- the axis and the hierarchy -------------------------------------------


def test_the_months_are_words_rather_than_roman_numerals(part_year):
    """A reader should not have to translate before they can read a chart."""
    axis = monthly_new_members_chart(part_year).option["xAxis"]["data"]

    assert axis[0] == "jaan"
    assert axis[11] == "dets"
    assert "VII" not in axis


def test_the_table_still_names_months_the_way_the_report_does(part_year):
    """The source uses numerals; someone checking a figure against the report
    they came from should see the same naming there."""
    months = {row[1] for row in monthly_new_members_chart(part_year).table_rows}

    assert "III" in months


def test_the_current_year_is_bars_in_front_of_one_benchmark_line(part_year):
    option = monthly_new_members_chart(part_year).option

    assert series_named(option, "2026")["type"] == "bar"
    assert series_named(option, "2025")["type"] == "line"
    assert series_named(option, "2026")["z"] > series_named(option, "2025")["z"]


def test_only_one_historical_series_is_drawn(part_year):
    """Not six lines of equal weight competing for the same attention."""
    option = monthly_new_members_chart(part_year).option

    assert len(option["series"]) == 2


# -- missing is not zero, anywhere ----------------------------------------


def test_an_explicit_zero_is_drawn_and_a_conflict_is_not(part_year):
    values = drawn(monthly_new_members_chart(part_year).option, "2026")

    assert values[1] == 0, "February reported nobody joined, which is a measurement"
    assert values[3] is None, "April is conflicted and has no number at all"
    assert values[11] is None, "December was never reported"


def test_the_cumulative_line_stops_at_the_first_unreported_month(part_year):
    """Carrying on would draw a flatter slope that reads as a slowdown nobody
    measured, and skipping the month would silently mean "everything except
    the month we lost"."""
    values = drawn(monthly_new_members_chart(part_year, view=VIEW_CUMULATIVE).option, "2026")

    assert values[:3] == [24, 24, 55]
    assert values[3] is None
    assert values[4] is None, "May is after the gap and cannot be accumulated through it"


def test_the_stopped_cumulative_line_says_why_it_stopped(part_year):
    chart = monthly_new_members_chart(part_year, view=VIEW_CUMULATIVE)

    assert any("puuduvat kuud ei loeta nulliks" in note for note in chart.footnotes)


def test_a_conflicted_month_is_withheld_rather_than_drawn_as_zero(part_year):
    """The footnote was struck out on the board's print-out; the behaviour it
    described is the point and is unchanged.

    A conflicted month is not drawn, and it is not replaced by a zero — which
    would read as "nobody joined that month" rather than "nobody knows". Its
    row still carries a status of its own in the table, which is where the
    disclosure now lives.
    """
    chart = monthly_new_members_chart(part_year)

    drawn = [value for value in chart.option["series"][0]["data"] if value is not None]
    assert 0 not in drawn, "a month nobody could measure must not be drawn as zero"
    assert any(row[3] for row in chart.table_rows), "the table still states each month's status"


# -- the year-to-date readout ---------------------------------------------


def test_the_year_to_date_stops_before_the_first_unknown_month(part_year):
    """January, February and March are known; April is not, so the total is the
    first three months and says so."""
    readouts = {item.label: item for item in monthly_new_members_chart(part_year).readouts}

    assert readouts["Uusi liikmeid 2026"].value == "55"
    assert readouts["Uusi liikmeid 2026"].note == "jaanuar–märts"


def test_the_comparison_covers_the_same_stretch_of_the_previous_year(part_year):
    """Never July year-to-date against a full previous year."""
    readouts = {item.label: item for item in monthly_new_members_chart(part_year).readouts}

    # 2025 reported 21, 22 and 23 in those months.
    assert readouts["Sama periood 2025"].value == "66"
    assert readouts["Sama periood 2025"].change == "\N{MINUS SIGN}11"


def test_a_year_starting_with_an_unknown_month_has_no_total_and_says_so():
    chart = monthly_new_members_chart({2026: (month(2026, 2, 12),)})
    readout = chart.readouts[0]

    assert readout.value == ""
    assert readout.note


def test_no_comparable_previous_period_is_stated_rather_than_omitted():
    chart = monthly_new_members_chart(
        {2025: (month(2025, 1, 20),), 2026: (month(2026, 1, 24), month(2026, 2, 18))}
    )
    readouts = {item.label: item for item in chart.readouts}

    assert readouts["Sama periood 2025"].value == ""
    assert readouts["Sama periood 2025"].note


# -- the benchmark --------------------------------------------------------


def test_the_average_benchmark_needs_three_complete_years():
    sparse = {2025: full_year(2025), 2026: (month(2026, 1, 24),)}
    deep = {year: full_year(year) for year in (2023, 2024, 2025)}
    deep[2026] = (month(2026, 1, 24),)

    assert available_benchmarks(sparse) == (BENCHMARK_PREVIOUS,)
    assert available_benchmarks(deep) == (BENCHMARK_PREVIOUS, BENCHMARK_AVERAGE)


def test_a_benchmark_that_cannot_be_drawn_says_so_rather_than_drawing_nothing(part_year):
    """A blank comparison line leaves the reader guessing whether they broke it."""
    chart = monthly_new_members_chart(part_year, benchmark=BENCHMARK_AVERAGE)

    assert any("ei saa kuvada" in note for note in chart.footnotes)


def test_the_average_is_drawn_when_every_year_reported_that_month():
    by_year = {year: full_year(year, base=18) for year in (2023, 2024, 2025)}
    by_year[2026] = (month(2026, 1, 24),)

    values = drawn(
        monthly_new_members_chart(by_year, benchmark=BENCHMARK_AVERAGE).option, "keskmine"
    )

    assert values[0] == 19.0


def test_a_month_one_year_missed_leaves_a_gap_in_the_average():
    """An average over "the years that happened to report" changes meaning from
    month to month."""
    by_year = {year: full_year(year) for year in (2023, 2024, 2025)}
    by_year[2024] = tuple(item for item in by_year[2024] if item.calendar_month != 5)
    by_year[2026] = (month(2026, 1, 24),)

    values = drawn(
        monthly_new_members_chart(by_year, benchmark=BENCHMARK_AVERAGE).option, "keskmine"
    )

    assert values[4] is None
    assert values[3] is not None


# -- the tooltip ----------------------------------------------------------


def test_the_tooltip_answers_whether_the_month_beat_its_benchmark(part_year):
    rows = {
        item["label"]: item["value"]
        for item in monthly_new_members_chart(part_year).option["dashkoda"]["tooltip"]["3"]["rows"]
    }

    assert rows["2026"] == "31"
    assert rows["2025"] == "23"
    assert rows["Erinevus"] == "+8 (+34,8%)"


def test_every_tooltip_row_is_labelled(part_year):
    """A value with nothing naming it is a number the reader has to guess at."""
    for readout in monthly_new_members_chart(part_year).option["dashkoda"]["tooltip"].values():
        assert all(item["label"] for item in readout["rows"])


def test_the_tooltip_names_the_month_in_estonian(part_year):
    readout = monthly_new_members_chart(part_year).option["dashkoda"]["tooltip"]["3"]

    assert readout["title"] == "Märts 2026"


def test_a_conflicted_month_has_no_tooltip_of_its_own(part_year):
    """There is no value to state, and an empty readout would imply there was."""
    tooltips = monthly_new_members_chart(part_year).option["dashkoda"]["tooltip"]

    assert "2026" not in [row["label"] for row in tooltips["4"]["rows"]]


# -- empty ----------------------------------------------------------------


def test_no_monthly_history_leaves_an_empty_state():
    chart = monthly_new_members_chart({})

    assert chart.option["series"] == []
    assert chart.table_rows == ()
    assert not chart.has_data
    assert available_benchmarks({}) == ()


def test_the_chart_asks_for_a_medium_frame(part_year):
    """The question line was struck out on the board's print-out.

    `ChartPayload.question` still exists and other charts may still carry one;
    this chart simply no longer states its own, because the section it sits in
    already says what it is about.
    """
    chart = monthly_new_members_chart(part_year)

    assert chart.size == "medium"
    assert chart.question == ""
