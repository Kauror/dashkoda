"""Server-prepared chart payloads for the Õigusloome intelligence dashboard.

The browser receives finished data and draws it. It never filters, never fills a
gap and never decides what is safe to show — those decisions belong to
`analytics.py`. A payload is read from a non-executable `application/json`
block, which is why no chart here needs an inline script or a relaxed Content
Security Policy.

The payload shape comes from `apps.core.chart_payload` — the contract of
`dashboard/components/chart_figure.html`, written once and owned by neither
feature module. What a legal-work chart says, asks and shows stays decided
here; only the shape those decisions travel in is shared.

Three rules hold throughout:

- **an absent value produces no point.** There is no zero substitution and no
  interpolation across a gap;
- **a partial period is marked as partial** wherever it appears — in the title,
  in the table and in the drawing — and never by colour alone;
- **annual counts are drawn as discrete observations.** A smoothed curve between
  two yearly totals would imply measurements that were never taken.
"""

from __future__ import annotations

from apps.core.chart_payload import ChartPayload, Readout
from apps.core.formatting import integer, month_name, percent, signed_integer, signed_percent

from .analytics import (
    ActiveAge,
    AnnualPoint,
    CategoryRow,
    DeadlinePressure,
    FeedbackCategoryRow,
    MonthlyFlow,
    ResponseWindowDistribution,
    ResponseWindowYear,
    StageBreakdown,
    YearOnYear,
)

GRID = {"left": 56, "right": 24, "top": 32, "bottom": 40, "containLabel": True}

#: A bar that carries its count at its end, as `chart_figure` readers expect to
#: take the figure straight off the drawing rather than from the axis.
BAR_LABEL = {"fontSize": 12, "fontWeight": 600, "distance": 6}
LABEL_LAYOUT = {"hideOverlap": True}

#: Suffix appended to a period that the data has not finished covering.
PARTIAL_SUFFIX = "osalise aasta seis"
PARTIAL_MONTH_SUFFIX = "osalise kuu seis"


def _base_option(*, legend: bool = True) -> dict:
    return {
        "grid": dict(GRID),
        "tooltip": {"trigger": "axis"},
        "legend": {"show": legend, "bottom": 0},
        "animation": True,
    }


def year_on_year_readout(label: str, comparison: YearOnYear | None) -> Readout:
    """The mandatory year-on-year readout, with both deltas and no false ratio.

    A percentage against a zero baseline is not infinity and not a hundred per
    cent, so it is rendered as a dash while the absolute delta — which remains
    perfectly valid — is still shown.
    """
    if comparison is None:
        return Readout(label=label, value="–", note="Andmeallikas ei ole ühendatud.")

    percent_text = (
        signed_percent(comparison.percent_change) if comparison.percent_change is not None else "–"
    )
    return Readout(
        label=label,
        value=integer(comparison.current),
        change=f"{signed_integer(comparison.absolute_change)} ({percent_text})",
        change_label=(
            f"{signed_integer(comparison.absolute_change)} võrreldes eelmise aasta "
            f"sama kuupäevaga, {percent_text}"
        ),
        direction=comparison.direction,
        note="",
    )


# --------------------------------------------------------------------------
# Mandatory: active matters by stage
# --------------------------------------------------------------------------


def active_stage_chart(breakdown: StageBreakdown) -> ChartPayload:
    """`Aktiivsed teemad hetkeseisu kaupa` — where open work sits in the process.

    Horizontal bars, sorted by size, each carrying its own count. One bar colour
    rather than a hue per stage: the categories are ordered by quantity, not by
    kind, and nine colours would suggest nine meanings the data does not carry.

    The bars reconcile exactly with the active total, blanks included, and the
    title states that total so the two can be checked against each other.
    """
    stages = breakdown.stages
    # ECharts draws a horizontal category axis bottom-up, so the largest bar
    # ends up on top only if the data is reversed.
    labels = [stage.label for stage in reversed(stages)]
    counts = [stage.count for stage in reversed(stages)]

    option = {
        **_base_option(legend=False),
        "grid": {**GRID, "left": 8, "right": 48},
        "tooltip": {"trigger": "item"},
        "xAxis": {"type": "value", "axisLabel": {"show": False}, "splitLine": {"show": False}},
        "yAxis": {"type": "category", "data": labels},
        "series": [
            {
                "type": "bar",
                "name": "Teemasid",
                "data": counts,
                "label": {"show": True, "position": "right", **BAR_LABEL},
                "labelLayout": LABEL_LAYOUT,
            }
        ],
    }

    return ChartPayload(
        payload_id="legal-active-stages",
        title=f"Aktiivsed teemad hetkeseisu kaupa – kokku {integer(breakdown.total)}",
        question="Kus asuvad praegu töös olevad teemad õigusloome protsessis?",
        option=option,
        table_headers=("Hetkeseis", "Teemasid", "Osakaal"),
        table_rows=tuple(
            (
                stage.label,
                integer(stage.count),
                percent(stage.count / breakdown.total * 100) if breakdown.total else "–",
            )
            for stage in stages
        ),
        summary=(
            f"{integer(breakdown.total)} aktiivset teemat jaguneb {len(stages)} hetkeseisu vahel."
        ),
        empty_message="Aktiivseid teemasid ei ole.",
        size="categorical",
    )


# --------------------------------------------------------------------------
# Mandatory: monthly flow
# --------------------------------------------------------------------------


def monthly_flow_chart(
    *,
    payload_id: str,
    title: str,
    question: str,
    current: MonthlyFlow,
    previous: MonthlyFlow,
    series_label: str,
) -> ChartPayload:
    """Twelve months of one measure, this year against last.

    Paired bars rather than two lines: these are discrete monthly counts, and a
    line between them would imply a value on the days in between. The current
    month is drawn hollow when the data has not finished covering it, and the
    same fact is repeated in the table — a reader who cannot see the difference
    in the drawing still reads it in words.
    """
    months = list(range(1, 13))
    labels = [month_name(month, short=True) for month in months]

    def series_data(flow: MonthlyFlow) -> list:
        points: list = []
        for month in months:
            if month > len(flow.counts):
                # The year has not reached this month: no bar, not a zero.
                points.append(None)
                continue
            value = flow.counts[month - 1]
            if month == flow.partial_month:
                points.append(
                    {
                        "value": value,
                        "itemStyle": {"color": "transparent", "borderWidth": 2},
                    }
                )
            else:
                points.append(value)
        return points

    option = {
        **_base_option(),
        "xAxis": {"type": "category", "data": labels},
        "yAxis": {"type": "value", "minInterval": 1},
        "series": [
            {
                "type": "bar",
                "name": str(previous.year),
                "data": series_data(previous),
            },
            {
                "type": "bar",
                "name": str(current.year),
                "data": series_data(current),
            },
        ],
    }

    def cell(flow: MonthlyFlow, month: int) -> str:
        if month > len(flow.counts):
            return "–"
        text = integer(flow.counts[month - 1])
        return f"{text} ({PARTIAL_MONTH_SUFFIX})" if month == flow.partial_month else text

    rows = tuple(
        (month_name(month), cell(current, month), cell(previous, month)) for month in months
    )

    footnotes: list[str] = []
    if current.partial_month is not None:
        footnotes.append(
            f"{month_name(current.partial_month)} {current.year} on osaline: "
            "andmed ulatuvad aruandekuupäevani."
        )
    if current.missing_date_count:
        footnotes.append(
            f"{integer(current.missing_date_count)} teemal puudub saabumise kuupäev "
            "ja neid ei ole kuugraafikus."
        )

    return ChartPayload(
        payload_id=payload_id,
        title=title,
        question=question,
        option=option,
        table_headers=("Kuu", str(current.year), str(previous.year)),
        table_rows=rows,
        summary=(
            f"{series_label} kuude lõikes: {current.year} kokku "
            f"{integer(current.total)}, {previous.year} kokku {integer(previous.total)}."
        ),
        footnotes=tuple(footnotes),
        empty_message="Kuuandmeid ei ole.",
    )


# --------------------------------------------------------------------------
# Mandatory: opinions sent per year
# --------------------------------------------------------------------------


def annual_sent_chart(
    points: tuple[AnnualPoint, ...], comparison: YearOnYear | None
) -> ChartPayload:
    """`Välja saadetud arvamused aastate lõikes`.

    Bars rather than a smoothed line. These are annual measured counts, and a
    spline through them would draw values for moments between two Decembers
    that nobody observed.

    The current year is both drawn hollow and labelled `YTD` in its axis
    category and its table row, because a partial bar that looks like a
    finished one invites the reader to see a collapse every January.
    """
    labels: list[str] = []
    data: list = []
    for point in points:
        labels.append(f"{point.year} (YTD)" if point.is_partial else str(point.year))
        if point.is_partial:
            data.append(
                {
                    "value": point.count,
                    "itemStyle": {"color": "transparent", "borderWidth": 2},
                }
            )
        else:
            data.append(point.count)

    option = {
        **_base_option(legend=False),
        "xAxis": {"type": "category", "data": labels},
        "yAxis": {"type": "value", "minInterval": 1},
        "series": [
            {
                "type": "bar",
                "name": "Arvamusi",
                "data": data,
                "label": {"show": True, "position": "top", **BAR_LABEL},
                "labelLayout": LABEL_LAYOUT,
            }
        ],
    }

    rows: list[tuple] = []
    previous_count: int | None = None
    for point in points:
        if point.is_partial or previous_count is None:
            # A part-year change against a full year would be the exact
            # comparison this dashboard exists to avoid.
            change = "–"
        else:
            change = signed_integer(point.count - previous_count)
        rows.append(
            (
                f"{point.year} (YTD)" if point.is_partial else str(point.year),
                integer(point.count),
                change,
            )
        )
        previous_count = point.count

    footnotes: list[str] = []
    if comparison is not None and any(point.is_partial for point in points):
        footnotes.append(
            f"{comparison.current_cutoff.year} on osaline aasta: "
            f"{integer(comparison.current)} arvamust seisuga "
            f"{comparison.current_cutoff:%d.%m.%Y}, eelmisel aastal sama kuupäevani "
            f"{integer(comparison.previous)}."
        )

    return ChartPayload(
        payload_id="legal-annual-sent",
        title="Välja saadetud arvamused aastate lõikes",
        question="Kuidas on Koja arvamuste maht aastate jooksul muutunud?",
        option=option,
        table_headers=("Aasta", "Arvamusi", "Muutus"),
        table_rows=tuple(rows),
        summary=(f"Välja saadetud arvamused {len(points)} aasta lõikes; jooksev aasta on osaline."),
        footnotes=tuple(footnotes),
        empty_message="Välja saadetud arvamusi ei ole.",
        size="large",
    )


# --------------------------------------------------------------------------
# Mandatory: response window, median and mean
# --------------------------------------------------------------------------


def response_window_chart(years: tuple[ResponseWindowYear, ...]) -> ChartPayload:
    """`Arvamuse esitamiseks antud keskmine aeg`, as median and mean together.

    Both series, on one axis in one unit. The median is the typical matter; the
    mean sits above it whenever a handful of long consultations pull it up, and
    showing only the mean would overstate how much time a lawyer usually gets.

    A year's eligible count travels with it in the table, because an average
    from four matters and one from a hundred and eighty are not equally
    informative and the line alone cannot say so.
    """
    labels = [f"{year.year} (YTD)" if year.is_partial else str(year.year) for year in years]

    option = {
        **_base_option(),
        "xAxis": {"type": "category", "data": labels},
        "yAxis": {"type": "value", "name": "päeva", "minInterval": 1},
        "series": [
            {
                "type": "line",
                "name": "Mediaan",
                "data": [year.median for year in years],
                "smooth": False,
                "connectNulls": False,
            },
            {
                "type": "line",
                "name": "Keskmine",
                "data": [year.mean for year in years],
                "smooth": False,
                "connectNulls": False,
            },
        ],
    }

    def days(value: float | None) -> str:
        return f"{value:.0f}" if value is not None else "–"

    return ChartPayload(
        payload_id="legal-response-window",
        title="Arvamuse esitamiseks antud keskmine aeg",
        question="Kui palju aega antakse Kojale arvamuse esitamiseks?",
        option=option,
        table_headers=("Aasta", "Mediaan (päeva)", "Keskmine (päeva)", "Arvestatud teemasid"),
        table_rows=tuple(
            (
                f"{year.year} (YTD)" if year.is_partial else str(year.year),
                days(year.median),
                days(year.mean),
                integer(year.eligible),
            )
            for year in years
        ),
        summary=("Arvamuse esitamiseks antud aeg aastate lõikes, mediaan ja keskmine päevades."),
        footnotes=(
            "Arvestatud on ainult teemad, millel on nii saabumise kuupäev kui ka tähtaeg "
            "ning tähtaeg ei ole saabumisest varasem.",
        ),
        empty_message="Vastamisaja andmeid ei ole.",
        size="large",
    )


def response_window_distribution_chart(
    distribution: ResponseWindowDistribution,
) -> ChartPayload:
    """How the consultation windows are actually spread.

    Answers what an annual average cannot: an unchanged mean hides a move from
    steady fortnights to a mix of three-day and two-month windows.
    """
    labels = [label for label, _count in distribution.bands]
    counts = [count for _label, count in distribution.bands]

    option = {
        **_base_option(legend=False),
        "xAxis": {"type": "category", "data": labels},
        "yAxis": {"type": "value", "minInterval": 1},
        "series": [
            {
                "type": "bar",
                "name": "Teemasid",
                "data": counts,
                "label": {"show": True, "position": "top", **BAR_LABEL},
                "labelLayout": LABEL_LAYOUT,
            }
        ],
    }

    share = distribution.short_window_share
    return ChartPayload(
        payload_id="legal-window-distribution",
        title="Arvamuse esitamiseks antud aja jaotus",
        question="Kui sageli on vastamisaeg lühike?",
        option=option,
        readouts=(
            Readout(
                label="Kuni 14 päeva arvamuse esitamiseks",
                value=integer(distribution.short_window_count),
                note=f"{percent(share)} arvestatud teemadest" if share is not None else "",
            ),
        ),
        table_headers=("Vahemik", "Teemasid"),
        table_rows=tuple((label, integer(count)) for label, count in distribution.bands),
        summary=(
            f"{integer(distribution.eligible)} teema vastamisaja jaotus "
            f"{len(distribution.bands)} vahemikus."
        ),
        empty_message="Vastamisaja andmeid ei ole.",
    )


# --------------------------------------------------------------------------
# Supporting analytical charts
# --------------------------------------------------------------------------


def active_age_chart(age: ActiveAge) -> ChartPayload:
    """How long the open matters have been open.

    Titled as age, not as delay. A European file legitimately stays open for
    years, and calling that a backlog would misread the process rather than
    describe it.
    """
    labels = [label for label, _count in age.bands]
    counts = [count for _label, count in age.bands]

    option = {
        **_base_option(legend=False),
        "grid": {**GRID, "left": 8, "right": 48},
        "tooltip": {"trigger": "item"},
        "xAxis": {"type": "value", "axisLabel": {"show": False}, "splitLine": {"show": False}},
        "yAxis": {"type": "category", "data": list(reversed(labels))},
        "series": [
            {
                "type": "bar",
                "name": "Teemasid",
                "data": list(reversed(counts)),
                "label": {"show": True, "position": "right", **BAR_LABEL},
                "labelLayout": LABEL_LAYOUT,
            }
        ],
    }

    footnotes: list[str] = []
    if age.missing_received_date:
        footnotes.append(
            f"{integer(age.missing_received_date)} aktiivsel teemal puudub saabumise "
            "kuupäev ja neid ei ole vanuse arvestuses."
        )
    if age.future_received_date:
        footnotes.append(
            f"{integer(age.future_received_date)} aktiivse teema saabumise kuupäev on "
            "aruandekuupäevast hilisem; need on andmekvaliteedi küsimus ja jäävad "
            "vanuse arvestusest välja."
        )

    return ChartPayload(
        payload_id="legal-active-age",
        title="Aktiivsete teemade vanus",
        question="Kui kaua on praegused teemad juba töös olnud?",
        option=option,
        readouts=(
            Readout(
                label="Mediaanvanus",
                value=f"{age.median:.0f} päeva" if age.median is not None else "–",
                note=f"{integer(age.measured)} teemal mõõdetud",
            ),
        ),
        table_headers=("Vanus", "Teemasid"),
        table_rows=tuple((label, integer(count)) for label, count in age.bands),
        summary=f"{integer(age.measured)} aktiivse teema vanus vahemike kaupa.",
        footnotes=tuple(footnotes),
        empty_message="Aktiivseid teemasid ei ole.",
        size="categorical",
    )


def deadline_pressure_chart(pressure: DeadlinePressure) -> ChartPayload:
    """Where deadline load is building among the open matters.

    The passed deadlines are split in two and reported beside the chart rather
    than inside it, because only one of them is outstanding work: a matter whose
    opinion has already gone out is not late.
    """
    labels = [label for label, _count in pressure.bands]
    counts = [count for _label, count in pressure.bands]

    option = {
        **_base_option(legend=False),
        "xAxis": {"type": "category", "data": labels},
        "yAxis": {"type": "value", "minInterval": 1},
        "series": [
            {
                "type": "bar",
                "name": "Teemasid",
                "data": counts,
                "label": {"show": True, "position": "top", **BAR_LABEL},
                "labelLayout": LABEL_LAYOUT,
            }
        ],
    }

    return ChartPayload(
        payload_id="legal-deadline-pressure",
        title="Tähtaegade koondumine",
        question="Kuhu koonduvad lähenevad tähtajad?",
        option=option,
        readouts=(
            Readout(label="Tähtaeg 7 päeva jooksul", value=integer(pressure.due_within_7)),
            Readout(
                label="Tähtaeg möödas, arvamus ootel",
                value=integer(pressure.overdue_pending),
            ),
            Readout(
                label="Tähtaeg möödas, arvamus juba saadetud",
                value=integer(pressure.overdue_already_sent),
                note="teema on endiselt avatud",
            ),
        ),
        table_headers=("Tähtajani", "Teemasid"),
        table_rows=tuple((label, integer(count)) for label, count in pressure.bands),
        summary=(
            f"{integer(pressure.upcoming_total)} aktiivsel teemal on tulevane tähtaeg; "
            f"{integer(pressure.without_deadline)} teemal tähtaeg puudub."
        ),
        footnotes=(
            f"{integer(pressure.without_deadline)} aktiivsel teemal ei ole tähtaega märgitud.",
        ),
        empty_message="Tähtaegadega teemasid ei ole.",
    )


def annual_topics_chart(points: tuple[AnnualPoint, ...]) -> ChartPayload:
    """`Teemad aastate lõikes`, by the register's own annual grouping.

    Context for the opinion series rather than a comparison with it: one
    arriving matter does not produce one opinion, so the two are never drawn on
    a shared axis or subtracted from each other.
    """
    labels: list[str] = []
    data: list = []
    for point in points:
        labels.append(f"{point.year} (YTD)" if point.is_partial else str(point.year))
        if point.is_partial:
            data.append(
                {"value": point.count, "itemStyle": {"color": "transparent", "borderWidth": 2}}
            )
        else:
            data.append(point.count)

    option = {
        **_base_option(legend=False),
        "xAxis": {"type": "category", "data": labels},
        "yAxis": {"type": "value", "minInterval": 1},
        "series": [
            {
                "type": "bar",
                "name": "Teemasid",
                "data": data,
                "label": {"show": True, "position": "top", **BAR_LABEL},
                "labelLayout": LABEL_LAYOUT,
            }
        ],
    }

    return ChartPayload(
        payload_id="legal-annual-topics",
        title="Teemad aastate lõikes",
        question="Kui palju õigusloome tööd on Kojal aastate lõikes olnud?",
        option=option,
        table_headers=("Aasta", "Teemasid"),
        table_rows=tuple(
            (f"{point.year} (YTD)" if point.is_partial else str(point.year), integer(point.count))
            for point in points
        ),
        summary=f"Teemade arv {len(points)} aasta lõikes; jooksev aasta on osaline.",
        footnotes=(
            "Aasta on registri enda jaotus (lähteaasta), mitte saabumise kuupäev, "
            "nii et detsembris saabunud teema võib kuuluda järgmise aasta hulka.",
        ),
        empty_message="Teemasid ei ole.",
        size="large",
    )


def feedback_category_chart(
    rows: tuple[FeedbackCategoryRow, ...],
    *,
    payload_id: str,
    title: str,
    question: str,
    category_header: str,
) -> ChartPayload:
    """Where member participation is concentrated.

    The number of *measured* topics travels beside every bar, because a category
    with two feedback topics out of three and one with two out of ninety would
    otherwise draw identical bars.
    """
    labels = [row.label for row in rows]
    counts = [row.with_feedback for row in rows]

    option = {
        **_base_option(legend=False),
        "grid": {**GRID, "left": 8, "right": 48},
        "tooltip": {"trigger": "item"},
        "xAxis": {"type": "value", "axisLabel": {"show": False}, "splitLine": {"show": False}},
        "yAxis": {"type": "category", "data": list(reversed(labels))},
        "series": [
            {
                "type": "bar",
                "name": "Tagasisidega teemasid",
                "data": list(reversed(counts)),
                "label": {"show": True, "position": "right", **BAR_LABEL},
                "labelLayout": LABEL_LAYOUT,
            }
        ],
    }

    return ChartPayload(
        payload_id=payload_id,
        title=title,
        question=question,
        option=option,
        table_headers=(category_header, "Tagasisidega teemasid", "Mõõdetud teemasid", "Juhtumeid"),
        table_rows=tuple(
            (
                row.label,
                integer(row.with_feedback),
                integer(row.tracked),
                integer(row.instances) if row.instances is not None else "–",
            )
            for row in rows
        ),
        summary=f"Liikmete tagasiside jaotus {len(rows)} kategooria lõikes.",
        footnotes=(
            "Kirjeldav jaotus. See, et mõne kategooria teemadel antakse rohkem "
            "tagasisidet, ei tähenda, et kategooria selle põhjustas.",
            "Kategooriad, kus tagasisidet ei ole üldse mõõdetud, jäetakse välja, "
            "et neid ei loetaks nullideks.",
        ),
        empty_message="Tagasiside andmeid ei ole veel piisavalt.",
        size="categorical",
    )


def category_chart(
    rows: tuple[CategoryRow, ...],
    *,
    payload_id: str,
    title: str,
    question: str,
    category_header: str,
) -> ChartPayload:
    """A ranking by volume, with the median withheld below the sample floor.

    Exact source strings are the categories. Two spellings of one ministry are
    not merged and a renamed ministry is not joined to its predecessor: no
    automatic rule can tell a typo from a reorganisation, and guessing would
    invent a continuity the register does not record.
    """
    labels = [row.label for row in rows]
    counts = [row.topics for row in rows]

    option = {
        **_base_option(legend=False),
        "grid": {**GRID, "left": 8, "right": 48},
        "tooltip": {"trigger": "item"},
        "xAxis": {"type": "value", "axisLabel": {"show": False}, "splitLine": {"show": False}},
        "yAxis": {"type": "category", "data": list(reversed(labels))},
        "series": [
            {
                "type": "bar",
                "name": "Teemasid",
                "data": list(reversed(counts)),
                "label": {"show": True, "position": "right", **BAR_LABEL},
                "labelLayout": LABEL_LAYOUT,
            }
        ],
    }

    return ChartPayload(
        payload_id=payload_id,
        title=title,
        question=question,
        option=option,
        table_headers=(category_header, "Teemasid", "Arvamusi", "Vastamisaja mediaan"),
        table_rows=tuple(
            (
                row.label,
                integer(row.topics),
                integer(row.sent),
                f"{row.median_window:.0f} päeva" if row.median_window is not None else "–",
            )
            for row in rows
        ),
        summary=f"{len(rows)} suurimat kategooriat teemade arvu järgi.",
        footnotes=(
            "Vastamisaja mediaan on näidatud ainult kategooriatel, kus on piisavalt "
            "arvestatavaid teemasid; mujal on tulemus liiga kõikuv, et võrrelda.",
            "Kategooriad on lähteandmete täpsed väärtused. Sarnaselt kirjutatud või "
            "ümber nimetatud asutusi ei ole automaatselt kokku liidetud.",
        ),
        empty_message="Kategooriaid ei ole.",
        size="categorical",
    )
