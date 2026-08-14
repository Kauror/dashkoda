"""Server-prepared chart payloads for the news dashboard.

The same contract the membership charts use, for the same reasons: the browser
receives finished data and draws it, the payload is read from a non-executable
`application/json` block so no inline script and no `unsafe-eval` is needed, and
every chart carries its accessible alternative — a text summary and the identical
values as table rows — in the same object.

Three rules hold here as they do there:

- **an absent value produces no point.** Nothing is zero-filled and nothing is
  interpolated across a gap;
- **a partial period is labelled partial.** The current month is nearly always
  incomplete, and a bar drawn to two-thirds of its eventual height reads as a
  collapse in publishing rather than as a month that has not finished;
- **no chart draws two units against two axes.** A percentage and a volume on one
  plot is a picture whose shape is chosen by the axis scaling, and the reader
  cannot tell which line they are being invited to believe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from apps.core.formatting import integer, month_name, percent, short_date

#: Chart geometry, shared so the news charts cannot drift apart from each other.
GRID = {"left": 56, "right": 24, "top": 32, "bottom": 40, "containLabel": True}

#: A bar's own count, written where the reader is meant to take it off the
#: drawing rather than off the axis.
BAR_LABEL = {"fontSize": 12, "fontWeight": 600, "distance": 6}


@dataclass(frozen=True)
class Readout:
    """One figure in a chart's analytical header. Every string arrives formatted."""

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
class ChartPayload:
    """One chart plus the accessible alternative that always accompanies it."""

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
    size: str = "medium"

    @property
    def has_data(self) -> bool:
        return bool(self.table_rows)


def _axis(labels: list[str]) -> dict:
    """A category axis that never rotates its labels.

    Article titles and month names are read horizontally or not at all; a
    forty-five-degree label is a label somebody has to tilt their head for.
    """
    return {
        "type": "category",
        "data": labels,
        "axisTick": {"show": False},
        "axisLine": {"show": False},
        "axisLabel": {"hideOverlap": True},
    }


def publishing_cadence(
    buckets: list[tuple[date, int, int, int]],
    *,
    grain: str,
    partial_from: date | None = None,
) -> ChartPayload:
    """How much was published per period, split by whose news it was.

    One stacked bar chart rather than two — a volume chart and a mix chart drawn
    from the same counts would be the same data twice, and the reader would have
    to check one against the other. The stack answers both: its height is the
    output and its division is the mix.

    `unknown` is stacked as its own segment rather than dropped or folded into
    either category. An article DashKoda could not classify is still an article
    the Chamber published, and hiding it would make the bars disagree with the
    publication count above them.
    """
    labels = [_bucket_label(start, grain) for start, _, _, _ in buckets]
    chamber = [row[1] for row in buckets]
    partner = [row[2] for row in buckets]
    unknown = [row[3] for row in buckets]

    def series(name: str, values: list[int], colour_index: int) -> dict:
        return {
            "name": name,
            "type": "bar",
            "stack": "avaldatud",
            "data": values,
            "emphasis": {"focus": "series"},
            "itemStyle": {"borderRadius": [0, 0, 0, 0]},
            "colorBy": "series",
            "z": colour_index,
        }

    option = {
        "grid": GRID,
        "xAxis": _axis(labels),
        "yAxis": {"type": "value", "splitLine": {"show": True}},
        "series": [
            series("Koja uudised", chamber, 1),
            series("Sõprade uudised", partner, 2),
            series("Liik teadmata", unknown, 3),
        ],
        "legend": {"show": True, "bottom": 0},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "animation": True,
    }

    rows = tuple(
        (
            _bucket_label(start, grain),
            integer(c + p + u),
            integer(c),
            integer(p),
            integer(u) if u else "–",
            "osaline" if partial_from and start >= partial_from else "",
        )
        for start, c, p, u in buckets
    )
    total = sum(c + p + u for _, c, p, u in buckets)
    return ChartPayload(
        payload_id="news-publishing-cadence",
        title="Avaldatud uudised",
        question="Kui palju me avaldame ja kuidas jaguneb Koja ja Sõprade uudiste vahel?",
        option=option,
        table_headers=("Periood", "Kokku", "Koja", "Sõprade", "Liik teadmata", "Märkus"),
        table_rows=rows,
        summary=(
            f"Tulpdiagramm avaldatud uudiste arvust perioodide kaupa, kokku "
            f"{integer(total)} uudist. Iga tulp on jagatud Koja ja Sõprade uudisteks."
        ),
        empty_message="Valitud perioodil ei ole avaldatud uudiseid.",
        footnotes=(("Viimane periood ei ole veel lõppenud." if partial_from else ""),)
        if partial_from
        else (),
        size="medium",
    )


def _bucket_label(start: date, grain: str) -> str:
    if grain == "month":
        return f"{month_name(start.month, short=True)} {start.year % 100:02d}"
    return short_date(start)


#: The bands a first-month distribution is drawn in.
#:
#: Fixed rather than derived from the cohort's own quartiles, because a histogram
#: whose bands *are* the quartiles shows four equal bars by construction and
#: tells the reader nothing. These were chosen against the real distribution —
#: median 28, p75 70, p90 149 — so the shape is visible rather than crammed into
#: one band.
DISTRIBUTION_BANDS: tuple[tuple[int, int | None, str], ...] = (
    (0, 0, "0"),
    (1, 9, "1–9"),
    (10, 29, "10–29"),
    (30, 69, "30–69"),
    (70, 149, "70–149"),
    (150, None, "150+"),
)


def first_month_distribution(values: list[int], stats) -> ChartPayload:
    """What a normal first month looks like, as a shape rather than a number.

    A median on its own does not tell a reader whether 300 views is remarkable or
    ordinary. The distribution does, and it is the difference between "this
    article got 300" and "this article is in the top tenth".
    """
    counts = []
    for low, high, _label in DISTRIBUTION_BANDS:
        counts.append(
            sum(1 for value in values if value >= low and (high is None or value <= high))
        )
    labels = [label for _, _, label in DISTRIBUTION_BANDS]

    option = {
        "grid": GRID,
        "xAxis": _axis(labels),
        "yAxis": {"type": "value", "splitLine": {"show": True}},
        "series": [
            {
                "type": "bar",
                "data": counts,
                "label": {"show": True, "position": "top", **BAR_LABEL},
                "emphasis": {"focus": "none"},
            }
        ],
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "animation": True,
    }
    rows = tuple((label, integer(count)) for label, count in zip(labels, counts, strict=True))
    return ChartPayload(
        payload_id="news-first-month-distribution",
        title="Esimese 30 päeva vaatamised",
        question="Mis on uudise puhul tavaline tulemus?",
        option=option,
        table_headers=("Vaatamisi esimese 30 päevaga", "Uudiseid"),
        table_rows=rows,
        summary=(
            f"Tulpdiagramm {integer(len(values))} uudise esimese 30 päeva vaatamiste "
            f"jaotusest. Mediaan on {integer(stats.median)} vaatamist."
        ),
        empty_message="Ühelgi uudisel ei ole veel täielikku 30 päeva mõõdetud.",
        readouts=(
            Readout(label="Mediaan", value=f"{integer(stats.median)} vaatamist"),
            Readout(
                label="Keskmine pool",
                value=f"{integer(stats.p25)} – {integer(stats.p75)}",
                note="25.–75. protsentiil",
            ),
            Readout(label="Uudiseid", value=integer(stats.count)),
        ),
        footnotes=(
            "Ainult uudised, mille esimesed 30 päeva jäävad tervikuna mõõdetud perioodi sisse.",
        ),
        size="categorical",
    )


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


__all__ = [
    "DISTRIBUTION_BANDS",
    "ChartPayload",
    "Readout",
    "first_month_distribution",
    "newsletter_rates",
    "publishing_cadence",
]
