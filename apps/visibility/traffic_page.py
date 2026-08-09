"""The website-traffic section of the Nähtavus page.

The overview card answers "how many visits yesterday". This answers the question
a card cannot: what has happened over thirty days, a year, or as far back as the
property goes.

Two things it must never do, both of which are easy to do by accident:

- **imply history that does not exist.** The five-year option on a property with
  two years of data shows two years and says so. Nothing pads the start of a
  chart with zeros for the period before collection began — that is not a quiet
  period, it is an unmeasured one;
- **add up people.** The period figures are sessions and page views, which are
  events and add. Where the interface wants a "users" number it gets the busiest
  single day, labelled as the busiest single day. See
  `apps.visibility.ga4_selectors` for why there is no other honest option.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

from apps.core.formatting import group_thousands, percent
from apps.dashboard.sparkline import TrendChart, TrendSource, build_trend_chart

from .ga4_selectors import (
    ChannelTotal,
    Coverage,
    PageTotal,
    TrafficSeries,
    get_channel_totals,
    get_coverage,
    get_top_pages,
    get_traffic_series,
)

#: The query parameter the period buttons carry.
PARAM_PERIOD = "periood"

#: The section of the site the "top news" table is drawn from.
NEWS_PREFIX = "/et/uudised"

#: How many rows each table shows. Long enough to be useful on a board's
#: dashboard, short enough that nobody scrolls past the point of interest.
TOP_PAGE_ROWS = 15
TOP_NEWS_ROWS = 10


@dataclass(frozen=True)
class Period:
    """One selectable window.

    `days` is `None` for "everything there is", which is the only option whose
    length is a property of the data rather than of the choice.
    """

    key: str
    label: str
    days: int | None

    @property
    def is_all(self) -> bool:
        return self.days is None


PERIODS: tuple[Period, ...] = (
    Period(key="30", label="30 päeva", days=30),
    Period(key="90", label="90 päeva", days=90),
    Period(key="1a", label="1 aasta", days=365),
    Period(key="3a", label="3 aastat", days=3 * 365),
    Period(key="5a", label="5 aastat", days=5 * 365),
    Period(key="koik", label="Kõik", days=None),
)

DEFAULT_PERIOD = PERIODS[0]

_BY_KEY = {period.key: period for period in PERIODS}


def parse_period(raw: str | None) -> Period:
    """The period asked for, or the default. Never raises.

    A hand-typed or stale value resolves to thirty days rather than to an error
    page, the same rule the membership range control follows.
    """
    return _BY_KEY.get((raw or "").strip(), DEFAULT_PERIOD)


@dataclass(frozen=True)
class PeriodOption:
    """One button: what it says, where it goes, and whether it is the one shown."""

    period: Period
    is_active: bool
    is_offered: bool

    @property
    def label(self) -> str:
        return self.period.label

    @property
    def query(self) -> str:
        return f"{PARAM_PERIOD}={self.period.key}"


def period_options(active: Period, coverage: Coverage) -> tuple[PeriodOption, ...]:
    """Every period, with the ones history cannot fill marked.

    They are marked rather than removed. A board member who looks for "5 aastat"
    and finds no such button learns nothing; one who finds it disabled learns
    that the Chamber has been measuring for less than five years, which is the
    actual answer to the question they were asking.
    """
    span = coverage.span_days
    return tuple(
        PeriodOption(
            period=period,
            is_active=period.key == active.key,
            # "Kõik" is always offered. A shorter window is offered when the
            # history can fill more than about a third of it — below that the
            # chart is mostly the empty space before collection started.
            is_offered=period.is_all or (span > 0 and span * 3 >= period.days),
        )
        for period in PERIODS
    )


def window_for(
    period: Period, coverage: Coverage, *, today: date | None = None
) -> tuple[date, date]:
    """The dates a period resolves to, clamped to what was actually measured.

    The end is the newest collected day, not today: a chart that ran to today
    would end in a flat gap the width of however late the collector is.
    """
    end = coverage.latest or (today or timezone.localdate())
    if period.is_all:
        return (coverage.earliest or end), end
    start = end - timedelta(days=period.days - 1)
    if coverage.earliest is not None:
        start = max(start, coverage.earliest)
    return start, end


@dataclass(frozen=True)
class TrafficFigure:
    """One headline figure, already spelled the way it is shown."""

    label: str
    value: str
    note: str = ""

    @property
    def has_value(self) -> bool:
        return bool(self.value)


@dataclass(frozen=True)
class TrafficSection:
    """Everything the traffic section renders."""

    coverage: Coverage
    period: Period
    options: tuple[PeriodOption, ...]
    start: date | None
    end: date | None
    series: TrafficSeries
    chart: TrendChart | None
    figures: tuple[TrafficFigure, ...]
    channels: tuple[ChannelTotal, ...]
    channel_sessions: int
    top_pages: tuple[PageTotal, ...]
    top_news: tuple[PageTotal, ...]

    @property
    def has_data(self) -> bool:
        return self.coverage.has_data and self.series.has_points

    @property
    def is_drawable(self) -> bool:
        """One point is a reading, not a trend, and is not drawn as one."""
        return self.series.is_drawable

    @property
    def grain_label(self) -> str:
        return {"day": "päevade", "week": "nädalate", "month": "kuude"}.get(
            self.series.grain, "päevade"
        )

    @property
    def coverage_note(self) -> str:
        """What the period actually covers, in words, always shown.

        The selector says "5 aastat"; this says what the Chamber has. When the
        two differ, the reader is told rather than left to infer it from where
        the line starts.
        """
        if not self.coverage.has_data:
            return ""
        return (
            f"Google Analytics andmed alates {self.coverage.earliest:%d.%m.%Y}. "
            f"Kuvatud {self.start:%d.%m.%Y}–{self.end:%d.%m.%Y}."
        )


def build_traffic_section(
    *, period_key: str | None = None, today: date | None = None
) -> TrafficSection:
    """Read the stored history once and shape it for the page."""
    coverage = get_coverage()
    period = parse_period(period_key)
    options = period_options(period, coverage)

    if not coverage.has_data:
        return TrafficSection(
            coverage=coverage,
            period=period,
            options=options,
            start=None,
            end=None,
            series=TrafficSeries(points=(), grain="day", start=None, end=None),
            chart=None,
            figures=(),
            channels=(),
            channel_sessions=0,
            top_pages=(),
            top_news=(),
        )

    start, end = window_for(period, coverage, today=today)
    series = get_traffic_series(start=start, end=end)
    channels = get_channel_totals(start=start, end=end)

    return TrafficSection(
        coverage=coverage,
        period=period,
        options=options,
        start=start,
        end=end,
        series=series,
        chart=_chart(series),
        figures=_figures(series),
        channels=channels,
        channel_sessions=sum(channel.sessions for channel in channels),
        top_pages=get_top_pages(start=start, end=end, limit=TOP_PAGE_ROWS),
        top_news=get_top_pages(start=start, end=end, limit=TOP_NEWS_ROWS, prefix=NEWS_PREFIX),
    )


def _chart(series: TrafficSeries) -> TrendChart | None:
    """Sessions and page views on one pair of axes.

    Both are event counts on the same scale of magnitude, so sharing the axis is
    a comparison rather than a drawing accident. Users are deliberately not a
    third line: the only per-period number available for them is a peak, and a
    peak drawn beside two totals reads as a third total.
    """
    sessions = tuple(
        (point.period_start, point.sessions)
        for point in series.points
        if point.sessions is not None
    )
    views = tuple(
        (point.period_start, point.page_views)
        for point in series.points
        if point.page_views is not None
    )
    sources = []
    if len(sessions) >= 2:
        sources.append(
            TrendSource(label="Seansid", style="solid", source="Google Analytics", series=sessions)
        )
    if len(views) >= 2:
        sources.append(
            TrendSource(
                label="Lehevaatamised", style="dashed", source="Google Analytics", series=views
            )
        )
    return build_trend_chart(sources) if sources else None


def _figures(series: TrafficSeries) -> tuple[TrafficFigure, ...]:
    """The four numbers above the chart, each spelled for what it is."""
    figures: list[TrafficFigure] = []

    if series.total_sessions is not None:
        figures.append(TrafficFigure(label="Seansse", value=group_thousands(series.total_sessions)))
    if series.total_page_views is not None:
        figures.append(
            TrafficFigure(label="Lehevaatamisi", value=group_thousands(series.total_page_views))
        )
    if series.peak_active_users is not None:
        figures.append(
            TrafficFigure(
                label="Kasutajaid tipppäeval",
                value=group_thousands(series.peak_active_users),
                # The label already says "peak day", and this says why there is
                # no period figure beside it. Distinct people cannot be added
                # across days, and GA4 is the only thing that can answer
                # "how many people this month".
                note="Perioodi kasutajate arv ei ole päevade summa.",
            )
        )

    engaged = [
        point.engaged_sessions for point in series.points if point.engaged_sessions is not None
    ]
    if engaged and series.total_sessions:
        figures.append(
            TrafficFigure(
                label="Kaasatud seansse",
                value=percent(100 * sum(engaged) / series.total_sessions),
            )
        )

    return tuple(figures)


__all__ = [
    "DEFAULT_PERIOD",
    "NEWS_PREFIX",
    "PARAM_PERIOD",
    "PERIODS",
    "Period",
    "PeriodOption",
    "TrafficFigure",
    "TrafficSection",
    "build_traffic_section",
    "parse_period",
    "period_options",
    "window_for",
]
