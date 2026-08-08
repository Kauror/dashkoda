"""The fee-collection chart's visualisation contract.

The chart it replaces drew received euros, budgeted euros, a reported
percentage and a computed percentage across two y axes. These tests pin what
replaced it: one measure, one axis, the current year in front of muted history,
and a disagreement between the two percentages disclosed rather than resolved.

Rows are plain dicts in the shape `get_fee_collection_trend` returns, so the
whole module runs without PostgreSQL.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.core.formatting import GROUP_SEPARATOR
from apps.membership.charts import BUDGET_TARGET_PCT, fee_collection_chart
from apps.membership.quality import computed_collection_pct

BUDGET = "1020000"


def row(
    day: dt.date,
    received: str,
    *,
    budget: str = BUDGET,
    reported: str | None = None,
    withheld: bool = False,
    year_precision: bool = False,
) -> dict:
    return {
        "observation_date": day,
        "received": Decimal(received),
        "budget": Decimal(budget),
        "reported_pct": None if reported is None else Decimal(reported),
        "computed_pct": computed_collection_pct(Decimal(received), Decimal(budget)),
        "reported_withheld": withheld,
        "is_year_precision": year_precision,
    }


def series_named(option: dict, name: str) -> dict:
    return next(item for item in option["series"] if item["name"] == name)


@pytest.fixture
def two_years() -> tuple[dict, ...]:
    return (
        row(dt.date(2025, 3, 31), "300000", reported="29.41"),
        row(dt.date(2025, 7, 31), "690000", reported="67.65"),
        row(dt.date(2026, 3, 31), "330000", reported="32.35"),
        row(dt.date(2026, 7, 31), "742400", reported="72.78"),
    )


# -- the shape that replaced the four-series dual axis --------------------


def test_there_is_one_y_axis_and_one_measure(two_years):
    """Two axes made a reader work out which one each series belonged to
    before they could read any of it."""
    option = fee_collection_chart(two_years).option

    assert isinstance(option["yAxis"], dict)
    assert all("yAxisIndex" not in item for item in option["series"])


def test_each_year_is_one_line_across_the_calendar_year(two_years):
    option = fee_collection_chart(two_years).option

    assert [item["name"] for item in option["series"]] == ["2025", "2026"]


def test_the_current_year_is_drawn_in_front_of_muted_history(two_years):
    """Not a rainbow in which every year competes for the same attention."""
    option = fee_collection_chart(two_years).option
    current = series_named(option, "2026")
    earlier = series_named(option, "2025")

    assert current["lineStyle"]["width"] > earlier["lineStyle"]["width"]
    assert earlier["lineStyle"]["opacity"] < 1
    assert current["z"] > earlier["z"]


def test_the_annual_budget_is_a_labelled_reference_line(two_years):
    option = fee_collection_chart(two_years).option
    marks = [item["markLine"] for item in option["series"] if "markLine" in item]

    assert len(marks) == 1, "one target line, on the current year"
    assert marks[0]["data"] == [{"yAxis": BUDGET_TARGET_PCT}]


def test_the_axis_starts_at_zero_because_completion_starts_at_nothing(two_years):
    """Unlike the membership trend, where zero would flatten every change: a
    budget genuinely begins the year unfilled."""
    assert fee_collection_chart(two_years).option["yAxis"]["min"] == 0


def test_collection_above_the_budget_is_visible_rather_than_clipped():
    """Revenue can exceed a budget; the ceiling has to clear the target."""
    option = fee_collection_chart((row(dt.date(2026, 12, 31), "1122000"),)).option

    assert option["yAxis"]["max"] > 110


def test_the_months_are_estonian_words_supplied_as_a_finite_list(two_years):
    labels = fee_collection_chart(two_years).option["dashkoda"]["axisLabels"]["x"]

    assert labels[0] == "jaan"
    assert labels[11] == "dets"
    assert len(labels) == 12


def test_a_point_sits_where_it_falls_in_its_year(two_years):
    """31 July is nearly August, not the start of July."""
    position = series_named(fee_collection_chart(two_years).option, "2026")["data"][-1]["value"][0]

    assert 6.9 < position < 7.0


# -- reported versus computed --------------------------------------------


def test_the_drawn_measure_is_the_one_implied_by_the_amounts(two_years):
    """`quality.py` withholds the reported percentage when it disagrees with the
    amounts, so the amounts are what survives and the measure derived from them
    is the one that can always be drawn."""
    drawn = series_named(fee_collection_chart(two_years).option, "2026")["data"][-1]["value"][1]

    assert drawn == pytest.approx(72.78, abs=0.01)


def test_a_withheld_reported_percentage_is_disclosed_not_hidden():
    chart = fee_collection_chart(
        (row(dt.date(2026, 7, 31), "742400", reported=None, withheld=True),)
    )

    assert any("ei ühti summadega" in note for note in chart.footnotes)


def test_a_reported_percentage_that_was_never_given_raises_no_disagreement():
    """Absent is not the same as contradicted, and only one is worth a note."""
    chart = fee_collection_chart((row(dt.date(2026, 7, 31), "742400", reported=None),))

    assert not any("ei ühti summadega" in note for note in chart.footnotes)


def test_both_percentages_keep_their_own_column_in_the_table(two_years):
    chart = fee_collection_chart(two_years)

    assert "Täitmine (summadest)" in chart.table_headers
    assert "Raporteeritud %" in chart.table_headers


# -- observations that cannot be placed ----------------------------------


def test_a_year_precision_observation_is_not_given_a_day_it_never_had():
    chart = fee_collection_chart(
        (
            row(dt.date(2026, 7, 31), "742400"),
            row(dt.date(2025, 1, 1), "980000", year_precision=True),
        )
    )

    assert [item["name"] for item in chart.option["series"]] == ["2026"]
    assert any("Aastatäpsusega" in note for note in chart.footnotes)
    # It still appears in the table: it is a real observation, just not a
    # placeable one.
    assert len(chart.table_rows) == 2


def test_an_observation_with_no_computable_completion_is_not_drawn():
    """A zero budget has no completion, and dividing by it would invent one."""
    chart = fee_collection_chart((row(dt.date(2026, 7, 31), "742400", budget="0"),))

    assert chart.option["series"] == []


# -- the readout and the tooltip -----------------------------------------


def test_the_readout_states_the_position_against_the_budget(two_years):
    readouts = {item.label: item.value for item in fee_collection_chart(two_years).readouts}

    assert readouts["2026 laekunud"] == f"742{GROUP_SEPARATOR}400{GROUP_SEPARATOR}€"
    assert readouts["Täitmine"] == "72,8%"
    assert readouts["Puudu aastaeelarvest"] == f"277{GROUP_SEPARATOR}600{GROUP_SEPARATOR}€"


def test_exceeding_the_budget_is_named_as_a_surplus_not_a_negative_shortfall():
    readouts = {
        item.label: item.value
        for item in fee_collection_chart((row(dt.date(2026, 12, 31), "1122000"),)).readouts
    }

    assert "Üle aastaeelarve" in readouts
    assert readouts["Üle aastaeelarve"] == f"102{GROUP_SEPARATOR}000{GROUP_SEPARATOR}€"


def test_the_tooltip_answers_the_management_question(two_years):
    rows = {
        item["label"]: item["value"]
        for item in fee_collection_chart(two_years).option["dashkoda"]["tooltip"]["2026-07-31"][
            "rows"
        ]
    }

    assert rows["Laekunud"] == f"742{GROUP_SEPARATOR}400{GROUP_SEPARATOR}€"
    assert rows["Täitmine"] == "72,8%"
    assert rows["2025 lähim võrreldav"] == "67,7%"
    assert rows["Erinevus"] == f"+5,1{GROUP_SEPARATOR}pp"


def test_no_tooltip_shows_a_raw_amount_or_an_iso_date(two_years):
    for readout in fee_collection_chart(two_years).option["dashkoda"]["tooltip"].values():
        assert "-" not in readout["title"]
        assert "742400" not in [item["value"] for item in readout["rows"]]


def test_a_year_with_no_comparable_point_offers_no_comparison():
    """March against the previous year's December is a different question
    wearing the same label."""
    rows = (
        row(dt.date(2025, 12, 31), "1000000"),
        row(dt.date(2026, 3, 31), "330000"),
    )

    labels = [
        item["label"]
        for item in fee_collection_chart(rows).option["dashkoda"]["tooltip"]["2026-03-31"]["rows"]
    ]

    assert not any("lähim võrreldav" in label for label in labels)


def test_no_observations_leaves_an_empty_state_rather_than_a_zero():
    chart = fee_collection_chart(())

    assert chart.readouts == ()
    assert chart.option["series"] == []
    assert not chart.has_data
