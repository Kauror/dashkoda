"""What the E-pood domain tells the main dashboard.

The E-pood card's figures.

## Event registrations are excluded, and that is the whole design

`ProductType.EVENT_REGISTRATION` rows are Commerce activity describing the same
events the Sündmused card counts from the programme workbook. If both cards
drew on them the overview would present one set of registrations as two separate
contributions to two separate strategic areas, and a reader adding the cards
— which the page never invites, but readers add things — would double count.

So this card is **the shop minus event registrations**: contract templates and
physical products, the things a member acquires that are not a seat at an event.
`NON_EVENT_TYPES` is that rule, written once. The Sündmused card takes the
programme and no Commerce at all, so the two cards share no row.

The cost is stated rather than hidden: the overview does not show how many
registrations were sold. The E-pood page does, with its own coverage report
beside it, and the card links there.

## Ordered value is not revenue

`ordered_value_net` is what the orders were worth at order time, excluding VAT.
It is not recognised revenue, not cash received, and not reconciled to any
ledger — an order can be cancelled, refunded or never paid, and none of that
reaches this dataset. The card's wording says `tellitud väärtus` and never
`tulu` or `käive`.

## The period is anchored to the export, not to today

`periods.resolve_period` counts back from Commerce coverage end. The source is a
manual export that stops on a stated day, and a "last 30 days" measured from the
reader's calendar would drift past the data and eventually select nothing —
a card reading `0 ostetud` for a month nobody has imported. The card states
the export's own date beside its figures for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from django.urls import reverse

from apps.core.executive import DomainSignal, SignalDirection, SignalPriority, SignalTone
from apps.core.formatting import euros, integer, percent

from .comparison import derive_period_pair
from .models import ProductType
from .periods import DEFAULT_PERIOD, resolve_period
from .selectors import (
    ComparisonWindow,
    MixBreakdown,
    ShopCoverage,
    get_free_paid_split,
    get_shop_coverage,
    get_totals,
)

#: Everything this card counts. Event registrations are deliberately absent —
#: see the module docstring. Physical products stay in: they are a digital
#: service in the sense that matters here, namely something a member acquires
#: through the shop rather than by attending something.
NON_EVENT_TYPES: tuple[str, ...] = (
    ProductType.DOCUMENT,
    ProductType.PHYSICAL_PRODUCT,
)

#: How far acquisitions must move against the preceding equal period before the
#: domain states it as a signal.
UNITS_CHANGE_PCT = 20.0


@dataclass(frozen=True)
class ShopExecutive:
    """Non-event acquisitions over one Commerce-anchored period."""

    units: Decimal | None = None
    previous_units: Decimal | None = None
    ordered_value_net: Decimal | None = None
    #: Free units as a percentage of the **classified** ones, as the domain
    #: computes it. Already a percentage, not a 0–1 ratio.
    free_share: Decimal | None = None
    #: The export's own coverage. Every figure above stops at `coverage_end`.
    source_as_of: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    period_label: str = ""

    signals: tuple[DomainSignal, ...] = ()

    @property
    def has_headline(self) -> bool:
        return self.units is not None

    @property
    def change_pct(self) -> float | None:
        if self.units is None or not self.previous_units:
            return None
        return float((self.units - self.previous_units) / self.previous_units * 100)

    @property
    def meaning(self) -> str:
        """Acquisitions and the free share, both about the same rows."""
        if not self.has_headline:
            return ""
        if self.free_share is None:
            return f"Mitte-sündmuse tooteid osteti {integer(self.units)} ühikut."
        return (
            f"Tasuta tooted moodustasid {percent(self.free_share)} "
            f"{integer(self.units)} ostetud ühikust."
        )


def get_shop_executive() -> ShopExecutive:
    """Read the anchored period once and shape the non-event figures.

    Four aggregate reads over `ShopDailyFact`, all filtered to
    `NON_EVENT_TYPES`. There was a fifth — the leading product, for the
    overview's `Praegu enim huvi` panel — until that section left the front page
    on 2026-08-18. Nothing here grows with the catalogue.
    """
    coverage = get_shop_coverage()
    if not coverage.has_data:
        return ShopExecutive()

    period = resolve_period(DEFAULT_PERIOD.key, anchor=coverage.coverage_end)
    if period.start is None or period.end is None:
        return ShopExecutive(source_as_of=coverage.source_as_of)

    window = ComparisonWindow(
        commerce_start=period.start,
        commerce_end=period.end,
        web_start=period.start,
        web_end=period.end,
    )
    totals = get_totals(window, product_types=NON_EVENT_TYPES)
    mix = get_free_paid_split(window, product_types=NON_EVENT_TYPES)

    executive = ShopExecutive(
        units=totals.units,
        previous_units=_previous_units(period.start, period.end, coverage),
        ordered_value_net=totals.ordered_value_net,
        free_share=_free_share(mix),
        source_as_of=coverage.source_as_of or coverage.coverage_end,
        period_start=period.start,
        period_end=period.end,
        period_label=period.label,
        signals=(),
    )
    return _with_signals(executive, coverage)


def _previous_units(start: date, end: date, coverage: ShopCoverage) -> Decimal | None:
    """Acquisitions over the equal-length period immediately before this one.

    The window comes from `derive_period_pair` — the same rule every figure on
    the E-pood page takes its previous period from. It refuses a previous window
    that would reach before Commerce coverage began, so the card can never
    compare a full period against the partial history that happens to precede
    it; the page and this card therefore agree about when a comparison exists.
    """
    pair = derive_period_pair(
        current_start=start, current_end=end, coverage_start=coverage.coverage_start
    )
    if not pair.is_available:
        return None
    window = ComparisonWindow(
        commerce_start=pair.previous_start,
        commerce_end=pair.previous_end,
        web_start=pair.previous_start,
        web_end=pair.previous_end,
    )
    return get_totals(window, product_types=NON_EVENT_TYPES).units


def _free_share(mix: MixBreakdown) -> Decimal | None:
    """The free share of acquired units, or `None` when the package cannot say.

    A 1.0 package carries no free/paid classification at all, and `is_known` is
    how the domain says so. The share's denominator excludes the unclassified
    remainder, which is the domain's own rule and not restated here.
    """
    return mix.free_share if mix.is_known else None


def _with_signals(executive: ShopExecutive, coverage: ShopCoverage) -> ShopExecutive:
    """At most one: acquisitions that moved materially against the period before."""
    change = executive.change_pct
    if change is None or abs(change) < UNITS_CHANGE_PCT:
        return executive

    falling = change < 0
    signal = DomainSignal(
        key="shop-units",
        headline=(
            f"Ostetud ühikute arv {'langes' if falling else 'kasvas'} "
            f"{percent(abs(change))} võrreldes eelmise sama pika perioodiga."
        ),
        evidence=(
            f"{integer(executive.units)} ühikut, "
            f"eelmisel perioodil {integer(executive.previous_units)}. "
            f"Tellitud väärtus {euros(executive.ordered_value_net)} (KM-ta)."
        ),
        priority=SignalPriority.ATTENTION if falling else SignalPriority.NOTABLE,
        direction=SignalDirection.DOWN if falling else SignalDirection.UP,
        tone=SignalTone.NEUTRAL if falling else SignalTone.POSITIVE,
        href=reverse("shop"),
        as_of=coverage.source_as_of or coverage.coverage_end,
    )
    return replace(executive, signals=(signal,))


__all__ = [
    "NON_EVENT_TYPES",
    "UNITS_CHANGE_PCT",
    "ShopExecutive",
    "get_shop_executive",
]
