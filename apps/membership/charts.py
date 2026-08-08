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

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from apps.core.formatting import (
    MONTH_ABBREVIATIONS,
    euros,
    integer,
    long_date,
    percent,
    percentage,
    percentage_points,
    signed_integer,
)

from .analytics import compare_with, pick_comparable, share_change, value_domain
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


# The reference line every year is read against.
BUDGET_TARGET_PCT = 100

# How far apart two observations may sit in the calendar year and still be
# called the same point in it. The same reasoning as the year-over-year
# tolerance: close enough to be the same season, far enough to survive a report
# that arrived a month late.
SEASON_TOLERANCE_DAYS = 45

# Years drawn behind the current one. More than three and the muted lines stop
# being context and become a thicket.
COMPARISON_YEARS = 3


def _year_position(day: date) -> float:
    """Where a date sits in its calendar year, as a month offset.

    31 July becomes 6.97 — just short of August. This is what lets several years
    share one axis: each is drawn against its own progress through the year
    rather than against an absolute date.
    """
    _, days_in_month = monthrange(day.year, day.month)
    return round((day.month - 1) + (day.day - 1) / days_in_month, 4)


def _same_season(day: date, *, year: int) -> date:
    """The same month and day in another year, for comparing like with like."""
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


def fee_collection_chart(rows: tuple[dict, ...]) -> ChartPayload:
    """Is fee collection tracking towards the annual budget, and against history?

    Replaces a chart that drew received euros, budgeted euros, the reported
    percentage and the computed percentage as four series across two y axes. A
    reader had to work out which axis each series belonged to before they could
    read any of it, and the two percentage lines invited a comparison the data
    does not support — they are the same quantity from two sources, not two
    quantities.

    What is drawn instead is one measure, budget completion, with each year as
    its own line across the calendar year. That is the question the board asks:
    are we further along than we were this time last year.

    The completion drawn is the one **implied by the reported amounts**.
    `quality.py` withholds the reported percentage when it disagrees with those
    amounts, so the amounts are what survives a disagreement and the percentage
    derived from them is the measure that can always be drawn. The reported
    figure keeps its own column in the table, and a disagreement is disclosed
    rather than quietly resolved.
    """
    drawable = [
        row for row in rows if row["computed_pct"] is not None and not row["is_year_precision"]
    ]
    by_year: dict[int, list[dict]] = {}
    for row in drawable:
        by_year.setdefault(row["observation_date"].year, []).append(row)

    years = sorted(by_year)
    current_year = years[-1] if years else None
    # Oldest first, so the current year is drawn last and sits on top.
    drawn_years = years[-(COMPARISON_YEARS + 1) :]

    series = []
    for year in drawn_years:
        is_current = year == current_year
        series.append(
            {
                "name": str(year),
                "type": "line",
                "showSymbol": True,
                "symbolSize": 7 if is_current else 5,
                "connectNulls": False,
                # The current year is the subject; the others are context. One
                # strong line against muted ones rather than a rainbow in which
                # every year competes for the same attention.
                "lineStyle": (
                    {"width": 2.5}
                    if is_current
                    else {"width": 1.5, "type": "dashed", "opacity": 0.6}
                ),
                "itemStyle": {} if is_current else {"opacity": 0.6},
                "endLabel": {"show": True, "formatter": "{a}"},
                "z": 3 if is_current else 2,
                "data": [
                    {
                        "value": [
                            _year_position(row["observation_date"]),
                            _number(row["computed_pct"]),
                        ],
                        "tip": _iso(row["observation_date"]),
                    }
                    for row in sorted(by_year[year], key=lambda item: item["observation_date"])
                ],
                **(
                    {
                        "markLine": {
                            "silent": True,
                            "symbol": "none",
                            "label": {"formatter": "Aastaeelarve"},
                            "data": [{"yAxis": BUDGET_TARGET_PCT}],
                        }
                    }
                    if is_current
                    else {}
                ),
            }
        )

    highest = max(
        [_number(row["computed_pct"]) for row in drawable] + [float(BUDGET_TARGET_PCT)],
        default=float(BUDGET_TARGET_PCT),
    )

    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {
                "type": "value",
                "min": 0,
                "max": 11,
                "interval": 1,
                "axisLabel": {"showMaxLabel": True},
            },
            "yAxis": {
                "type": "value",
                "name": "Eelarve täitmine",
                # Zero is the right floor here, unlike the membership trend:
                # completion is a proportion of a budget and starts the year at
                # nothing. The ceiling clears the target so exceeding it is
                # visible rather than clipped.
                "min": 0,
                "max": max(BUDGET_TARGET_PCT + 10, int(highest) + 10),
            },
            "tooltip": {"trigger": "item"},
            "series": series,
            "dashkoda": {
                "tooltip": _fee_tooltips(by_year, current_year),
                # A finite list the browser indexes into. No date logic crosses
                # over; the axis is 0–11 and these are its twelve labels.
                "axisLabels": {"x": list(MONTH_ABBREVIATIONS)},
            },
        }
    )

    footnotes = []
    if any(row["reported_withheld"] for row in rows):
        footnotes.append(
            "Mõne vaatluse raporteeritud protsent ei ühti summadega ja on kõrvale jäetud; "
            "graafik kasutab summadest arvutatud täitmist."
        )
    if any(row["is_year_precision"] for row in rows):
        footnotes.append(
            "Aastatäpsusega vaatlusi ei ole graafikule kantud, sest neil ei ole kuupäeva."
        )

    return ChartPayload(
        payload_id="internal-membership-fees",
        title="Liikmemaksu laekumine eelarvest",
        question=(
            "Kas liikmemaksu laekumine liigub aastaeelarve täitmise suunas ja kuidas "
            "see võrdub varasemate aastatega?"
        ),
        option=option,
        size="large",
        readouts=_fee_readouts(by_year, current_year),
        observation_label=(
            f"Seisuga {long_date(by_year[current_year][-1]['observation_date'])}"
            if current_year
            else ""
        ),
        table_headers=(
            "Kuupäev",
            "Laekunud",
            "Aastaeelarve",
            "Täitmine (summadest)",
            "Raporteeritud %",
        ),
        table_rows=tuple(
            (
                row["observation_date"],
                euros(row["received"]),
                euros(row["budget"]),
                percent(row["computed_pct"]),
                percent(row["reported_pct"]) if row["reported_pct"] is not None else None,
            )
            for row in rows
        ),
        summary=(
            f"Joongraafik eelarve täitmisest {len(drawn_years)} aasta kohta kalendriaasta "
            "lõikes; jooksev aasta on esile tõstetud ja 100% on aastaeelarve."
        ),
        empty_message="Liikmemaksu andmeid ei ole veel imporditud.",
        footnotes=tuple(footnotes),
    )


def _fee_tooltips(by_year: dict[int, list[dict]], current_year: int | None) -> dict:
    """One readout per observation, with last year's nearest comparable point.

    The comparison is only offered when the previous year actually reported near
    the same point in its year. An observation from a different season would be
    a different question wearing the same label.
    """
    tooltips = {}
    for year, rows in by_year.items():
        previous = by_year.get(year - 1, [])
        for row in rows:
            when = row["observation_date"]
            readout = [
                {"label": "Laekunud", "value": euros(row["received"]), "emphasis": True},
                {"label": "Aastaeelarve", "value": euros(row["budget"]), "emphasis": False},
                {"label": "Täitmine", "value": percent(row["computed_pct"]), "emphasis": False},
            ]
            if previous:
                candidates = tuple(
                    (_same_season(other["observation_date"], year=year), other["computed_pct"])
                    for other in previous
                )
                found = pick_comparable(candidates, when, tolerance_days=SEASON_TOLERANCE_DAYS)
                if found is not None:
                    _, earlier_pct = found
                    readout.append(
                        {
                            "label": f"{year - 1} lähim võrreldav",
                            "value": percent(earlier_pct),
                            "emphasis": False,
                        }
                    )
                    readout.append(
                        {
                            "label": "Erinevus",
                            "value": percentage_points(
                                share_change(row["computed_pct"], earlier_pct)
                            ),
                            "emphasis": False,
                        }
                    )
            tooltips[_iso(when)] = {
                "title": f"{long_date(when)}",
                "rows": readout,
                "note": "" if year == current_year else f"{year}. aasta võrdlus",
            }
    return tooltips


def _fee_readouts(by_year: dict[int, list[dict]], current_year: int | None) -> tuple[Readout, ...]:
    """Where this year stands against its budget, in three figures."""
    if current_year is None:
        return ()
    latest = by_year[current_year][-1]
    received, budget = latest["received"], latest["budget"]

    readouts = [
        Readout(label=f"{current_year} laekunud", value=euros(received)),
        Readout(label="Aastaeelarve", value=euros(budget)),
        Readout(label="Täitmine", value=percent(latest["computed_pct"])),
    ]

    if received is not None and budget is not None:
        remaining = Decimal(budget) - Decimal(received)
        readouts.append(
            Readout(
                label="Puudu aastaeelarvest" if remaining > 0 else "Üle aastaeelarve",
                value=euros(abs(remaining)),
                direction="down" if remaining > 0 else "up",
            )
        )
    return tuple(readouts)


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
