"""Comparisons between membership observations, with the rules written down.

Every readout on the Liikmeskond page that says "compared with" is built here.
The page asks a question — is this bigger than a year ago, are we ahead of last
year by July — and the answer is either a number this module is willing to
stand behind or an explicit refusal. There is no third outcome, and in
particular there is no quietly-plausible number.

The refusal matters as much as the answer. The internal board report arrives
when it arrives, some months carry no report at all, and a conflicted metric is
withheld rather than repaired. A comparison helper that silently reached for the
nearest available figure would turn any of those into a confident percentage
that no source supports, and the reader has no way to tell the two apart. So
each helper returns a `Comparison`, and a `Comparison` that is not
`is_available` carries the reason it is not.

Four rules run through all of it:

- **a missing value is not a zero.** `None` propagates to an unavailable
  comparison. It never becomes 0, and 0 never stands in for "unknown";
- **an explicit zero is a real measurement.** Zero new members in a month is a
  fact and is compared like any other number. Only the *denominator* case is
  special, because dividing by it has no answer;
- **the baseline must be near enough to mean what it claims.** "A year ago" is
  the observation closest to the anniversary and inside a stated tolerance. When
  nothing is inside it, there is no year-ago comparison — not the nearest report
  from eight months back wearing the label;
- **nothing is interpolated.** A comparison is between two observations that
  both exist. No value is manufactured to sit at a convenient date.

Nothing here reads the database. Callers pass in the points they already hold,
which keeps the whole module testable without PostgreSQL and keeps comparison
work off the per-observation query path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

# How far from the anniversary an observation may sit and still be called "a
# year ago".
#
# The internal report is a monthly series, so the report nearest an anniversary
# is normally within about half a month of it. Forty-five days tolerates two
# consecutive months without a report — which the history does contain — while
# staying far short of the distance at which a different part of the year could
# stand in for the anniversary. A membership figure has an annual shape; a
# baseline four months out would be comparing a different season and calling it
# a year.
YOY_TOLERANCE_DAYS = 45


class Unavailable:
    """Why a comparison could not be made, in words the page can show."""

    NO_CURRENT = "Praegust väärtust ei ole."
    NO_BASELINE = "Võrreldavat varasemat vaatlust ei ole."
    OUT_OF_TOLERANCE = "Lähim varasem vaatlus on võrdluseks liiga kaugel."
    ZERO_BASELINE = "Varasem väärtus on null, suhtelist muutu ei saa arvutada."


@dataclass(frozen=True)
class Comparison:
    """One current figure set against one earlier figure.

    `absolute` and `relative_pct` are both optional even when the comparison is
    available: a baseline of zero gives a real absolute change and no meaningful
    percentage, and the page shows what exists rather than suppressing both.
    """

    current: Decimal | int | None = None
    baseline: Decimal | int | None = None
    baseline_date: date | None = None
    absolute: Decimal | int | None = None
    relative_pct: Decimal | None = None
    baseline_is_provisional: bool = False
    unavailable_reason: str = ""

    @property
    def is_available(self) -> bool:
        return not self.unavailable_reason

    @property
    def has_relative(self) -> bool:
        return self.relative_pct is not None


def _unavailable(reason: str, **known) -> Comparison:
    return Comparison(unavailable_reason=reason, **known)


def _quantise(value: Decimal, places: int = 2) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def anniversary(day: date, *, years: int = 1) -> date:
    """The same calendar date `years` earlier.

    29 February has no anniversary in a common year. It becomes 28 February,
    which is the same position in the month and one day out at worst — the
    alternative, 1 March, moves the baseline into the next month and would make
    a February report compare against a March one.
    """
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def pick_comparable(
    candidates: tuple[tuple[date, object], ...],
    target: date,
    *,
    tolerance_days: int = YOY_TOLERANCE_DAYS,
) -> tuple[date, object] | None:
    """The dated value closest to `target`, or nothing if none is close enough.

    Ties — one observation the same distance either side of the anniversary —
    resolve to the earlier one. Either is equally defensible; picking
    deterministically is what stops the same page rendering two different
    baselines on two requests.

    `candidates` carries only values that exist. A caller builds it from
    `InternalTrend.series`, which already drops withheld and absent metrics, so
    a conflicted observation cannot become a baseline by passing through here.
    """
    best: tuple[date, object] | None = None
    best_distance: int | None = None
    for day, value in candidates:
        distance = abs((day - target).days)
        if distance > tolerance_days:
            continue
        if (
            best_distance is None
            or distance < best_distance
            or (distance == best_distance and best is not None and day < best[0])
        ):
            best, best_distance = (day, value), distance
    return best


def change(current, baseline) -> tuple[Decimal | int | None, Decimal | None]:
    """Absolute and relative change, with the zero-baseline case decided.

    Growth from zero has no percentage. It is not 100%, it is not infinite, and
    reporting either would be inventing a rate for a base that cannot support
    one — so the absolute change is returned and the relative is `None`.
    """
    if current is None or baseline is None:
        return None, None
    absolute = current - baseline
    if baseline == 0:
        return absolute, None
    relative = _quantise(Decimal(absolute) / Decimal(abs(baseline)) * 100)
    return absolute, relative


def compare_with(
    current_value,
    current_date: date | None,
    history: tuple[tuple[date, object], ...],
    *,
    years: int = 1,
    tolerance_days: int = YOY_TOLERANCE_DAYS,
    provisional_dates: frozenset[date] = frozenset(),
) -> Comparison:
    """Compare a current figure with the one closest to its anniversary.

    `history` may contain the current observation itself; it is excluded by the
    tolerance rather than by identity, because an observation inside 45 days of
    its own anniversary would mean the history spans less than a year, and then
    there is no year-ago figure to find.
    """
    if current_value is None or current_date is None:
        return _unavailable(Unavailable.NO_CURRENT)

    earlier = tuple((day, value) for day, value in history if day < current_date)
    if not earlier:
        return _unavailable(Unavailable.NO_BASELINE, current=current_value)

    found = pick_comparable(
        earlier, anniversary(current_date, years=years), tolerance_days=tolerance_days
    )
    if found is None:
        return _unavailable(Unavailable.OUT_OF_TOLERANCE, current=current_value)

    baseline_date, baseline = found
    absolute, relative = change(current_value, baseline)
    return Comparison(
        current=current_value,
        baseline=baseline,
        baseline_date=baseline_date,
        absolute=absolute,
        relative_pct=relative,
        baseline_is_provisional=baseline_date in provisional_dates,
        # A zero baseline is a real comparison with no meaningful rate. It stays
        # available so the absolute change is shown; only the percentage is
        # missing, and `has_relative` is how a template asks.
        unavailable_reason="",
    )


def share_change(current_pct, baseline_pct) -> Decimal | None:
    """The gap between two shares, in percentage points.

    A share that went from 92,6% to 96,0% rose by 3,4 percentage points and by
    3,7 percent. Both are true and they are different numbers; the page states
    the first, because a share of a membership is read as a level rather than as
    something with its own growth rate.
    """
    if current_pct is None or baseline_pct is None:
        return None
    return _quantise(Decimal(current_pct) - Decimal(baseline_pct), places=1)


def net_movement(joined: int | None, removed: int | None) -> int | None:
    """Joined minus removed, for one size band.

    Derived for presentation and never stored. Both sides must be known: a band
    that reported arrivals but not departures has no net, and showing the
    arrivals alone under a "net" heading would read as a gain that nobody
    measured.
    """
    if joined is None or removed is None:
        return None
    return joined - removed


@dataclass(frozen=True)
class CumulativeSeries:
    """A running total, and where it had to stop.

    `values` holds one running total per period actually accumulated. `stopped_at`
    names the first period whose value is unknown, or is `None` when the whole
    sequence was known.
    """

    values: tuple[tuple[int, int], ...]
    stopped_at: int | None = None

    @property
    def is_complete(self) -> bool:
        return self.stopped_at is None


def cumulative(monthly: tuple[tuple[int, int | None], ...]) -> CumulativeSeries:
    """Accumulate month by month, stopping at the first unknown month.

    This is the rule that keeps a cumulative line honest. Treating an unreported
    month as zero would let the running total keep climbing at a visibly flatter
    slope, which reads as "recruitment slowed" — a claim about the Chamber's
    year that came from a missing row rather than from a measurement. Skipping
    the month instead is no better: the total after it would silently mean
    "everything except the month we lost".

    So the line stops. A reader sees the year accumulate to the last month that
    was actually reported and nothing beyond it, and `stopped_at` is what the
    page uses to say why.

    An explicit zero is a reported month and accumulates normally.
    """
    running = 0
    values: list[tuple[int, int]] = []
    for period, value in monthly:
        if value is None:
            return CumulativeSeries(values=tuple(values), stopped_at=period)
        running += value
        values.append((period, running))
    return CumulativeSeries(values=tuple(values))


def elapsed_total(monthly: tuple[tuple[int, int | None], ...], *, through: int) -> int | None:
    """The sum of periods 1..`through`, or nothing if any of them is unknown.

    Used for "the same period last year". A partial sum compared against a
    complete one is the comparison this exists to prevent: July's year-to-date
    against last year's full twelve months is a 40% collapse that never
    happened.
    """
    total = 0
    seen = 0
    for period, value in monthly:
        if period > through:
            continue
        if value is None:
            return None
        total += value
        seen += 1
    return total if seen == through else None


def mean_of_complete_years(
    by_year: dict[int, tuple[tuple[int, int | None], ...]],
    *,
    period: int,
    years: tuple[int, ...],
) -> Decimal | None:
    """The average value for one month across several years.

    Every named year must have reported that month. An average over "the years
    that happened to report" is a different quantity each month, and a benchmark
    line whose meaning changes from point to point is worse than no benchmark:
    it looks like one series and is several.
    """
    if not years:
        return None
    values = []
    for year in years:
        months = dict(by_year.get(year, ()))
        value = months.get(period)
        if value is None:
            return None
        values.append(value)
    return _quantise(Decimal(sum(values)) / Decimal(len(values)), places=1)


# How much of the drawn height a series is allowed to be flat before the axis
# stops zooming in on it.
#
# A membership that moved between 3 380 and 3 412 has a span of 32 on a base of
# 3 412 — one percent. An axis fitted tightly to that span draws a one-percent
# drift as a cliff, and the reader takes away a collapse that did not happen.
# Requiring the domain to cover at least this fraction of the largest value puts
# the movement back in proportion to the quantity it is a movement in.
#
# The opposite failure is forcing zero: a 0–3 412 axis draws every real
# membership change as a flat line, which is the same lie told the other way.
# Neither end is anchored, and the floor below is what keeps the middle honest.
MIN_DOMAIN_FRACTION = Decimal("0.05")

# Breathing room above and below, so the newest point is not welded to the frame.
DOMAIN_PADDING_FRACTION = Decimal("0.10")


@dataclass(frozen=True)
class Domain:
    """The value range an axis should cover."""

    minimum: Decimal
    maximum: Decimal

    @property
    def height(self) -> Decimal:
        return self.maximum - self.minimum


def value_domain(
    values: tuple[Decimal | int, ...],
    *,
    min_fraction: Decimal = MIN_DOMAIN_FRACTION,
    padding: Decimal = DOMAIN_PADDING_FRACTION,
) -> Domain | None:
    """An axis range that neither flattens a real change nor magnifies a small one.

    Zero is never forced. A membership series lives in a narrow band far from
    the origin, and anchoring the axis at zero would compress every movement the
    page exists to show into a flat line near the top of the frame.

    Instead the domain is the observed span plus padding, widened when the span
    is a small fraction of the values themselves. That widening is the rule that
    stops the axis from turning a one-percent drift into a cliff — the most
    common way a truthful series becomes a misleading picture.

    A single observation has no span at all, so it is given a domain around
    itself rather than a zero-height axis.
    """
    if not values:
        return None

    lowest = Decimal(min(values))
    highest = Decimal(max(values))
    span = highest - lowest

    # The smallest height this data is allowed to be drawn in, so a nearly flat
    # series stays nearly flat.
    floor = abs(highest) * min_fraction
    if span < floor:
        middle = (highest + lowest) / 2
        lowest = middle - floor / 2
        highest = middle + floor / 2
        span = highest - lowest

    room = span * padding
    return Domain(
        minimum=_quantise(lowest - room, places=0), maximum=_quantise(highest + room, places=0)
    )
