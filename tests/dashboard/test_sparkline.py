"""Sparkline geometry.

Pure functions, no database. What is being pinned down here is not the pixel
positions but the two rules the drawing must not break: an absent value produces
no point, and a series too short to describe a trend produces no drawing at all.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.dashboard.sparkline import (
    VIEWBOX_HEIGHT,
    VIEWBOX_WIDTH,
    TrendSource,
    build_sparkline,
    build_trend_chart,
    meter_width,
)

DAY = dt.date(2026, 1, 1)


def series(*values):
    return tuple((DAY + dt.timedelta(days=index), value) for index, value in enumerate(values))


def monthly(start, *values):
    """One value a month, which is how the board report arrives."""
    return tuple(
        (
            dt.date(
                start.year + (start.month - 1 + index) // 12, (start.month - 1 + index) % 12 + 1, 1
            ),
            value,
        )
        for index, value in enumerate(values)
    )


def coordinates(sparkline):
    return [tuple(float(part) for part in pair.split(",")) for pair in sparkline.points.split(" ")]


def test_a_series_spans_the_full_width():
    sparkline = build_sparkline(series(10, 20, 30))

    points = coordinates(sparkline)

    assert len(points) == 3
    assert points[0][0] == 0.0
    assert points[-1][0] == VIEWBOX_WIDTH


def test_the_largest_value_sits_above_the_smallest():
    """SVG's y axis grows downward, so a larger value must have a smaller y."""
    sparkline = build_sparkline(series(10, 30))

    low, high = coordinates(sparkline)

    assert high[1] < low[1]
    assert all(0 <= y <= VIEWBOX_HEIGHT for _x, y in (low, high))


def test_absent_values_produce_no_point_and_are_never_zero():
    sparkline = build_sparkline(series(10, None, 30))

    assert sparkline.point_count == 2
    assert sparkline.minimum == 10
    assert len(coordinates(sparkline)) == 2


def test_a_single_point_is_not_a_trend():
    assert build_sparkline(series(10)) is None
    assert build_sparkline(()) is None
    assert build_sparkline(series(None, None)) is None


def test_a_flat_series_is_drawn_on_the_centre_line():
    """Scaling a flat series to the edges would dramatise movement that is not
    in the data."""
    sparkline = build_sparkline(series(500, 500, 500))

    ys = {y for _x, y in coordinates(sparkline)}

    assert sparkline.is_flat is True
    assert len(ys) == 1
    assert ys.pop() == VIEWBOX_HEIGHT / 2


def test_decimal_values_survive_the_conversion():
    sparkline = build_sparkline(series(Decimal("10.5"), Decimal("20.5")))

    assert sparkline.first_value == 10.5
    assert sparkline.last_value == 20.5


# -- two series on shared axes ------------------------------------------


def public_and_internal():
    """A daily-ish total and a monthly paid count, as the card draws them."""
    return (
        TrendSource(
            label="Liikmeid kokku",
            style="solid",
            source="Koda.ee liikmekataloog · iga päev",
            series=monthly(dt.date(2025, 8, 1), 3300, 3340, 3380, 3412),
        ),
        TrendSource(
            label="Tasunud liikmeid",
            style="dashed",
            source="Sisemine liikmeskonna aruanne · kord kuus",
            series=monthly(dt.date(2025, 8, 1), 2600, 2700, 2750, 2798),
        ),
    )


def test_both_lines_are_measured_against_one_scale():
    """The card exists to show the gap between the two, which only means
    something if both are drawn against the same values and the same dates."""
    chart = build_trend_chart(public_and_internal())

    assert chart.minimum == 2600
    assert chart.maximum == 3412
    # The larger series never dips below the smaller one, so every one of its
    # points must sit higher on the drawing (SVG y grows downward).
    total_ys = [y for _x, y in _points(chart.lines[0])]
    paid_ys = [y for _x, y in _points(chart.lines[1])]
    assert all(total < paid for total, paid in zip(total_ys, paid_ys, strict=True))


def test_the_lines_keep_their_own_identity():
    chart = build_trend_chart(public_and_internal())

    assert [line.label for line in chart.lines] == ["Liikmeid kokku", "Tasunud liikmeid"]
    assert [line.style for line in chart.lines] == ["solid", "dashed"]
    assert "Koda.ee liikmekataloog" in chart.lines[0].source
    assert "Sisemine liikmeskonna aruanne" in chart.lines[1].source
    # Nothing is summed, and neither line inherits the other's observations.
    assert chart.lines[0].maximum == 3412
    assert chart.lines[1].maximum == 2798


def test_a_point_sits_on_its_own_date_not_on_its_position_in_the_series():
    """The two sources report on their own days, so a shared x axis has to be
    time. Placing the fourth point of a monthly series beside the fourth point
    of a daily one would be a drawing accident."""
    chart = build_trend_chart(
        (
            TrendSource(
                label="Iga päev",
                style="solid",
                source="Sünteetiline",
                series=series(10, 11, 12, 13),
            ),
            TrendSource(
                label="Harva",
                style="dashed",
                source="Sünteetiline",
                series=((DAY, 10), (DAY + dt.timedelta(days=3), 13)),
            ),
        )
    )

    dense = _points(chart.lines[0])
    sparse = _points(chart.lines[1])

    assert [x for x, _y in dense] == [
        0.0,
        pytest.approx(33.33, abs=0.01),
        pytest.approx(66.67, abs=0.01),
        100.0,
    ]
    assert [x for x, _y in sparse] == [0.0, 100.0]


def test_the_axis_names_every_month_and_states_the_year_where_it_turns():
    chart = build_trend_chart(public_and_internal())

    labels = [tick.label for tick in chart.ticks]
    years = [tick.year for tick in chart.ticks]

    assert labels == ["aug", "sept", "okt", "nov"]
    assert years == ["", "", "", ""]
    assert chart.range_label == "viimased 4 kuud · aug 2025 – nov 2025"


def test_the_year_is_stated_once_where_it_changes():
    chart = build_trend_chart(
        (
            TrendSource(
                label="Sünteetiline",
                style="solid",
                source="Sünteetiline",
                series=monthly(dt.date(2025, 11, 1), 10, 20, 30),
            ),
        )
    )

    assert [(tick.label, tick.year) for tick in chart.ticks] == [
        ("nov", ""),
        ("dets", ""),
        ("jaan", "2026"),
    ]
    assert chart.range_label == "viimased 3 kuud · nov 2025 – jaan 2026"


def test_a_series_too_short_to_draw_is_left_out_rather_than_faked():
    chart = build_trend_chart(
        (
            TrendSource(
                label="Piisav", style="solid", source="Sünteetiline", series=series(10, 20, 30)
            ),
            TrendSource(label="Üksik", style="dashed", source="Sünteetiline", series=series(10)),
        )
    )

    assert [line.label for line in chart.lines] == ["Piisav"]


def test_nothing_drawable_produces_no_chart():
    assert build_trend_chart(()) is None
    assert (
        build_trend_chart(
            (TrendSource(label="Üksik", style="solid", source="S", series=series(10)),)
        )
        is None
    )
    # Every observation on one day: a trend needs two dates, not two rows.
    assert (
        build_trend_chart(
            (
                TrendSource(
                    label="Sama päev",
                    style="solid",
                    source="S",
                    series=((DAY, 10), (DAY, 20)),
                ),
            )
        )
        is None
    )


def test_an_absent_value_leaves_a_gap_rather_than_a_zero():
    chart = build_trend_chart(
        (
            TrendSource(
                label="Auguga",
                style="solid",
                source="Sünteetiline",
                series=series(10, None, 30),
            ),
        )
    )

    assert chart.lines[0].point_count == 2
    assert chart.lines[0].minimum == 10


def _points(line):
    return [tuple(float(part) for part in pair.split(",")) for pair in line.points.split(" ")]


# -- the hoverable observations -----------------------------------------


def test_every_drawn_point_is_addressable_on_its_own():
    """The polyline carries these coordinates as a run of numbers inside one
    attribute. A marker is the same point the drawing can put a dot at."""
    chart = build_trend_chart(public_and_internal())

    for line in chart.lines:
        # The polyline rounds to two places on its way into the attribute, so
        # the comparison is to that precision and not to the float behind it.
        drawn = [value for marker in line.markers for value in (marker.x, marker.y)]
        written = [value for point in _points(line) for value in point]
        assert drawn == pytest.approx(written, abs=0.01)
        assert [marker.when for marker in line.markers] == [
            when for when, _value in line.observations
        ]


def test_one_band_per_observation_date_covers_the_whole_drawing():
    """A gap between two bands would be a place where hovering says nothing."""
    chart = build_trend_chart(public_and_internal())

    assert len(chart.bands) == 4
    assert chart.bands[0].x == 0.0
    assert chart.bands[-1].x + chart.bands[-1].width == pytest.approx(VIEWBOX_WIDTH)
    for left, right in zip(chart.bands, chart.bands[1:], strict=False):
        assert left.x + left.width == pytest.approx(right.x)


def test_a_band_reads_out_every_line_that_reported_that_day():
    """The card is read as one reading — the total, the paid count and the gap
    between them. A tooltip per line would make that two hovers and a memory."""
    chart = build_trend_chart(public_and_internal())

    assert chart.bands[0].readout == "1.08.25 · Liikmeid kokku 3300 · Tasunud liikmeid 2600"
    assert chart.bands[-1].readout == "1.11.25 · Liikmeid kokku 3412 · Tasunud liikmeid 2798"


def test_a_line_with_nothing_on_a_date_contributes_no_phrase_and_no_zero():
    chart = build_trend_chart(
        (
            TrendSource(
                label="Mõlemal päeval",
                style="solid",
                source="Sünteetiline",
                series=((DAY, 10), (DAY + dt.timedelta(days=1), 20)),
            ),
            TrendSource(
                label="Ainult hiljem",
                style="dashed",
                source="Sünteetiline",
                series=((DAY + dt.timedelta(days=1), 30), (DAY + dt.timedelta(days=2), 40)),
            ),
        )
    )

    first, second, third = chart.bands
    assert first.readout == "1.01.26 · Mõlemal päeval 10"
    assert second.readout == "2.01.26 · Mõlemal päeval 20 · Ainult hiljem 30"
    assert third.readout == "3.01.26 · Ainult hiljem 40"
    # The absent line is not named with a zero beside it, and not named at all.
    assert "Ainult hiljem" not in first.readout
    assert "Mõlemal päeval" not in third.readout


def test_two_observations_split_the_box_between_them():
    """Every position in the drawing belongs to whichever observation is
    nearest, so no pointer can land between two bands."""
    chart = build_trend_chart(
        (
            TrendSource(
                label="Kaks",
                style="solid",
                source="Sünteetiline",
                series=series(10, 20),
            ),
        )
    )

    assert [band.width for band in chart.bands] == [
        pytest.approx(VIEWBOX_WIDTH / 2),
        pytest.approx(VIEWBOX_WIDTH / 2),
    ]


def test_the_meter_clamps_its_rectangle_without_hiding_the_number():
    assert meter_width(78) == 78.0
    assert meter_width(Decimal("42.5")) == 42.5
    assert meter_width(None) is None
    # A collection percentage above 100 is a real thing a board report can
    # state. Only the drawing is clamped; the value itself is printed beside it.
    assert meter_width(140) == 100.0
    assert meter_width(-5) == 0.0
