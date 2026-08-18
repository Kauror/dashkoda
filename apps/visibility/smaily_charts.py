"""The Otsepostitused drawing, built whole on the server.

One chart. It followed the Smaily material out of `apps/news/charts.py` when
the newsletters became their own section, and it belongs here for the same
reason every other Smaily module does: this app owns the campaign models, the
collector and the selectors, so it owns the picture drawn from them.

The contract is the shared one — `apps.core.chart_payload.ChartPayload`,
rendered by `dashboard/components/chart_figure.html`: the browser receives
finished data and draws it, the payload is read from a non-executable
`application/json` block so no inline script and no `unsafe-eval` is needed,
and the accessible alternative — a summary and the identical values as table
rows — travels in the same object.

Two rules from the news charts came with it and still hold: an absent value
produces no point and is never zero-filled or interpolated across, and no chart
draws two units against two axes.
"""

from __future__ import annotations

from apps.core.chart_payload import ChartPayload, Readout
from apps.core.formatting import (
    integer,
    month_and_year,
    month_name,
    percent,
    percentage_points,
    short_date,
)

#: Chart geometry. Its own copy rather than an import from the news charts:
#: those numbers are that module's to change, and a shared constant across two
#: apps would make either one's tuning the other's regression.
GRID = {"left": 56, "right": 24, "top": 32, "bottom": 40, "containLabel": True}


def _axis(labels: list[str]) -> dict:
    """A category axis that never rotates its labels.

    Send dates are read horizontally or not at all; a forty-five-degree label is
    a label somebody has to tilt their head for.
    """
    return {
        "type": "category",
        "data": labels,
        "axisTick": {"show": False},
        "axisLine": {"show": False},
        "axisLabel": {"hideOverlap": True},
    }


def newsletter_rates(sends) -> ChartPayload:
    """Open and click rate per send, on one percentage axis.

    Delivered volume is deliberately **not** drawn here as a second axis. Two
    units on one plot make the shape a function of the scaling, and the volume is
    already stated per send in the table below and in the rankings beside it.
    """
    labels = [short_date(send.completed_at) for send in sends]
    opens = [
        round(send.open_rate * 100, 1) if send.open_rate is not None else None for send in sends
    ]
    clicks = [
        round(send.click_rate * 100, 1) if send.click_rate is not None else None for send in sends
    ]

    def line(name: str, values: list) -> dict:
        return {
            "name": name,
            "type": "line",
            "data": values,
            "smooth": False,
            "showSymbol": True,
            "symbolSize": 6,
            # A gap is a gap: ECharts must not bridge a send whose figures are
            # missing, because the bridge would look like a measurement.
            "connectNulls": False,
        }

    option = {
        "grid": GRID,
        "xAxis": _axis(labels),
        "yAxis": {"type": "value", "splitLine": {"show": True}, "min": 0},
        "series": [line("Avamismäär", opens), line("Klikimäär", clicks)],
        "legend": {"show": True, "bottom": 0},
        "tooltip": {"trigger": "axis"},
        "animation": True,
    }
    rows = tuple(
        (
            short_date(send.completed_at),
            send.name,
            integer(send.delivered) if send.delivered is not None else "–",
            percent(send.open_rate * 100) if send.open_rate is not None else "–",
            percent(send.click_rate * 100) if send.click_rate is not None else "–",
        )
        for send in sends
    )
    return ChartPayload(
        payload_id="newsletter-rates",
        title="Uudiskirja avamis- ja klikimäär",
        question="Kas saadetisi loetakse ja klikitakse rohkem või vähem kui varem?",
        option=option,
        table_headers=("Saadetud", "Pealkiri", "Kättetoimetatud", "Avamismäär", "Klikimäär"),
        table_rows=rows,
        summary=(
            f"Joondiagramm {integer(len(sends))} saadetise avamis- ja klikimäärast, "
            "mõlemad protsendina kättetoimetatutest."
        ),
        empty_message="Sellel uudiskirjal ei ole veel mõõdetud saadetisi.",
        footnotes=(
            "Avamismäär ja klikimäär on osakaal kättetoimetatud kirjadest. "
            "Kättetoimetatud maht on tabelis, mitte teisel teljel.",
        ),
        size="medium",
    )


def monthly_open_rate(rows, *, newsletter_label: str) -> ChartPayload:
    """One newsletter's weighted open rate, one point per calendar month.

    A single line rather than the per-send open+click pair `newsletter_rates`
    draws: this chart answers "is the trend moving", which a month's worth of
    sends folded into one point makes readable in a way a dense per-send line
    is not. Click rate is not drawn here — see `newsletter_rates` for it,
    still exact to a send.
    """
    labels = [month_name(row.month.month, short=True) for row in rows]
    values = [round(row.open_rate * 100, 1) if row.open_rate is not None else None for row in rows]

    # The last measured point carries its own value on the drawing — every
    # other point's figure is in the accessible table below it.
    last_measured = max((i for i, value in enumerate(values) if value is not None), default=None)
    series_data = [
        {"value": value, "label": {"show": index == last_measured}}
        for index, value in enumerate(values)
    ]

    option = {
        "grid": GRID,
        "xAxis": _axis(labels),
        "yAxis": {"type": "value", "splitLine": {"show": True}, "min": 0, "max": 100},
        "series": [
            {
                "name": "Avamismäär",
                "type": "line",
                "data": series_data,
                "smooth": False,
                "showSymbol": True,
                "symbolSize": 6,
                "connectNulls": False,
                "label": {
                    "position": "top",
                    "fontSize": 12,
                    "fontWeight": 600,
                    "formatter": "{c} %",
                },
            }
        ],
        "tooltip": {"trigger": "axis"},
        "animation": True,
    }

    rows_with_data = list(zip(labels, values, strict=True))
    dated = [(row.month, rate) for row, rate in zip(rows, values, strict=True) if rate is not None]
    footnote = ""
    if len(dated) >= 2:
        _, first_rate = dated[0]
        _, last_rate = dated[-1]
        peak_month, _ = max(dated, key=lambda item: item[1])
        trough_month, _ = min(dated, key=lambda item: item[1])
        change = last_rate - first_rate
        footnote = (
            f"Avamismäär on perioodi jooksul muutunud {percentage_points(change)} "
            f"— tipp {month_and_year(peak_month)}, madalseis {month_and_year(trough_month)}."
        )

    return ChartPayload(
        payload_id="mailings-monthly-open-rate",
        title="Avamismäär kuude lõikes",
        question=f"{newsletter_label} · {integer(len(rows))} kuud" if rows else newsletter_label,
        option=option,
        table_headers=("Kuu", "Avamismäär"),
        table_rows=tuple(
            (month, percent(rate) if rate is not None else "–") for month, rate in rows_with_data
        ),
        summary=(
            f"Joondiagramm {newsletter_label} avamismäärast kuude lõikes, "
            f"{integer(len(rows))} kuu ulatuses."
        ),
        empty_message="Sellel uudiskirjal ei ole veel mõõdetud kuid.",
        footnotes=(footnote,) if footnote else (),
        size="medium",
    )


__all__ = ["ChartPayload", "Readout", "monthly_open_rate", "newsletter_rates"]
