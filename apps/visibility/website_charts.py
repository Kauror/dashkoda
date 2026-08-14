"""Koduleht's drawings, built whole on the server.

Every payload here is a plain ECharts option plus a `dashkoda` block carrying
what ECharts must not receive as option — the pre-rendered tooltips and the
names of the axis spellings. `frontend/src/charts.js` documents that contract and
is not changed by this module; the bundle it ships is reused as it stands.

## Why a local payload type

`chart_figure.html` renders anything with these fields, and `apps.membership`
holds the version the Liikmeskond page builds. Importing that one would couple
two feature modules through a dataclass neither owns, so Koduleht declares its
own with the same shape. The component's contract is the shared thing; the
dataclass is not.

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

from dataclasses import dataclass, field
from datetime import date

from apps.core.formatting import (
    day_and_month,
    integer,
    long_date,
    month_and_year,
    percent,
)

from .ga4_selectors import GRAIN_DAY, GRAIN_MONTH, TrafficSeries
from .website_analytics import (
    QUADRANT_LABELS,
    EngagementMatrix,
    WebsiteChannelPerformance,
    WebsiteContentMix,
    WebsiteLanguageMix,
    WebsitePageEngagement,
    WeekdayAverage,
)

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


@dataclass(frozen=True)
class Readout:
    """One figure in a chart's analytical header, already spelled.

    `direction` is the non-colour signal and `change_label` is what a screen
    reader receives instead of an arrow glyph it cannot describe.
    """

    label: str
    value: str
    change: str = ""
    change_label: str = ""
    direction: str = ""
    note: str = ""

    @property
    def has_change(self) -> bool:
        return bool(self.change)


@dataclass(frozen=True)
class WebsiteChart:
    """One chart plus the accessible alternative that always accompanies it.

    The table is not a fallback: it stays in the document for every reader, and
    only the canvas is hidden when there is nothing to draw.
    """

    payload_id: str
    title: str
    option: dict
    table_headers: tuple[str, ...]
    table_rows: tuple[tuple, ...]
    summary: str
    empty_message: str = "Mõõtmisandmed puuduvad."
    footnotes: tuple[str, ...] = field(default_factory=tuple)
    question: str = ""
    observation_label: str = ""
    readouts: tuple[Readout, ...] = field(default_factory=tuple)
    size: str = "medium"

    @property
    def has_data(self) -> bool:
        return bool(self.table_rows)


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
TRAFFIC_METRICS: tuple[tuple[str, str, str], ...] = (
    ("seansid", "Seansid", "sessions"),
    ("lehevaatamised", "Lehevaatamised", "page_views"),
    ("kaasatud", "Kaasatud seansid", "engaged_sessions"),
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


def channel_sessions_chart(
    channels: tuple[WebsiteChannelPerformance, ...], *, site_sessions: int | None
) -> WebsiteChart:
    """Which channels brought the sessions, largest first.

    The share beside each bar is of **all site sessions**, not of the channels
    drawn. A share computed over the visible rows always adds to 100% and would
    therefore be right whatever was left out.
    """
    drawn = tuple(reversed(channels))
    data = [
        {
            "value": channel.sessions,
            "tip": channel.channel,
            "label": {
                "show": True,
                "position": "right",
                "formatter": integer(channel.sessions),
                **BAR_LABEL,
            },
        }
        for channel in drawn
    ]
    tooltips = {
        channel.channel: {
            "title": channel.channel,
            "rows": [
                {"label": "Seansid", "value": integer(channel.sessions), "emphasis": True},
                {
                    "label": "Osakaal kõigist seanssidest",
                    "value": percent(channel.share * 100) if channel.share is not None else "–",
                },
                {
                    "label": "Kaasatuse määr",
                    "value": (
                        percent(channel.engagement_rate * 100)
                        if channel.engagement_rate is not None
                        else "–"
                    ),
                },
            ],
        }
        for channel in drawn
    }

    option = {
        "grid": dict(RANKING_GRID),
        "tooltip": {"trigger": "item"},
        "legend": {"show": False},
        "xAxis": {"type": "value", "show": False},
        "yAxis": {
            "type": "category",
            "data": [channel.channel for channel in drawn],
            "axisLabel": _truncated_axis_label(),
            "axisTick": {"show": False},
            "axisLine": {"show": False},
        },
        "series": [{"type": "bar", "data": data, "barMaxWidth": 22}],
        "dashkoda": {"tooltip": tooltips},
    }

    return WebsiteChart(
        payload_id="koduleht-kanalid",
        title="Seansid kanalite kaupa",
        question="Kust külastajad tulid?",
        option=option,
        table_headers=("Kanal", "Seansid", "Osakaal", "Kaasatuse määr"),
        table_rows=tuple(
            (
                channel.channel,
                integer(channel.sessions),
                percent(channel.share * 100) if channel.share is not None else "–",
                percent(channel.engagement_rate * 100)
                if channel.engagement_rate is not None
                else "–",
            )
            for channel in channels
        ),
        summary=f"{len(channels)} kanalit, kokku {integer(site_sessions)} seanssi.",
        footnotes=("Osakaal on arvutatud kogu kodulehe seansside suhtes.",),
        size="categorical",
    )


def channel_engagement_chart(
    channels: tuple[WebsiteChannelPerformance, ...],
) -> WebsiteChart:
    """How engaged each channel's sessions were.

    Its own chart rather than a second axis on the one above: a count and a
    proportion on one pair of axes is a picture whose two halves are read at
    different scales and compared anyway.
    """
    measured = tuple(channel for channel in channels if channel.engagement_rate is not None)
    ordered = tuple(sorted(measured, key=lambda channel: channel.engagement_rate))
    data = [
        {
            "value": round(channel.engagement_rate * 100, 1),
            "tip": channel.channel,
            "label": {
                "show": True,
                "position": "right",
                "formatter": percent(channel.engagement_rate * 100),
                **BAR_LABEL,
            },
        }
        for channel in ordered
    ]
    tooltips = {
        channel.channel: {
            "title": channel.channel,
            "rows": [
                {
                    "label": "Kaasatuse määr",
                    "value": percent(channel.engagement_rate * 100),
                    "emphasis": True,
                },
                {"label": "Seansid", "value": integer(channel.sessions)},
                {
                    "label": "Kaasatud seansid",
                    "value": integer(channel.engaged_sessions)
                    if channel.engaged_sessions is not None
                    else "–",
                },
            ],
        }
        for channel in ordered
    }

    option = {
        "grid": dict(RANKING_GRID),
        "tooltip": {"trigger": "item"},
        "legend": {"show": False},
        "xAxis": {"type": "value", "show": False, "max": 100},
        "yAxis": {
            "type": "category",
            "data": [channel.channel for channel in ordered],
            "axisLabel": _truncated_axis_label(),
            "axisTick": {"show": False},
            "axisLine": {"show": False},
        },
        "series": [{"type": "bar", "data": data, "barMaxWidth": 22}],
        "dashkoda": {"tooltip": tooltips},
    }

    return WebsiteChart(
        payload_id="koduleht-kanalite-kaasatus",
        title="Kaasatud seansside osakaal kanali kaupa",
        question="Millised kanalid toovad kaasatumaid seansse?",
        option=option,
        table_headers=("Kanal", "Kaasatuse määr", "Seansid"),
        table_rows=tuple(
            (
                channel.channel,
                percent(channel.engagement_rate * 100),
                integer(channel.sessions),
            )
            for channel in sorted(measured, key=lambda c: c.engagement_rate, reverse=True)
        ),
        summary=f"Kaasatuse määr {len(measured)} kanali kohta.",
        footnotes=("Kaasatuse määr = kaasatud seansid / seansid samal perioodil.",),
        size="categorical",
    )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def _composition_chart(
    *,
    payload_id: str,
    title: str,
    question: str,
    rows: tuple,
    total: int | None,
    denominator_note: str,
    label_of,
    views_of,
    share_of,
) -> WebsiteChart:
    """One 100% stacked horizontal bar.

    Used for both compositions on the page, because the denominator is the only
    thing that differs and it is stated in the footnote rather than assumed by
    the drawing.
    """
    present = tuple(row for row in rows if views_of(row))
    series = [
        {
            "name": label_of(row),
            "type": "bar",
            "stack": "koik",
            "barMaxWidth": 44,
            "data": [
                {
                    "value": views_of(row),
                    "tip": label_of(row),
                    "label": {
                        "show": True,
                        "position": "inside",
                        "formatter": percent(share_of(row) * 100)
                        if share_of(row) is not None
                        else "",
                        **BAR_LABEL,
                    },
                }
            ],
        }
        for row in present
    ]
    tooltips = {
        label_of(row): {
            "title": label_of(row),
            "rows": [
                {"label": "Lehevaatamised", "value": integer(views_of(row)), "emphasis": True},
                {
                    "label": "Osakaal",
                    "value": percent(share_of(row) * 100) if share_of(row) is not None else "–",
                },
            ],
        }
        for row in present
    }

    option = {
        "grid": {"left": 8, "right": 8, "top": 8, "bottom": 8, "containLabel": True},
        "tooltip": {"trigger": "item"},
        "legend": {"show": True, "bottom": 0},
        "xAxis": {"type": "value", "show": False},
        "yAxis": {"type": "category", "data": [""], "show": False},
        "series": series,
        "dashkoda": {"tooltip": tooltips},
    }

    return WebsiteChart(
        payload_id=payload_id,
        title=title,
        question=question,
        option=option,
        table_headers=("Osa", "Lehevaatamised", "Osakaal"),
        table_rows=tuple(
            (
                label_of(row),
                integer(views_of(row)),
                percent(share_of(row) * 100) if share_of(row) is not None else "–",
            )
            for row in present
        ),
        summary=f"{len(present)} osa, kokku {integer(total)} lehevaatamist.",
        footnotes=(denominator_note,),
        size="categorical",
    )


def content_mix_chart(mix: WebsiteContentMix) -> WebsiteChart:
    return _composition_chart(
        payload_id="koduleht-sisujaotus",
        title="Vaadatud sisu jaotus",
        question="Millised kodulehe osad tähelepanu saavad?",
        rows=mix.rows,
        total=mix.total_page_views,
        denominator_note=(
            "Nimetajaks on kõigi järjestatavaks sisuks loetud lehtede lehevaatamised. "
            "Keelte avalehed, ostukorv, siseotsing ja veadokumendid ei ole sisu ega "
            "kuulu siia — kogu kodulehe liiklus on sellest suurem."
        ),
        label_of=lambda row: row.label,
        views_of=lambda row: row.page_views,
        share_of=lambda row: row.share,
    )


def language_chart(mix: WebsiteLanguageMix) -> WebsiteChart:
    return _composition_chart(
        payload_id="koduleht-keeled",
        title="Lehevaatamised sisukeele järgi",
        question="Milliseid keeleversioone loetakse?",
        rows=mix.rows,
        total=mix.total_page_views,
        denominator_note=(
            "Nimetajaks on kõik mõõdetud lehevaatamised. Näitab vaadatud lehe "
            "keeleversiooni, mitte külastaja rahvust, riiki ega eelistatud keelt."
        ),
        label_of=lambda row: row.label,
        views_of=lambda row: row.page_views,
        share_of=lambda row: row.share,
    )


# ---------------------------------------------------------------------------
# Content ranking
# ---------------------------------------------------------------------------


def top_pages_chart(rows: tuple, *, total_page_views: int | None) -> WebsiteChart:
    """The most-read content of the period, as a horizontal ranking.

    `rows` are `ContentPerformanceRow`s, so a page carries the title DashKoda
    holds on authority or its own path — never a title invented from a slug.
    """
    drawn = tuple(reversed(rows))
    data = [
        {
            "value": row.page_views,
            "tip": row.path,
            "label": {
                "show": True,
                "position": "right",
                "formatter": integer(row.page_views),
                **BAR_LABEL,
            },
        }
        for row in drawn
    ]
    tooltips = {
        row.path: {
            "title": row.label,
            "rows": [
                {"label": "Lehevaatamised", "value": integer(row.page_views), "emphasis": True},
                {
                    "label": "Osakaal sisu vaatamistest",
                    "value": percent(100 * row.page_views / total_page_views)
                    if total_page_views
                    else "–",
                },
            ],
            "note": row.path,
        }
        for row in drawn
    }

    option = {
        "grid": dict(RANKING_GRID),
        "tooltip": {"trigger": "item"},
        "legend": {"show": False},
        "xAxis": {"type": "value", "show": False},
        "yAxis": {
            "type": "category",
            "data": [row.label for row in drawn],
            "axisLabel": _truncated_axis_label(),
            "axisTick": {"show": False},
            "axisLine": {"show": False},
        },
        "series": [{"type": "bar", "data": data, "barMaxWidth": 20}],
        "dashkoda": {"tooltip": tooltips},
    }

    return WebsiteChart(
        payload_id="koduleht-enim-vaadatud",
        title="Enim vaadatud sisu",
        question="Mida perioodil kõige rohkem loeti?",
        option=option,
        table_headers=("Leht", "Tüüp", "Lehevaatamised", "Osakaal"),
        table_rows=tuple(
            (
                row.label,
                row.type_label or "–",
                integer(row.page_views),
                percent(100 * row.page_views / total_page_views) if total_page_views else "–",
            )
            for row in rows
        ),
        summary=f"{len(rows)} enim vaadatud sisulehte.",
        size="categorical",
    )


# ---------------------------------------------------------------------------
# Attention against engagement
# ---------------------------------------------------------------------------


def engagement_matrix_chart(
    matrix: EngagementMatrix, *, labels: dict[str, str], limit: int
) -> WebsiteChart:
    """Page views against engagement seconds per view, split at both medians.

    The two reference lines are the medians of the eligible pages themselves, so
    the quadrants describe this site in this window rather than a benchmark from
    somewhere else. Both thresholds are drawn, printed in the footnotes and
    repeated in the methodology, so the rule a page was filed under can be read.

    The quadrant names describe measurements. A page with many views and shorter
    engagement may be answering a question quickly, may be reached by the wrong
    audience or may be thin, and nothing in this data separates those.
    """
    measured = tuple(page for page in matrix.pages if page.seconds_per_view is not None)
    drawn = measured[:limit]

    def _title(page: WebsitePageEngagement) -> str:
        return labels.get(page.path, page.path)

    data = [
        {
            "value": [page.page_views, round(page.seconds_per_view, 1)],
            "tip": page.path,
        }
        for page in drawn
    ]
    tooltips = {
        page.path: {
            "title": _title(page),
            "rows": [
                {"label": "Lehevaatamised", "value": integer(page.page_views), "emphasis": True},
                {
                    "label": "Kaasatuse aeg / lehevaatamine",
                    "value": f"{page.seconds_per_view:.0f} s".replace(".", ","),
                },
                {
                    "label": "Rühm",
                    "value": QUADRANT_LABELS.get(matrix.quadrant_of(page), "–"),
                },
            ],
            "note": page.path,
        }
        for page in drawn
    }

    mark_lines = []
    if matrix.median_page_views is not None:
        mark_lines.append(
            {"xAxis": round(matrix.median_page_views, 2), "name": "Vaatamiste mediaan"}
        )
    if matrix.median_seconds_per_view is not None:
        mark_lines.append(
            {"yAxis": round(matrix.median_seconds_per_view, 2), "name": "Kaasatuse mediaan"}
        )

    option = {
        "grid": {"left": 56, "right": 32, "top": 24, "bottom": 48, "containLabel": True},
        "tooltip": {"trigger": "item"},
        "legend": {"show": False},
        "xAxis": {
            "type": "value",
            "name": "Lehevaatamised",
            "nameLocation": "middle",
            "nameGap": 28,
        },
        "yAxis": {
            "type": "value",
            "name": "Sekundit / vaatamine",
            "nameLocation": "middle",
            "nameGap": 40,
        },
        "series": [
            {
                "type": "scatter",
                "symbolSize": 9,
                "data": data,
                "markLine": {
                    "silent": True,
                    "symbol": "none",
                    "label": {"show": True, "formatter": "{b}"},
                    "data": mark_lines,
                },
            }
        ],
        "dashkoda": {"axisFormat": {"x": "integer"}, "tooltip": tooltips},
    }

    return WebsiteChart(
        payload_id="koduleht-kaasatuse-maatriks",
        title="Tähelepanu ja kaasatus",
        question="Millised lehed ühendavad liiklust ja pikemat kaasatust?",
        option=option,
        table_headers=("Leht", "Lehevaatamised", "Sekundit / vaatamine", "Rühm"),
        table_rows=tuple(
            (
                _title(page),
                integer(page.page_views),
                f"{page.seconds_per_view:.0f}".replace(".", ","),
                QUADRANT_LABELS.get(matrix.quadrant_of(page), "–"),
            )
            for page in measured
        ),
        summary=(
            f"{len(measured)} lehte, mis ületasid {matrix.minimum_page_views} lehevaatamise piiri."
        ),
        footnotes=(
            (
                f"Piirid on selle perioodi mediaanid: {integer(round(matrix.median_page_views))} "
                f"lehevaatamist ja {matrix.median_seconds_per_view:.0f} sekundit vaatamise kohta."
            ).replace(".", ",")
            if matrix.has_data
            else "Mediaanide arvutamiseks ei olnud piisavalt mõõdetud lehti.",
            (
                f"Arvestatud on lehti, millel oli perioodil vähemalt "
                f"{matrix.minimum_page_views} lehevaatamist."
            ),
            "Rühmad kirjeldavad mõõdetud käitumist, mitte sisu kvaliteeti.",
        ),
        size="medium",
        empty_message="Piisava mahuga lehti ei olnud, et rühmi eristada.",
    )


# ---------------------------------------------------------------------------
# Weekday pattern
# ---------------------------------------------------------------------------


def weekday_chart(pattern: tuple[WeekdayAverage, ...], *, names: tuple[str, ...]) -> WebsiteChart:
    """Mean sessions per weekday. Descriptive, never causal."""
    data = [
        {
            "value": round(day.mean_sessions),
            "tip": str(day.weekday),
            "label": {
                "show": True,
                "position": "top",
                "formatter": integer(round(day.mean_sessions)),
                **BAR_LABEL,
            },
        }
        for day in pattern
    ]
    tooltips = {
        str(day.weekday): {
            "title": names[day.weekday - 1].capitalize(),
            "rows": [
                {
                    "label": "Keskmine seansside arv",
                    "value": integer(round(day.mean_sessions)),
                    "emphasis": True,
                },
                {"label": "Mõõdetud päevi", "value": integer(day.observed_days)},
            ],
        }
        for day in pattern
    }

    option = {
        "grid": dict(GRID),
        "tooltip": {"trigger": "item"},
        "legend": {"show": False},
        "xAxis": {
            "type": "category",
            "data": [names[day.weekday - 1] for day in pattern],
            "axisTick": {"show": False},
        },
        "yAxis": {"type": "value", "min": 0},
        "series": [{"type": "bar", "data": data, "barMaxWidth": 40}],
        "dashkoda": {"axisFormat": {"y": "integer"}, "tooltip": tooltips},
    }

    return WebsiteChart(
        payload_id="koduleht-nadalapaevad",
        title="Nädalapäevade muster",
        question="Kuidas jaguneb liiklus nädalapäevade vahel?",
        option=option,
        table_headers=("Nädalapäev", "Keskmine seansside arv", "Mõõdetud päevi"),
        table_rows=tuple(
            (
                names[day.weekday - 1].capitalize(),
                integer(round(day.mean_sessions)),
                integer(day.observed_days),
            )
            for day in pattern
        ),
        summary="Keskmine seansside arv mõõdetud nädalapäevade kohta.",
        footnotes=(
            "Kirjeldav jaotus mõõdetud päevadest. See ei ütle, et nädalapäev "
            "põhjustaks liikluse erinevust.",
        ),
        size="categorical",
    )


__all__ = [
    "DEFAULT_TRAFFIC_METRIC",
    "TRAFFIC_METRICS",
    "Readout",
    "WebsiteChart",
    "channel_engagement_chart",
    "channel_sessions_chart",
    "content_mix_chart",
    "engagement_matrix_chart",
    "language_chart",
    "parse_traffic_metric",
    "top_pages_chart",
    "weekday_chart",
]
