"""Server-prepared chart payloads for the internal membership history.

The browser receives finished data and draws it. It never filters, never fills a
gap and never decides what is safe to show — those are quality decisions and
they belong to `quality.py` and the selectors. A payload here is read from a
non-executable `application/json` block, which is why no chart needs an inline
script or a relaxed Content Security Policy.

Every chart is built with its accessible alternative in the same object: a short
text summary and the identical values as table rows. The table is not a fallback
that appears when something breaks — it stays in the document, and a reader who
never sees the canvas gets the same numbers.

Two rules are absolute here:

- an absent value produces **no point**. There is no zero substitution and no
  interpolation across a gap;
- a provisional value is labelled as provisional wherever it appears, including
  in the table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from apps.core.formatting import (
    integer,
    long_date,
    percent,
    percentage,
    percentage_points,
    signed_integer,
    whole_euros,
)

from .analytics import compare_with, share_change, value_domain
from .internal_selectors import InternalTrend, MonthlyValue
from .models import QualityStatus, SizeBand

# Board reports number their months in Roman numerals, and the monthly chart
# keeps that convention so the axis matches the source people are used to.
MONTH_LABELS: tuple[str, ...] = (
    "I",
    "II",
    "III",
    "IV",
    "V",
    "VI",
    "VII",
    "VIII",
    "IX",
    "X",
    "XI",
    "XII",
)

GRID = {"left": 56, "right": 24, "top": 32, "bottom": 40, "containLabel": True}


@dataclass(frozen=True)
class Readout:
    """One figure in a chart's analytical header.

    Every string arrives formatted. A template that had to decide how to write a
    signed percentage would be the second place that decision lived, and the two
    would drift the first time one of them changed.

    `direction` is the non-colour signal — a reader who cannot separate the hues
    still gets the sense of the change from the glyph beside it, and a reader
    using a screen reader gets it from `change_label`.
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
class TooltipRow:
    """One line of a pre-rendered tooltip.

    The value is a finished string, built by the same formatters the readouts
    use. Nothing about how a figure is written crosses into JavaScript, so the
    tooltip cannot spell a number differently from the header above it, and the
    browser never has to know what a percentage point is.
    """

    label: str
    value: str
    emphasis: bool = False


@dataclass(frozen=True)
class ChartPayload:
    """One chart plus the accessible alternative that always accompanies it.

    The fields beyond `option` are the analytical frame: the question the chart
    answers, the two or three figures that answer it before the reader looks at
    the drawing, and the date the drawing describes. A chart is free to use none
    of them — the movement charts have no time controls and no comparison — and
    a template renders only what is present.
    """

    payload_id: str
    title: str
    option: dict
    table_headers: tuple[str, ...]
    table_rows: tuple[tuple, ...]
    summary: str
    empty_message: str = "Andmed puuduvad."
    footnotes: tuple[str, ...] = field(default_factory=tuple)
    question: str = ""
    observation_label: str = ""
    readouts: tuple[Readout, ...] = field(default_factory=tuple)
    # A design-system size name, not a pixel count: `chart_figure.html` maps it
    # to a height class. A distribution chart with four categories and a
    # five-year time series do not want the same frame, and JavaScript is not
    # needed to say so.
    size: str = "medium"

    @property
    def has_data(self) -> bool:
        return bool(self.table_rows)


def _iso(value: date) -> str:
    return value.isoformat()


def _number(value) -> float | int | None:
    """ECharts reads JSON numbers; Decimal is not one."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _base_option(*, legend: bool = True) -> dict:
    return {
        "grid": dict(GRID),
        "tooltip": {"trigger": "axis"},
        "legend": {"show": legend, "bottom": 0},
        "animation": True,
    }


# --------------------------------------------------------------------------
# A. Total and paid members over time
# --------------------------------------------------------------------------


# Beyond this many observations the per-point markers stop helping and become
# the picture: a five-year monthly history is sixty dots on one line. The line
# still carries every point and the axis pointer still lands on each of them, so
# nothing is hidden — only the dots go.
SYMBOL_DENSITY_LIMIT = 24


def _provisional_dates(trend: InternalTrend) -> frozenset[date]:
    return frozenset(
        point.observation_date
        for point in trend.points
        if point.observation.quality_status == QualityStatus.PROVISIONAL
    )


def _direction(value) -> str:
    """The non-colour signal for a change.

    A reader who cannot separate the hues still gets the sense from the glyph
    beside the figure, and a screen reader gets it from the change label.
    """
    if value is None or value == 0:
        return "flat"
    return "up" if value > 0 else "down"


def _point(day: date, value, *, provisional: bool) -> dict:
    """One drawn point, carrying the key its tooltip is stored under.

    The key travels with the datum rather than being derived in the browser from
    an axis timestamp, which would put a timezone between a point and its own
    readout.

    A provisional observation is drawn hollow. That is the whole visual rule —
    not a warning colour, because an estimate that will firm up next month is
    not an error, and the design system's warning hue already means something
    else.
    """
    item = {"value": [_iso(day), _number(value)], "tip": _iso(day)}
    if provisional:
        item["symbol"] = "circle"
        item["symbolSize"] = 8
        item["itemStyle"] = {"color": "transparent", "borderWidth": 2}
    return item


def total_and_paid_chart(trend: InternalTrend) -> ChartPayload:
    """Is the membership growing or shrinking, and how much of it has paid?

    Two lines on a real time axis. A time axis rather than evenly spaced
    categories, because the observations are genuinely irregular — some months
    carry a board report and some carry none — and spacing them evenly would
    misrepresent when the Chamber actually counted.

    The legend is gone. Two series labelled directly at their last point cost
    the reader no glance away from the line.
    """
    provisional = _provisional_dates(trend)
    total_series = trend.series("total_members")
    paid_series = trend.series("paid_members")

    total = [_point(day, value, provisional=day in provisional) for day, value in total_series]
    paid = [_point(day, value, provisional=day in provisional) for day, value in paid_series]

    # One domain for both lines. The paid count is read against the total, and
    # a second axis would let the gap between them mean nothing.
    domain = value_domain(tuple(value for _, value in total_series + paid_series))
    show_symbols = max(len(total), len(paid), 1) <= SYMBOL_DENSITY_LIMIT

    y_axis = {"type": "value", "name": "Liikmeid"}
    if domain is not None:
        y_axis["min"] = _number(domain.minimum)
        y_axis["max"] = _number(domain.maximum)

    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {"type": "time"},
            "yAxis": y_axis,
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "line"}},
            "series": [
                {
                    "name": "Liikmeid kokku",
                    "type": "line",
                    "showSymbol": show_symbols,
                    "symbolSize": 6,
                    "lineStyle": {"width": 2.5},
                    "endLabel": {"show": True, "formatter": "{a}"},
                    # Absent values are not in the data at all, so there is
                    # nothing to connect across. This flag makes that explicit.
                    "connectNulls": False,
                    "data": total,
                },
                {
                    "name": "Tasunud liikmeid",
                    "type": "line",
                    "showSymbol": show_symbols,
                    "symbolSize": 6,
                    # Dashed as well as differently coloured, so the two lines
                    # stay separable in greyscale and for a reader who cannot
                    # tell the hues apart.
                    "lineStyle": {"width": 2, "type": "dashed"},
                    "endLabel": {"show": True, "formatter": "{a}"},
                    "connectNulls": False,
                    "data": paid,
                },
            ],
            "dashkoda": {"tooltip": _trend_tooltips(trend, provisional)},
        }
    )

    rows = []
    for point in trend.points:
        total_value = point.value("total_members")
        paid_value = point.value("paid_members")
        if total_value is None and paid_value is None:
            continue
        rows.append(
            (
                point.observation_date,
                total_value,
                paid_value,
                percentage(point.paid_member_share_pct),
                point.observation.get_quality_status_display(),
            )
        )

    footnotes = []
    if trend.withheld_metric_points:
        footnotes.append("Osad ajaloolised punktid on vastuolude tõttu graafikult välja jäetud.")
    if provisional:
        footnotes.append("Esialgsed vaatlused on graafikul tühja markeriga.")

    return ChartPayload(
        payload_id="internal-membership-trend",
        title="Liikmeid kokku ja tasunud liikmeid",
        question="Kas liikmeskond kasvab või kahaneb ja kui suur osa liikmetest on tasunud?",
        option=option,
        size="large",
        readouts=_trend_readouts(trend, provisional),
        observation_label=(
            f"Seisuga {long_date(trend.points[-1].observation_date)}" if trend.points else ""
        ),
        table_headers=("Kuupäev", "Liikmeid kokku", "Tasunud liikmeid", "Tasunute osakaal", "Olek"),
        table_rows=tuple(rows),
        summary=(
            f"Joongraafik {len(total)} liikmete koguarvu ja {len(paid)} tasunud liikmete "
            "vaatlusega. Kuvatakse kinnitatud või eelistatud vaatlus."
        ),
        empty_message="Sisemise aruande vaatlusi ei ole veel imporditud.",
        footnotes=tuple(footnotes),
    )


def _trend_tooltips(trend: InternalTrend, provisional: frozenset[date]) -> dict:
    """One finished readout per observation date.

    Built here rather than in the browser so a tooltip cannot spell a figure
    differently from the header above it, and so the gap between the two counts
    is named rather than left for the reader to subtract.
    """
    tooltips = {}
    for point in trend.points:
        total = point.value("total_members")
        paid = point.value("paid_members")
        if total is None and paid is None:
            continue
        rows = [
            TooltipRow(label="Liikmeid kokku", value=integer(total), emphasis=True),
            TooltipRow(label="Tasunud liikmeid", value=integer(paid)),
        ]
        if total is not None and paid is not None:
            # Named "Vahe" and nothing more. The board report says how many
            # members there are and how many have paid; it does not say the
            # remainder is an unpaid invoice, and calling it one would be this
            # page inventing a meaning the source never carried.
            rows.append(TooltipRow(label="Vahe", value=integer(total - paid)))
        share = point.paid_member_share_pct
        if share is not None:
            rows.append(TooltipRow(label="Tasunute osakaal", value=percent(share)))
        tooltips[_iso(point.observation_date)] = {
            "title": long_date(point.observation_date),
            "rows": [
                {"label": row.label, "value": row.value, "emphasis": row.emphasis} for row in rows
            ],
            "note": "Olek: esialgne" if point.observation_date in provisional else "",
        }
    return tooltips


def _trend_readouts(trend: InternalTrend, provisional: frozenset[date]) -> tuple[Readout, ...]:
    """The figures that answer the question before the chart is looked at.

    Each comparison is against the observation nearest a year before the latest
    one, and `apps.membership.analytics` refuses rather than reaches when
    nothing is near enough. A readout whose comparison is unavailable still
    shows its value, and says why the comparison is missing.
    """
    if not trend.points:
        return ()

    latest = trend.points[-1]
    when = latest.observation_date
    readouts = []

    for label, field_name in (
        ("Liikmeid kokku", "total_members"),
        ("Tasunud liikmeid", "paid_members"),
    ):
        value = latest.value(field_name)
        if value is None:
            continue
        comparison = compare_with(
            value, when, trend.series(field_name), provisional_dates=provisional
        )
        readouts.append(
            Readout(
                label=label,
                value=integer(value),
                change=signed_integer(comparison.absolute) if comparison.is_available else "",
                change_label=(
                    f"{signed_integer(comparison.absolute)} võrreldes "
                    f"{long_date(comparison.baseline_date)}"
                    if comparison.is_available
                    else comparison.unavailable_reason
                ),
                direction=_direction(comparison.absolute) if comparison.is_available else "",
                note="" if comparison.is_available else comparison.unavailable_reason,
            )
        )

    share = latest.paid_member_share_pct
    if share is not None:
        shares = tuple(
            (point.observation_date, point.paid_member_share_pct)
            for point in trend.points
            if point.paid_member_share_pct is not None
        )
        comparison = compare_with(share, when, shares, provisional_dates=provisional)
        points = share_change(share, comparison.baseline) if comparison.is_available else None
        readouts.append(
            Readout(
                label="Tasunute osakaal",
                value=percent(share),
                # A share moves in percentage points, not percent: the two are
                # different numbers describing the same movement.
                change=percentage_points(points) if points is not None else "",
                change_label=(
                    f"{percentage_points(points)} võrreldes {long_date(comparison.baseline_date)}"
                    if points is not None
                    else comparison.unavailable_reason
                ),
                direction=_direction(points),
                note="" if comparison.is_available else comparison.unavailable_reason,
            )
        )

    return tuple(readouts)


# --------------------------------------------------------------------------
# B. Monthly new members
# --------------------------------------------------------------------------


def monthly_new_members_chart(by_year: dict[int, tuple[MonthlyValue, ...]]) -> ChartPayload:
    """One series per selected year across months I–XII.

    A conflicted month and a month nobody reported are both simply absent. This
    is the chart where substituting zero would do the most damage, because a
    zero here reads as "nobody joined that month", which no source ever said.
    """
    series = []
    rows = []
    provisional_seen = False
    conflict_seen = False

    for year in sorted(by_year):
        values = {value.calendar_month: value for value in by_year[year]}
        data = []
        for month in range(1, 13):
            value = values.get(month)
            if value is None or not value.is_chartable:
                if value is not None and value.is_conflict:
                    conflict_seen = True
                data.append(None)
                continue
            if value.is_provisional:
                provisional_seen = True
            data.append(value.new_members)
        series.append(
            {
                "name": str(year),
                "type": "line",
                "showSymbol": True,
                "connectNulls": False,
                "data": data,
            }
        )

        for month in range(1, 13):
            value = values.get(month)
            if value is None:
                continue
            rows.append(
                (
                    year,
                    MONTH_LABELS[month - 1],
                    value.new_members,
                    _monthly_status_label(value),
                )
            )

    option = _base_option()
    option.update(
        {
            "xAxis": {"type": "category", "data": list(MONTH_LABELS)},
            "yAxis": {"type": "value", "name": "Uusi liikmeid"},
            "series": series,
        }
    )

    footnotes = []
    if provisional_seen:
        footnotes.append("Jooksva kuu väärtus on esialgne.")
    if conflict_seen:
        footnotes.append("Vastuolulisi kuid ei kuvata graafikul ja neid ei asendata nulliga.")

    return ChartPayload(
        payload_id="internal-membership-monthly",
        title="Uusi liikmeid kuude lõikes",
        option=option,
        table_headers=("Aasta", "Kuu", "Uusi liikmeid", "Olek"),
        table_rows=tuple(rows),
        summary=(
            f"Joongraafik {len(series)} aasta kohta kuude I–XII lõikes. "
            "Puuduvad ja vastuolulised kuud on välja jäetud."
        ),
        empty_message="Kuude kaupa andmeid ei ole veel imporditud.",
        footnotes=tuple(footnotes),
    )


def _monthly_status_label(value: MonthlyValue) -> str:
    if value.is_conflict:
        return "Vastuoluline – väärtust ei kuvata"
    if value.is_provisional:
        return "Esialgne"
    return "Kinnitatud"


# --------------------------------------------------------------------------
# C. Membership-fee collection
# --------------------------------------------------------------------------


def fee_collection_chart(rows: tuple[dict, ...]) -> ChartPayload:
    """Received against budget, with both percentages kept separate.

    No circular gauge: a gauge shows one number well and this is three. When the
    reported and the calculated percentage differ, both are shown rather than
    one being silently preferred.
    """
    dates = [_iso(row["observation_date"]) for row in rows]
    received = [_number(row["received"]) for row in rows]
    budget = [_number(row["budget"]) for row in rows]
    reported_pct = [_number(row["reported_pct"]) for row in rows]
    computed_pct = [_number(row["computed_pct"]) for row in rows]

    option = _base_option()
    option.update(
        {
            "xAxis": {"type": "category", "data": dates},
            "yAxis": [
                {"type": "value", "name": "EUR"},
                {"type": "value", "name": "%", "position": "right"},
            ],
            "series": [
                {"name": "Laekunud", "type": "bar", "data": received},
                {"name": "Eelarve", "type": "bar", "data": budget},
                {
                    "name": "Raporteeritud %",
                    "type": "line",
                    "yAxisIndex": 1,
                    "connectNulls": False,
                    "data": reported_pct,
                },
                {
                    "name": "Arvutatud %",
                    "type": "line",
                    "yAxisIndex": 1,
                    "connectNulls": False,
                    "data": computed_pct,
                },
            ],
        }
    )

    return ChartPayload(
        payload_id="internal-membership-fees",
        title="Liikmemaksu laekumine",
        option=option,
        table_headers=(
            "Kuupäev",
            "Laekunud (EUR)",
            "Eelarve (EUR)",
            "Raporteeritud %",
            "Arvutatud %",
        ),
        table_rows=tuple(
            (
                row["observation_date"],
                whole_euros(row["received"]),
                whole_euros(row["budget"]),
                percentage(row["reported_pct"]),
                percentage(row["computed_pct"]),
            )
            for row in rows
        ),
        summary=(
            f"Tulpgraafik laekumisest ja eelarvest {len(rows)} vaatluse kohta, "
            "koos raporteeritud ja arvutatud protsendiga."
        ),
        empty_message="Liikmemaksu andmeid ei ole veel imporditud.",
    )


# --------------------------------------------------------------------------
# D. Joined versus removed by company size
# --------------------------------------------------------------------------


def size_movement_chart(rows: tuple[dict, ...], *, observation_date: date | None) -> ChartPayload:
    """Diverging horizontal bars: removed to the left, joined to the right.

    The removed series is negated for drawing only. The table and the tooltip
    both show the real positive count, because nobody reports "minus eleven
    members left".
    """
    labels = [row["label"] for row in rows]
    joined = [_number(row["joined"]) for row in rows]
    removed = [None if row["removed"] is None else -_number(row["removed"]) for row in rows]

    option = _base_option()
    option.update(
        {
            "xAxis": {"type": "value", "name": "Liikmeid"},
            "yAxis": {"type": "category", "data": labels, "inverse": True},
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "series": [
                {"name": "Lahkunud", "type": "bar", "stack": "movement", "data": removed},
                {"name": "Liitunud", "type": "bar", "stack": "movement", "data": joined},
            ],
        }
    )

    footnotes: tuple[str, ...] = ()
    if any(row["band"] == SizeBand.SUPPORTER for row in rows):
        footnotes = ("Toetajaliige ei ole töötajate arvu klass ja on loetelus eraldi.",)

    return ChartPayload(
        payload_id="internal-membership-size-movement",
        title="Liitunud ja lahkunud suurusklassiti",
        option=option,
        table_headers=("Suurusklass", "Liitunud", "Lahkunud"),
        table_rows=tuple((row["label"], row["joined"], row["removed"]) for row in rows),
        summary=(
            "Vastandsuunaline tulpgraafik: lahkunud vasakul, liitunud paremal, "
            f"{len(rows)} suurusklassi kohta"
            + (f" seisuga {observation_date:%d.%m.%Y}." if observation_date else ".")
        ),
        empty_message="Suurusklasside jaotust selle vaatluse kohta ei ole.",
        footnotes=footnotes,
    )


# --------------------------------------------------------------------------
# E. Removal reasons
# --------------------------------------------------------------------------


def removal_reasons_chart(rows: tuple[dict, ...], *, observation_date: date | None) -> ChartPayload:
    """Horizontal bars with counts, and shares in the table.

    Not a pie: five categories of similar size are hard to compare as angles,
    and the design system offers no pie component to justify one.
    """
    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {"type": "value", "name": "Liikmeid"},
            "yAxis": {"type": "category", "data": [row["label"] for row in rows], "inverse": True},
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "series": [
                {
                    "name": "Lahkunuid",
                    "type": "bar",
                    "data": [_number(row["count"]) for row in rows],
                }
            ],
        }
    )

    return ChartPayload(
        payload_id="internal-membership-removal-reasons",
        title="Lahkumise põhjused",
        option=option,
        table_headers=("Põhjus", "Liikmeid", "Osakaal"),
        table_rows=tuple(
            (row["label"], row["count"], percentage(row["share_pct"])) for row in rows
        ),
        summary=(
            f"Horisontaalne tulpgraafik {len(rows)} lahkumise põhjuse kohta"
            + (f" seisuga {observation_date:%d.%m.%Y}." if observation_date else ".")
        ),
        empty_message="Lahkumise põhjuseid selle vaatluse kohta ei ole.",
    )
