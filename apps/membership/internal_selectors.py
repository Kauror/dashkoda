"""Read paths for the internal board-report membership history.

Separate from `selectors.py`, which serves the public Koda.ee directory count.
Nothing in this module reads that model and nothing in that module reads these,
so the two series cannot accidentally become one. They are presented side by
side on the page and are never added, averaged or continued into each other.

Every selector here:

- reads PostgreSQL and nothing else — no remote call is possible from a page
  render;
- returns only preferred, non-superseded observations by default;
- omits a **metric** that is conflicted or impossible rather than the whole
  observation, so one disputed figure never hides the good ones beside it;
- reports how many points it withheld, so the page can say so honestly instead
  of quietly showing a shorter line;
- returns provenance and quality state alongside the numbers, because a figure
  without its date and its status is not something this dashboard shows.

A withheld or missing value is `None`. It is never `0`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Max, Min, Prefetch, Q

from .models import (
    SIZE_BAND_ORDER,
    InternalMembershipObservation,
    MembershipDataIssue,
    MembershipMetricConflict,
    MembershipMonthlyNewMemberValue,
    MembershipRemovalReason,
    MembershipSizeMovement,
    MonthlyValueStatus,
    MovementDirection,
    QualityStatus,
    RemovalReasonKey,
)
from .quality import METRIC_FIELDS, MetricFacts, computed_collection_pct, impossible_metrics

# Charted metrics, and the order the trend selector returns them in.
TREND_METRICS: tuple[str, ...] = (
    "total_members",
    "paid_members",
    "membership_fees_received_eur",
    "membership_fee_budget_eur",
    "new_members_ytd",
    "removed_members_ytd",
    "suspended_members",
)

# The default window on the trend charts. Fourteen years of irregular
# observations is unreadable at once; the full range stays one click away.
DEFAULT_TREND_YEARS = 5

# How many complete years the monthly chart shows beside the current one.
DEFAULT_MONTHLY_HISTORY_YEARS = 3


def _internal_source_slug() -> str:
    return settings.MEMBERSHIP_INTERNAL_SOURCE_SLUG


def _preferred_queryset():
    return InternalMembershipObservation.objects.filter(
        source__slug=_internal_source_slug(),
        is_preferred_for_date=True,
    ).exclude(quality_status=QualityStatus.SUPERSEDED)


def _facts_of(observation: InternalMembershipObservation) -> MetricFacts:
    return MetricFacts(
        total_members=observation.total_members,
        paid_members=observation.paid_members,
        membership_fees_received_eur=observation.membership_fees_received_eur,
        membership_fee_budget_eur=observation.membership_fee_budget_eur,
        membership_fee_collection_pct_reported=(observation.membership_fee_collection_pct_reported),
        new_members_ytd=observation.new_members_ytd,
        suspended_members=observation.suspended_members,
        removed_members_ytd=observation.removed_members_ytd,
    )


def _conflicted_fields_by_date(
    date_from: date | None, date_to: date | None
) -> dict[date, set[str]]:
    """Which model fields are disputed on which dates.

    Unresolved conflicts only: once someone has recorded a resolution the metric
    is no longer withheld.
    """
    query = MembershipMetricConflict.objects.filter(
        source__slug=_internal_source_slug(), resolved=False
    )
    if date_from is not None:
        query = query.filter(observation_date__gte=date_from)
    if date_to is not None:
        query = query.filter(observation_date__lte=date_to)

    conflicted: dict[date, set[str]] = {}
    for conflict in query.only("observation_date", "metric"):
        field = METRIC_FIELDS.get(conflict.metric)
        if field is not None:
            conflicted.setdefault(conflict.observation_date, set()).add(field)
    return conflicted


@dataclass(frozen=True)
class ObservationPoint:
    """One observation, with the metrics that may not be drawn removed."""

    observation: InternalMembershipObservation
    withheld: frozenset[str]

    @property
    def observation_date(self) -> date:
        return self.observation.observation_date

    @property
    def is_year_precision(self) -> bool:
        return self.observation.observation_date_precision == "year"

    def value(self, field: str):
        """The metric, or `None` when it may not be shown.

        `None` is the only way this returns "no value". A caller that wants to
        draw a point checks for `None`; nothing here ever substitutes a zero.
        """
        if field in self.withheld:
            return None
        return getattr(self.observation, field, None)

    @property
    def computed_collection_pct(self) -> Decimal | None:
        received = self.value("membership_fees_received_eur")
        budget = self.value("membership_fee_budget_eur")
        return computed_collection_pct(received, budget)

    @property
    def paid_member_share_pct(self) -> Decimal | None:
        total = self.value("total_members")
        paid = self.value("paid_members")
        if not total or paid is None:
            return None
        return (Decimal(paid) / Decimal(total) * 100).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class InternalTrend:
    """A bounded series of observation points, plus what was left out."""

    points: tuple[ObservationPoint, ...]
    date_from: date | None
    date_to: date | None
    withheld_metric_points: int
    review_required_points: int

    @property
    def has_data(self) -> bool:
        return bool(self.points)

    def series(self, field: str) -> tuple[tuple[date, object], ...]:
        """Date/value pairs for one metric, skipping every absent value.

        Skipping rather than emitting `null` keeps the chart honest in both
        directions: no zero appears, and no line is drawn across a gap as though
        the value in between were known.
        """
        return tuple(
            (point.observation_date, point.value(field))
            for point in self.points
            if point.value(field) is not None
        )


def get_internal_membership_latest() -> ObservationPoint | None:
    """The most recent preferred observation, with its withheld metrics known."""
    observation = (
        _preferred_queryset()
        .select_related("source", "source_document")
        .order_by("-observation_date", "-id")
        .first()
    )
    if observation is None:
        return None
    conflicted = _conflicted_fields_by_date(
        observation.observation_date, observation.observation_date
    )
    withheld = set(conflicted.get(observation.observation_date, set()))
    withheld |= impossible_metrics(_facts_of(observation))
    return ObservationPoint(observation=observation, withheld=frozenset(withheld))


def get_internal_membership_observations(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    metric: str | None = None,
) -> tuple[ObservationPoint, ...]:
    """Preferred observations in a bounded range, oldest first.

    `metric` narrows the result to observations that actually have a drawable
    value for that metric, which is what a single-series chart wants.
    """
    query = _preferred_queryset().select_related("source", "source_document")
    if date_from is not None:
        query = query.filter(observation_date__gte=date_from)
    if date_to is not None:
        query = query.filter(observation_date__lte=date_to)

    conflicted = _conflicted_fields_by_date(date_from, date_to)
    points = []
    for observation in query.order_by("observation_date", "id"):
        withheld = set(conflicted.get(observation.observation_date, set()))
        withheld |= impossible_metrics(_facts_of(observation))
        point = ObservationPoint(observation=observation, withheld=frozenset(withheld))
        if metric is not None and point.value(metric) is None:
            continue
        points.append(point)
    return tuple(points)


def get_internal_membership_trend(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> InternalTrend:
    """The default trend series, with an honest count of what it omits."""
    points = get_internal_membership_observations(date_from=date_from, date_to=date_to)
    withheld_points = sum(len(point.withheld) for point in points)
    review_points = sum(
        1
        for point in points
        if point.observation.quality_status
        in (QualityStatus.REVIEW_REQUIRED, QualityStatus.CONFLICTED)
    )
    return InternalTrend(
        points=points,
        date_from=date_from,
        date_to=date_to,
        withheld_metric_points=withheld_points,
        review_required_points=review_points,
    )


def get_paid_membership_trend(
    *, date_from: date | None = None, date_to: date | None = None
) -> tuple[tuple[date, int], ...]:
    return get_internal_membership_trend(date_from=date_from, date_to=date_to).series(
        "paid_members"
    )


def get_fee_collection_trend(
    *, date_from: date | None = None, date_to: date | None = None
) -> tuple[dict, ...]:
    """Received, budget and both percentages per observation.

    The reported and the calculated percentage are returned separately and are
    never reconciled here. When they differ that is a fact about the report, and
    the page shows both.
    """
    rows = []
    for point in get_internal_membership_observations(date_from=date_from, date_to=date_to):
        received = point.value("membership_fees_received_eur")
        budget = point.value("membership_fee_budget_eur")
        if received is None and budget is None:
            continue
        rows.append(
            {
                "observation_date": point.observation_date,
                "received": received,
                "budget": budget,
                "reported_pct": point.value("membership_fee_collection_pct_reported"),
                "computed_pct": point.computed_collection_pct,
            }
        )
    return tuple(rows)


@dataclass(frozen=True)
class MonthlyValue:
    calendar_year: int
    calendar_month: int
    new_members: int | None
    value_status: str

    @property
    def is_provisional(self) -> bool:
        return self.value_status == MonthlyValueStatus.PROVISIONAL_CURRENT_MONTH

    @property
    def is_conflict(self) -> bool:
        return self.value_status == MonthlyValueStatus.CONFLICT

    @property
    def is_chartable(self) -> bool:
        """A conflict has no number, so it has no point. It is never a zero."""
        return self.new_members is not None


def get_monthly_new_members(
    years: list[int] | tuple[int, ...],
    *,
    include_provisional: bool = True,
) -> dict[int, tuple[MonthlyValue, ...]]:
    """Current monthly values for the given years, one tuple per year.

    A month with no row is absent from the result: it was never reported, which
    is not the same as nobody having joined. A conflict is present with a `None`
    count so the page can mark it, and is never drawn as zero.
    """
    if not years:
        return {}

    query = MembershipMonthlyNewMemberValue.objects.filter(
        source__slug=_internal_source_slug(),
        calendar_year__in=list(years),
        is_current_for_month=True,
    ).exclude(value_status=MonthlyValueStatus.SUPERSEDED)
    if not include_provisional:
        query = query.exclude(value_status=MonthlyValueStatus.PROVISIONAL_CURRENT_MONTH)

    grouped: dict[int, list[MonthlyValue]] = {year: [] for year in years}
    for row in query.order_by("calendar_year", "calendar_month").only(
        "calendar_year", "calendar_month", "new_members", "value_status"
    ):
        grouped.setdefault(row.calendar_year, []).append(
            MonthlyValue(
                calendar_year=row.calendar_year,
                calendar_month=row.calendar_month,
                new_members=row.new_members,
                value_status=row.value_status,
            )
        )
    return {year: tuple(values) for year, values in grouped.items()}


def get_membership_size_movement(observation_id: int) -> tuple[dict, ...]:
    """Joined and removed counts per band, in canonical band order."""
    rows = MembershipSizeMovement.objects.filter(
        observation_id=observation_id,
        observation__source__slug=_internal_source_slug(),
    ).only("direction", "size_band_key", "size_band_label_raw", "member_count")

    by_band: dict[str, dict] = {}
    for row in rows:
        entry = by_band.setdefault(
            row.size_band_key,
            {
                "band": row.size_band_key,
                "label": row.get_size_band_key_display(),
                "joined": None,
                "removed": None,
            },
        )
        if row.direction == MovementDirection.JOINED:
            entry["joined"] = row.member_count
        else:
            entry["removed"] = row.member_count

    ordered = [by_band[band] for band in SIZE_BAND_ORDER if band in by_band]
    return tuple(
        entry for entry in ordered if entry["joined"] is not None or entry["removed"] is not None
    )


def get_removal_reasons(observation_id: int) -> tuple[dict, ...]:
    """Removal reasons with their share of the reported total.

    The share is computed from the reasons actually present, not from the
    observation's own removed total, so a partial table does not produce
    percentages that fail to add up to anything meaningful.
    """
    rows = list(
        MembershipRemovalReason.objects.filter(
            observation_id=observation_id,
            observation__source__slug=_internal_source_slug(),
        ).only("reason_key", "reason_label_raw", "member_count")
    )
    counted = [row for row in rows if row.member_count is not None]
    total = sum(row.member_count for row in counted) or 0

    return tuple(
        {
            "key": row.reason_key,
            # A manually entered `other` keeps the words that were written; the
            # canonical categories use their own labels.
            "label": (
                row.reason_label_raw
                if row.reason_key == RemovalReasonKey.OTHER and row.reason_label_raw
                else row.get_reason_key_display()
            ),
            "count": row.member_count,
            "share_pct": (
                (Decimal(row.member_count) / Decimal(total) * 100).quantize(Decimal("0.1"))
                if total and row.member_count is not None
                else None
            ),
        }
        for row in counted
    )


@dataclass(frozen=True)
class InternalQualitySummary:
    """A concise, viewer-safe statement of data quality.

    Counts only. No warning code, no filesystem path, no parser detail and no
    conflicting value ever leaves this object.
    """

    observation_count: int
    preferred_count: int
    conflicted_metric_count: int
    review_required_count: int
    unresolved_error_count: int
    provisional_month_count: int
    conflict_month_count: int
    earliest_observation_date: date | None
    latest_observation_date: date | None

    @property
    def has_omissions(self) -> bool:
        return bool(self.conflicted_metric_count or self.review_required_count)


def get_internal_membership_quality_summary() -> InternalQualitySummary:
    slug = _internal_source_slug()
    observations = InternalMembershipObservation.objects.filter(source__slug=slug)
    preferred = observations.filter(is_preferred_for_date=True).exclude(
        quality_status=QualityStatus.SUPERSEDED
    )
    span = preferred.aggregate(
        earliest=Min("observation_date"),
        latest=Max("observation_date"),
    )

    monthly = MembershipMonthlyNewMemberValue.objects.filter(
        source__slug=slug, is_current_for_month=True
    )
    return InternalQualitySummary(
        observation_count=observations.count(),
        preferred_count=preferred.count(),
        conflicted_metric_count=MembershipMetricConflict.objects.filter(
            source__slug=slug, resolved=False
        ).count(),
        review_required_count=preferred.filter(
            Q(quality_status=QualityStatus.REVIEW_REQUIRED)
            | Q(quality_status=QualityStatus.CONFLICTED)
        ).count(),
        unresolved_error_count=MembershipDataIssue.objects.filter(
            source__slug=slug, severity="error", resolved=False
        ).count(),
        provisional_month_count=monthly.filter(
            value_status=MonthlyValueStatus.PROVISIONAL_CURRENT_MONTH
        ).count(),
        conflict_month_count=monthly.filter(value_status=MonthlyValueStatus.CONFLICT).count(),
        earliest_observation_date=span["earliest"],
        latest_observation_date=span["latest"],
    )


def get_manual_entry_defaults(reporting_year: int) -> dict:
    """What the manual form should show before the user types anything.

    Existing current monthly values for the year are returned so the form can
    display them; a month the user then changes creates a superseding record
    rather than editing the old one.
    """
    monthly = get_monthly_new_members([reporting_year])
    latest = get_internal_membership_latest()
    return {
        "reporting_year": reporting_year,
        "existing_monthly": monthly.get(reporting_year, ()),
        "latest_observation": latest.observation if latest else None,
        "latest_observation_date": latest.observation_date if latest else None,
    }


def get_observation_detail(observation_id: int) -> ObservationPoint | None:
    """One observation with its children prefetched, for the detail panel."""
    observation = (
        InternalMembershipObservation.objects.filter(
            source__slug=_internal_source_slug(), pk=observation_id
        )
        .select_related("source", "source_document")
        .prefetch_related(
            Prefetch("size_movements", queryset=MembershipSizeMovement.objects.all()),
            Prefetch("removal_reasons", queryset=MembershipRemovalReason.objects.all()),
        )
        .first()
    )
    if observation is None:
        return None
    conflicted = _conflicted_fields_by_date(
        observation.observation_date, observation.observation_date
    )
    withheld = set(conflicted.get(observation.observation_date, set()))
    withheld |= impossible_metrics(_facts_of(observation))
    return ObservationPoint(observation=observation, withheld=frozenset(withheld))
