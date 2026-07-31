"""Turn a small series into SVG geometry, on the server.

The overview draws miniature trends inside its cards — one line on its own for a
single figure, and two lines on shared axes where the Liikmeskond card shows the
member total against the paid members. ECharts would do either, but it is a
large bundle and the landing page should not carry one to draw a line of a dozen
points, so the coordinates are computed here and the template emits a plain
`<polyline>`.

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
# Two lines need more room between them than one line needs on its own.
CHART_HEIGHT = 40
# Keeps the stroke and its end caps inside the box instead of clipping them.
VERTICAL_PADDING = 3

MINIMUM_POINTS = 2

# Estonian month names as an axis writes them. Spelled out rather than taken
# from a locale because the short forms are irregular — `märts`, `mai`, `juuni`
# and `juuli` do not shorten at all — and an axis is far too small to carry a
# wrong one.
MONTH_LABELS = (
    "jaan",
    "veebr",
    "märts",
    "apr",
    "mai",
    "juuni",
    "juuli",
    "aug",
    "sept",
    "okt",
    "nov",
    "dets",
)

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


@dataclass(frozen=True)
class TrendSource:
    """One series offered to `build_trend_chart`, with what names it.

    `style` is `solid` or `dashed` and is the only thing that separates the
    lines apart from colour, so a reader who cannot tell the two colours apart
    can still tell the two lines apart.
    """

    label: str
    style: str
    source: str
    series: Sequence[Point]


@dataclass(frozen=True)
class TrendLine:
    """One drawn series: its polyline, its name and the points behind it."""

    label: str
    style: str
    source: str
    points: str
    observations: tuple[tuple[date, float], ...]
    minimum: float
    maximum: float

    @property
    def point_count(self) -> int:
        return len(self.observations)


@dataclass(frozen=True)
class MonthTick:
    """One label on the time axis. `year` is set only where the year turns."""

    label: str
    year: str


@dataclass(frozen=True)
class TrendChart:
    """Several dated series sharing one pair of axes."""

    lines: tuple[TrendLine, ...]
    ticks: tuple[MonthTick, ...]
    minimum: float
    maximum: float
    start: date
    end: date

    @property
    def month_count(self) -> int:
        return len(self.ticks)

    @property
    def range_label(self) -> str:
        return (
            f"viimased {self.month_count} kuud · "
            f"{_month_label(self.start)} {self.start.year} – "
            f"{_month_label(self.end)} {self.end.year}"
        )


def build_trend_chart(sources: Sequence[TrendSource]) -> TrendChart | None:
    """Map several dated series onto one shared time and value scale.

    Sharing the axes is the point: two lines a reader compares by eye have to
    be measured against the same scale and placed on the same dates, or the
    comparison is a drawing accident.

    What is never shared is identity. Each line keeps its own label and its own
    source, nothing is added to anything, and neither line is extended with the
    other's observations. A date one source did not report simply gets no point
    on that line — the daily directory and the monthly board report are drawn at
    their own cadences rather than resampled to a common one.

    Returns `None` when nothing left can describe a trend, so the template
    renders no axis with a dot on it.
    """
    plotted = []
    for source in sources:
        points = _observations(source.series)
        if len(points) >= MINIMUM_POINTS:
            plotted.append((source, points))
    if not plotted:
        return None

    every_point = [point for _source, points in plotted for point in points]
    start = min(when for when, _value in every_point)
    end = max(when for when, _value in every_point)
    span_days = (end - start).days
    if span_days <= 0:
        return None

    minimum = min(value for _when, value in every_point)
    maximum = max(value for _when, value in every_point)
    value_span = maximum - minimum
    usable_height = CHART_HEIGHT - (2 * VERTICAL_PADDING)

    lines = []
    for source, points in plotted:
        coordinates = []
        for when, value in points:
            x = ((when - start).days / span_days) * VIEWBOX_WIDTH
            # A flat set of values sits on the centre line rather than being
            # scaled to the top, which would dramatise noise that is not there.
            offset = 0.5 if value_span == 0 else (value - minimum) / value_span
            coordinates.append((x, CHART_HEIGHT - VERTICAL_PADDING - (offset * usable_height)))
        lines.append(
            TrendLine(
                label=source.label,
                style=source.style,
                source=source.source,
                points=" ".join(f"{x:.2f},{y:.2f}" for x, y in coordinates),
                observations=points,
                minimum=min(value for _when, value in points),
                maximum=max(value for _when, value in points),
            )
        )

    return TrendChart(
        lines=tuple(lines),
        ticks=_month_ticks(start, end),
        minimum=minimum,
        maximum=maximum,
        start=start,
        end=end,
    )


def _observations(series: Sequence[Point]) -> tuple[tuple[date, float], ...]:
    """The dated values that can be drawn, oldest first.

    An absent value is dropped rather than plotted: a gap in the source stays a
    gap in the line instead of becoming a zero or a straight run across it.
    """
    return tuple(
        sorted((_as_date(when), float(value)) for when, value in series if value is not None)
    )


def _as_date(when: date | datetime) -> date:
    return when.date() if isinstance(when, datetime) else when


def _month_label(day: date) -> str:
    return MONTH_LABELS[day.month - 1]


def _month_ticks(start: date, end: date) -> tuple[MonthTick, ...]:
    """One label per month the window covers, ends included."""
    ticks: list[MonthTick] = []
    year, month = start.year, start.month
    previous_year: int | None = None
    while (year, month) <= (end.year, end.month):
        ticks.append(
            MonthTick(
                label=MONTH_LABELS[month - 1],
                # Stated once, where the year turns, so twelve labels do not
                # carry twelve copies of it.
                year=str(year) if previous_year is not None and year != previous_year else "",
            )
        )
        previous_year = year
        month += 1
        if month > 12:
            month = 1
            year += 1
    return tuple(ticks)


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
