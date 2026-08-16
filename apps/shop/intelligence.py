"""Statements the dashboard is willing to make, and the rules that produce them.

Everything here is **deterministic**. A signal is a threshold crossed by a
measured figure, worded as the measurement and nothing more. There is no score,
no weighting, no ranking model and no generated prose, for two reasons that are
really the same reason:

- a composite score is unauditable. "Product health: 62" cannot be checked,
  argued with, or reproduced next month, and the number it replaces — 1 240
  views and 8 acquisitions per hundred — can be all three;
- a causal sentence is a claim about the world that this dataset cannot support.
  "Customers are losing interest" and "the price is too high" are hypotheses
  about people; what the tables hold is that acquisitions fell by 24 units.

So a signal here says what changed and by how much, and stops. What it means is
the reader's judgement, and the link beside it is how they go and form one.

## The order-structure metrics

`units / distinct orders` and `ordered value / distinct orders` are only
computed when `ShopDailySummary` can answer the population on screen. Under a
category or member filter the denominator would come from a wider population
than the numerator, producing a figure that is arithmetically fine and about
nothing. `value / unit` has no such constraint because both sides come from the
same filtered facts — but it is an average across whatever mix is selected, not
a price, and is worded accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from .selectors import ZERO

#: How many percentage points the free share must move before it is worth
#: stating. Below this the movement is ordinary period noise on a mix that
#: changes whenever one popular template is reclassified.
FREE_SHARE_POINTS = Decimal("5")

#: The top-ten share above which demand is called concentrated. Descriptive
#: only: a template catalogue is expected to be top-heavy, and the signal says
#: how top-heavy rather than whether that is a problem.
CONCENTRATION_POINTS = Decimal("60")


@dataclass(frozen=True)
class Signal:
    """One thing worth looking at, already worded as a measurement."""

    kind: str
    text: str
    detail: str = ""
    href: str = ""

    @property
    def has_detail(self) -> bool:
        return bool(self.detail)


@dataclass(frozen=True)
class OrderStructure:
    """What one order carried, where the population supports the question."""

    units_per_order: Decimal | None = None
    value_per_order: Decimal | None = None
    value_per_unit: Decimal | None = None
    #: Whether the two per-order figures rest on real distinct orders. When
    #: false they are withheld entirely rather than computed from order lines,
    #: which would divide by a number 39% too large on the real dataset.
    is_distinct: bool = False

    @property
    def has_per_order(self) -> bool:
        return self.units_per_order is not None or self.value_per_order is not None

    @property
    def has_any(self) -> bool:
        return self.has_per_order or self.value_per_unit is not None


def build_order_structure(
    *,
    units: Decimal,
    ordered_value_net: Decimal,
    distinct_orders: int | None,
    supports_distinct: bool,
) -> OrderStructure:
    """Derived order metrics, withheld rather than approximated.

    `supports_distinct` is `selectors.distinct_orders_supported` for the filters
    now in force. When it is false the per-order figures are `None`: an order
    count for the whole product type divided into one category's units is not a
    smaller version of the right answer, it is a different question.
    """
    value_per_unit = (
        (ordered_value_net / units).quantize(Decimal("0.01")) if units and units > 0 else None
    )
    if not supports_distinct or not distinct_orders:
        return OrderStructure(value_per_unit=value_per_unit, is_distinct=False)
    return OrderStructure(
        units_per_order=(units / Decimal(distinct_orders)).quantize(Decimal("0.01")),
        value_per_order=(ordered_value_net / Decimal(distinct_orders)).quantize(Decimal("0.01")),
        value_per_unit=value_per_unit,
        is_distinct=True,
    )


@dataclass(frozen=True)
class MatrixCell:
    """One quadrant of the attention/acquisition matrix."""

    key: str
    label: str
    products: tuple = ()

    @property
    def count(self) -> int:
        return len(self.products)


@dataclass(frozen=True)
class AttentionMatrix:
    """Measured products split by traffic and acquisition rate.

    The thresholds are the **medians of the measured population**, which makes
    the split deterministic, reproducible from the same data, and explainable in
    one sentence — rather than a tuned constant that would quietly reclassify
    products whenever the catalogue grew.

    Only products with a measured acquisition page take part. An unmeasured
    product is not a low-traffic product.
    """

    cells: tuple[MatrixCell, ...] = ()
    median_views: int | None = None
    median_rate: Decimal | None = None
    population: int = 0

    @property
    def is_available(self) -> bool:
        return self.population > 0 and self.median_views is not None


def build_attention_matrix(rows, *, minimum_views: int) -> AttentionMatrix:
    """Split sufficiently measured products into four quadrants.

    `rows` are `selectors.ProductRow`. A row qualifies when its acquisition page
    was measured and carried at least `minimum_views` — the same floor the
    opportunity lists use, so a product cannot appear in one and be excluded
    from the other.
    """
    eligible = []
    for row in rows:
        figure = row.denominator_page_views
        rate = row.acquisitions_per_hundred
        if figure is None or figure.views is None or figure.views < minimum_views:
            continue
        if rate is None:
            continue
        eligible.append((row, figure.views, rate))

    if not eligible:
        return AttentionMatrix()

    views_median = int(median([views for _, views, _ in eligible]))
    rate_median = Decimal(median([rate for _, _, rate in eligible])).quantize(Decimal("0.1"))

    buckets: dict[str, list] = {
        "high_high": [],
        "high_low": [],
        "low_high": [],
        "low_low": [],
    }
    for row, views, rate in eligible:
        busy = views >= views_median
        converting = rate >= rate_median
        key = f"{'high' if busy else 'low'}_{'high' if converting else 'low'}"
        buckets[key].append(row)

    labels = {
        "high_high": "Palju vaatamisi, tugev ostmise määr",
        "high_low": "Palju vaatamisi, nõrk ostmise määr",
        "low_high": "Vähem vaatamisi, tugev ostmise määr",
        "low_low": "Vähem vaatamisi, nõrk ostmise määr",
    }
    return AttentionMatrix(
        cells=tuple(
            MatrixCell(key=key, label=labels[key], products=tuple(rows_in))
            for key, rows_in in buckets.items()
        ),
        median_views=views_median,
        median_rate=rate_median,
        population=len(eligible),
    )


def build_signals(
    *,
    units_change: Decimal | None,
    units_percentage: Decimal | None,
    weak_acquisition: tuple,
    strong_acquisition: tuple,
    product_fallers: tuple,
    category_fallers: tuple,
    free_share: Decimal | None,
    previous_free_share: Decimal | None,
    concentration,
    focus_query,
    minimum_views: int,
) -> tuple[Signal, ...]:
    """Everything the overview thinks is worth a second look, in priority order.

    Each rule is a threshold on a measured figure. Nothing is inferred about
    cause, and nothing is ranked by a composite of several metrics — the order
    below is a fixed editorial priority, which is auditable in a way a weighted
    score is not.
    """
    signals: list[Signal] = []

    # 1. Attention that is not converting. The most actionable thing the join of
    #    Commerce and GA4 can say, and the reason the two are joined at all.
    if weak_acquisition:
        worst = weak_acquisition[0]
        signals.append(
            Signal(
                kind="weak_acquisition",
                text=f"„{worst.title}“ saab tähelepanu, kuid oste on vähe.",
                detail=(
                    f"{_thousands(worst.views)} vaatamist, "
                    f"{_rate(worst.rate)} ostu 100 vaatamise kohta."
                ),
                href=focus_query("nahtavus"),
            )
        )

    # 2. The reverse: converting well, and possibly under-seen.
    if strong_acquisition:
        best = strong_acquisition[0]
        signals.append(
            Signal(
                kind="strong_acquisition",
                text=f"„{best.title}“ ostetakse vaatamiste kohta sageli.",
                detail=(
                    f"{_thousands(best.views)} vaatamist, "
                    f"{_rate(best.rate)} ostu 100 vaatamise kohta."
                ),
                href=focus_query("nahtavus"),
            )
        )

    # 3. The largest absolute decline, product then category.
    if product_fallers:
        worst = product_fallers[0]
        signals.append(
            Signal(
                kind="product_decline",
                text=f"„{worst.title}“ osteti {_abs_units(worst.change)} võrra vähem.",
                detail=(
                    f"{_thousands(int(worst.previous_units))} → "
                    f"{_thousands(int(worst.current_units))} ühikut."
                ),
                href=focus_query("tooted"),
            )
        )
    if category_fallers:
        worst = category_fallers[0]
        signals.append(
            Signal(
                kind="category_decline",
                text=f"Kategooria „{worst.name}“ langes {_abs_units(worst.change)} võrra.",
                detail=(
                    f"{_thousands(int(worst.previous_units))} → "
                    f"{_thousands(int(worst.current_units))} ühikut."
                ),
                href=focus_query("tooted"),
            )
        )

    # 4. A material move in the free share, stated in percentage points because
    #    a percentage change of a percentage is the least readable form there is.
    if free_share is not None and previous_free_share is not None:
        movement = free_share - previous_free_share
        if abs(movement) >= FREE_SHARE_POINTS:
            direction = "tõusis" if movement > 0 else "langes"
            signals.append(
                Signal(
                    kind="free_share",
                    text=(
                        f"Tasuta ostude osakaal {direction} "
                        f"{_points(abs(movement))} protsendipunkti."
                    ),
                    detail=(
                        f"{_percent(previous_free_share)} → {_percent(free_share)} "
                        "klassifitseeritud ühikutest."
                    ),
                    href=focus_query("ostud"),
                )
            )

    # 5. Concentration, as a description of the shape of demand.
    if concentration is not None and concentration.top_share is not None:
        if concentration.top_share >= CONCENTRATION_POINTS:
            detail = ""
            if concentration.long_tail_count is not None:
                detail = (
                    f"80% ostudest tuleb {concentration.long_tail_count} tootest "
                    f"({concentration.population} seast)."
                )
            signals.append(
                Signal(
                    kind="concentration",
                    text=(
                        f"Top 10 toodet moodustavad {_percent(concentration.top_share)} ostudest."
                    ),
                    detail=detail,
                    href=focus_query("tooted"),
                )
            )

    # 6. The period's own direction, last: it restates the headline, so it earns
    #    a place only when nothing more specific was found.
    if not signals and units_change is not None and units_percentage is not None:
        direction = "kasvasid" if units_change > 0 else "vähenesid"
        signals.append(
            Signal(
                kind="units_trend",
                text=(
                    f"Ostud {direction} võrreldes eelmise sama pika perioodiga "
                    f"{_percent(abs(units_percentage))}."
                ),
                href=focus_query("ostud"),
            )
        )

    return tuple(signals)


def _thousands(value: int | None) -> str:
    from apps.core.formatting import group_thousands

    return group_thousands(value or 0)


def _rate(value: Decimal | None) -> str:
    return "—" if value is None else f"{value}".replace(".", ",")


def _percent(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}%".replace(".", ",")


def _points(value: Decimal) -> str:
    return f"{value:.0f}".replace(".", ",")


def _abs_units(change: Decimal) -> str:
    return f"{_thousands(abs(int(change)))} ühiku"


__all__ = [
    "CONCENTRATION_POINTS",
    "FREE_SHARE_POINTS",
    "AttentionMatrix",
    "MatrixCell",
    "OrderStructure",
    "Signal",
    "build_attention_matrix",
    "build_order_structure",
    "build_signals",
    "ZERO",
]
