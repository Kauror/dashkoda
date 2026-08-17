"""Does the membership add up?

A stock-and-flow check on the board reports:

```text
expected end = start total + joined - removed
residual     = reported end - expected end
```

A residual of zero means the period's arrivals and departures fully explain the
change in the membership. Anything else means one of the four figures is
measuring something slightly different from what this arithmetic assumes —
members reinstated without a join, a total counted on a different basis, a
correction applied to one figure and not another.

## This is evidence, not a correction

A non-zero residual is **a question**, never an answer. Nothing here overwrites
a source, marks one figure as the wrong one, or picks whichever value makes the
sum close. That would be inventing provenance: the board reported all four
numbers, and which of them is off is not something arithmetic can decide.

The residual is surfaced under `Andmete kohta`, not as a headline. A reader
looking up the membership total does not need to be told that a flow identity
left a remainder of four; a reader checking whether the numbers hang together
does.

## Preconditions, and why each one matters

A reconciliation is only computed when all of these hold, and is otherwise
*unavailable* with the reason attached — never zero, never approximate:

- **both totals exist and are drawable.** A withheld or conflicted total cannot
  anchor either end;
- **both flows exist and are drawable**, and they come from the *same*
  observation as the closing total, so they cover exactly the stretch between
  the two anchors;
- **the opening total sits close to the year boundary.** `new_members_ytd`
  counts from 1 January. If the previous year's last report is from October,
  then November and December of that year are in neither the opening stock nor
  the flow counters, and the identity is quietly measuring a different period
  than it claims.

That last one is the rule that stops this from producing confident nonsense, and
it is why the tolerance is stated as a constant rather than left implicit.

Nothing here reads the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# How far the opening observation may sit from 31 December and still anchor a
# year's reconciliation.
#
# `new_members_ytd` and `removed_members_ytd` both count from 1 January, so the
# opening stock has to be measured at effectively the same moment. Thirty days
# tolerates a report dated in the first days of January or the last of December
# — which the history contains — without letting an October reading stand in for
# a year end, which would leave two months of real movement outside every term
# of the identity.
YEAR_BOUNDARY_TOLERANCE_DAYS = 30


class Unreconcilable:
    """Why a period could not be checked, in words the page can show."""

    NO_OPENING = "Aasta alguse liikmete arvu ei ole."
    NO_CLOSING = "Perioodi lõpu liikmete arvu ei ole."
    NO_FLOWS = "Liitumiste või väljaarvamiste arvu ei ole."
    OPENING_TOO_FAR = "Aasta alguse vaatlus on aastavahetusest liiga kaugel."


@dataclass(frozen=True)
class Reconciliation:
    """One period's stock-and-flow check, or an explicit refusal to do one."""

    year: int
    opening_date: date | None = None
    opening_total: int | None = None
    closing_date: date | None = None
    closing_total: int | None = None
    joined: int | None = None
    removed: int | None = None
    expected_total: int | None = None
    residual: int | None = None
    unavailable_reason: str = ""

    @property
    def is_available(self) -> bool:
        return not self.unavailable_reason

    @property
    def reconciles(self) -> bool:
        """Whether the flows fully explain the change in the stock."""
        return self.residual == 0

    @property
    def is_partial_year(self) -> bool:
        """Whether the period stops short of the year end.

        A partial period is a perfectly good reconciliation of the stretch it
        covers. It is only wrong to *call* it the year, so the page labels it
        with its actual end date and this is what it asks.
        """
        if self.closing_date is None:
            return False
        return (self.closing_date.month, self.closing_date.day) < (12, 1)


def _drawable(point, field: str):
    """A metric, or `None` when the point withholds it."""
    return point.value(field) if point is not None else None


def reconcile_year(
    year: int,
    points: tuple,
    *,
    tolerance_days: int = YEAR_BOUNDARY_TOLERANCE_DAYS,
) -> Reconciliation:
    """Check one calendar year's arrivals and departures against its stock.

    `points` is the whole run of observations, oldest first, as
    `internal_selectors` returns them. The opening anchor is the newest
    observation before 1 January of `year`; the closing anchor is the newest
    observation inside it that carries both flow counters, because those two
    figures and the closing total have to come off the same report to describe
    the same stretch.
    """
    opening = None
    for point in points:
        if point.observation_date.year < year and _drawable(point, "total_members") is not None:
            opening = point

    closing = None
    for point in points:
        if point.observation_date.year != year:
            continue
        if (
            _drawable(point, "total_members") is not None
            and _drawable(point, "new_members_ytd") is not None
            and _drawable(point, "removed_members_ytd") is not None
        ):
            closing = point

    if opening is None:
        return Reconciliation(year=year, unavailable_reason=Unreconcilable.NO_OPENING)

    boundary = date(year, 1, 1)
    if abs((opening.observation_date - boundary).days) > tolerance_days:
        return Reconciliation(
            year=year,
            opening_date=opening.observation_date,
            opening_total=opening.value("total_members"),
            unavailable_reason=Unreconcilable.OPENING_TOO_FAR,
        )

    if closing is None:
        # Distinguish "no closing observation at all" from "one exists but
        # reported no flows": they are different gaps and the second is
        # actionable.
        has_total = any(
            point.observation_date.year == year and _drawable(point, "total_members") is not None
            for point in points
        )
        return Reconciliation(
            year=year,
            opening_date=opening.observation_date,
            opening_total=opening.value("total_members"),
            unavailable_reason=(
                Unreconcilable.NO_FLOWS if has_total else Unreconcilable.NO_CLOSING
            ),
        )

    opening_total = opening.value("total_members")
    closing_total = closing.value("total_members")
    joined = closing.value("new_members_ytd")
    removed = closing.value("removed_members_ytd")
    expected = opening_total + joined - removed

    return Reconciliation(
        year=year,
        opening_date=opening.observation_date,
        opening_total=opening_total,
        closing_date=closing.observation_date,
        closing_total=closing_total,
        joined=joined,
        removed=removed,
        expected_total=expected,
        residual=closing_total - expected,
    )


#: How far back the caller should fetch observations for `reconcile_history` to
#: check. Seven years is enough to fill the six periods `limit` lists below.
#: Moved here from `apps/membership/views.py` on 2026-08-17, when the
#: diagnostic itself moved off the dashboard to `/haldus/`: the lookback is a
#: property of the check, not of whichever page happens to run it.
RECONCILIATION_LOOKBACK_YEARS = 7


def reconcile_history(points: tuple, *, limit: int = 6) -> tuple[Reconciliation, ...]:
    """The most recent periods that can be checked, newest first.

    Only available reconciliations are returned. A year that cannot be checked
    is not a finding — most of the early history has no January observation to
    anchor on — and listing a dozen refusals under a data-quality heading would
    bury the two years that genuinely do not add up.
    """
    if not points:
        return ()

    years = sorted({point.observation_date.year for point in points}, reverse=True)
    results = []
    for year in years:
        result = reconcile_year(year, points)
        if result.is_available:
            results.append(result)
        if len(results) >= limit:
            break
    return tuple(results)
