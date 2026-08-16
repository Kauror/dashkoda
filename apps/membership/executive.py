"""What the Liikmeskond domain tells the main dashboard.

A compact summary for the executive overview's Liikmeskond pillar. It is
deliberately **not** `intelligence.build_headlines`: that builds three headline
figures, a trend, a movement summary and quality badges for a page that is about
membership. The overview needs one figure, one comparison, three supporting
facts and at most two signals, and calling the page builder to obtain them would
run every query the Liikmeskond page runs.

## Which total leads, and why it is not the one this app's own page leads with

**The headline here is the public Koda.ee directory count.** The Liikmeskond
page leads with the internal board report instead, and that difference is
intentional rather than an inconsistency to be tidied away: when the Liikmeskond
dashboard was rebuilt it took the public-catalogue section off the top of its own
page *because* the overview carries that count. If this pillar switched to the
internal total, the public directory figure would not appear anywhere in
DashKoda.

The two are never mixed. AGENTS.md is explicit that they count different things,
and everything the internal report contributes here is a **ratio or a movement
inside that report** — a paid share whose denominator is the report's own total,
a fee collection against the report's own budget, joins and removals the report
itself counted. No figure on this pillar divides one source by the other, and
the two never appear as two unlabelled totals side by side.

## Why the comparison is a year, not thirty days

The overview's old headline compared against the reading thirty days earlier.
That window answers "did something happen recently", which the signal section
now answers better and with a link. The pillar answers "is the membership base
growing or shrinking", and a year is the shortest window over which a chamber's
membership answers that: a thirty-day move is dominated by the timing of the
annual fee cycle and reads as decline every spring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from apps.core.executive import DomainSignal, SignalDirection, SignalPriority
from apps.core.formatting import integer, percent, percentage_points

from .analytics import share_change
from .internal_selectors import (
    ObservationPoint,
    get_internal_membership_latest,
    get_internal_membership_observations,
)
from .selectors import MembershipChange, get_membership_change_over

#: The pillar's comparison window. A year, for the reason in the module
#: docstring. Public observations are written only when the count changes, so
#: the baseline is the newest reading older than the window rather than a
#: reading taken exactly 365 days ago.
COMPARISON_DAYS = 365

#: How far the paid share must move between consecutive reports before it is
#: worth a signal. Below this it is the ordinary drift of a collection cycle.
PAID_SHARE_POINTS = Decimal("2")

#: How far back a predecessor report may be and still be a fair baseline for the
#: paid share. The report is monthly; a gap beyond a year means the series was
#: interrupted, and comparing across that gap would describe a change in
#: reporting rather than a change in collection.
PREDECESSOR_LOOKBACK_DAYS = 400


@dataclass(frozen=True)
class MembershipExecutive:
    """The Liikmeskond pillar's figures, each with its own source and date."""

    #: Koda.ee directory. The headline.
    total_members: int | None = None
    total_as_of: date | None = None
    change_absolute: int | None = None
    change_relative_pct: Decimal | None = None
    baseline_as_of: date | None = None

    #: Internal board report. Supporting only, and never a denominator for the
    #: figure above.
    paid_share_pct: Decimal | None = None
    fee_collection_pct: Decimal | None = None
    joined_ytd: int | None = None
    removed_ytd: int | None = None
    internal_as_of: date | None = None

    #: The public count's recent readings, for the pillar's small trend. Same
    #: metric as the headline, which is the only line that may be drawn here.
    series: tuple[tuple[date, int], ...] = ()

    signals: tuple[DomainSignal, ...] = ()

    @property
    def has_headline(self) -> bool:
        return self.total_members is not None

    @property
    def has_comparison(self) -> bool:
        return self.change_absolute is not None

    @property
    def meaning(self) -> str:
        """One sentence, composed from the figures above and nothing else.

        Empty when there is no comparison to describe. A pillar with a figure
        and no baseline states the figure and says nothing about direction,
        rather than reaching for a word like `stabiilne` that no measurement
        supports.
        """
        if not self.has_headline or not self.has_comparison:
            return ""
        if self.change_relative_pct is None:
            return (
                f"Liikmeid on {integer(abs(self.change_absolute))} "
                f"{'rohkem' if self.change_absolute > 0 else 'vähem'} kui aasta tagasi."
            )
        if self.change_absolute == 0:
            return "Liikmete arv on aastataguse vaatlusega võrreldes muutumatu."
        word = "suurem" if self.change_absolute > 0 else "väiksem"
        return f"Liikmeskond on {percent(abs(self.change_relative_pct))} {word} kui aasta tagasi."


def get_membership_executive() -> MembershipExecutive:
    """Read the two membership sources once each and shape the pillar.

    Three queries for the public side (current reading, baseline reading, the
    short series the sparkline draws) and two for the internal report. Nothing
    here grows with the size of the membership.
    """
    change = get_membership_change_over(days=COMPARISON_DAYS)
    internal = get_internal_membership_latest()
    return MembershipExecutive(
        total_members=change.current.total_members if change.current else None,
        total_as_of=change.current.observed_at if change.current else None,
        change_absolute=change.difference,
        change_relative_pct=_relative(change),
        baseline_as_of=change.since,
        paid_share_pct=internal.paid_member_share_pct if internal else None,
        fee_collection_pct=_fee_pct(internal),
        joined_ytd=internal.value("new_members_ytd") if internal else None,
        removed_ytd=internal.value("removed_members_ytd") if internal else None,
        internal_as_of=internal.observation_date if internal else None,
        series=_public_series(change),
        signals=_signals(internal),
    )


def _relative(change: MembershipChange) -> Decimal | None:
    """The year's movement as a percentage of the baseline.

    `None` when the baseline is zero or missing. A change from nothing is not a
    percentage, and the absolute figure already says what happened.
    """
    if not change.has_change or not change.previous.total_members:
        return None
    difference = Decimal(change.difference)
    baseline = Decimal(change.previous.total_members)
    return (difference / baseline * 100).quantize(Decimal("0.1"))


def _public_series(change: MembershipChange) -> tuple[tuple[date, int], ...]:
    """The two readings the comparison rests on, oldest first.

    Deliberately not a full history. The directory writes an observation only
    when the count changes, so a year holds a handful of points and a
    `sparkline` over them is a line between the two dates the comparison already
    names. Drawing exactly those two keeps the picture and the sentence agreeing.
    """
    points = [
        (observation.observed_at, observation.total_members)
        for observation in (change.previous, change.current)
        if observation is not None
    ]
    return tuple(points)


def _fee_pct(internal: ObservationPoint | None) -> Decimal | None:
    """Fee collection as the report states it, or as its own amounts give it.

    The reported percentage wins when both exist. The Liikmeskond page shows the
    two side by side without reconciling them; a pillar has room for one, and
    the source's own statement is the one to show.
    """
    if internal is None:
        return None
    reported = internal.value("membership_fee_collection_pct_reported")
    return reported if reported is not None else internal.computed_collection_pct


def _signals(internal: ObservationPoint | None) -> tuple[DomainSignal, ...]:
    """At most one, and only for a paid share that actually moved.

    The membership total's own movement is the pillar's headline and does not
    need repeating as a signal. What the pillar cannot show is a *ratio inside
    the report* turning, which is why this is the one thing the domain flags.
    """
    if internal is None:
        return ()
    current = internal.paid_member_share_pct
    if current is None:
        return ()

    previous = _previous_paid_share(internal)
    if previous is None:
        return ()
    movement = share_change(current, previous)
    if movement is None or abs(movement) < PAID_SHARE_POINTS:
        return ()

    falling = movement < 0
    return (
        DomainSignal(
            key="membership-paid-share",
            headline=(
                f"Tasunud liikmete osakaal {'langes' if falling else 'tõusis'} "
                f"{percentage_points(abs(movement))}."
            ),
            # No evidence sentence. The two share levels restated the headline
            # movement, and the board struck them; the Liikmeskond page states
            # both readings with their dates.
            evidence="",
            # A falling collection rate is worth a manager's attention; a rising
            # one is worth knowing and is not a problem to be solved.
            priority=SignalPriority.ATTENTION if falling else SignalPriority.NOTABLE,
            direction=SignalDirection.DOWN if falling else SignalDirection.UP,
            as_of=internal.observation_date,
        ),
    )


def _previous_paid_share(latest: ObservationPoint) -> Decimal | None:
    """The paid share in the report before this one.

    Bounded by date rather than by row count, because the selector that applies
    the withheld-metric rules takes a range and not a limit — and a range is the
    honest bound here anyway: the report is monthly, so a year of lookback is a
    dozen rows whatever the membership does. A gap longer than that means there
    is no comparable predecessor, and no signal is better than one against a
    baseline from a different era of the report.
    """
    points = get_internal_membership_observations(
        date_from=latest.observation_date - timedelta(days=PREDECESSOR_LOOKBACK_DAYS),
        date_to=latest.observation_date - timedelta(days=1),
        metric="paid_members",
    )
    return points[-1].paid_member_share_pct if points else None


__all__ = [
    "COMPARISON_DAYS",
    "PAID_SHARE_POINTS",
    "MembershipExecutive",
    "get_membership_executive",
]
