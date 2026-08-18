"""One rule for "compared with what", used by every figure on the page.

A dashboard that lets each widget derive its own comparison is a dashboard whose
widgets eventually disagree. The KPI deltas, the movers list, the free-share
line and the trend overlay all take their previous period from
:func:`derive_period_pair` and their arithmetic from :class:`MetricComparison`.

## The rule

The previous period is the **immediately preceding window of exactly the same
length**:

```text
length         = current_end - current_start + 1 day
previous_end   = current_start - 1 day
previous_start = previous_end - length + 1 day
```

It is offered only when the whole of it lies inside the imported Commerce
coverage. A 365-day period compared against the 83 days that happen to exist
before it is not a year-on-year comparison, and printing one would be worse than
printing nothing: the reader has no way to see that the denominator was short.

## Zero is not the same as new

`previous = 0, current > 0` has no percentage — the arithmetic is a division by
zero and the honest rendering is `Uus`. `previous = 0, current = 0` is not new;
it is nothing happening, and it renders as a dash. Both are decided here so no
template has to.

Nothing in this module reads the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

#: What a comparison could not be built from.
NO_CURRENT = "no_current_period"
NO_COVERAGE = "no_coverage"
TOO_SHORT = "history_too_short"

#: Which way a change points. Text as well as colour, always.
UP = "up"
DOWN = "down"
FLAT = "flat"


@dataclass(frozen=True)
class PeriodPair:
    """A selected window and the equal-length window immediately before it."""

    current_start: date | None
    current_end: date | None
    previous_start: date | None = None
    previous_end: date | None = None
    unavailable_reason: str = ""

    @property
    def has_current(self) -> bool:
        return self.current_start is not None and self.current_end is not None

    @property
    def is_available(self) -> bool:
        """Whether a full equal-length previous window exists inside coverage."""
        return (
            self.has_current
            and self.previous_start is not None
            and self.previous_end is not None
            and not self.unavailable_reason
        )

    @property
    def length_days(self) -> int:
        if not self.has_current:
            return 0
        return (self.current_end - self.current_start).days + 1

    @property
    def previous_label(self) -> str:
        if not self.is_available:
            return ""
        return f"{self.previous_start:%d.%m.%Y}–{self.previous_end:%d.%m.%Y}"

    @property
    def current_label(self) -> str:
        if not self.has_current:
            return ""
        return f"{self.current_start:%d.%m.%Y}–{self.current_end:%d.%m.%Y}"


def derive_period_pair(
    *,
    current_start: date | None,
    current_end: date | None,
    coverage_start: date | None,
) -> PeriodPair:
    """The previous equal-length window, or a stated reason there is none.

    `coverage_start` is where the imported Commerce history begins. A previous
    window that would reach behind it is refused rather than truncated.
    """
    if current_start is None or current_end is None or current_start > current_end:
        return PeriodPair(current_start, current_end, unavailable_reason=NO_CURRENT)
    if coverage_start is None:
        return PeriodPair(current_start, current_end, unavailable_reason=NO_COVERAGE)

    length = (current_end - current_start).days + 1
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=length - 1)

    if previous_start < coverage_start:
        # Deliberately not clamped. A shorter window silently compared against a
        # full one is the error this whole module exists to prevent.
        return PeriodPair(current_start, current_end, unavailable_reason=TOO_SHORT)

    return PeriodPair(
        current_start=current_start,
        current_end=current_end,
        previous_start=previous_start,
        previous_end=previous_end,
    )


@dataclass(frozen=True)
class MetricComparison:
    """One measure now, the same measure before, and the honest difference.

    `previous` of `None` means no comparison was available at all — a different
    fact from a previous value of zero, and the two must not render alike.
    """

    current: Decimal
    previous: Decimal | None = None
    period: PeriodPair | None = None

    @classmethod
    def of(
        cls,
        current,
        previous=None,
        *,
        period: PeriodPair | None = None,
    ) -> MetricComparison:
        """Build from anything numeric, normalising to `Decimal`."""
        return cls(
            current=Decimal(str(current or 0)),
            previous=None if previous is None else Decimal(str(previous or 0)),
            period=period,
        )

    @property
    def is_available(self) -> bool:
        return self.previous is not None

    @property
    def absolute_change(self) -> Decimal | None:
        if self.previous is None:
            return None
        return self.current - self.previous

    @property
    def is_new(self) -> bool:
        """Something now where there was nothing, which has no percentage.

        Only true when the current value is actually positive: zero against zero
        is not a new product, it is a quiet one.
        """
        return self.previous is not None and self.previous == 0 and self.current > 0

    @property
    def percentage_change(self) -> Decimal | None:
        """The change as a percentage, or `None` where that is meaningless.

        `None` covers three cases the interface renders differently: no
        comparison, a previous value of zero (there is no percentage), and a
        previous value of zero with nothing now either.
        """
        if self.previous is None or self.previous == 0:
            return None
        return ((self.current - self.previous) / self.previous * 100).quantize(Decimal("0.1"))

    @property
    def direction(self) -> str:
        change = self.absolute_change
        if change is None or change == 0:
            return FLAT
        return UP if change > 0 else DOWN


__all__ = [
    "DOWN",
    "FLAT",
    "NO_COVERAGE",
    "NO_CURRENT",
    "TOO_SHORT",
    "UP",
    "MetricComparison",
    "PeriodPair",
    "derive_period_pair",
]
