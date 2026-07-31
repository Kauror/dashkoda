"""Sparkline geometry.

Pure functions, no database. What is being pinned down here is not the pixel
positions but the two rules the drawing must not break: an absent value produces
no point, and a series too short to describe a trend produces no drawing at all.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from apps.dashboard.sparkline import (
    VIEWBOX_HEIGHT,
    VIEWBOX_WIDTH,
    build_sparkline,
    meter_width,
)

DAY = dt.date(2026, 1, 1)


def series(*values):
    return tuple((DAY + dt.timedelta(days=index), value) for index, value in enumerate(values))


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


def test_the_meter_clamps_its_rectangle_without_hiding_the_number():
    assert meter_width(78) == 78.0
    assert meter_width(Decimal("42.5")) == 42.5
    assert meter_width(None) is None
    # A collection percentage above 100 is a real thing a board report can
    # state. Only the drawing is clamped; the value itself is printed beside it.
    assert meter_width(140) == 100.0
    assert meter_width(-5) == 0.0
