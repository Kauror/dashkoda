"""Server-prepared chart payloads for the Sündmused dashboard.

The browser receives a finished ECharts option and draws it. It never filters,
never fills a gap and never decides what is safe to show — those decisions live
in `analytics.py`, `attention.py` and `commerce.py`, where they can be tested.

Every payload here satisfies the contract of
`dashboard/components/chart_figure.html` and `frontend/src/charts.js`: a plain
option object, plus the text summary and the identical values as table rows. The
table is **not** a fallback that appears when something breaks — it stays in the
document, so a reader who never sees the canvas gets the same numbers, and a
keyboard reader gets them at all.

The payload shape comes from `apps.core.chart_payload` — the template's
contract written once, owned by neither dashboard. While the dashboards were
built on parallel branches each carried its own copy rather than import a
sibling's presenter; with the branches integrated, the copies were folded into
that one definition.

Chart grammar, following `docs/design-system.md`:

- volume over time is **bars**, never a smoothed line. A monthly event count is
  a discrete measurement and a spline between two months draws events that do
  not exist;
- a ranking of long names is **horizontal bars**. Event titles and Estonian
  theme labels do not fit a rotated x axis, and a rotated axis is unreadable
  anyway;
- a mix is a **100% stacked bar**, not a pie;
- no dual axes, and no chart mixing a count with a rate.
"""

from __future__ import annotations

from apps.core.chart_payload import ChartPayload, Readout
from apps.core.formatting import integer, percent

GRID = {"left": 56, "right": 24, "top": 32, "bottom": 40, "containLabel": True}

#: A horizontal ranking needs room for its value labels at the bar ends and for
#: the category names on the left. Estonian theme labels run long.
GRID_RANKING = {"left": 8, "right": 72, "top": 16, "bottom": 24, "containLabel": True}

BAR_LABEL = {"fontSize": 12, "fontWeight": 600, "distance": 6}

#: `hideOverlap` belongs to the series, not to the option root — set at the root
#: ECharts silently ignores it and narrow widths print labels over each other.
LABEL_LAYOUT = {"hideOverlap": True}

#: How many bars a horizontal ranking draws before it stops being readable.
RANKING_LIMIT = 10


def _tip(title: str, rows: tuple[tuple[str, str], ...], note: str = "") -> dict:
    """A pre-rendered tooltip, formatted in Python like every other figure."""
    return {
        "title": title,
        "rows": [{"label": label, "value": value} for label, value in rows],
        "note": note,
    }


def _bar_series(name: str, data: list, *, colour: str | None = None) -> dict:
    series = {
        "name": name,
        "type": "bar",
        "data": data,
        "label": {"show": True, "position": "top", **BAR_LABEL},
        "labelLayout": dict(LABEL_LAYOUT),
        "barMaxWidth": 44,
    }
    if colour:
        series["itemStyle"] = {"color": colour}
    return series


def _empty(payload_id: str, title: str, message: str) -> ChartPayload:
    return ChartPayload(
        payload_id=payload_id,
        title=title,
        option={"series": []},
        table_headers=(),
        table_rows=(),
        summary=message,
        empty_message=message,
    )


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


def events_by_year_chart(volume) -> ChartPayload:
    """How much does the Chamber organise, and how has that changed?

    Bars, one per event year. Undated events are **not** in any bar — they have
    no year — and the footnote says how many there are rather than letting the
    chart quietly describe a smaller programme than the one that exists.
    """
    years = volume.years
    if not years:
        return _empty("events-by-year", "Sündmused aastate lõikes", "Programmis pole sündmusi.")

    tooltips: dict[str, dict] = {}
    data = []
    for row in years:
        key = str(row.year)
        note = ""
        if row.is_partial_history:
            note = "Programmi ajalugu algab selle aasta sees, seega pole aasta täielik."
        elif row.is_current:
            note = "Käimasolev aasta: sisaldab ka juba planeeritud sündmusi."
        tooltips[key] = _tip(
            f"{row.year}. aasta",
            (("Sündmusi", integer(row.count)),),
            note,
        )
        item = {"value": row.count, "tip": key}
        if row.is_partial_history or row.is_current:
            # Not a warning colour: an incomplete year is not an error. The
            # hollow fill is the same device the design system uses for a
            # provisional membership reading.
            item["itemStyle"] = {"color": "transparent", "borderWidth": 2}
        data.append(item)

    option = {
        "grid": dict(GRID),
        "tooltip": {"trigger": "axis"},
        "legend": {"show": False},
        "xAxis": {"type": "category", "data": [str(row.year) for row in years]},
        "yAxis": {"type": "value", "minInterval": 1},
        "series": [_bar_series("Sündmusi", data)],
        "dashkoda": {"tooltip": tooltips},
    }

    footnotes = []
    if volume.undated_count:
        footnotes.append(
            f"{integer(volume.undated_count)} sündmust ei ole ühelgi aastal: "
            "nende lähterea kuupäeva ei õnnestunud lugeda. Neid ei ole ühelgi tulbal."
        )
    if volume.first_year_is_partial and years:
        footnotes.append(
            f"{years[0].year}. aasta on osaline — programmi varaseim sündmus on "
            f"{years[0].year}. aasta sees, mitte 1. jaanuaril."
        )

    return ChartPayload(
        payload_id="events-by-year",
        title="Sündmused aastate lõikes",
        question="Kui palju Koda korraldab ja kuidas maht on muutunud?",
        option=option,
        table_headers=("Aasta", "Sündmusi", "Märkus"),
        table_rows=tuple(
            (
                str(row.year),
                integer(row.count),
                "osaline ajalugu"
                if row.is_partial_history
                else ("käimasolev aasta" if row.is_current else ""),
            )
            for row in years
        ),
        summary=(
            f"Tulpdiagramm: sündmuste arv aastate {years[0].year}–{years[-1].year} lõikes, "
            f"kokku {integer(sum(row.count for row in years))} kuupäevaga sündmust."
        ),
        footnotes=tuple(footnotes),
        size="medium",
    )


def events_by_month_with_typical_chart(volume, *, year: int) -> ChartPayload:
    """`Kuude lõikes`, with the typical month riding alongside as a line.

    The same Toimunud/Tulemas stacked bars `events_by_month_chart` draws, plus
    `seasonality_chart`'s median-per-month as a third series on the same axis
    — a dashed line rather than a fourth bar, so a reader reads it as context
    for the year rather than as one more thing the programme did. Its own
    caveats travel with it: only complete years feed the median, and it is a
    description of history, never a forecast for the months still ahead in
    `year`.
    """
    months = volume.months
    if not months or not any(row.count for row in months):
        return _empty(
            "events-by-month", "Sündmused kuude lõikes", "Sel perioodil pole kuupäevaga sündmusi."
        )

    seasonal_by_month = {row.month: row for row in volume.seasonality}
    split = any(row.completed is not None for row in months)
    years = volume.complete_years
    typical_label = f"Tüüpiline kuu (mediaan {years[0]}–{years[-1]})" if years else "Tüüpiline kuu"

    tooltips: dict[str, dict] = {}
    for row in months:
        rows: list[tuple[str, str]] = [("Sündmusi", integer(row.count))]
        if split and row.count:
            rows.append(("Toimunud", integer(row.completed or 0)))
            rows.append(("Tulemas", integer(row.upcoming or 0)))
        typical = seasonal_by_month.get(row.month)
        if typical is not None:
            rows.append(("Tüüpiline", integer(round(typical.median))))
        tooltips[str(row.month)] = _tip(f"{row.label} {year}", tuple(rows))

    labels = [row.label for row in months]
    if split:
        series = [
            _bar_series(
                "Toimunud",
                [{"value": row.completed or 0, "tip": str(row.month)} for row in months],
            ),
            _bar_series(
                "Tulemas",
                [{"value": row.upcoming or 0, "tip": str(row.month)} for row in months],
            ),
        ]
        for entry in series:
            entry["stack"] = "kuu"
            entry["label"] = {"show": False}
    else:
        series = [
            _bar_series("Sündmusi", [{"value": row.count, "tip": str(row.month)} for row in months])
        ]

    if seasonal_by_month:
        series.append(
            {
                "name": typical_label,
                "type": "line",
                "lineStyle": {"type": "dashed"},
                "symbol": "none",
                "data": [
                    {
                        "value": (
                            seasonal_by_month[row.month].median
                            if row.month in seasonal_by_month
                            else None
                        ),
                        "tip": str(row.month),
                    }
                    for row in months
                ],
            }
        )

    option = {
        "grid": dict(GRID),
        "tooltip": {"trigger": "axis"},
        "legend": {"show": True, "bottom": 0},
        "xAxis": {"type": "category", "data": labels},
        "yAxis": {"type": "value", "minInterval": 1},
        "series": series,
        "dashkoda": {"tooltip": tooltips},
    }

    headers = ("Kuu", "Sündmusi") + (("Toimunud", "Tulemas") if split else ()) + ("Tüüpiline",)
    return ChartPayload(
        payload_id="events-by-month",
        title=f"Kuude lõikes — {year}",
        question="Millised kuud on kõige tihedamad, ja kas see on tavapärane?",
        option=option,
        table_headers=headers,
        table_rows=tuple(
            (row.label, integer(row.count))
            + ((integer(row.completed or 0), integer(row.upcoming or 0)) if split else ())
            + (
                (
                    integer(round(seasonal_by_month[row.month].median))
                    if row.month in seasonal_by_month
                    else "–"
                ),
            )
            for row in months
        ),
        summary=(
            f"Tulpdiagramm: {year}. aasta sündmuste arv kuude kaupa, "
            f"kokku {integer(sum(row.count for row in months))} sündmust, "
            f"koos tüüpilise kuu mediaaniga."
        ),
        footnotes=(
            (
                "Käimasoleva aasta tulbad sisaldavad ka veel toimumata sündmusi — "
                "see on programm, mitte aasta algusest tänaseni kogunenud summa.",
            )
            if split
            else ()
        )
        + (
            ("Tüüpiline kuu on täisaastate mediaan; see on kirjeldus, mitte prognoos.",)
            if seasonal_by_month
            else ()
        ),
        size="medium",
    )


# ---------------------------------------------------------------------------
# Rankings and mixes
# ---------------------------------------------------------------------------


def ranking_chart(
    distribution,
    *,
    payload_id: str,
    title: str,
    question: str = "",
    unit: str = "sündmust",
    footnotes: tuple[str, ...] = (),
    empty_message: str = "Andmed puuduvad.",
) -> ChartPayload:
    """A horizontal ranking of one complete, mutually exclusive dimension.

    Horizontal because the labels are Estonian category names and a rotated x
    axis is unreadable. The share is drawn beside the count, so a reader takes
    both off the bar without hovering.

    `Määramata` is a row like any other. Dropping it would leave the shares
    summing to less than the population while looking like they summed to it.
    """
    if distribution is None or not distribution.has_data:
        return _empty(payload_id, title, empty_message)

    rows = list(distribution.all_rows)
    if not rows:
        return _empty(payload_id, title, empty_message)

    # ECharts draws a horizontal bar axis bottom-up, so the largest has to be
    # last in the data for it to appear at the top.
    drawn = list(reversed(rows[:RANKING_LIMIT]))
    tooltips = {
        row.key: _tip(
            row.label,
            (("Sündmusi", integer(row.count)), ("Osakaal", percent(row.share))),
        )
        for row in rows
    }
    option = {
        "grid": dict(GRID_RANKING),
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"show": False},
        "xAxis": {"type": "value", "show": False},
        "yAxis": {"type": "category", "data": [row.label for row in drawn]},
        "series": [
            {
                "name": title,
                "type": "bar",
                "data": [{"value": row.count, "tip": row.key} for row in drawn],
                "label": {"show": True, "position": "right", **BAR_LABEL},
                "labelLayout": dict(LABEL_LAYOUT),
                "barMaxWidth": 22,
            }
        ],
        "dashkoda": {"tooltip": tooltips},
    }
    return ChartPayload(
        payload_id=payload_id,
        title=title,
        question=question,
        option=option,
        table_headers=("Kategooria", unit.capitalize(), "Osakaal"),
        table_rows=tuple((row.label, integer(row.count), percent(row.share)) for row in rows),
        summary=(
            f"Horisontaalne tulpdiagramm: {title.lower()}, "
            f"{integer(distribution.total)} {unit} kokku."
        ),
        footnotes=footnotes,
        size="categorical",
    )


def mix_over_time_chart(
    by_year: dict[int, object],
    *,
    payload_id: str,
    title: str,
    keys: tuple[tuple[str, str], ...],
    # Optional, as it is on `ranking_chart`. A chart whose title already asks
    # the question does not need it asked twice, and the board struck both of
    # these on 2026-08-15.
    question: str = "",
    footnotes: tuple[str, ...] = (),
) -> ChartPayload:
    """A 100% stacked bar per year: how a mix has shifted, without a level claim.

    Shares rather than counts on purpose. The question is what proportion of the
    programme each category was, and a year with more events would otherwise
    look like a year with more of everything.
    """
    years = sorted(by_year)
    if len(years) < 2:
        return _empty(payload_id, title, "Muutuse näitamiseks on vaja vähemalt kahte aastat.")

    series = []
    for key, label in keys:
        data = []
        for year in years:
            distribution = by_year[year]
            row = next((entry for entry in distribution.all_rows if entry.key == key), None)
            data.append(
                {
                    "value": round(row.share, 1) if row else 0,
                    "tip": f"{year}:{key}",
                }
            )
        series.append(
            {
                "name": label,
                "type": "bar",
                "stack": "mix",
                "data": data,
                "barMaxWidth": 44,
            }
        )

    tooltips = {}
    for year in years:
        distribution = by_year[year]
        for key, label in keys:
            row = next((entry for entry in distribution.all_rows if entry.key == key), None)
            tooltips[f"{year}:{key}"] = _tip(
                f"{label} — {year}",
                (
                    ("Osakaal", percent(row.share if row else 0)),
                    ("Sündmusi", integer(row.count if row else 0)),
                    ("Aasta kokku", integer(distribution.total)),
                ),
            )

    option = {
        "grid": dict(GRID),
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"show": True, "bottom": 0},
        "xAxis": {"type": "category", "data": [str(year) for year in years]},
        "yAxis": {"type": "value", "max": 100, "axisLabel": {"formatter": "{value}%"}},
        "series": series,
        "dashkoda": {"tooltip": tooltips},
    }

    table_rows = []
    for year in years:
        distribution = by_year[year]
        cells = [str(year)]
        for key, _label in keys:
            row = next((entry for entry in distribution.all_rows if entry.key == key), None)
            cells.append(percent(row.share if row else 0))
        cells.append(integer(distribution.total))
        table_rows.append(tuple(cells))

    return ChartPayload(
        payload_id=payload_id,
        title=title,
        question=question,
        option=option,
        table_headers=("Aasta", *(label for _key, label in keys), "Sündmusi kokku"),
        table_rows=tuple(table_rows),
        summary=(
            f"100% virnastatud tulbad: {title.lower()} aastate {years[0]}–{years[-1]} lõikes."
        ),
        footnotes=footnotes,
        size="medium",
    )


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def attention_ranking_chart(
    rows: tuple[tuple[str, int], ...],
    *,
    payload_id: str,
    title: str,
    question: str,
    value_header: str,
    footnotes: tuple[str, ...] = (),
    empty_message: str = "Mõõdetud lehti ei ole piisavalt.",
) -> ChartPayload:
    """A ranking of events by measured page views.

    Event names go on the **y** axis. They are long, and a rotated x axis would
    make them unreadable — which the design system forbids for exactly this
    reason.
    """
    if not rows:
        return _empty(payload_id, title, empty_message)
    drawn = list(reversed(rows))
    tooltips = {name: _tip(name, ((value_header, integer(value)),)) for name, value in rows}
    option = {
        "grid": dict(GRID_RANKING),
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"show": False},
        "xAxis": {"type": "value", "show": False},
        "yAxis": {
            "type": "category",
            "data": [name for name, _value in drawn],
            "axisLabel": {"width": 220, "overflow": "truncate"},
        },
        "series": [
            {
                "name": value_header,
                "type": "bar",
                "data": [{"value": value, "tip": name} for name, value in drawn],
                "label": {"show": True, "position": "right", **BAR_LABEL},
                "labelLayout": dict(LABEL_LAYOUT),
                "barMaxWidth": 20,
            }
        ],
        "dashkoda": {"tooltip": tooltips},
    }
    return ChartPayload(
        payload_id=payload_id,
        title=title,
        question=question,
        option=option,
        table_headers=("Sündmus", value_header),
        table_rows=tuple((name, integer(value)) for name, value in rows),
        summary=f"Horisontaalne tulpdiagramm: {title.lower()}.",
        footnotes=footnotes,
        size="categorical",
    )


__all__ = [
    "ChartPayload",
    "Readout",
    "attention_ranking_chart",
    "events_by_year_chart",
    "mix_over_time_chart",
    "ranking_chart",
]
