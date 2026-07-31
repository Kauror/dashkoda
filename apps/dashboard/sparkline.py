"""Turn a small series into SVG geometry, on the server.

The overview draws two miniature trends inside a card. ECharts would do it, but
it is a large bundle and the landing page should not carry one to draw a line of
a dozen points, so the coordinates are computed here and the template emits a
plain `<polyline>`.

Geometry travels as SVG **attributes** (`points`, `width`, `x`), never as a
`style`. That is what keeps `style-src 'self'` intact: the strict Content
Security Policy forbids an inline style, and `tests/dashboard/test_overview.py`
asserts the rendered page contains no `style="` at all. Colour and size come
from Tailwind classes on the element.

Two rules carried over from `apps/membership/charts.py`, because they are
properties of the data and not of the drawing library:

- an absent value produces no point. Nothing is substituted and no line is drawn
  across a gap;
- a series with fewer than two points is not a trend, so it returns `None` and
  the template renders nothing rather than an axis with a dot on it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

# The drawing box. A wide, short viewBox scaled with `preserveAspectRatio="none"`
# lets one set of coordinates serve every card width.
VIEWBOX_WIDTH = 100
VIEWBOX_HEIGHT = 32
# Keeps the stroke and its end caps inside the box instead of clipping them.
VERTICAL_PADDING = 3

MINIMUM_POINTS = 2

type Point = tuple[date | datetime, int | float | Decimal | None]


@dataclass(frozen=True)
class Sparkline:
    """One drawable series: its polyline, its ends and its range."""

    points: str
    first_value: float
    last_value: float
    minimum: float
    maximum: float
    point_count: int
    last_x: float
    last_y: float

    @property
    def is_flat(self) -> bool:
        return self.maximum == self.minimum


def build_sparkline(series: Sequence[Point]) -> Sparkline | None:
    """Map `(when, value)` pairs onto the viewBox, oldest first.

    `None` values are dropped rather than plotted, so a gap in the source stays
    a gap in the data. Returns `None` when what is left cannot describe a trend.
    """
    values = [float(value) for _when, value in series if value is not None]
    if len(values) < MINIMUM_POINTS:
        return None

    minimum = min(values)
    maximum = max(values)
    span = maximum - minimum
    usable_height = VIEWBOX_HEIGHT - (2 * VERTICAL_PADDING)
    last_index = len(values) - 1

    coordinates = []
    for index, value in enumerate(values):
        x = (index / last_index) * VIEWBOX_WIDTH
        # A flat series sits on the centre line. Scaling it to the top or the
        # bottom would dramatise noise that is not there.
        offset = 0.5 if span == 0 else (value - minimum) / span
        y = VIEWBOX_HEIGHT - VERTICAL_PADDING - (offset * usable_height)
        coordinates.append((x, y))

    return Sparkline(
        points=" ".join(f"{x:.2f},{y:.2f}" for x, y in coordinates),
        first_value=values[0],
        last_value=values[-1],
        minimum=minimum,
        maximum=maximum,
        point_count=len(values),
        last_x=coordinates[-1][0],
        last_y=coordinates[-1][1],
    )


def meter_width(percentage) -> float | None:
    """A percentage as a width on a 0–100 viewBox, clamped to the box.

    Clamping is drawing only. A collection percentage above 100 is a real thing
    a board report can state, and the number itself is always printed beside the
    bar; what is clamped is the rectangle, so it cannot overflow its track.
    """
    if percentage is None:
        return None
    value = float(percentage)
    return min(max(value, 0.0), 100.0)
