"""The movement charts' visualisation contract.

The size-movement chart draws departures as negative numbers so the bars extend
leftwards. That negation is geometry, and the defect it caused — a tooltip
reading `Lahkunud: -11` — is the reason several tests here exist. Nobody reports
that minus eleven members left.

Rows are plain dicts in the shape the selectors return, so the module runs
without PostgreSQL.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from apps.membership.charts import removal_reasons_chart, size_movement_chart

WHEN = dt.date(2026, 7, 31)


def band(key: str, label: str, joined: int | None, removed: int | None) -> dict:
    return {"band": key, "label": label, "joined": joined, "removed": removed}


def reason(key: str, label: str, count: int | None, share: str | None) -> dict:
    return {
        "key": key,
        "label": label,
        "count": count,
        "share_pct": None if share is None else Decimal(share),
    }


def series_named(option: dict, name: str) -> dict:
    return next(item for item in option["series"] if item["name"] == name)


@pytest.fixture
def movement() -> tuple[dict, ...]:
    return (
        band("micro", "1–9 töötajat", 21, 38),
        band("small", "10–49 töötajat", 27, 22),
        band("medium", "50–249 töötajat", 14, 11),
    )


@pytest.fixture
def reasons() -> tuple[dict, ...]:
    return (
        reason("voluntary_financial", "Majanduslikud põhjused", 84, "31.0"),
        reason("dissolved", "Tegevuse lõpetamine", 120, "44.3"),
        reason("other", "Muu", 67, "24.7"),
    )


# -- the negation is geometry and must stay there -------------------------


def test_departures_are_drawn_leftwards_as_negative_geometry(movement):
    drawn = [
        item["value"]
        for item in series_named(
            size_movement_chart(movement, observation_date=WHEN).option, "Lahkunud"
        )["data"]
    ]

    assert drawn == [-38, -22, -11]


def test_a_tooltip_never_shows_a_departure_as_a_negative_count(movement):
    """The defect this chart shipped with. `Lahkunud: -11` is not a number any
    source reported."""
    rows = {
        item["label"]: item["value"]
        for item in size_movement_chart(movement, observation_date=WHEN).option["dashkoda"][
            "tooltip"
        ]["micro"]["rows"]
    }

    assert rows["Lahkunud"] == "38"
    assert not rows["Lahkunud"].startswith("-")
    assert not rows["Lahkunud"].startswith("\N{MINUS SIGN}")


def test_no_drawn_negative_reaches_any_reader_facing_string(movement):
    """A blanket check across every prepared readout, not just the one band —
    the negation must not leak through a path nobody thought to test."""
    chart = size_movement_chart(movement, observation_date=WHEN)
    prepared = json.dumps(chart.option["dashkoda"], ensure_ascii=False)

    for drawn in ("-38", "-22", "-11", "\N{MINUS SIGN}38", "\N{MINUS SIGN}22"):
        assert drawn not in prepared


def test_the_bar_end_label_states_the_positive_count(movement):
    """The chart is readable without hovering, and what it states is the count."""
    labels = [
        item["label"]["formatter"]
        for item in series_named(
            size_movement_chart(movement, observation_date=WHEN).option, "Lahkunud"
        )["data"]
    ]

    assert labels == ["38", "22", "11"]


def test_the_table_carries_the_positive_counts_too(movement):
    rows = size_movement_chart(movement, observation_date=WHEN).table_rows

    assert rows[0] == ("1–9 töötajat", 21, 38, -17)


# -- net movement ---------------------------------------------------------


def test_net_is_joined_minus_removed_and_is_signed(movement):
    rows = {
        item["label"]: item["value"]
        for item in size_movement_chart(movement, observation_date=WHEN).option["dashkoda"][
            "tooltip"
        ]["micro"]["rows"]
    }

    assert rows["Neto"] == "\N{MINUS SIGN}17"


def test_a_positive_net_is_signed_and_zero_is_not(movement):
    chart = size_movement_chart(
        (band("small", "10–49 töötajat", 27, 22), band("even", "Tasakaalus", 14, 14)),
        observation_date=WHEN,
    )
    tooltips = chart.option["dashkoda"]["tooltip"]

    assert next(r["value"] for r in tooltips["small"]["rows"] if r["label"] == "Neto") == "+5"
    assert next(r["value"] for r in tooltips["even"]["rows"] if r["label"] == "Neto") == "0"


def test_a_band_missing_one_direction_has_no_net():
    """Arrivals alone under a "net" heading would read as a measured gain."""
    chart = size_movement_chart((band("large", "250+ töötajat", 8, None),), observation_date=WHEN)

    labels = [row["label"] for row in chart.option["dashkoda"]["tooltip"]["large"]["rows"]]
    assert "Neto" not in labels
    assert chart.table_rows[0][3] is None


def test_the_header_totals_only_the_bands_that_reported_each_direction():
    chart = size_movement_chart(
        (band("micro", "1–9", 21, 38), band("large", "250+", 8, None)), observation_date=WHEN
    )
    readouts = {item.label: item.value for item in chart.readouts}

    assert readouts["Liitunud kokku"] == "29"
    assert readouts["Lahkunud kokku"] == "38"


def test_net_is_not_drawn_as_a_third_bar(movement):
    """Arrivals, departures and their difference is the same fact twice, and
    invites the reader to add the picture up."""
    option = size_movement_chart(movement, observation_date=WHEN).option

    assert [item["name"] for item in option["series"]] == ["Lahkunud", "Liitunud"]


# -- the frame ------------------------------------------------------------


def test_the_movement_chart_states_its_observation_date_rather_than_a_range(movement):
    """This section has no time control; it describes one snapshot and says so."""
    chart = size_movement_chart(movement, observation_date=WHEN)

    assert chart.observation_label == "Seisuga 31.07.2026"


def test_overlapping_labels_are_dropped_rather_than_printed_on_each_other(movement):
    option = size_movement_chart(movement, observation_date=WHEN).option

    assert option["labelLayout"]["hideOverlap"] is True


def test_no_observation_leaves_an_empty_state():
    chart = size_movement_chart((), observation_date=None)

    assert chart.readouts == ()
    assert chart.table_rows == ()
    assert not chart.has_data


# -- removal reasons ------------------------------------------------------


def test_reasons_are_ranked_largest_first(reasons):
    """The selector returns them unordered and the source documents no ordering
    of its own, so ranking is this chart's decision — and it is most of the
    answer to "why are members leaving"."""
    chart = removal_reasons_chart(reasons, observation_date=WHEN)

    assert chart.option["yAxis"]["data"] == [
        "Tegevuse lõpetamine",
        "Majanduslikud põhjused",
        "Muu",
    ]


def test_each_bar_states_its_count_and_share_without_a_hover(reasons):
    labels = [
        item["label"]["formatter"]
        for item in removal_reasons_chart(reasons, observation_date=WHEN).option["series"][0][
            "data"
        ]
    ]

    assert labels[0] == "120  44,3%"


def test_there_is_no_legend_for_a_single_series(reasons):
    option = removal_reasons_chart(reasons, observation_date=WHEN).option

    assert option["legend"]["show"] is False


def test_the_reason_tooltip_carries_the_count_share_and_date(reasons):
    readout = removal_reasons_chart(reasons, observation_date=WHEN).option["dashkoda"]["tooltip"][
        "dissolved"
    ]

    assert readout["title"] == "Tegevuse lõpetamine"
    assert {item["label"]: item["value"] for item in readout["rows"]} == {
        "Liikmeid": "120",
        "Osakaal": "44,3%",
    }
    assert readout["note"] == "Seisuga 31.07.2026"


def test_a_reason_with_no_count_is_not_drawn():
    """A reason nobody counted has no bar. It is not a zero-length one."""
    chart = removal_reasons_chart(
        (
            reason("dissolved", "Tegevuse lõpetamine", 120, "100.0"),
            reason("other", "Muu", None, None),
        ),
        observation_date=WHEN,
    )

    assert chart.option["yAxis"]["data"] == ["Tegevuse lõpetamine"]
    # It is still a reported row, so the table keeps it.
    assert len(chart.table_rows) == 2


def test_a_reason_without_a_share_still_states_its_count():
    chart = removal_reasons_chart((reason("other", "Muu", 12, None),), observation_date=WHEN)

    assert chart.option["series"][0]["data"][0]["label"]["formatter"] == "12"


def test_both_movement_charts_ask_for_the_categorical_frame(movement, reasons):
    """Height follows the number of categories, not the viewport."""
    assert size_movement_chart(movement, observation_date=WHEN).size == "categorical"
    assert removal_reasons_chart(reasons, observation_date=WHEN).size == "categorical"
