"""Koduleht's drawings, built whole on the server.

Every payload here is a plain ECharts option plus a `dashkoda` block carrying
what ECharts must not receive as option — the pre-rendered tooltips and the
names of the axis spellings. `frontend/src/charts.js` documents that contract and
is not changed by this module; the bundle it ships is reused as it stands.

## The payload type

`chart_figure.html` renders `apps.core.chart_payload.ChartPayload`, which is
the template's contract written once and owned by neither feature module.
While the dashboards were built on parallel branches each declared its own
copy of the shape rather than import a sibling's; with the branches
integrated, the copies were folded into that one definition. `WebsiteChart`
below is this module's name for it.

## The drawing rules this module follows

- **time is a line, a ranking is horizontal bars, a composition is one stacked
  bar.** No pie, and no dual axis pairing a count with a percentage;
- **a page title never becomes an axis label on a rotated x axis.** Rankings run
  horizontally, labels are truncated to a measured width with the whole title in
  the tooltip, and the table below carries every value untruncated;
- **every figure is spelled in Python.** A tooltip cannot write a number
  differently from the heading above it, because the browser never formats one;
- **a series with one point is not drawn.** One reading is not a trend, and a
  single dot joined to nothing invites a reader to see a direction that no
  measurement supports.
"""

from __future__ import annotations

from datetime import date

from apps.core.chart_payload import ChartPayload, Readout
from apps.core.formatting import (
    day_and_month,
    integer,
    long_date,
    month_and_year,
)

from .ga4_selectors import GRAIN_DAY, GRAIN_MONTH, TrafficSeries

GRID = {"left": 56, "right": 24, "top": 24, "bottom": 40, "containLabel": True}

#: A ranking's labels are page titles and channel names, which are long. The
#: grid gives them a fixed column and the axis truncates into it; the whole
#: string stays in the tooltip and in the table.
RANKING_GRID = {"left": 8, "right": 64, "top": 8, "bottom": 32, "containLabel": True}

#: How wide a category label may be before it is truncated. Chosen to hold a
#: readable fragment of a page title at the narrowest layout the design system
#: draws a chart in, rather than to fit the longest title the site has.
CATEGORY_LABEL_WIDTH = 260

#: A value written at the end of a bar, so the reader takes it off the drawing
#: rather than off a hover a touch screen never delivers.
BAR_LABEL = {"fontSize": 12, "fontWeight": 600, "distance": 6}


#: This module's name for the shared payload the chart template renders. The
#: shape lives in `apps.core.chart_payload`; the website keeps its own name for
#: it because 29 signatures here and in `website_page` describe their charts
#: with it, and its own wording for absence, stated per construction below.
WebsiteChart = ChartPayload

#: The website's empty-state line. GA4 charts are empty because measurement is
#: missing, not because nothing happened, and the wording says which.
EMPTY_MESSAGE = "Mõõtmisandmed puuduvad."


def _bucket_label(day: date, grain: str) -> str:
    """What one point on the time axis is called, at the grain being drawn."""
    if grain == GRAIN_MONTH:
        return month_and_year(day)
    if grain == GRAIN_DAY:
        return long_date(day)
    return f"nädal {day_and_month(day)}"


def _truncated_axis_label() -> dict:
    return {"width": CATEGORY_LABEL_WIDTH, "overflow": "truncate"}


# ---------------------------------------------------------------------------
# Traffic over time
# ---------------------------------------------------------------------------

#: The metrics the main trend can draw, one at a time. Drawing all three at once
#: puts a count of sessions beside a count of page views beside a subset of the
#: first, and the reader has to work out which line answers their question.
#: The slugs are a URL contract and stay as they were spelled when the links
#: were first shareable. Only the labels follow the seanss → külastus rename;
#: renaming a slug would quietly demote every saved `?mõõdik=seansid` link to
#: the default instead of the metric it names.
TRAFFIC_METRICS: tuple[tuple[str, str, str], ...] = (
    ("seansid", "Külastused", "sessions"),
    ("lehevaatamised", "Lehevaatamised", "page_views"),
    ("kaasatud", "Kaasatud külastused", "engaged_sessions"),
)

DEFAULT_TRAFFIC_METRIC = TRAFFIC_METRICS[0][0]

_METRIC_BY_KEY = {key: (label, attribute) for key, label, attribute in TRAFFIC_METRICS}


def parse_traffic_metric(raw: str | None) -> str:
    """The metric asked for, or sessions. Never raises."""
    key = (raw or "").strip()
    return key if key in _METRIC_BY_KEY else DEFAULT_TRAFFIC_METRIC


def traffic_trend_chart(series: TrafficSeries, *, metric: str) -> WebsiteChart:
    """How much traffic, over time, at the grain the span deserves.

    A missing bucket contributes **no point** rather than a zero, and ECharts is
    told not to join across the gap: an uncollected week is not a quiet week, and
    a line drawn through it would state a measurement nobody made.
    """
    label, attribute = _METRIC_BY_KEY[metric]

    points = []
    rows = []
    for point in series.points:
        value = getattr(point, attribute)
        when = _bucket_label(point.period_start, series.grain)
        if value is None:
            continue
        # The tooltip key travels with the datum rather than being derived in the
        # browser from an axis timestamp, which would put a timezone between a
        # point and its own readout.
        stamp = point.period_start.isoformat()
        points.append({"value": [stamp, value], "tip": stamp})
        rows.append((when, integer(value)))

    tooltips = {
        point["tip"]: {
            "title": _bucket_label(date.fromisoformat(point["tip"]), series.grain),
            "rows": [{"label": label, "value": integer(point["value"][1]), "emphasis": True}],
        }
        for point in points
    }

    # One point is a reading, not a trend. The table still carries it.
    drawable = points if len(points) >= 2 else []

    option = {
        "grid": dict(GRID),
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "line"}},
        "legend": {"show": False},
        "xAxis": {"type": "time"},
        "yAxis": {"type": "value", "min": 0},
        "series": [
            {
                "name": label,
                "type": "line",
                "smooth": False,
                "showSymbol": len(drawable) <= 40,
                "symbolSize": 6,
                # A gap stays a gap.
                "connectNulls": False,
                "data": drawable,
            }
        ],
        "dashkoda": {"axisFormat": {"y": "integer"}, "tooltip": tooltips},
    }

    return WebsiteChart(
        payload_id="koduleht-liiklus",
        title=f"{label} ajas",
        title_hidden=True,
        option=option,
        table_headers=("Periood", label),
        table_rows=tuple(rows),
        summary=(
            f"{label} {series.start:%d.%m.%Y}–{series.end:%d.%m.%Y}, {len(rows)} mõõdetud punkti."
            if series.start and series.end
            else f"{label}: mõõtmisandmed puuduvad."
        ),
        size="large",
        empty_message="Perioodil on liiga vähe mõõdetud päevi, et joont joonistada.",
    )


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Content ranking
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Attention against engagement
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Weekday pattern
# ---------------------------------------------------------------------------


__all__ = [
    "DEFAULT_TRAFFIC_METRIC",
    "TRAFFIC_METRICS",
    "Readout",
    "WebsiteChart",
    "parse_traffic_metric",
]
