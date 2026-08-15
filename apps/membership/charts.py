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
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from apps.core.chart_payload import ChartPayload, Readout
from apps.core.formatting import (
    MONTH_ABBREVIATIONS,
    day_and_month,
    euros,
    integer,
    long_date,
    month_name,
    percent,
    percentage,
    percentage_points,
    signed_integer,
    signed_percent,
)

from .analytics import (
    change,
    compare_with,
    cumulative,
    elapsed_total,
    mean_of_complete_years,
    net_movement,
    pick_comparable,
    share_change,
    value_domain,
)
from .internal_selectors import InternalTrend, MonthlyValue
from .models import QualityStatus

# Board reports number their months in Roman numerals. The **table** keeps that
# convention, so a reader checking a figure against the report they came from
# sees the same month naming. The chart axis does not: a reader should not have
# to translate a numeral before they can read a graphic, and the axis is where
# comprehension matters more than matching the source's typography.
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

# A chart that labels its series at the last point needs the margin to put the
# label in. With the ordinary right margin the label is drawn past the edge of
# the canvas and clipped mid-word, which is worse than no label: "Liit" names
# nothing.
#
# Sized for the longest label these charts draw — "Tasunud 3 156" — at the
# weight and size the browser gives an end label, plus its chip padding and its
# distance from the point. 116 was measured against the 12px unstyled default
# and stopped being enough the moment the label became legible.
GRID_WITH_END_LABELS = {**GRID, "right": 140}

# How a value drawn at the end of a bar is set.
#
# ECharts' default label is the chart's body size in the chart's body colour,
# which on a bar end — half over the fill, half over the surface — reads as
# neither. These are figures a reader is meant to take straight off the drawing,
# so they get their own weight and sit clear of the bar.
BAR_LABEL = {
    "fontSize": 12,
    "fontWeight": 600,
    "distance": 6,
}

# `hideOverlap` belongs to the **series**, not to the root of the option. Set at
# the root it is silently ignored, which is how two labels on a one-member bar
# came to be printed on top of each other.
LABEL_LAYOUT = {"hideOverlap": True}


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
class ToggleOption:
    """One choice in a section's control, as a link the server can already build."""

    label: str
    query: str
    is_active: bool


@dataclass(frozen=True)
class Toggle:
    """A named control belonging to one section.

    A control is only built when it has something to switch between. Offering a
    choice that cannot change the picture is worse than offering none: the
    reader clicks it, nothing moves, and they are left wondering what they broke.
    """

    label: str
    options: tuple[ToggleOption, ...]

    @property
    def is_offered(self) -> bool:
        return len(self.options) > 1


@dataclass(frozen=True)
class AnalyticsSection:
    """One analytical tool: a question, its charts, and the controls that
    belong to *it* rather than to the page.

    The page used to carry a single date range above a pile of charts, only two
    of which obeyed it. Everything a control governs is now inside the same
    section as the control, so a reader can tell what a change will affect by
    looking at where it sits.
    """

    section_id: str
    #: Always set: it is the section landmark's accessible name even when the
    #: heading is not drawn. Blanking it to hide a heading would take the name
    #: away too, which is why `show_title` exists instead.
    title: str
    description: str = ""
    #: Whether the heading is drawn. A section whose title was struck out keeps
    #: its name for assistive technology and shows none.
    show_title: bool = True
    charts: tuple[ChartPayload, ...] = ()
    presets: tuple = ()
    show_custom_range: bool = False
    toggles: tuple[Toggle, ...] = ()

    @property
    def has_charts(self) -> bool:
        return bool(self.charts)

    @property
    def has_controls(self) -> bool:
        return (
            bool(self.presets)
            or self.show_custom_range
            or any(toggle.is_offered for toggle in self.toggles)
        )


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
    option["grid"] = dict(GRID_WITH_END_LABELS)
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
                    "endLabel": {"show": True, "formatter": _end_label("Kokku", total_series)},
                    "labelLayout": dict(LABEL_LAYOUT),
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
                    "endLabel": {"show": True, "formatter": _end_label("Tasunud", paid_series)},
                    "labelLayout": dict(LABEL_LAYOUT),
                    "connectNulls": False,
                    "data": paid,
                },
            ],
            "dashkoda": {
                "tooltip": _trend_tooltips(trend, provisional),
                "axisFormat": {"y": "integer"},
            },
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
    # The withheld-points footnote was struck out on the board's print-out. The
    # points are still withheld — `trend.withheld_metric_points` still counts
    # them and the quality section still reports them — the chart just no
    # longer says so underneath itself.
    if provisional:
        footnotes.append("Esialgsed vaatlused on graafikul tühja markeriga.")

    return ChartPayload(
        payload_id="internal-membership-trend",
        title="Liikmeid kokku ja tasunud liikmeid",
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


def _end_label(name: str, series: tuple) -> str:
    """A series' label at its last point: what it is, and what it now reads.

    A short name and the latest figure together, because the reader is looking
    at the end of the line anyway and the two answers they want there are which
    line this is and where it ended up. The full series name would be legible
    only by taking a sixth of the plot width away from the drawing.
    """
    if not series:
        return name
    return f"{name} {integer(series[-1][1])}"


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
                # Struck out on the print-out. The reason is still computed
                # and still available to `apps.membership.analytics`; what is
                # gone is printing it under the figure, where three readouts
                # repeated the same sentence.
                note="",
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
                note="",
            )
        )

    return tuple(readouts)


# --------------------------------------------------------------------------
# B. Monthly new members
# --------------------------------------------------------------------------


# The two things a reader can ask this chart for. Both are query-string values,
# so a view survives a bookmark and a shared link.
PARAM_VIEW = "vaade"
PARAM_BENCHMARK = "vordlus"

VIEW_MONTHLY = "kuu"
VIEW_CUMULATIVE = "kumulatiivne"
VIEWS = (VIEW_MONTHLY, VIEW_CUMULATIVE)

BENCHMARK_PREVIOUS = "eelmine"
BENCHMARK_AVERAGE = "keskmine"
BENCHMARKS = (BENCHMARK_PREVIOUS, BENCHMARK_AVERAGE)

# How many complete years the averaged benchmark is drawn from.
BENCHMARK_YEARS = 3


def monthly_pairs(values: tuple[MonthlyValue, ...]) -> tuple[tuple[int, int | None], ...]:
    """Twelve months, each a number or nothing.

    A conflict and a month nobody reported both arrive as `None`; an explicitly
    reported `0` stays a zero. This is the distinction the whole chart rests on,
    so it is made once here rather than at each place a month is read.
    """
    known = {value.calendar_month: value for value in values}
    months = []
    for month in range(1, 13):
        value = known.get(month)
        months.append((month, value.new_members if value and value.is_chartable else None))
    return tuple(months)


def last_complete_month(months: tuple[tuple[int, int | None], ...]) -> int | None:
    """The last month up to which every month is known.

    A year-to-date figure that skipped an unreported March would be a total of
    "everything except the month we lost", presented as if it were the year.
    """
    through = 0
    for month, value in months:
        if value is None:
            break
        through = month
    return through or None


def available_benchmarks(by_year: dict[int, tuple[MonthlyValue, ...]]) -> tuple[str, ...]:
    """Which comparisons the history can actually support.

    A selector offering a benchmark that draws nothing is a control with no
    effect, and the reader is left to guess whether they broke it. The page asks
    this before it renders the choice.
    """
    years = sorted(by_year)
    if not years:
        return ()
    current_year = years[-1]
    supported = []
    for benchmark in BENCHMARKS:
        _, months = _benchmark_series(by_year, current_year=current_year, benchmark=benchmark)
        if any(value is not None for _, value in months):
            supported.append(benchmark)
    return tuple(supported)


def _benchmark_series(
    by_year: dict[int, tuple[MonthlyValue, ...]],
    *,
    current_year: int,
    benchmark: str,
) -> tuple[str, tuple[tuple[int, Decimal | int | None], ...]]:
    """The comparison line: last year, or an average of complete years.

    The average withdraws for any month one of its years did not report, so the
    line has a gap there rather than quietly averaging fewer years at that
    point. A benchmark whose meaning changes from month to month is several
    series wearing one name.
    """
    if benchmark == BENCHMARK_AVERAGE:
        years = tuple(range(current_year - BENCHMARK_YEARS, current_year))
        months = {year: monthly_pairs(by_year.get(year, ())) for year in years}
        return (
            f"{BENCHMARK_YEARS} a keskmine",
            tuple(
                (month, mean_of_complete_years(months, period=month, years=years))
                for month in range(1, 13)
            ),
        )
    previous = current_year - 1
    return str(previous), monthly_pairs(by_year.get(previous, ()))


def monthly_new_members_chart(
    by_year: dict[int, tuple[MonthlyValue, ...]],
    *,
    view: str = VIEW_MONTHLY,
    benchmark: str = BENCHMARK_PREVIOUS,
) -> ChartPayload:
    """Is new-member recruitment stronger or weaker than usual?

    The chart used to draw one equally weighted line per year across months
    numbered I–XII, which asked a reader to translate the axis before they could
    read it and then to pick their own year out of a bundle of similar lines.

    Now the current year is the subject — bars, in front — and exactly one
    historical benchmark sits behind it as a line. Roman numerals are gone from
    the axis; the board reports still use them and the table still names the
    month the way the source does.

    `Kumulatiivselt` answers the other half of the question — are we ahead of
    last year — and it stops at the first month nobody reported rather than
    carrying on as though that month were zero.
    """
    years = sorted(by_year)
    if not years:
        return _empty_monthly_chart(view, benchmark)

    current_year = years[-1]
    current_months = monthly_pairs(by_year[current_year])
    benchmark_label, benchmark_months = _benchmark_series(
        by_year, current_year=current_year, benchmark=benchmark
    )

    cumulative_view = view == VIEW_CUMULATIVE
    if cumulative_view:
        current_running = cumulative(current_months)
        benchmark_running = cumulative(benchmark_months)
        current_points = dict(current_running.values)
        benchmark_points = dict(benchmark_running.values)
        stopped_at = current_running.stopped_at
    else:
        current_points = {month: value for month, value in current_months if value is not None}
        benchmark_points = {month: value for month, value in benchmark_months if value is not None}
        stopped_at = None

    def data(points: dict) -> list:
        return [
            ({"value": _number(points[month]), "tip": str(month)} if month in points else None)
            for month in range(1, 13)
        ]

    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {"type": "category", "data": list(MONTH_ABBREVIATIONS)},
            "yAxis": {
                "type": "value",
                "name": "Uusi liikmeid" if not cumulative_view else "Uusi liikmeid kokku",
                # Counts of arrivals genuinely start at nothing, so zero is the
                # honest floor. Nothing here is a level far from the origin.
                "min": 0,
            },
            "tooltip": {"trigger": "axis"},
            "series": [
                {
                    "name": benchmark_label,
                    "type": "line",
                    "showSymbol": True,
                    "symbolSize": 5,
                    "connectNulls": False,
                    "lineStyle": {"width": 1.5, "type": "dashed", "opacity": 0.7},
                    "itemStyle": {"opacity": 0.7},
                    "z": 2,
                    "data": data(benchmark_points),
                },
                {
                    "name": str(current_year),
                    # Bars for the subject year: a month's recruitment is a
                    # quantity in that month rather than a level moving through
                    # it, and a bar says so where a line implies travel between
                    # the points.
                    "type": "line" if cumulative_view else "bar",
                    "connectNulls": False,
                    "showSymbol": True,
                    "lineStyle": {"width": 2.5},
                    "z": 3,
                    "data": data(current_points),
                },
            ],
            "dashkoda": {
                "axisFormat": {"y": "integer"},
                "tooltip": _monthly_tooltips(
                    current_year=current_year,
                    current_points=current_points,
                    benchmark_label=benchmark_label,
                    benchmark_points=benchmark_points,
                    cumulative_view=cumulative_view,
                ),
            },
        }
    )

    rows = []
    for year in years:
        known = {value.calendar_month: value for value in by_year[year]}
        for month in range(1, 13):
            value = known.get(month)
            if value is None:
                continue
            rows.append(
                (year, MONTH_LABELS[month - 1], value.new_members, _monthly_status_label(value))
            )

    footnotes = []
    # The provisional-month and conflicted-month footnotes were struck out, and
    # the two flags that existed only to raise them went with them. Neither
    # behaviour changed: a provisional month is still marked on the drawing and
    # in the table's own status column, and a conflicted one is still withheld
    # from the line rather than drawn as zero. `_monthly_status_label` is what
    # states both, per row, where the value is.
    if not benchmark_points:
        footnotes.append(
            f"Võrdlust „{benchmark_label}“ ei saa kuvada, sest selle perioodi kohta "
            "puuduvad täielikud andmed."
        )
    if stopped_at is not None:
        footnotes.append(
            f"Kumulatiivne joon lõpeb enne kuud {MONTH_ABBREVIATIONS[stopped_at - 1]}, "
            "mille kohta andmed puuduvad — puuduvat kuud ei loeta nulliks."
        )

    return ChartPayload(
        payload_id="internal-membership-monthly",
        title=(
            "Uusi liikmeid kuude lõikes" if not cumulative_view else "Uusi liikmeid kumulatiivselt"
        ),
        option=option,
        size="medium",
        readouts=_monthly_readouts(
            current_year=current_year,
            current_months=current_months,
            by_year=by_year,
        ),
        table_headers=("Aasta", "Kuu", "Uusi liikmeid", "Olek"),
        table_rows=tuple(rows),
        summary=(
            f"{current_year}. aasta uued liikmed kuude kaupa, taustaks {benchmark_label}. "
            "Puuduvad ja vastuolulised kuud on välja jäetud."
        ),
        empty_message="Kuude kaupa andmeid ei ole veel imporditud.",
        footnotes=tuple(footnotes),
    )


def _empty_monthly_chart(view: str, benchmark: str) -> ChartPayload:
    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {"type": "category", "data": list(MONTH_ABBREVIATIONS)},
            "yAxis": {"type": "value", "name": "Uusi liikmeid", "min": 0},
            "series": [],
        }
    )
    return ChartPayload(
        payload_id="internal-membership-monthly",
        title="Uusi liikmeid kuude lõikes",
        option=option,
        size="medium",
        table_headers=("Aasta", "Kuu", "Uusi liikmeid", "Olek"),
        table_rows=(),
        summary="Kuude kaupa andmeid ei ole.",
        empty_message="Kuude kaupa andmeid ei ole veel imporditud.",
    )


def _monthly_tooltips(
    *,
    current_year: int,
    current_points: dict,
    benchmark_label: str,
    benchmark_points: dict,
    cumulative_view: bool,
) -> dict:
    tooltips = {}
    for month in range(1, 13):
        if month not in current_points and month not in benchmark_points:
            continue
        rows = []
        if month in current_points:
            rows.append(
                {
                    "label": f"{current_year} kokku" if cumulative_view else str(current_year),
                    "value": integer(current_points[month]),
                    "emphasis": True,
                }
            )
        if month in benchmark_points:
            rows.append(
                {
                    "label": (
                        f"{benchmark_label} sama periood" if cumulative_view else benchmark_label
                    ),
                    "value": integer(benchmark_points[month]),
                    "emphasis": False,
                }
            )
        if month in current_points and month in benchmark_points:
            absolute, relative = change(current_points[month], benchmark_points[month])
            # The rate qualifies the difference rather than standing beside it,
            # so they share one row: an empty label would leave the readout with
            # a value nothing names.
            difference = signed_integer(absolute)
            if relative is not None:
                difference = f"{difference} ({signed_percent(relative)})"
            rows.append({"label": "Erinevus", "value": difference, "emphasis": False})
        tooltips[str(month)] = {
            "title": f"{month_name(month).capitalize()} {current_year}",
            "rows": rows,
            "note": "",
        }
    return tooltips


def _monthly_readouts(
    *,
    current_year: int,
    current_months: tuple[tuple[int, int | None], ...],
    by_year: dict[int, tuple[MonthlyValue, ...]],
) -> tuple[Readout, ...]:
    """This year's arrivals so far, against the same stretch of last year.

    "So far" is the run of months from January that are all known. A total that
    jumped over an unreported month would be a different quantity presented as
    the same one, and comparing it with a full previous year would be the
    collapse this refuses to draw.
    """
    through = last_complete_month(current_months)
    if through is None:
        return (
            Readout(
                label=f"Uusi liikmeid {current_year}",
                value="",
                note="Selle aasta kuude kohta ei ole veel katkematut rida.",
            ),
        )

    total = elapsed_total(current_months, through=through)
    readouts = [
        Readout(
            label=f"Uusi liikmeid {current_year}",
            value=integer(total),
            note=f"jaanuar–{month_name(through)}",
        )
    ]

    previous = elapsed_total(monthly_pairs(by_year.get(current_year - 1, ())), through=through)
    if previous is None:
        readouts.append(
            Readout(
                label=f"Sama periood {current_year - 1}",
                value="",
                note="Eelmise aasta sama perioodi kohta ei ole katkematut rida.",
            )
        )
        return tuple(readouts)

    absolute, relative = change(total, previous)
    readouts.append(
        Readout(
            label=f"Sama periood {current_year - 1}",
            value=integer(previous),
            change=signed_integer(absolute),
            change_label=(
                f"{signed_integer(absolute)}"
                + (f" ({signed_percent(relative)})" if relative is not None else "")
            ),
            direction=_direction(absolute),
        )
    )
    return tuple(readouts)


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

# The deepest the completion axis is ever drawn.
#
# Collection does begin each year at nothing, which is why this axis started at
# zero. But the board reports start in February at three quarters of the budget,
# so the bottom half of the plot was empty every year and the twenty points that
# matter were squeezed into the top third. The axis now starts below the lowest
# reading rather than below the year, and never higher than this — so the
# reference line at 100% is always in view with room beneath it, and a year that
# genuinely collapses still gets an axis that reaches it.
FEE_AXIS_FLOOR_CEILING = 50
FEE_AXIS_HEADROOM_PCT = 10

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
                "endLabel": {"show": True, "formatter": "{a}", "distance": 8},
                # Three of four years finish within a few points of the budget,
                # so without this their labels are drawn on top of each other.
                "labelLayout": dict(LABEL_LAYOUT),
                "z": 3 if is_current else 2,
                # The line itself answers a pointer, not only the dots on it.
                # Four sparse years of dots is a lot of aiming for a reader who
                # just wants to know which year a line is.
                "triggerLineEvent": True,
                "emphasis": {
                    # Hovering one year lifts it and fades the others, which is
                    # the whole question this chart asks: where is this year
                    # against the ones before it.
                    "focus": "series",
                    "lineStyle": {"width": 3},
                },
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
                            # At the left. The series label themselves sit at
                            # the right end of each line, and the target label
                            # was landing on top of them.
                            "label": {
                                "formatter": "Aastaeelarve",
                                "position": "insideStartTop",
                            },
                            "data": [{"yAxis": BUDGET_TARGET_PCT}],
                        }
                    }
                    if is_current
                    else {}
                ),
            }
        )

    drawn_values = [_number(row["computed_pct"]) for row in drawable]
    highest = max(drawn_values + [float(BUDGET_TARGET_PCT)], default=float(BUDGET_TARGET_PCT))
    lowest = min(drawn_values, default=float(BUDGET_TARGET_PCT))
    # Down to a ten-point step below the lowest reading, and never so high that
    # the target loses its context.
    floor = min(FEE_AXIS_FLOOR_CEILING, max(0, int((lowest - FEE_AXIS_HEADROOM_PCT) // 10) * 10))

    option = _base_option(legend=False)
    # The year labels are short, but they still need somewhere to sit — and
    # since the end label became a chip with a border and padding rather than
    # four bare digits, "somewhere" is wider than four digits.
    option["grid"] = {**GRID, "right": 76}
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
                # Not zero: see FEE_AXIS_FLOOR_CEILING. The ceiling still
                # clears the target so exceeding it is visible rather than
                # clipped.
                "min": floor,
                "max": max(BUDGET_TARGET_PCT + 10, int(highest) + 10),
            },
            "tooltip": {"trigger": "item"},
            "series": series,
            "dashkoda": {
                "tooltip": _fee_tooltips(by_year, current_year),
                "axisFormat": {"y": "percent"},
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
        # Both struck out. The section heading two lines above already says
        # `Liikmemaksu laekumine`; this repeated it with one word added.
        title="",
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
    """Which company sizes are we gaining members in, and which are we losing?

    Diverging horizontal bars: departures to the left, arrivals to the right,
    one row per size band in the source's own band order.

    **The negation is geometry and nothing else.** The removed count is drawn as
    a negative number because that is what makes a bar extend leftwards, and
    that value must never reach a reader: nobody reports that minus eleven
    members left. Every figure a reader sees — the bar-end label, the tooltip,
    the table — carries the positive count. The tooltip is built here from the
    source values rather than from the drawn ones, which is what makes that
    structural rather than a rule someone has to remember.

    Net movement is derived for presentation and is not stored anywhere. It is
    stated in the header, in each row's tooltip and in the table, but not as a
    third bar: a chart that draws arrivals, departures and their difference
    draws the same fact twice and invites the reader to add the picture up.
    """
    labels = [row["label"] for row in rows]

    def bar(row: dict, key: str, *, negate: bool) -> dict | None:
        value = row[key]
        if value is None:
            return None
        drawn = -value if negate else value
        return {
            "value": _number(drawn),
            "tip": row["band"],
            # The label states the count, never the drawn geometry.
            "label": {
                **BAR_LABEL,
                "show": True,
                "position": "left" if negate else "right",
                "formatter": integer(value),
            },
        }

    option = _base_option(legend=False)
    option.update(
        {
            # The axis name sits under the middle of the axis. Left at its
            # default it is drawn past the last tick and clipped by the edge of
            # the canvas, which turned `Liikmeid` into `Li`.
            "xAxis": {
                "type": "value",
                "name": "Liikmeid",
                "nameLocation": "middle",
                "nameGap": 28,
            },
            "yAxis": {"type": "category", "data": labels, "inverse": True},
            # The readout describes a whole size class — arrivals, departures
            # and the net between them — so the axis is what triggers it. With
            # `item` a reader had to land on one of the two bars, and a class
            # with two departures draws a bar a few pixels wide: hovering its
            # row returned nothing at all. `tooltipFormatter` is written for the
            # axis trigger and reads the key off whichever datum carries it.
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "series": [
                {
                    "name": "Lahkunud",
                    "type": "bar",
                    "stack": "movement",
                    "labelLayout": dict(LABEL_LAYOUT),
                    "data": [bar(row, "removed", negate=True) for row in rows],
                },
                {
                    "name": "Liitunud",
                    "type": "bar",
                    "stack": "movement",
                    "labelLayout": dict(LABEL_LAYOUT),
                    "data": [bar(row, "joined", negate=False) for row in rows],
                },
            ],
            "dashkoda": {
                "tooltip": _movement_tooltips(rows, observation_date),
                # The bars extend leftwards because the removed count is drawn
                # negative. An axis tick reading `−40` would state that geometry
                # as a business quantity, which is the same defect the tooltip
                # already refuses to repeat.
                "axisFormat": {"x": "absolute"},
            },
        }
    )

    # The supporter-member footnote was struck out; the band is still listed
    # separately in the chart itself, which is what the sentence described.
    footnotes: tuple[str, ...] = ()

    return ChartPayload(
        payload_id="internal-membership-size-movement",
        title="Liitunud ja lahkunud suurusklassiti",
        option=option,
        size="categorical",
        readouts=_movement_readouts(rows),
        observation_label=(f"Seisuga {long_date(observation_date)}" if observation_date else ""),
        table_headers=("Suurusklass", "Liitunud", "Lahkunud", "Neto"),
        table_rows=tuple(
            (
                row["label"],
                row["joined"],
                row["removed"],
                net_movement(row["joined"], row["removed"]),
            )
            for row in rows
        ),
        summary=(
            "Vastandsuunaline tulpgraafik: lahkunud vasakul, liitunud paremal, "
            f"{len(rows)} suurusklassi kohta"
            + (f" seisuga {long_date(observation_date)}." if observation_date else ".")
        ),
        empty_message="Suurusklasside jaotust selle vaatluse kohta ei ole.",
        footnotes=footnotes,
    )


def _movement_tooltips(rows: tuple[dict, ...], observation_date: date | None) -> dict:
    """One readout per band, stating counts as the source reported them.

    Built from `row["removed"]`, never from the negated value the bar carries.
    """
    tooltips = {}
    for row in rows:
        readout = []
        if row["joined"] is not None:
            readout.append(
                {"label": "Liitunud", "value": integer(row["joined"]), "emphasis": False}
            )
        if row["removed"] is not None:
            readout.append(
                {"label": "Lahkunud", "value": integer(row["removed"]), "emphasis": False}
            )
        net = net_movement(row["joined"], row["removed"])
        if net is not None:
            readout.append({"label": "Neto", "value": signed_integer(net), "emphasis": True})
        tooltips[row["band"]] = {
            "title": row["label"],
            "rows": readout,
            "note": f"Seisuga {long_date(observation_date)}" if observation_date else "",
        }
    return tooltips


def _movement_readouts(rows: tuple[dict, ...]) -> tuple[Readout, ...]:
    """The whole observation's arrivals, departures and net.

    Each total counts only the bands that reported that direction, so a band
    missing one side does not quietly contribute a zero to it.
    """
    joined = [row["joined"] for row in rows if row["joined"] is not None]
    removed = [row["removed"] for row in rows if row["removed"] is not None]
    if not joined and not removed:
        return ()

    readouts = [
        Readout(label="Liitunud kokku", value=integer(sum(joined)) if joined else ""),
        Readout(label="Lahkunud kokku", value=integer(sum(removed)) if removed else ""),
    ]
    if joined and removed:
        net = sum(joined) - sum(removed)
        readouts.append(
            Readout(
                label="Neto",
                value=signed_integer(net),
                direction=_direction(net),
                note="liitunud miinus lahkunud",
            )
        )
    return tuple(readouts)


# --------------------------------------------------------------------------
# E. Removal reasons
# --------------------------------------------------------------------------


def removal_reasons_chart(rows: tuple[dict, ...], *, observation_date: date | None) -> ChartPayload:
    """Why are members leaving?

    Horizontal bars, largest first. The selector returns them in whatever order
    the rows come back in and the source documents no ordering of its own, so
    the ranking is this chart's decision — and ranking is most of the answer to
    the question.

    Not a pie: categories of similar size are hard to compare as angles, and the
    design system offers no pie component to justify one.

    Each bar carries its count and its share at the end, so the chart answers
    the question without being hovered at all. A legend would name a single
    series that the heading already names.
    """
    ordered = sorted(
        (row for row in rows if row["count"] is not None),
        key=lambda row: (-row["count"], row["label"]),
    )

    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {
                "type": "value",
                "name": "Liikmeid",
                "nameLocation": "middle",
                "nameGap": 28,
            },
            "yAxis": {
                "type": "category",
                "data": [row["label"] for row in ordered],
                "inverse": True,
            },
            "tooltip": {"trigger": "item"},
            "series": [
                {
                    "name": "Lahkunuid",
                    "type": "bar",
                    "labelLayout": dict(LABEL_LAYOUT),
                    "data": [
                        {
                            "value": _number(row["count"]),
                            "tip": row["key"],
                            "label": {
                                **BAR_LABEL,
                                "show": True,
                                "position": "right",
                                # Two runs of spaces between a count and its
                                # share collapse when the label is drawn to a
                                # canvas, so `52  54,7%` arrived as `5254,7%`.
                                # A separator cannot collapse.
                                "formatter": (
                                    f"{integer(row['count'])} · {percent(row['share_pct'])}"
                                    if row["share_pct"] is not None
                                    else integer(row["count"])
                                ),
                            },
                        }
                        for row in ordered
                    ],
                }
            ],
            "dashkoda": {
                "tooltip": _reason_tooltips(ordered, observation_date),
                "axisFormat": {"x": "integer"},
            },
        }
    )

    return ChartPayload(
        payload_id="internal-membership-removal-reasons",
        title="Lahkumise põhjused",
        option=option,
        size="categorical",
        observation_label=(f"Seisuga {long_date(observation_date)}" if observation_date else ""),
        table_headers=("Põhjus", "Liikmeid", "Osakaal"),
        table_rows=tuple(
            (row["label"], row["count"], percentage(row["share_pct"], places=1)) for row in rows
        ),
        summary=(
            f"Horisontaalne tulpgraafik {len(ordered)} lahkumise põhjusega, suuremast "
            "väiksemani"
            + (f", seisuga {long_date(observation_date)}." if observation_date else ".")
        ),
        empty_message="Lahkumise põhjuseid selle vaatluse kohta ei ole.",
    )


# ---------------------------------------------------------------------------
# F. Board-decision batches
# ---------------------------------------------------------------------------


def decision_batch_reasons_chart(batch) -> ChartPayload:
    """Why did the members in *this board decision* leave?

    Scoped to one decision, and labelled that way in every string. The reason
    breakdown on an observation answers a different question — what the year has
    done so far — and the two must never be read as one series. That is why this
    chart names its decision in the title rather than only its date.
    """
    ordered = [row for row in batch.reasons if row["count"] is not None]

    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {
                "type": "value",
                "name": "Liikmeid",
                "nameLocation": "middle",
                "nameGap": 28,
            },
            "yAxis": {
                "type": "category",
                "data": [row["label"] for row in ordered],
                "inverse": True,
            },
            "tooltip": {"trigger": "item"},
            "series": [
                {
                    "name": batch.kind_label,
                    "type": "bar",
                    "labelLayout": dict(LABEL_LAYOUT),
                    "data": [
                        {
                            "value": _number(row["count"]),
                            "tip": row["key"],
                            "label": {
                                **BAR_LABEL,
                                "show": True,
                                "position": "right",
                                "formatter": (
                                    f"{integer(row['count'])} · {percent(row['share_pct'])}"
                                    if row["share_pct"] is not None
                                    else integer(row["count"])
                                ),
                            },
                        }
                        for row in ordered
                    ],
                }
            ],
            "dashkoda": {
                "tooltip": _reason_tooltips(ordered, batch.as_of_date),
                "axisFormat": {"x": "integer"},
            },
        }
    )

    return ChartPayload(
        payload_id=f"internal-membership-decision-reasons-{batch.id}",
        title=f"{batch.kind_label} — põhjused",
        option=option,
        size="categorical",
        observation_label=_batch_label(batch),
        table_headers=("Põhjus", "Liikmeid", "Osakaal"),
        table_rows=tuple(
            (row["label"], row["count"], percentage(row["share_pct"], places=1))
            for row in batch.reasons
        ),
        summary=(
            f"Horisontaalne tulpgraafik {len(ordered)} lahkumise põhjusega ühes "
            f"juhatuse otsuses, {_batch_label(batch).lower()}."
        ),
        empty_message="Selle otsuse kohta põhjuseid ei ole.",
    )


def decision_batch_sizes_chart(batch) -> ChartPayload:
    """How large were the companies in this decision?

    Canonical band order rather than largest-first: the bands are an ordinal
    scale, and reordering them by count would destroy the only thing the axis
    means.
    """
    rows = [row for row in batch.sizes if row["count"] is not None]

    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {
                "type": "value",
                "name": "Liikmeid",
                "nameLocation": "middle",
                "nameGap": 28,
            },
            "yAxis": {
                "type": "category",
                "data": [row["label"] for row in rows],
                "inverse": True,
            },
            "tooltip": {"trigger": "item"},
            "series": [
                {
                    "name": batch.kind_label,
                    "type": "bar",
                    "labelLayout": dict(LABEL_LAYOUT),
                    "data": [
                        {
                            "value": _number(row["count"]),
                            "tip": row["band"],
                            "label": {
                                **BAR_LABEL,
                                "show": True,
                                "position": "right",
                                "formatter": integer(row["count"]),
                            },
                        }
                        for row in rows
                    ],
                }
            ],
            "dashkoda": {"axisFormat": {"x": "integer"}},
        }
    )

    return ChartPayload(
        payload_id=f"internal-membership-decision-sizes-{batch.id}",
        title=f"{batch.kind_label} — suurusklassid",
        option=option,
        size="categorical",
        observation_label=_batch_label(batch),
        table_headers=("Suurusklass", "Liikmeid"),
        table_rows=tuple((row["label"], row["count"]) for row in batch.sizes),
        summary=(
            f"Horisontaalne tulpgraafik {len(rows)} suurusklassiga ühes juhatuse "
            f"otsuses, {_batch_label(batch).lower()}."
        ),
        empty_message="Selle otsuse kohta suurusjaotust ei ole.",
    )


def _batch_label(batch) -> str:
    """Name the decision by both of its dates when they differ.

    The appendix is compiled on one day and signed on another, and collapsing
    them would lose which of the two a figure describes.
    """
    parts = []
    if batch.as_of_date:
        parts.append(f"seisuga {long_date(batch.as_of_date)}")
    if batch.decision_date and batch.decision_date != batch.as_of_date:
        parts.append(f"otsus {long_date(batch.decision_date)}")
    if batch.reference:
        parts.append(batch.reference)
    return ", ".join(parts)


def _reason_tooltips(rows: list[dict], observation_date: date | None) -> dict:
    tooltips = {}
    for row in rows:
        readout = [{"label": "Liikmeid", "value": integer(row["count"]), "emphasis": True}]
        if row["share_pct"] is not None:
            readout.append(
                {"label": "Osakaal", "value": percent(row["share_pct"]), "emphasis": False}
            )
        tooltips[row["key"]] = {
            "title": row["label"],
            "rows": readout,
            "note": f"Seisuga {long_date(observation_date)}" if observation_date else "",
        }
    return tooltips


# ---------------------------------------------------------------------------
# G. Seasonality: is this month unusual for the time of year?
# ---------------------------------------------------------------------------


# How many months must have both a current value and a historical mean before
# the deviation chart is worth drawing. Two points do not describe a seasonal
# shape, and a chart with one bar states a single fact more clearly as a
# sentence.
MIN_SEASONALITY_MONTHS = 3


def seasonality_chart(by_year: dict[int, tuple[MonthlyValue, ...]]) -> ChartPayload | None:
    """Is this calendar month unusually strong or weak for the time of year?

    The recruitment chart already answers "how is the year going". This answers a
    different question: February is always quiet and December is always busy, so
    a February that is down on January is not news. The subject here is the gap
    between a month and *its own* historical norm.

    Diverging bars, one per calendar month, showing the current year minus the
    mean of the same month across the previous complete years. A month is drawn
    only when both sides exist, and the mean withdraws entirely unless every one
    of those years reported that month — an average over "the years that
    happened to report" changes meaning from bar to bar.

    The statistics are deliberately ordinary: a mean over three years. Twelve
    points a year for a decade does not support a seasonal decomposition, and a
    forecast drawn from it would be a confident line describing nothing.

    Returns `None` when too few months qualify, so the caller can leave the
    section out rather than draw an empty frame.
    """
    years = sorted(by_year)
    if not years:
        return None
    current_year = years[-1]
    baseline_years = tuple(range(current_year - BENCHMARK_YEARS, current_year))
    baseline_months = {year: monthly_pairs(by_year.get(year, ())) for year in baseline_years}
    current = dict(monthly_pairs(by_year.get(current_year, ())))

    rows: list[dict] = []
    for month in range(1, 13):
        value = current.get(month)
        mean = mean_of_complete_years(baseline_months, period=month, years=baseline_years)
        if value is None or mean is None:
            continue
        rows.append(
            {
                "month": month,
                "label": MONTH_LABELS[month - 1],
                "name": month_name(month),
                "value": value,
                "mean": mean,
                "deviation": Decimal(value) - mean,
            }
        )

    if len(rows) < MIN_SEASONALITY_MONTHS:
        return None

    baseline_label = f"{baseline_years[0]}–{baseline_years[-1]}"

    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {"type": "category", "data": [row["label"] for row in rows]},
            "yAxis": {
                "type": "value",
                "name": "Erinevus keskmisest",
                "nameLocation": "middle",
                "nameGap": 44,
            },
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "series": [
                {
                    "name": "Erinevus keskmisest",
                    "type": "bar",
                    "labelLayout": dict(LABEL_LAYOUT),
                    "data": [
                        {
                            "value": _number(row["deviation"]),
                            "tip": str(row["month"]),
                            "label": {
                                **BAR_LABEL,
                                "show": True,
                                "position": "top" if row["deviation"] >= 0 else "bottom",
                                "formatter": signed_integer(row["deviation"]),
                            },
                        }
                        for row in rows
                    ],
                }
            ],
            "dashkoda": {
                "tooltip": {
                    str(row["month"]): {
                        "title": f"{row['name']} {current_year}",
                        "rows": [
                            {"label": "Liitus", "value": integer(row["value"]), "emphasis": False},
                            {
                                # Whole members in the tooltip; the table below
                                # carries the unrounded average, which is where
                                # an exact figure is meant to be looked up.
                                "label": f"{BENCHMARK_YEARS} a keskmine",
                                "value": integer(row["mean"]),
                                "emphasis": False,
                            },
                            {
                                "label": "Erinevus",
                                "value": signed_integer(row["deviation"]),
                                "emphasis": True,
                            },
                        ],
                        "note": f"Keskmine aastatest {baseline_label}",
                    }
                    for row in rows
                },
                "axisFormat": {"y": "integer"},
            },
        }
    )

    strongest = max(rows, key=lambda row: row["deviation"])
    weakest = min(rows, key=lambda row: row["deviation"])
    readouts = (
        Readout(
            label="Tugevaim kuu",
            value=strongest["name"],
            change=signed_integer(strongest["deviation"]),
            change_label=f"{signed_integer(strongest['deviation'])} vorreldes sama kuu keskmisega",
            direction=_direction(strongest["deviation"]),
        ),
        Readout(
            label="Norgim kuu",
            value=weakest["name"],
            change=signed_integer(weakest["deviation"]),
            change_label=f"{signed_integer(weakest['deviation'])} vorreldes sama kuu keskmisega",
            direction=_direction(weakest["deviation"]),
        ),
    )

    return ChartPayload(
        payload_id="internal-membership-seasonality",
        title=f"{current_year}. aasta kuud võrreldes sama kuu keskmisega",
        option=option,
        size="categorical",
        question="Kas see kuu on aastaajale tavapärasest tugevam või nõrgem?",
        observation_label=(f"Võrdlusalus {baseline_label}, {len(rows)} võrreldavat kuud"),
        readouts=readouts,
        table_headers=("Kuu", str(current_year), f"{BENCHMARK_YEARS} a keskmine", "Erinevus"),
        table_rows=tuple(
            (row["name"], row["value"], row["mean"], row["deviation"]) for row in rows
        ),
        summary=(
            f"Tulpgraafik {len(rows)} kuu kohta, mis näitab {current_year}. aasta "
            "liitumiste erinevust sama kalendrikuu keskmisest."
        ),
        empty_message="Hooajalisuse võrdluseks ei ole piisavalt täielikke aastaid.",
        footnotes=(
            "Keskmine arvutatakse ainult siis, kui kõik võrdlusaastad on selle kuu "
            "kohta andmed esitanud. Muidu jäetakse kuu graafikult välja.",
        ),
    )


# ---------------------------------------------------------------------------
# H. Multi-month recruitment periods
# ---------------------------------------------------------------------------


def new_member_periods_chart(periods: tuple) -> ChartPayload | None:
    """New members over spans the board never broke into months.

    Some reports give a single arrivals figure for June and July together. That
    number is real and it is not two monthly numbers; splitting it in half would
    invent a distribution nobody measured, and dropping it would discard a
    reported fact.

    So it is drawn on its own, against its actual reported span. One horizontal
    bar per period, labelled with the dates the board used. Nothing here shares
    an axis with the monthly series and nothing is added to it.

    Returns `None` when no such period exists, which is the common case.
    """
    rows = [period for period in periods if period.new_members is not None]
    if not rows:
        return None

    def span(period) -> str:
        return (
            f"{day_and_month(period.period_start)} – "
            f"{day_and_month(period.period_end)} {period.period_end.year}"
        )

    labels = [span(period) for period in rows]

    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {
                "type": "value",
                "name": "Liikmeid",
                "nameLocation": "middle",
                "nameGap": 28,
            },
            "yAxis": {"type": "category", "data": labels, "inverse": True},
            "tooltip": {"trigger": "item"},
            "series": [
                {
                    "name": "Liitunud",
                    "type": "bar",
                    "labelLayout": dict(LABEL_LAYOUT),
                    "data": [
                        {
                            "value": _number(period.new_members),
                            "tip": str(period.id),
                            "label": {
                                **BAR_LABEL,
                                "show": True,
                                "position": "right",
                                "formatter": integer(period.new_members),
                            },
                        }
                        for period in rows
                    ],
                }
            ],
            "dashkoda": {
                "tooltip": {
                    str(period.id): {
                        "title": span(period),
                        "rows": [
                            {
                                "label": "Liitunud",
                                "value": integer(period.new_members),
                                "emphasis": True,
                            }
                        ],
                        "note": "Periood, mida aruanne kuudeks ei jaganud",
                    }
                    for period in rows
                },
                "axisFormat": {"x": "integer"},
            },
        }
    )

    return ChartPayload(
        payload_id="internal-membership-new-member-periods",
        title="Mitut kuud hõlmavad liitumisperioodid",
        option=option,
        size="categorical",
        question="Kui palju liikmeid lisandus perioodidel, mida aruanne kuudeks ei jaganud?",
        table_headers=("Periood", "Liitunud"),
        table_rows=tuple((span(period), period.new_members) for period in rows),
        summary=(f"Horisontaalne tulpgraafik {len(rows)} mitmekuulise liitumisperioodi kohta."),
        empty_message="Mitmekuulisi liitumisperioode ei ole.",
        footnotes=(
            "Need arvud katavad mitut kuud korraga ja neid ei jagata kuude vahel. "
            "Kuude graafikuga neid kokku ei liideta.",
        ),
    )


# ---------------------------------------------------------------------------
# I. Composition of the current roster
# ---------------------------------------------------------------------------


def composition_chart(
    result,
    *,
    payload_id: str,
    title: str,
    question: str,
    snapshot_date: date,
    ranked: bool,
    axis_name: str = "Liikmeid",
    limit: int = 10,
    footnotes: tuple[str, ...] = (),
) -> ChartPayload | None:
    """One dimension of the membership, as horizontal bars.

    Horizontal rather than vertical because the categories are Estonian
    sector and county names, which need a readable line of text beside each
    bar rather than a rotated axis label.

    Not a pie, ever: categories of similar size are hard to compare as angles,
    fifteen counties would be unreadable as one, and the design system offers no
    pie component to justify inventing one.

    `ranked` decides the order and it is a property of the dimension, not a
    preference. A size class and a tenure band are an ordinal scale whose order
    *is* the meaning, so they keep it; a county or a sector has no inherent
    order, so ranking them largest-first is most of the answer.

    Returns `None` when there is nothing to draw, so a caller can leave the
    section out rather than render an empty frame.
    """
    if result is None or not result.has_data:
        return None

    rows = result.ranked(limit=limit) if ranked else result.categories
    rows = [row for row in rows if row.count]
    if not rows:
        return None

    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {
                "type": "value",
                "name": axis_name,
                "nameLocation": "middle",
                "nameGap": 28,
            },
            "yAxis": {
                "type": "category",
                "data": [row.label for row in rows],
                "inverse": True,
            },
            "tooltip": {"trigger": "item"},
            "series": [
                {
                    "name": axis_name,
                    "type": "bar",
                    "labelLayout": dict(LABEL_LAYOUT),
                    "data": [
                        {
                            "value": _number(row.count),
                            "tip": row.key,
                            "label": {
                                **BAR_LABEL,
                                "show": True,
                                "position": "right",
                                # A separator rather than spaces: two runs of
                                # spaces collapse when a label is drawn to a
                                # canvas, which turns `52  54,7%` into
                                # `5254,7%`.
                                "formatter": f"{integer(row.count)} · {percent(row.share_pct)}",
                            },
                        }
                        for row in rows
                    ],
                }
            ],
            "dashkoda": {
                "tooltip": {
                    row.key: {
                        "title": row.label,
                        "rows": [
                            {"label": "Liikmeid", "value": integer(row.count), "emphasis": True},
                            {
                                "label": "Osakaal",
                                "value": percent(row.share_pct),
                                "emphasis": False,
                            },
                        ],
                        "note": f"Seisuga {long_date(snapshot_date)}",
                    }
                    for row in rows
                },
                "axisFormat": {"x": "integer"},
            },
        }
    )

    return ChartPayload(
        payload_id=payload_id,
        title=title,
        option=option,
        size="categorical",
        question=question,
        observation_label=(f"Seisuga {long_date(snapshot_date)} · {integer(result.total)} liiget"),
        table_headers=("Kategooria", "Liikmeid", "Osakaal"),
        # The table lists every category at full detail, including any the chart
        # folded into `Muu`. Exact lookup is what the table is for.
        table_rows=tuple(
            (row.label, row.count, percentage(row.share_pct, places=1))
            for row in sorted(result.categories, key=lambda c: (-c.count, c.label))
        ),
        summary=(
            f"Horisontaalne tulpgraafik: {title.lower()}, {len(rows)} kategooriat, "
            f"seisuga {long_date(snapshot_date)}."
        ),
        empty_message="Koosseisu andmeid ei ole.",
        footnotes=footnotes,
    )


# How many joining years are drawn individually before the rest become one bar.
#
# The roster reaches back to 1925 and holds 46 distinct joining years. Drawing
# all of them puts eight decades of one- and two-member bars beside the years a
# reader is actually asking about, and the early ones are not individually
# meaningful at that distance.
COHORT_YEARS_SHOWN = 20


def join_cohort_chart(result, *, snapshot_date: date) -> ChartPayload | None:
    """Which joining years are represented in today's membership?

    Vertical bars on a year axis, because a joining year is a position in time
    and reads left to right.

    **This is not cohort retention.** The roster holds the members who are here
    now, so every year is seen only through the organisations that stayed; a
    year with few bars may have recruited few members or may have lost many, and
    nothing in this application can tell the two apart. The title says which
    question it answers and the footnote says which one it does not.
    """
    if result is None or not result.has_data:
        return None

    years = sorted(
        (row for row in result.categories if row.key.isdigit()),
        key=lambda row: int(row.key),
    )
    if not years:
        return None

    recent = years[-COHORT_YEARS_SHOWN:]
    older = years[:-COHORT_YEARS_SHOWN]
    labels = [row.key for row in recent]
    counts = [row.count for row in recent]
    keys = [row.key for row in recent]

    if older:
        labels.insert(0, f"enne {recent[0].key}")
        counts.insert(0, sum(row.count for row in older))
        keys.insert(0, "earlier")

    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {"type": "category", "data": labels},
            "yAxis": {
                "type": "value",
                "name": "Liikmeid",
                "nameLocation": "middle",
                "nameGap": 44,
            },
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "series": [
                {
                    "name": "Tänaseid liikmeid",
                    "type": "bar",
                    "labelLayout": dict(LABEL_LAYOUT),
                    "data": [
                        {"value": _number(count), "tip": key}
                        for key, count in zip(keys, counts, strict=True)
                    ],
                }
            ],
            "dashkoda": {
                "tooltip": {
                    key: {
                        "title": (f"Liitus {label}" if key != "earlier" else f"Liitus {label}"),
                        "rows": [
                            {
                                "label": "Tänaseid liikmeid",
                                "value": integer(count),
                                "emphasis": True,
                            }
                        ],
                        "note": "Praeguses liikmeskonnas alles olevad ettevõtted",
                    }
                    for key, label, count in zip(keys, labels, counts, strict=True)
                },
                "axisFormat": {"y": "integer"},
            },
        }
    )

    return ChartPayload(
        payload_id="membership-composition-join-cohort",
        title="Tänased liikmed liitumisaasta järgi",
        option=option,
        size="medium",
        question="Millistest liitumisaastatest on tänane liikmeskond koos?",
        observation_label=(f"Seisuga {long_date(snapshot_date)} · {integer(result.total)} liiget"),
        table_headers=("Liitumisaasta", "Tänaseid liikmeid", "Osakaal"),
        table_rows=tuple(
            (row.key, row.count, percentage(row.share_pct, places=1))
            for row in sorted(result.categories, key=lambda c: c.key)
        ),
        summary=(
            f"Tulpgraafik {len(labels)} liitumisaasta kohta, seisuga {long_date(snapshot_date)}."
        ),
        empty_message="Liitumisaastate jaotust ei ole.",
        footnotes=(
            "Graafik näitab, millal tänased liikmed liitusid. See ei ole "
            "püsimamäär: lahkunud liikmeid nimekirjas ei ole ja ükski allikas "
            "siin rakenduses neid ei loenda.",
        ),
    )


def growth_index_chart(
    rows: tuple,
    suppressed: tuple[str, ...],
    *,
    dimension_label: str,
    snapshot_date: date,
    recent_total: int,
) -> ChartPayload | None:
    """Which kinds of organisation are over-represented among recent joiners?

    One share divided by another, times a hundred. A category at 100 holds the
    same share of the recent joiners as it does of the membership; above that it
    is over-represented among them and below it under-represented.

    That is the whole calculation, and it is stated on the page beside the
    chart. It is descriptive: no model, no smoothing, no significance test, and
    no claim that a difference will persist.

    Categories with too few members on either side are **named as withheld**
    rather than drawn at zero or at 100. A ratio built on two organisations
    swings by tens of points on a single membership, and ranking that beside a
    category of nine hundred would present noise as a finding.
    """
    if not rows:
        return None

    option = _base_option(legend=False)
    option.update(
        {
            "xAxis": {
                "type": "value",
                "name": "Kasvuindeks (100 = sama osakaal)",
                "nameLocation": "middle",
                "nameGap": 28,
            },
            "yAxis": {
                "type": "category",
                "data": [row.label for row in rows],
                "inverse": True,
            },
            "tooltip": {"trigger": "item"},
            "series": [
                {
                    "name": "Kasvuindeks",
                    "type": "bar",
                    "labelLayout": dict(LABEL_LAYOUT),
                    "data": [
                        {
                            "value": _number(row.index),
                            "tip": row.key,
                            "label": {
                                **BAR_LABEL,
                                "show": True,
                                "position": "right",
                                "formatter": integer(row.index),
                            },
                        }
                        for row in rows
                    ],
                    # The reference line is the whole point of the scale: a bar
                    # means nothing until you can see which side of parity it
                    # falls on.
                    "markLine": {
                        "silent": True,
                        "symbol": "none",
                        "data": [{"xAxis": 100}],
                        "label": {"formatter": "100", "position": "end"},
                    },
                }
            ],
            "dashkoda": {
                "tooltip": {
                    row.key: {
                        "title": row.label,
                        "rows": [
                            {
                                "label": "Osakaal hiljuti liitunutest",
                                "value": (
                                    f"{percent(row.recent_share_pct)} ({integer(row.recent_count)})"
                                ),
                                "emphasis": False,
                            },
                            {
                                "label": "Osakaal kogu liikmeskonnast",
                                "value": (
                                    f"{percent(row.overall_share_pct)} "
                                    f"({integer(row.overall_count)})"
                                ),
                                "emphasis": False,
                            },
                            {
                                "label": "Kasvuindeks",
                                "value": integer(row.index),
                                "emphasis": True,
                            },
                        ],
                        "note": "100 = sama osakaal mõlemas",
                    }
                    for row in rows
                },
                "axisFormat": {"x": "integer"},
            },
        }
    )

    footnotes = [
        "Kasvuindeks = hiljuti liitunute osakaal jagatud kogu liikmeskonna "
        "osakaaluga, korrutatud sajaga. 100 tähendab sama esindatust, üle 100 "
        "suuremat ja alla 100 väiksemat.",
        "«Hiljuti liitunud» on need tänased liikmed, kelle liitumiskuupäev jääb "
        "hetkeseisule eelnenud 12 kuu sisse. See ei ole kõigi viimase aasta "
        "uute liikmete arv — vahepeal lahkunuid nimekirjas ei ole.",
    ]
    if suppressed:
        footnotes.append(
            f"{len(suppressed)} kategooriat on välja jäetud, sest neis on "
            "võrdluseks liiga vähe liikmeid. Neid ei kuvata nullina."
        )

    return ChartPayload(
        payload_id="membership-composition-growth-index",
        title=f"{dimension_label}: esindatus hiljuti liitunute seas",
        option=option,
        size="categorical",
        question="Millised organisatsioonid on hiljuti liitunute seas üle esindatud?",
        observation_label=(
            f"Seisuga {long_date(snapshot_date)} · {integer(recent_total)} hiljuti liitunut"
        ),
        table_headers=(
            "Kategooria",
            "Hiljuti liitunuid",
            "Osakaal hiljuti",
            "Liikmeid kokku",
            "Osakaal kokku",
            "Kasvuindeks",
        ),
        table_rows=tuple(
            (
                row.label,
                row.recent_count,
                percentage(row.recent_share_pct, places=1),
                row.overall_count,
                percentage(row.overall_share_pct, places=1),
                row.index,
            )
            for row in rows
        ),
        summary=(
            f"Horisontaalne tulpgraafik {len(rows)} kategooria kasvuindeksiga, "
            "võrdlusjoon 100 juures."
        ),
        empty_message="Kasvuindeksi arvutamiseks ei ole piisavalt liikmeid.",
        footnotes=tuple(footnotes),
    )
