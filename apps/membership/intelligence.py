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
    percent,
    percentage_points,
    signed_integer,
    signed_percent,
)

from .analytics import (
    compare_with,
    share_change,
)
from .internal_selectors import InternalQualitySummary, ObservationPoint

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

# How far from its anniversary a **year-to-date** baseline may sit.
#
# Much tighter than `YOY_TOLERANCE_DAYS`, and for a reason that is about the
# metric rather than about the source. A membership total is a stock: the
# reading nearest an anniversary describes the same kind of thing whether it
# landed in July or in August, so 45 days of slack costs nothing. `Liitunud` and
# `Väljaarvatud` are cumulative counts from 1 January, and they grow all year —
# a baseline six weeks short of the anniversary is six weeks short of the year
# it is standing in for, and every previous year would read low by construction.
#
# Fifteen days is one monthly report either side of the anniversary. Beyond
# that the comparison is withheld, which is the honest answer: the strip prints
# the count with no percentage rather than a percentage nobody can trust.
YTD_TOLERANCE_DAYS = 15

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
    """One of the questions the page answers before it is scrolled.

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
    #: 0-100, for the one figure that is a completion against a stated target.
    #: Drawn as SVG geometry, never an inline width — see `kpi_card.html`, whose
    #: meter this reuses. `None` on every other headline: a proportion bar under
    #: a member count would be a bar against no denominator.
    meter_pct: float | None = None

    @property
    def is_available(self) -> bool:
        return bool(self.value)

    @property
    def has_change(self) -> bool:
        return bool(self.change)


@dataclass(frozen=True)
class MovementFigure:
    """One of the current year's movement counts, with its own comparison.

    Structured rather than three parallel tuples on the summary, because each
    figure compares differently: arrivals rising is good news, departures rising
    is not, and the suspended total is a state with no year-ago figure to set it
    against at all.
    """

    key: str
    value: str
    #: The noun that follows the figure — `liitunud`, `väljaarvatud`. It sits
    #: after the number rather than above it, because these three read as one
    #: sentence about the year.
    label: str
    change: str = ""
    change_label: str = ""
    direction: str = ""
    tone: str = TONE_NEUTRAL
    comparison_label: str = ""

    @property
    def has_change(self) -> bool:
        return bool(self.change)


@dataclass(frozen=True)
class MembershipMovementSummary:
    """`Sel aastal` — what the current year's report says about movement.

    The gap between arrivals and departures is `difference`, and it is labelled
    as the difference between two reported counts everywhere it appears. It is
    not the change in the membership, and this dataset cannot show that it is.
    Nothing has rendered it since 2026-08-16.
    """

    observation_label: str
    figures: tuple[MovementFigure, ...] = ()
    joined: str = ""
    removed: str = ""
    difference: str = ""
    difference_direction: str = ""
    difference_tone: str = TONE_NEUTRAL
    suspended: str = ""
    suspended_note: str = ""

    @property
    def has_data(self) -> bool:
        return bool(self.figures)

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

    # 2. Tasunud liikmeid — the paid count, its share, and the gap.
    #
    #    It led with both totals (`3 406 · 3 233`) until 2026-08-18, and the
    #    first of them was the card to its left printed a second time. What is
    #    unique to this card is the *paid* side, so that is what it leads with;
    #    the share sits beside the count because a paid count without its
    #    denominator is a number nobody can size.
    #
    #    **The change is the share's, in percentage points, not the total's.**
    #    The card it replaced compared the member total year on year — which is
    #    `Liikmeid kokku`'s comparison, printed a second time a card later. The
    #    pp movement is the one figure in this card that appears nowhere else in
    #    the strip, so it is the one worth the change line.
    #
    #    The gap is **members who have not paid**, not a movement: nobody left,
    #    nobody joined, and describing it as a change would be the same error the
    #    card this replaced existed to avoid.
    share = latest.paid_member_share_pct
    share_history = _share_series(history)
    share_comparison = compare_with(share, on, share_history)
    points_moved = (
        share_change(share, share_comparison.baseline) if share_comparison.is_available else None
    )

    gap = total - paid if total is not None and paid is not None else None
    share_label = percent(share) if share is not None else ""
    # Members who have **not** paid, which is a state and not a movement:
    # nobody left and nobody joined. The share moved into the value above it on
    # 2026-08-18, so this line no longer repeats it.
    if gap is not None:
        detail = f"vahe {integer(gap)}"
    else:
        detail = "Vahet ei saa arvutada, sest üks pooltest puudub."

    movement = MembershipHeadline(
        key="members_and_paid",
        label="Tasunud liikmeid",
        value=(
            f"{integer(paid)} · {share_label}"
            if paid is not None and share_label
            else integer(paid)
            if paid is not None
            else ""
        ),
        detail=detail,
        change=percentage_points(points_moved) if points_moved is not None else "",
        change_label=(
            f"tasunute osakaal {percentage_points(points_moved)} võrreldes aastataguse vaatlusega"
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

    return (members, movement, _fee_headline(latest, history))


def _fee_headline(
    latest: ObservationPoint, history: tuple[ObservationPoint, ...]
) -> MembershipHeadline:
    """Fee collection as one readout: the completion, and the amounts under it.

    The percentage shown is the one the **amounts imply**, which is the same
    figure the fee chart draws. `quality.py` withholds a reported percentage that
    disagrees with the amounts beside it, so when the two disagree the amounts
    are what survives — and the disagreement is disclosed here rather than
    silently resolved.

    When the amounts cannot produce a percentage but the report stated one, that
    reported figure is shown and labelled as reported. The two are never averaged
    and one never quietly stands in for the other.

    The year-ago comparison joined this card on 2026-08-17, matching the other
    two: computed percentages on both sides, so a reported figure that disagreed
    with its own amounts cannot enter the comparison from either end. The
    `Mis muutus?` strip carried the same comparison as one of its candidates
    until that strip retired on 2026-08-18.
    """
    received = latest.value("membership_fees_received_eur")
    budget = latest.value("membership_fee_budget_eur")
    computed = latest.computed_collection_pct
    reported = latest.value("membership_fee_collection_pct_reported")
    reported_withheld = "membership_fee_collection_pct_reported" in latest.withheld
    on = latest.observation_date

    if computed is not None:
        value, note = percent(computed), ""
        if reported_withheld:
            note = "Aruandes esitatud protsent ei klapi summadega ja on välja jäetud."
    elif reported is not None:
        value = percent(reported)
        note = "Aruandes esitatud protsent; summadest seda arvutada ei saa."
    else:
        value, note = "", "Laekumise protsenti ei saa arvutada."

    detail = (
        f"{euros(received)} / {euros(budget)}"
        if received is not None and budget is not None
        else (euros(received) if received is not None else "")
    )

    collection_history = tuple(
        (point.observation_date, point.computed_collection_pct)
        for point in history
        if point.computed_collection_pct is not None
    )
    collection_comparison = compare_with(computed, on, collection_history)
    moved_pp = (
        share_change(computed, collection_comparison.baseline)
        if collection_comparison.is_available
        else None
    )

    # The one figure on this strip that is a completion against a stated
    # target, so the one that may carry a bar. Clamped rather than clipped,
    # because a year that collected more than it budgeted is a real result and
    # the amounts beneath the bar still state it exactly.
    meter = (
        min(100.0, max(0.0, float(computed)))
        if computed is not None and received is not None and budget is not None
        else None
    )

    return MembershipHeadline(
        key="fee_collection",
        label="Liikmemaksu laekumine",
        value=value,
        detail=detail,
        meter_pct=meter,
        change=percentage_points(moved_pp) if moved_pp is not None else "",
        change_label=(
            f"laekumine {percentage_points(moved_pp)} võrreldes aastataguse vaatlusega"
            if moved_pp is not None
            else ""
        ),
        direction=_direction(moved_pp),
        tone=_tone(moved_pp, rising_is_good=True),
        comparison_label=(
            f"aasta tagasi · {_comparison_label(collection_comparison)[3:]}"
            if collection_comparison.is_available
            else ""
        ),
        note=note,
    )


# ---------------------------------------------------------------------------
# Sel aastal
# ---------------------------------------------------------------------------


def build_movement_summary(
    latest: ObservationPoint | None,
    history: tuple[ObservationPoint, ...] = (),
) -> MembershipMovementSummary | None:
    """The current year's arrivals and departures, each against last year's.

    The fourth cell of the headline strip since 2026-08-18. It was a section of
    its own below the strip, which put the year's movement a scroll away from
    the membership total it moves — and left the strip a column short.

    Deliberately typography rather than a chart. Three counts are three numbers,
    and drawing three numbers as bars adds a canvas, an axis and a legend to a
    fact that fits on one line; `Sisse-välja` carries the size-band diverging
    chart for the question that genuinely needs a picture.

    ## Why these comparisons use a tighter anniversary than the rest of the page

    `new_members_ytd` is a **cumulative flow**, not a stock. It counts from
    1 January and grows all year, so a baseline drawn 45 days off its
    anniversary — which `YOY_TOLERANCE_DAYS` permits, correctly, for a
    membership total — would be six weeks short of the same point in its own
    year and would understate every previous year by construction. `Liikmeid
    kokku` tolerates a loose anniversary because a stock in July and a stock in
    August describe the same kind of thing. A year-to-date count does not.

    The suspended total carries no comparison. It is a state rather than a
    year-to-date flow, and it is not reported with a prior-year figure.
    """
    if latest is None:
        return None

    joined = latest.value("new_members_ytd")
    removed = latest.value("removed_members_ytd")
    suspended = latest.value("suspended_members")
    difference = joined - removed if joined is not None and removed is not None else None

    figures: list[MovementFigure] = []
    for key, value, label, rising_is_good in (
        ("joined", joined, "liitunud", True),
        # A rise in departures is an increase and bad news. Direction and tone
        # are separate fields for exactly this: a strip that painted it green
        # because the number rose would be worse than one with no colour.
        ("removed", removed, "väljaarvatud", False),
    ):
        if value is None:
            continue
        figures.append(
            _movement_figure(
                key=key,
                value=value,
                label=label,
                rising_is_good=rising_is_good,
                latest=latest,
                history=history,
            )
        )
    if suspended is not None:
        figures.append(MovementFigure(key="suspended", value=integer(suspended), label="peatatud"))

    summary = MembershipMovementSummary(
        observation_label=f"{latest.observation_date.year}. aasta algusest",
        figures=tuple(figures),
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


def _movement_figure(
    *,
    key: str,
    value,
    label: str,
    rising_is_good: bool,
    latest: ObservationPoint,
    history: tuple[ObservationPoint, ...],
) -> MovementFigure:
    """One count, compared with the same point in the previous year.

    The comparison is a percentage rather than an absolute difference, because
    that is what one cell of a four-column strip has room for. The counts
    themselves are on the page, so a reader who wants the absolute change has
    both sides of it.
    """
    field = "new_members_ytd" if key == "joined" else "removed_members_ytd"
    comparison = compare_with(
        value,
        latest.observation_date,
        _series(history, field),
        tolerance_days=YTD_TOLERANCE_DAYS,
    )
    if not comparison.is_available or not comparison.has_relative:
        return MovementFigure(key=key, value=integer(value), label=label)
    return MovementFigure(
        key=key,
        value=integer(value),
        label=label,
        change=signed_percent(comparison.relative_pct),
        change_label=(
            f"{signed_percent(comparison.relative_pct)} võrreldes "
            f"{comparison.baseline_date.year}. aasta sama ajaga"
        ),
        direction=_direction(comparison.absolute),
        tone=_tone(comparison.absolute, rising_is_good=rising_is_good),
        comparison_label=f"vs {comparison.baseline_date.year}",
    )


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
    register_date: date | None = None,
) -> tuple[SourceStamp, ...]:
    """What each source is as of, listed only where a source exists.

    One freshness date for the whole page would be a claim no membership
    dashboard can make: the board report, the roster snapshot and the annual
    history are sources read on different days. Naming them separately is the
    only honest option, and a source that has not been imported is simply not
    listed.

    The register and the composition are usually two imports of the *same*
    export, so an identical pair of dates is stated once. They are separate
    stamps when they differ, which is exactly the case a reader needs to see:
    it means the list on the page and the charts beside it describe two
    different days.
    """
    stamps: list[SourceStamp] = []
    if latest is not None:
        stamps.append(SourceStamp("Sisemine aruanne", long_date(latest.observation_date)))
    if composition_date is not None:
        stamps.append(SourceStamp("Koosseis", long_date(composition_date)))
    if register_date is not None and register_date != composition_date:
        stamps.append(SourceStamp("Nimekiri", long_date(register_date)))
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


def composition_subtitles(snapshot) -> dict[str, str]:
    """One short line per composition dimension, naming what leads it.

    These four facts were a strip of their own — `Kes on meie liikmed?`, four
    readouts sitting above four charts that drew the same four dimensions. On
    2026-08-18 the strip and the charts became one section: each chart carries
    its own fact as a subtitle, so the answer sits on the drawing that proves it
    rather than a scroll above it.

    Each names the largest group in its dimension, **ignoring `Teadmata`**:
    "most members are unclassified" is a fact about the import rather than about
    the Chamber, and it does not belong in a line that reads as a statement
    about the membership. `CompositionDimensionResult.largest` is where that
    rule lives; this only formats what it returns.

    Tenure is the exception and gets the median instead of a largest band. The
    bands are an ordinal scale, so "the biggest band" says much less than the
    midpoint and the share above eleven years — which is the pair the board
    reads it for.
    """
    if snapshot is None:
        return {}

    from .composition import Dimension

    subtitles: dict[str, str] = {}
    for dimension in (Dimension.EMPLOYEE_SIZE, Dimension.REGION, Dimension.SECTOR):
        result = snapshot.dimension(dimension)
        largest = result.largest if result else None
        if largest is not None:
            subtitles[dimension] = f"suurim: {largest.label.lower()}"

    median = snapshot.median_tenure_years
    if median is not None:
        line = f"mediaan {percent(median).rstrip('%')} a"
        long_share = snapshot.long_tenure_share_pct
        if long_share is not None:
            line = f"{line} · {percent(long_share)} on 11+ aastat"
        subtitles[Dimension.TENURE_BAND] = line
    return subtitles
