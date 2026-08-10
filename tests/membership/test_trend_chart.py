"""The membership trend chart's visualisation contract.

Not an equality assertion against a whole ECharts option — that makes a harmless
refinement look like a regression. These pin the handful of properties that
carry meaning: which points exist, what the axis is allowed to do, how a
provisional observation is drawn, and what a tooltip says.

Every observation here is an unsaved model instance, so the whole module runs
without PostgreSQL.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.core.formatting import GROUP_SEPARATOR, MINUS_SIGN
from apps.membership.charts import SYMBOL_DENSITY_LIMIT, total_and_paid_chart
from apps.membership.internal_selectors import InternalTrend, ObservationPoint
from apps.membership.models import InternalMembershipObservation, QualityStatus


def point(
    day: dt.date,
    *,
    total: int | None = None,
    paid: int | None = None,
    status: str = QualityStatus.VERIFIED,
    withheld: frozenset[str] = frozenset(),
) -> ObservationPoint:
    observation = InternalMembershipObservation(
        observation_date=day,
        total_members=total,
        paid_members=paid,
        quality_status=status,
    )
    return ObservationPoint(observation=observation, withheld=withheld)


def trend(*points: ObservationPoint, withheld_metric_points: int = 0) -> InternalTrend:
    return InternalTrend(
        points=tuple(points),
        date_from=points[0].observation_date if points else None,
        date_to=points[-1].observation_date if points else None,
        withheld_metric_points=withheld_metric_points,
        review_required_points=0,
    )


def series_named(option: dict, name: str) -> dict:
    return next(item for item in option["series"] if item["name"] == name)


YEAR_AGO = dt.date(2025, 7, 31)
LATEST = dt.date(2026, 7, 31)


@pytest.fixture
def two_year_trend() -> InternalTrend:
    return trend(
        point(YEAR_AGO, total=3547, paid=3282),
        point(dt.date(2026, 1, 31), total=3470, paid=3100),
        point(LATEST, total=3412, paid=3208),
    )


# -- what is drawn --------------------------------------------------------


def test_the_axis_is_real_time_rather_than_evenly_spaced_categories():
    """Board reports arrive irregularly; evenly spaced points would misstate
    when the Chamber actually counted."""
    chart = total_and_paid_chart(trend(point(LATEST, total=3412, paid=3208)))

    assert chart.option["xAxis"]["type"] == "time"


def test_both_series_are_present_and_separable_without_colour(two_year_trend):
    chart = total_and_paid_chart(two_year_trend)

    total = series_named(chart.option, "Liikmeid kokku")
    paid = series_named(chart.option, "Tasunud liikmeid")
    assert total["lineStyle"].get("type") in (None, "solid")
    assert paid["lineStyle"]["type"] == "dashed"


def test_a_missing_metric_produces_no_point_and_no_zero():
    """A withheld total is absent from the line. It is never a zero, and the
    line is never drawn across the gap as though the value were known."""
    chart = total_and_paid_chart(
        trend(
            point(YEAR_AGO, total=3547, paid=3282),
            point(LATEST, total=None, paid=3208),
        )
    )

    total = series_named(chart.option, "Liikmeid kokku")
    assert len(total["data"]) == 1
    assert total["connectNulls"] is False
    assert all(item["value"][1] is not None for item in total["data"])


def test_a_conflicted_metric_is_withheld_from_the_line():
    chart = total_and_paid_chart(
        trend(point(LATEST, total=3412, paid=3208, withheld=frozenset({"paid_members"})))
    )

    assert series_named(chart.option, "Tasunud liikmeid")["data"] == []


def test_there_is_no_legend_because_the_lines_label_themselves(two_year_trend):
    chart = total_and_paid_chart(two_year_trend)

    assert chart.option["legend"]["show"] is False
    assert series_named(chart.option, "Liikmeid kokku")["endLabel"]["show"] is True


def test_markers_disappear_once_they_stop_helping():
    """Sixty dots on one line is the picture rather than the data."""
    many = trend(
        *(
            point(dt.date(2020, 1, 1) + dt.timedelta(days=30 * i), total=3400 + i, paid=3200 + i)
            for i in range(SYMBOL_DENSITY_LIMIT + 5)
        )
    )
    few = trend(point(YEAR_AGO, total=3547, paid=3282), point(LATEST, total=3412, paid=3208))

    assert series_named(total_and_paid_chart(many).option, "Liikmeid kokku")["showSymbol"] is False
    assert series_named(total_and_paid_chart(few).option, "Liikmeid kokku")["showSymbol"] is True


# -- the axis, where a truthful series most easily lies --------------------


def test_the_y_axis_is_not_anchored_at_zero(two_year_trend):
    chart = total_and_paid_chart(two_year_trend)

    assert chart.option["yAxis"]["min"] > 0


def test_both_lines_share_one_axis():
    """The paid count is read against the total; a second axis would let the
    gap between the two lines mean nothing."""
    chart = total_and_paid_chart(trend(point(LATEST, total=3412, paid=3208)))

    assert isinstance(chart.option["yAxis"], dict)


# -- provisional is visible, not just footnoted ---------------------------


def test_a_provisional_observation_is_drawn_hollow():
    chart = total_and_paid_chart(
        trend(
            point(YEAR_AGO, total=3547, paid=3282),
            point(LATEST, total=3412, paid=3208, status=QualityStatus.PROVISIONAL),
        )
    )

    drawn = series_named(chart.option, "Liikmeid kokku")["data"]
    assert drawn[-1]["itemStyle"]["color"] == "transparent"
    assert "itemStyle" not in drawn[0]
    # The footnote explains the hollow marker; it is not the only place the
    # provisional state appears, which is the point of drawing it differently.
    assert any("markeriga" in note.lower() for note in chart.footnotes)


def test_a_provisional_point_says_so_in_its_own_tooltip():
    chart = total_and_paid_chart(
        trend(point(LATEST, total=3412, paid=3208, status=QualityStatus.PROVISIONAL))
    )

    assert chart.option["dashkoda"]["tooltip"]["2026-07-31"]["note"] == "Olek: esialgne"


# -- the tooltip ----------------------------------------------------------


def test_the_tooltip_is_prepared_on_the_server_and_keyed_to_its_point(two_year_trend):
    chart = total_and_paid_chart(two_year_trend)
    readout = chart.option["dashkoda"]["tooltip"]["2026-07-31"]

    assert readout["title"] == "31.07.2026"
    assert series_named(chart.option, "Liikmeid kokku")["data"][-1]["tip"] == "2026-07-31"


def test_the_tooltip_states_formatted_estonian_figures(two_year_trend):
    rows = {
        row["label"]: row["value"]
        for row in total_and_paid_chart(two_year_trend).option["dashkoda"]["tooltip"]["2026-07-31"][
            "rows"
        ]
    }

    assert rows["Liikmeid kokku"] == f"3{GROUP_SEPARATOR}412"
    assert rows["Tasunute osakaal"] == "94,0%"


def test_no_tooltip_ever_shows_a_raw_iso_date_or_an_ungrouped_number(two_year_trend):
    readouts = total_and_paid_chart(two_year_trend).option["dashkoda"]["tooltip"]

    for readout in readouts.values():
        assert "2026-07-31" != readout["title"]
        assert "3412" not in [row["value"] for row in readout["rows"]]


def test_the_gap_between_the_counts_is_named_without_claiming_a_meaning(two_year_trend):
    """The board report says how many members there are and how many have paid.
    It does not say the remainder is an unpaid invoice."""
    rows = {
        row["label"]: row["value"]
        for row in total_and_paid_chart(two_year_trend).option["dashkoda"]["tooltip"]["2026-07-31"][
            "rows"
        ]
    }

    assert rows["Vahe"] == "204"
    assert "Tasumata" not in rows
    assert "arve" not in " ".join(rows)


# -- the analytical header ------------------------------------------------


def test_the_headline_compares_against_the_observation_a_year_back(two_year_trend):
    chart = total_and_paid_chart(two_year_trend)
    readouts = {readout.label: readout for readout in chart.readouts}

    assert readouts["Liikmeid kokku"].value == f"3{GROUP_SEPARATOR}412"
    assert readouts["Liikmeid kokku"].change == f"{MINUS_SIGN}135"
    assert readouts["Liikmeid kokku"].direction == "down"


def test_the_share_moves_in_percentage_points(two_year_trend):
    readouts = {r.label: r for r in total_and_paid_chart(two_year_trend).readouts}

    assert readouts["Tasunute osakaal"].change.endswith("pp")


def test_a_history_too_short_to_compare_shows_the_value_and_no_comparison():
    """No fabricated year-ago figure.

    The sentence explaining *why* the comparison is missing was struck out, so
    the readout now shows the value and nothing beside it. The rule it protected
    is the one still asserted: nothing is invented to fill the gap, and the
    reason remains on the comparison object for anything that wants it.
    """
    chart = total_and_paid_chart(trend(point(LATEST, total=3412, paid=3208)))
    readout = next(r for r in chart.readouts if r.label == "Liikmeid kokku")

    assert readout.value == f"3{GROUP_SEPARATOR}412"
    assert readout.change == ""
    assert readout.direction == ""
    assert readout.note == ""
    # Still computed, still says why — just not printed under the figure.
    assert readout.change_label


def test_an_empty_trend_offers_no_readouts_and_no_false_zero():
    chart = total_and_paid_chart(trend())

    assert chart.readouts == ()
    assert chart.table_rows == ()
    assert not chart.has_data


def test_the_chart_asks_for_the_large_frame_and_dates_itself(two_year_trend):
    """The question line was struck out; the observation date was not, and it
    is the part that says what the drawing describes."""
    chart = total_and_paid_chart(two_year_trend)

    assert chart.size == "large"
    assert chart.question == ""
    assert chart.observation_label == "Seisuga 31.07.2026"


def test_a_withheld_point_is_withheld_rather_than_drawn_as_a_value():
    """The footnote naming the withheld points was struck out on the board's
    print-out. What it described is unchanged and is what matters: a point held
    back for a conflict is absent from the drawing and from the table, and is
    never replaced by a zero or by an interpolation between its neighbours.

    The count itself survives on the trend and is reported in the page's own
    data-quality section, which is where a reader who wants it goes.
    """
    withheld = trend(point(LATEST, total=3412, paid=3208), withheld_metric_points=2)
    chart = total_and_paid_chart(withheld)

    assert withheld.withheld_metric_points == 2
    # One observation went in, so one row comes out — the withheld points did
    # not become rows, and nothing was invented to stand in for them.
    assert len(chart.table_rows) == 1
    assert all(0 not in row[1:] for row in chart.table_rows)


def test_the_table_keeps_the_exact_values_the_chart_drew(two_year_trend):
    chart = total_and_paid_chart(two_year_trend)

    assert chart.table_rows[-1][1] == 3412
    assert chart.table_rows[-1][3] == Decimal("94.02")
