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

from apps.core.formatting import percentage, whole_euros

from .internal_selectors import InternalTrend, MonthlyValue
from .models import SizeBand

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


def total_and_paid_chart(trend: InternalTrend) -> ChartPayload:
    """Two lines on a real time axis.

    A time axis rather than evenly spaced categories, because the observations
    are genuinely irregular — some months have one board report, some have none
    — and spacing them evenly would misrepresent when the Chamber actually
    counted.
    """
    total = [[_iso(day), _number(value)] for day, value in trend.series("total_members")]
    paid = [[_iso(day), _number(value)] for day, value in trend.series("paid_members")]

    option = _base_option()
    option.update(
        {
            "xAxis": {"type": "time"},
            "yAxis": {"type": "value", "name": "Liikmeid"},
            "series": [
                {
                    "name": "Liikmeid kokku",
                    "type": "line",
                    "showSymbol": True,
                    "symbolSize": 6,
                    # Absent values are not in the data at all, so there is
                    # nothing to connect across. This flag makes that explicit.
                    "connectNulls": False,
                    "data": total,
                },
                {
                    "name": "Tasunud liikmeid",
                    "type": "line",
                    "showSymbol": True,
                    "symbolSize": 6,
                    "connectNulls": False,
                    "data": paid,
                },
            ],
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

    return ChartPayload(
        payload_id="internal-membership-trend",
        title="Liikmeid kokku ja tasunud liikmeid",
        option=option,
        table_headers=("Kuupäev", "Liikmeid kokku", "Tasunud liikmeid", "Tasunute osakaal", "Olek"),
        table_rows=tuple(rows),
        summary=(
            f"Joongraafik {len(total)} liikmete koguarvu ja {len(paid)} tasunud liikmete "
            "vaatlusega. Kuvatakse kinnitatud või eelistatud vaatlus."
        ),
        empty_message="Sisemise aruande vaatlusi ei ole veel imporditud.",
        footnotes=tuple(footnotes),
    )


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
