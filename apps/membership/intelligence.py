"""The membership dashboard's answers, assembled before the template runs.

`charts.py` draws; `analytics.py` calculates; this module decides *what the page
says*. It turns observations into the four figures a board member opens the page
for, the handful of movements worth naming, and an honest statement of how much
of that is actually known.

Nothing here reads the database. A caller passes in the points it already
fetched, which keeps every rule below testable without PostgreSQL and keeps the
work off the per-observation query path — the same arrangement `analytics.py`
uses and for the same reasons.

Four rules govern everything in this module:

- **a refusal is a result.** Every readout is either a figure this module will
  stand behind or an explicit absence carrying the reason. There is no
  quietly-plausible number, and `None` never becomes `0`;
- **a difference is not a net change.** `new_members_ytd` and
  `removed_members_ytd` are two reported counts; subtracting them gives the gap
  between two reports, not the movement of the membership stock. Nothing here
  calls it `neto`, `netokasv` or `liikmeskonna muutus`, and the reconciliation
  that would license such a claim is a separate diagnostic;
- **a comparison names its baseline.** "Vs aasta tagasi" is the observation
  nearest the anniversary and inside `analytics.YOY_TOLERANCE_DAYS`; when there
  is none, the comparison is absent rather than reaching for the nearest report;
- **an insight is derived, never written.** The strip below the headline is
  computed from the same numbers the charts draw. No prose is generated, no
  model is consulted, and no composite score is invented — a "membership health
  score" would be a number with no unit that nobody can check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from apps.core.formatting import (
    euros,
    integer,
    long_date,
    month_name,
    percent,
    percentage_points,
    signed_integer,
    signed_percent,
)

from .analytics import (
    change,
    compare_with,
    elapsed_total,
    share_change,
)
from .charts import BENCHMARK_YEARS, last_complete_month, monthly_pairs
from .internal_selectors import InternalQualitySummary, MonthlyValue, ObservationPoint

# How far back the headline comparisons need observations to reach.
#
# A year-ago baseline is the observation nearest the anniversary and within
# `YOY_TOLERANCE_DAYS` of it, so the fetch has to clear the anniversary by at
# least that tolerance or a perfectly good baseline would be missed for sitting
# just outside an arbitrary window. 425 days is a year, the 45-day tolerance and
# a fortnight of slack.
#
# It is deliberately not "the whole history": a bounded range is what keeps this
# one query a lookup rather than a table scan that grows every month.
KPI_BASELINE_LOOKBACK_DAYS = 425

# How many signals the "Mis muutus?" strip shows.
#
# Four fills the row at desktop width and stays readable at 375 px. More than
# that stops being a summary and becomes a second KPI wall, which is the thing
# this redesign removed.
MAX_INSIGHTS = 4

# Tone is what a change *means*, and it is kept apart from `direction`, which is
# only the arithmetic sign. They agree for membership and disagree for
# departures: more removals is an increase and bad news, and a strip that
# painted it green because the number went up would be worse than one with no
# colour at all.
TONE_POSITIVE = "positive"
TONE_NEGATIVE = "negative"
TONE_NEUTRAL = "neutral"


def _direction(value) -> str:
    """The arithmetic sign, as the glyph a reader sees beside a figure."""
    if value is None or value == 0:
        return "flat"
    return "up" if value > 0 else "down"


def _tone(value, *, rising_is_good: bool) -> str:
    """What the change means, given which way is good for this metric."""
    if value is None or value == 0:
        return TONE_NEUTRAL
    good = (value > 0) if rising_is_good else (value < 0)
    return TONE_POSITIVE if good else TONE_NEGATIVE


# ---------------------------------------------------------------------------
# Page objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MembershipHeadline:
    """One of the four questions the page answers before it is scrolled.

    Every string arrives formatted. A template that had to decide how to write a
    signed percentage would be the second place that decision lived, and the two
    would drift the first time either changed.
    """

    key: str
    label: str
    value: str = ""
    unit: str = ""
    detail: str = ""
    change: str = ""
    change_label: str = ""
    direction: str = ""
    tone: str = TONE_NEUTRAL
    comparison_label: str = ""
    note: str = ""

    @property
    def is_available(self) -> bool:
        return bool(self.value)

    @property
    def has_change(self) -> bool:
        return bool(self.change)


@dataclass(frozen=True)
class MembershipInsight:
    """One deterministic signal in the `Mis muutus?` strip.

    Structured rather than a sentence: the label names the metric, the change
    carries the movement, and the detail names the baseline it was measured
    against. A reader can check every one of them against the charts below.
    """

    key: str
    label: str
    value: str
    change: str
    change_label: str
    direction: str
    tone: str
    detail: str = ""


@dataclass(frozen=True)
class MembershipMovementSummary:
    """`Sel aastal` — what the current year's report says about movement.

    The gap between arrivals and departures is `difference`, and it is labelled
    as the difference between two reported counts everywhere it appears. It is
    not the change in the membership, and this dataset cannot show that it is.
    """

    observation_label: str
    joined: str = ""
    removed: str = ""
    difference: str = ""
    difference_direction: str = ""
    difference_tone: str = TONE_NEUTRAL
    suspended: str = ""
    suspended_note: str = ""

    @property
    def has_data(self) -> bool:
        return bool(self.joined or self.removed or self.suspended)

    @property
    def has_difference(self) -> bool:
        return bool(self.difference)


@dataclass(frozen=True)
class MembershipQualityBadge:
    """One line on the overview saying whether anything needs a second look.

    The long methodology stays behind a disclosure. This is the part that has to
    be in the reading path, because a reader relying on a figure deserves to
    know that some figures were withheld — but not to have the reason pushed at
    them before they have read the number.
    """

    label: str
    tone: str
    needs_attention: bool


# ---------------------------------------------------------------------------
# Headline figures
# ---------------------------------------------------------------------------


def _series(points: tuple[ObservationPoint, ...], field: str) -> tuple[tuple[date, object], ...]:
    """Dated values for one metric, skipping everything withheld or absent."""
    return tuple(
        (point.observation_date, point.value(field))
        for point in points
        if point.value(field) is not None
    )


def _share_series(points: tuple[ObservationPoint, ...]) -> tuple[tuple[date, Decimal], ...]:
    return tuple(
        (point.observation_date, point.paid_member_share_pct)
        for point in points
        if point.paid_member_share_pct is not None
    )


def _baseline_note(comparison) -> str:
    """How a missing comparison is explained, in the words `analytics` chose."""
    return comparison.unavailable_reason


def _comparison_label(comparison) -> str:
    if comparison.baseline_date is None:
        return ""
    return f"vs {long_date(comparison.baseline_date)}"


def build_headlines(
    latest: ObservationPoint | None,
    history: tuple[ObservationPoint, ...],
) -> tuple[MembershipHeadline, ...]:
    """The four questions, in the order a reader asks them.

    Nine equally weighted figures asked the reader to decide which mattered.
    These four are ordered: how many members there are, how that number moved,
    how much of the membership has paid, and how the fee year is progressing.
    Everything the old strip carried is still on the page — the fee amounts moved
    into the fourth figure's detail line and the suspended count into
    `Sel aastal`, where it is read beside the movement it belongs with.
    """
    if latest is None:
        return ()

    on = latest.observation_date
    total = latest.value("total_members")
    paid = latest.value("paid_members")

    # 1. Liikmeid kokku
    total_comparison = compare_with(total, on, _series(history, "total_members"))
    members = MembershipHeadline(
        key="total_members",
        label="Liikmeid kokku",
        value=integer(total),
        change=(
            f"{signed_integer(total_comparison.absolute)}"
            + (
                f" · {signed_percent(total_comparison.relative_pct)}"
                if total_comparison.has_relative
                else ""
            )
            if total_comparison.is_available
            else ""
        ),
        change_label=(
            f"{signed_integer(total_comparison.absolute)} võrreldes aastataguse vaatlusega"
            if total_comparison.is_available
            else ""
        ),
        direction=_direction(total_comparison.absolute),
        tone=_tone(total_comparison.absolute, rising_is_good=True),
        comparison_label=(
            f"aasta tagasi · {_comparison_label(total_comparison)[3:]}"
            if total_comparison.is_available
            else ""
        ),
        note="" if total_comparison.is_available else _baseline_note(total_comparison),
    )

    # 2. Liikmed ja tasunud liikmeid. Replaced `Liitumised ja väljaarvamised`
    #    on 2026-08-16 at the owner's request.
    #
    #    The two counts side by side and the gap between them. `Vahe` is the
    #    only figure here that was not already somewhere on the page — the pair
    #    itself is also the detail line of `Tasunute osakaal` below, which is a
    #    duplication to resolve rather than a bug to fix, and the note is here
    #    so whoever resolves it can see both at once.
    #
    #    The gap is **members who have not paid**, not a movement: nobody left,
    #    nobody joined, and describing it as a change would be the same error the
    #    card it replaced existed to avoid.
    gap = total - paid if total is not None and paid is not None else None
    movement = MembershipHeadline(
        key="members_and_paid",
        label="Liikmed ja tasunud liikmeid",
        value=(
            f"{integer(total)} · {integer(paid)}" if total is not None and paid is not None else ""
        ),
        detail=(
            f"vahe {integer(gap)}"
            if gap is not None
            else "Vahet ei saa arvutada, sest üks pooltest puudub."
        ),
        change=(
            f"{signed_integer(total_comparison.absolute)}" if total_comparison.is_available else ""
        ),
        change_label=(
            f"{signed_integer(total_comparison.absolute)} liiget võrreldes aastataguse vaatlusega"
            if total_comparison.is_available
            else ""
        ),
        direction=_direction(total_comparison.absolute),
        tone=_tone(total_comparison.absolute, rising_is_good=True),
        comparison_label=(
            f"aasta tagasi · {_comparison_label(total_comparison)[3:]}"
            if total_comparison.is_available
            else ""
        ),
        note="" if total_comparison.is_available else _baseline_note(total_comparison),
    )

    # 3. Tasunute osakaal, moved in percentage points rather than percent.
    share = latest.paid_member_share_pct
    share_history = _share_series(history)
    share_comparison = compare_with(share, on, share_history)
    points_moved = (
        share_change(share, share_comparison.baseline) if share_comparison.is_available else None
    )
    paid_share = MembershipHeadline(
        key="paid_share",
        label="Tasunute osakaal",
        value=percent(share),
        detail=(
            f"{integer(paid)} / {integer(total)} liiget"
            if paid is not None and total is not None
            else ""
        ),
        change=percentage_points(points_moved) if points_moved is not None else "",
        change_label=(
            f"{percentage_points(points_moved)} võrreldes aastataguse vaatlusega"
            if points_moved is not None
            else ""
        ),
        direction=_direction(points_moved),
        tone=_tone(points_moved, rising_is_good=True),
        comparison_label=(
            f"aasta tagasi · {_comparison_label(share_comparison)[3:]}"
            if share_comparison.is_available
            else ""
        ),
        note="" if share_comparison.is_available else _baseline_note(share_comparison),
    )

    return (members, movement, paid_share, _fee_headline(latest))


def _fee_headline(latest: ObservationPoint) -> MembershipHeadline:
    """Fee collection as one readout: the completion, and the amounts under it.

    The percentage shown is the one the **amounts imply**, which is the same
    figure the fee chart draws. `quality.py` withholds a reported percentage that
    disagrees with the amounts beside it, so when the two disagree the amounts
    are what survives — and the disagreement is disclosed here rather than
    silently resolved.

    When the amounts cannot produce a percentage but the report stated one, that
    reported figure is shown and labelled as reported. The two are never averaged
    and one never quietly stands in for the other.
    """
    received = latest.value("membership_fees_received_eur")
    budget = latest.value("membership_fee_budget_eur")
    computed = latest.computed_collection_pct
    reported = latest.value("membership_fee_collection_pct_reported")
    reported_withheld = "membership_fee_collection_pct_reported" in latest.withheld

    if computed is not None:
        value, note = percent(computed), ""
        if reported_withheld:
            note = "Aruandes esitatud protsent ei klapi summadega ja on välja jäetud."
    elif reported is not None:
        value = percent(reported)
        note = "Aruandes esitatud protsent; summadest seda arvutada ei saa."
    else:
        value, note = "", "Laekumise protsenti ei saa arvutada."

    return MembershipHeadline(
        key="fee_collection",
        label="Liikmemaksu laekumine",
        value=value,
        detail=(
            f"{euros(received)} / {euros(budget)}"
            if received is not None and budget is not None
            else (euros(received) if received is not None else "")
        ),
        comparison_label="eelarvest",
        note=note,
    )


# ---------------------------------------------------------------------------
# Mis muutus?
# ---------------------------------------------------------------------------


def _elapsed_mean(
    by_year: dict[int, tuple[MonthlyValue, ...]],
    *,
    through: int,
    years: tuple[int, ...],
) -> Decimal | None:
    """The average January-to-`through` total across several years.

    Every named year must have reported every one of those months. An average
    over "the years that happened to report" is a different quantity each time
    it is drawn, and a benchmark whose meaning moves is worse than none — the
    same rule `mean_of_complete_years` applies month by month.
    """
    if not years:
        return None
    totals = []
    for year in years:
        total = elapsed_total(monthly_pairs(by_year.get(year, ())), through=through)
        if total is None:
            return None
        totals.append(total)
    return (Decimal(sum(totals)) / Decimal(len(totals))).quantize(Decimal("0.1"))


def _insight(
    *,
    key: str,
    label: str,
    value: str,
    absolute,
    relative=None,
    rising_is_good: bool,
    detail: str,
    points: bool = False,
) -> MembershipInsight:
    body = percentage_points(absolute) if points else signed_integer(absolute)
    if relative is not None and not points:
        body = f"{body} · {signed_percent(relative)}"
    return MembershipInsight(
        key=key,
        label=label,
        value=value,
        change=body,
        change_label=f"{body} {detail}",
        direction=_direction(absolute),
        tone=_tone(absolute, rising_is_good=rising_is_good),
        detail=detail,
    )


def build_insights(
    latest: ObservationPoint | None,
    history: tuple[ObservationPoint, ...],
    monthly: dict[int, tuple[MonthlyValue, ...]],
) -> tuple[MembershipInsight, ...]:
    """The three or four movements worth naming, in a fixed priority order.

    The order is a product decision written down once, not a ranking by
    magnitude: a strip that reordered itself by whichever number moved most
    would put a different metric first on every visit, and a reader would lose
    the ability to look at the same place twice. Signals that cannot be computed
    are absent — never shown as zero, never shown as "no change".

    The whole strip is omitted when nothing is available, because an empty panel
    headed "Mis muutus?" answers its own question wrongly.
    """
    if latest is None:
        return ()

    on = latest.observation_date
    candidates: list[MembershipInsight | None] = []

    # 1. Membership against a year ago.
    total = compare_with(latest.value("total_members"), on, _series(history, "total_members"))
    candidates.append(
        _insight(
            key="members_yoy",
            label="Liikmeid",
            value=integer(latest.value("total_members")),
            absolute=total.absolute,
            relative=total.relative_pct,
            rising_is_good=True,
            detail=f"vs {long_date(total.baseline_date)}",
        )
        if total.is_available
        else None
    )

    # 2. Recruitment against the same elapsed stretch of last year. Only the
    #    unbroken run of months from January counts, on both sides.
    years = sorted(monthly)
    current_year = years[-1] if years else None
    through = last_complete_month(monthly_pairs(monthly.get(current_year, ()))) if years else None
    joined_now = (
        elapsed_total(monthly_pairs(monthly.get(current_year, ())), through=through)
        if through
        else None
    )
    if through and joined_now is not None:
        previous = elapsed_total(monthly_pairs(monthly.get(current_year - 1, ())), through=through)
        if previous is not None:
            absolute, relative = change(joined_now, previous)
            candidates.append(
                _insight(
                    key="joined_vs_previous_year",
                    label="Liitumised",
                    value=integer(joined_now),
                    absolute=absolute,
                    relative=relative,
                    rising_is_good=True,
                    detail=(f"vs jaanuar–{month_name(through)} {current_year - 1}"),
                )
            )
        else:
            candidates.append(None)
    else:
        candidates.append(None)

    # 3. Paid share, in percentage points.
    share = latest.paid_member_share_pct
    share_comparison = compare_with(share, on, _share_series(history))
    moved = (
        share_change(share, share_comparison.baseline) if share_comparison.is_available else None
    )
    candidates.append(
        _insight(
            key="paid_share_yoy",
            label="Tasunute osakaal",
            value=percent(share),
            absolute=moved,
            rising_is_good=True,
            points=True,
            detail=f"vs {long_date(share_comparison.baseline_date)}",
        )
        if moved is not None
        else None
    )

    # 4. Departures against the same point last year. Both sides are
    #    year-to-date counts read at nearly the same position in their year,
    #    which is what makes them comparable at all.
    removed = compare_with(
        latest.value("removed_members_ytd"), on, _series(history, "removed_members_ytd")
    )
    candidates.append(
        _insight(
            key="removed_yoy",
            label="Väljaarvamised",
            value=integer(latest.value("removed_members_ytd")),
            absolute=removed.absolute,
            relative=removed.relative_pct,
            rising_is_good=False,
            detail=f"vs {long_date(removed.baseline_date)}",
        )
        if removed.is_available
        else None
    )

    # 5. Recruitment against the multi-year average for the same stretch.
    if through and joined_now is not None and current_year is not None:
        average = _elapsed_mean(
            monthly,
            through=through,
            years=tuple(range(current_year - BENCHMARK_YEARS, current_year)),
        )
        if average is not None:
            absolute, relative = change(Decimal(joined_now), average)
            candidates.append(
                _insight(
                    key="joined_vs_average",
                    label="Liitumised",
                    value=integer(joined_now),
                    absolute=absolute,
                    relative=relative,
                    rising_is_good=True,
                    detail=f"vs {BENCHMARK_YEARS} a keskmine ({integer(average)})",
                )
            )

    # 6. Fee collection against the comparable earlier observation. Computed
    #    percentages on both sides, so a reported figure that disagreed with its
    #    own amounts cannot enter the comparison from either end.
    collection_history = tuple(
        (point.observation_date, point.computed_collection_pct)
        for point in history
        if point.computed_collection_pct is not None
    )
    collection = compare_with(latest.computed_collection_pct, on, collection_history)
    if collection.is_available:
        moved_pp = share_change(latest.computed_collection_pct, collection.baseline)
        if moved_pp is not None:
            candidates.append(
                _insight(
                    key="fee_collection_yoy",
                    label="Liikmemaksu laekumine",
                    value=percent(latest.computed_collection_pct),
                    absolute=moved_pp,
                    rising_is_good=True,
                    points=True,
                    detail=f"vs {long_date(collection.baseline_date)}",
                )
            )

    return tuple(insight for insight in candidates if insight is not None)[:MAX_INSIGHTS]


# ---------------------------------------------------------------------------
# Sel aastal
# ---------------------------------------------------------------------------


def build_movement_summary(latest: ObservationPoint | None) -> MembershipMovementSummary | None:
    """The current year's arrivals, departures and the gap between them.

    Deliberately typography rather than a chart. Two counts, their difference
    and the suspended total are four numbers; drawing four numbers as bars adds
    a canvas, an axis and a legend to a fact that fits on one line, and the
    dashboard already carries the size-band diverging chart for the question
    that genuinely needs a picture.

    The suspended count lives here rather than in the headline strip. It was one
    of nine equal cards and is a secondary status: it belongs beside the movement
    it describes, not beside the membership total.
    """
    if latest is None:
        return None

    joined = latest.value("new_members_ytd")
    removed = latest.value("removed_members_ytd")
    suspended = latest.value("suspended_members")
    difference = joined - removed if joined is not None and removed is not None else None

    summary = MembershipMovementSummary(
        observation_label=(
            f"{latest.observation_date.year}. aasta algusest, seisuga "
            f"{long_date(latest.observation_date)}"
        ),
        joined=integer(joined),
        removed=integer(removed),
        difference=signed_integer(difference) if difference is not None else "",
        difference_direction=_direction(difference),
        difference_tone=_tone(difference, rising_is_good=True),
        suspended=integer(suspended),
        suspended_note="Peatatud liikmeid seisuga " + long_date(latest.observation_date)
        if suspended is not None
        else "",
    )
    return summary if summary.has_data else None


# ---------------------------------------------------------------------------
# Data quality, in one line
# ---------------------------------------------------------------------------


def build_quality_badge(quality: InternalQualitySummary) -> MembershipQualityBadge:
    """Whether anything on this page was withheld, in one readable line.

    The full methodology moved behind `Andmete kohta`. What stays in the reading
    path is the one fact a reader relying on a number needs: whether some numbers
    were left out. The count is of things a person could act on — unresolved
    metric conflicts and observations flagged for review — and never of parser
    internals.
    """
    outstanding = quality.conflicted_metric_count + quality.review_required_count
    if not outstanding:
        return MembershipQualityBadge(
            label="Andmed korras", tone=TONE_POSITIVE, needs_attention=False
        )
    return MembershipQualityBadge(
        label=f"{integer(outstanding)} näitajat vajavad ülevaatamist",
        tone=TONE_NEGATIVE,
        needs_attention=True,
    )


# ---------------------------------------------------------------------------
# Source freshness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceStamp:
    """One source and the date it actually describes."""

    label: str
    value: str


def build_source_stamps(
    *,
    latest: ObservationPoint | None,
    quality: InternalQualitySummary,
    composition_date: date | None = None,
) -> tuple[SourceStamp, ...]:
    """What each source is as of, listed only where a source exists.

    One freshness date for the whole page would be a claim no membership
    dashboard can make: the board report, the roster snapshot and the annual
    history are three sources read on three different days. Naming them
    separately is the only honest option, and a source that has not been
    imported is simply not listed.
    """
    stamps: list[SourceStamp] = []
    if latest is not None:
        stamps.append(SourceStamp("Sisemine aruanne", long_date(latest.observation_date)))
    if composition_date is not None:
        stamps.append(SourceStamp("Koosseis", long_date(composition_date)))
    if quality.earliest_observation_date and quality.latest_observation_date:
        stamps.append(
            SourceStamp(
                "Ajalugu",
                f"{quality.earliest_observation_date.year}–{quality.latest_observation_date.year}",
            )
        )
    return tuple(stamps)


# ---------------------------------------------------------------------------
# Composition preview on the overview
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompositionFact:
    """One line of the overview's composition preview."""

    label: str
    value: str
    detail: str = ""


@dataclass(frozen=True)
class CompositionPreview:
    """`Kes on meie liikmed?` — four facts and a way through to the rest.

    Deliberately not a second copy of the composition page. Four readouts and a
    link: a reader who wants the distributions follows the link, and a reader
    who wants a sense of the membership gets it without leaving the overview.

    Omitted entirely when no roster has been imported. An empty panel headed
    with a question is worse than no panel, and a decorative placeholder would
    imply a source that does not exist.
    """

    snapshot_label: str
    facts: tuple[CompositionFact, ...]
    link_query: str
    link_label: str = "Vaata koosseisu"

    @property
    def has_data(self) -> bool:
        return bool(self.facts)


def build_composition_preview(snapshot, *, link_query: str) -> CompositionPreview | None:
    """The four facts that describe the membership in one glance.

    Each names the largest group in its dimension, ignoring `Teadmata`: "most
    members are unclassified" is a fact about the import rather than about the
    Chamber, and it does not belong in a sentence that reads as a statement
    about the membership.
    """
    if snapshot is None:
        return None

    from .composition import Dimension

    facts: list[CompositionFact] = []

    for dimension, label in (
        (Dimension.EMPLOYEE_SIZE, "Suurim suurusklass"),
        (Dimension.REGION, "Suurim piirkond"),
        (Dimension.SECTOR, "Suurim tegevusala"),
    ):
        result = snapshot.dimension(dimension)
        largest = result.largest if result else None
        if largest is not None:
            facts.append(
                CompositionFact(
                    label=label,
                    value=largest.label,
                    detail=f"{integer(largest.count)} liiget · {percent(largest.share_pct)}",
                )
            )

    median = snapshot.median_tenure_years
    if median is not None:
        long_share = snapshot.long_tenure_share_pct
        facts.append(
            CompositionFact(
                label="Mediaanstaaž",
                value=f"{percent(median).rstrip('%')} a",
                detail=(
                    f"{percent(long_share)} on liikmed 11+ aastat" if long_share is not None else ""
                ),
            )
        )

    if not facts:
        return None

    return CompositionPreview(
        snapshot_label=f"Seisuga {long_date(snapshot.snapshot_date)}",
        facts=tuple(facts),
        link_query=link_query,
    )
