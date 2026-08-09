"""Read paths for the manually observed audience figures.

Every selector here reads PostgreSQL and nothing else. **No page render fetches
a social profile, calls a platform API or resolves a URL** — the fixed links in
`registry.py` are display links, and nothing in this module touches them.

Four rules run through all of it:

- a metric nobody has entered returns `None`, never `0`. An explicitly entered
  `0` is a real reading and stays distinguishable from an absent one;
- a superseded row is excluded from every current-value query and stays fully
  readable as history;
- a change is measured against the **actual previous observation**, whatever its
  date, and that date is returned with it. A difference without its baseline is
  not something this dashboard shows;
- freshness comes from the registry threshold for that metric, and only ever
  labels a figure. An old reading is still the last thing anybody counted, so it
  is never hidden.

The three newsletter lists are reported one by one and never added up, here or
anywhere else. Nobody has counted how many people are on more than one of them,
so a total would silently claim an overlap of zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from django.db.models import F, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from .models import (
    CollectionMethod,
    VisibilityEntryBatch,
    VisibilityMetric,
    VisibilityObservation,
)
from .registry import (
    METRICS,
    NEWSLETTER_METRICS,
    SOCIAL_METRICS,
    VisibilityMetricSpec,
    spec_for,
)


class ReadingState(StrEnum):
    """What can honestly be said about a manually observed figure.

    Deliberately *not* `apps.dashboard.connections.ConnectionState`. That
    vocabulary describes a connected feed, and its `Ühendatud` label would tell a
    board member an integration exists. Nothing here is synchronised, connected
    to an API or automatically updated, and the wording says so.
    """

    OBSERVED = "observed"
    STALE = "stale"
    MISSING = "missing"


_STATE_LABELS = {
    ReadingState.OBSERVED: "Käsitsi sisestatud",
    ReadingState.STALE: "Vajab uuendamist",
    ReadingState.MISSING: "Andmed puuduvad",
}

_STATE_VARIANTS = {
    ReadingState.OBSERVED: "neutral",
    ReadingState.STALE: "warning",
    ReadingState.MISSING: "neutral",
}

#: Shown wherever a value appears without a full provenance row.
MANUAL_METHOD_LABEL = "Käsitsi sisestatud"

#: The website card's honest state until a GA4 observation actually exists.
GA4_NOT_CONNECTED_MESSAGE = "Google Analytics ei ole ühendatud."


@dataclass(frozen=True)
class MetricReading:
    """One metric's latest value, its provenance and what it moved from.

    A reading with `observation is None` is the truthful empty state: nobody has
    entered this metric. It renders as "Andmed puuduvad" and never as a zero.
    """

    spec: VisibilityMetricSpec
    observation: VisibilityObservation | None = None
    previous: VisibilityObservation | None = None
    #: Injected so a whole page shares one "today" and cannot disagree with
    #: itself about what is stale.
    today: date | None = None

    @property
    def has_data(self) -> bool:
        return self.observation is not None

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def unit(self) -> str:
        return self.spec.unit

    @property
    def value(self) -> int | None:
        return self.observation.value if self.observation is not None else None

    @property
    def as_of(self) -> date | None:
        return self.observation.observation_date if self.observation is not None else None

    @property
    def source_label(self) -> str:
        return self.spec.source_label

    @property
    def profile_url(self) -> str:
        return self.spec.profile_url

    @property
    def collection_method(self) -> str:
        if self.observation is None:
            return ""
        return self.observation.collection_method

    @property
    def method_label(self) -> str:
        if self.observation is None:
            return ""
        return CollectionMethod(self.observation.collection_method).label

    @property
    def is_manual(self) -> bool:
        return self.collection_method == CollectionMethod.MANUAL

    @property
    def is_stale(self) -> bool:
        if self.observation is None or self.today is None:
            return False
        return self.spec.is_stale_on(self.observation.observation_date, today=self.today)

    @property
    def state(self) -> ReadingState:
        if self.observation is None:
            return ReadingState.MISSING
        return ReadingState.STALE if self.is_stale else ReadingState.OBSERVED

    @property
    def state_label(self) -> str:
        return _STATE_LABELS[self.state]

    @property
    def state_variant(self) -> str:
        return _STATE_VARIANTS[self.state]

    @property
    def previous_value(self) -> int | None:
        return self.previous.value if self.previous is not None else None

    @property
    def previous_date(self) -> date | None:
        return self.previous.observation_date if self.previous is not None else None

    @property
    def change(self) -> int | None:
        if self.observation is None or self.previous is None:
            return None
        return self.observation.value - self.previous.value

    @property
    def change_label(self) -> str:
        """A signed difference, or empty when there is nothing to compare with."""
        difference = self.change
        if difference is None:
            return ""
        return f"+{difference}" if difference > 0 else str(difference)

    @property
    def change_direction(self) -> str:
        difference = self.change
        if difference is None:
            return ""
        if difference > 0:
            return "up"
        return "down" if difference < 0 else "flat"

    @property
    def change_pct(self) -> Decimal | None:
        """`None` when there is no baseline, and when the baseline was zero."""
        if self.change is None or self.previous.value == 0:
            return None
        return (Decimal(self.change) / Decimal(self.previous.value) * 100).quantize(Decimal("0.1"))

    @property
    def comparison_period(self) -> str:
        if self.previous is None:
            return ""
        return f"võrreldes seisuga {self.previous_date:%d.%m.%Y}"


def get_latest_visibility_observation(metric: str) -> VisibilityObservation | None:
    """The newest current observation for one metric, or `None`.

    "Newest current" and not merely "newest": a row that a same-date correction
    retired is history, and reading it would show a value the Chamber has already
    replaced.
    """
    return (
        VisibilityObservation.objects.filter(metric=metric, is_current_for_date=True)
        .select_related("source", "batch")
        .order_by("-observation_date", "-id")
        .first()
    )


def get_previous_visibility_observation(
    metric: str, *, before: date | None = None
) -> VisibilityObservation | None:
    """The current observation immediately preceding `before`.

    With no `before`, it is the one preceding the latest — which is what a
    "change since last time" comparison needs.
    """
    if before is None:
        latest = get_latest_visibility_observation(metric)
        if latest is None:
            return None
        before = latest.observation_date
    return (
        VisibilityObservation.objects.filter(
            metric=metric,
            is_current_for_date=True,
            observation_date__lt=before,
        )
        .select_related("source")
        .order_by("-observation_date", "-id")
        .first()
    )


def get_visibility_history(
    metric: str,
    *,
    limit: int | None = None,
    date_from: date | None = None,
) -> tuple[VisibilityObservation, ...]:
    """Current observations for one metric, oldest first.

    Oldest first because that is the order a trend is drawn in. `limit` keeps the
    most **recent** rows, then restores chronological order, so asking for the
    last five points does not silently return the first five.
    """
    query = VisibilityObservation.objects.filter(metric=metric, is_current_for_date=True)
    if date_from is not None:
        query = query.filter(observation_date__gte=date_from)
    query = query.select_related("source").order_by("observation_date", "id")
    if limit is not None:
        rows = list(query)[-limit:]
        return tuple(rows)
    return tuple(query)


def get_visibility_series(
    metric: str, *, date_from: date | None = None
) -> tuple[tuple[date, int], ...]:
    """`(date, value)` pairs for a sparkline, oldest first.

    A date with no observation is simply absent. Nothing is interpolated and no
    zero is substituted, so a gap in the readings stays a gap.
    """
    return tuple(
        (row.observation_date, row.value)
        for row in get_visibility_history(metric, date_from=date_from)
    )


def _readings_by_metric(metrics, *, today: date) -> dict[str, MetricReading]:
    """Every requested metric's current reading, in **one** query.

    Each reading needs two rows: the newest current observation and the one
    before it, which is what a change is measured against. Asking per metric
    cost two queries each — fourteen for the seven metrics the page draws — and
    every one of them was the same query with a different constant.

    A window ranks the rows per metric and keeps the first two. The second row
    is the previous observation and not merely the second-newest row, because
    `visibilityobservation_one_current_per_metric_date` guarantees exactly one
    current row per metric per date: among current rows the dates are unique, so
    rank 2 is the newest with a strictly earlier date. That is the same row
    `get_previous_visibility_observation` returns.

    A metric nobody has entered has no rows and therefore no reading — absent,
    never zero.
    """
    ranked = (
        VisibilityObservation.objects.filter(metric__in=list(metrics), is_current_for_date=True)
        .annotate(
            position=Window(
                expression=RowNumber(),
                partition_by=[F("metric")],
                order_by=[F("observation_date").desc(), F("id").desc()],
            )
        )
        .filter(position__lte=2)
        .select_related("source", "batch")
        .order_by("metric", "position")
    )

    rows: dict[str, list] = {}
    for observation in ranked:
        rows.setdefault(observation.metric, []).append(observation)

    return {
        metric: MetricReading(
            spec=spec_for(metric),
            observation=found[0] if found else None,
            previous=found[1] if len(found) > 1 else None,
            today=today,
        )
        for metric in metrics
        for found in [rows.get(metric, [])]
    }


@dataclass(frozen=True)
class NewsletterSummary:
    """The Chamber's three newsletters, each list on its own.

    There is no union figure and no total. The three lists are separate
    audiences, and nobody has ever counted how many people appear on more than
    one of them, so any sum would silently claim an overlap of zero. A reader
    who wants "how many people do we reach" is given three numbers and the
    honest answer that they are three lists.
    """

    lists: tuple[MetricReading, ...]

    @property
    def readings(self) -> tuple[MetricReading, ...]:
        return self.lists

    @property
    def has_any_data(self) -> bool:
        return any(reading.has_data for reading in self.readings)

    @property
    def entered(self) -> tuple[MetricReading, ...]:
        """Only the lists somebody has actually read off Smaily."""
        return tuple(reading for reading in self.readings if reading.has_data)

    @property
    def _dates(self) -> tuple[date, ...]:
        return tuple(reading.as_of for reading in self.readings if reading.as_of is not None)

    @property
    def as_of(self) -> date | None:
        """The **oldest** entered reading.

        The card is only as current as its stalest list, so claiming the newest
        date would overstate it.
        """
        dates = self._dates
        return min(dates) if dates else None

    @property
    def readings_share_a_date(self) -> bool:
        dates = self._dates
        return len(set(dates)) <= 1

    @property
    def is_stale(self) -> bool:
        return any(reading.is_stale for reading in self.readings if reading.has_data)


def get_newsletter_summary(*, today: date | None = None) -> NewsletterSummary:
    today = today or timezone.localdate()
    readings = _readings_by_metric(NEWSLETTER_METRICS, today=today)
    return NewsletterSummary(lists=tuple(readings[metric] for metric in NEWSLETTER_METRICS))


@dataclass(frozen=True)
class VisibilitySummary:
    """Every metric's current state, in registry display order."""

    newsletter: NewsletterSummary
    social: tuple[MetricReading, ...]
    today: date

    @property
    def readings(self) -> tuple[MetricReading, ...]:
        return (*self.newsletter.readings, *self.social)

    @property
    def has_any_data(self) -> bool:
        return any(reading.has_data for reading in self.readings)

    @property
    def stale_count(self) -> int:
        return sum(1 for reading in self.readings if reading.is_stale)

    def reading(self, metric: str) -> MetricReading | None:
        for candidate in self.readings:
            if candidate.spec.key == metric:
                return candidate
        return None


def get_visibility_summary(*, today: date | None = None) -> VisibilitySummary:
    """Every metric's current state. One query for all seven."""
    today = today or timezone.localdate()
    readings = _readings_by_metric((*NEWSLETTER_METRICS, *SOCIAL_METRICS), today=today)
    return VisibilitySummary(
        newsletter=NewsletterSummary(
            lists=tuple(readings[metric] for metric in NEWSLETTER_METRICS)
        ),
        social=tuple(readings[metric] for metric in SOCIAL_METRICS),
        today=today,
    )


@dataclass(frozen=True)
class EntryHistoryRow:
    """One past submission, summarised for the staff history list."""

    batch: VisibilityEntryBatch
    observations: tuple[VisibilityObservation, ...]

    @property
    def observation_date(self) -> date:
        return self.batch.observation_date

    @property
    def metric_labels(self) -> tuple[str, ...]:
        return tuple(
            spec.label
            for spec in METRICS
            if any(row.metric == spec.key for row in self.observations)
        )

    @property
    def metric_count(self) -> int:
        return len(self.observations)

    @property
    def correction_count(self) -> int:
        return sum(1 for row in self.observations if row.supersedes_id is not None)

    @property
    def superseded_count(self) -> int:
        return sum(1 for row in self.observations if not row.is_current_for_date)


def get_visibility_entry_history(*, limit: int | None = None) -> tuple[EntryHistoryRow, ...]:
    """Every manual submission, newest first, with what it published."""
    query = (
        VisibilityEntryBatch.objects.select_related("created_by")
        .prefetch_related("observations")
        .order_by("-observation_date", "-id")
    )
    if limit is not None:
        query = query[:limit]
    return tuple(
        EntryHistoryRow(
            batch=batch,
            observations=tuple(
                sorted(
                    batch.observations.all(),
                    key=lambda row: [spec.key for spec in METRICS].index(row.metric),
                )
            ),
        )
        for batch in query
    )


def get_batch_detail(batch_id: int) -> EntryHistoryRow | None:
    """One submission with everything it published, for the confirmation page."""
    batch = (
        VisibilityEntryBatch.objects.select_related("created_by")
        .prefetch_related("observations__source", "observations__supersedes")
        .filter(pk=batch_id)
        .first()
    )
    if batch is None:
        return None
    order = [spec.key for spec in METRICS]
    return EntryHistoryRow(
        batch=batch,
        observations=tuple(
            sorted(batch.observations.all(), key=lambda row: order.index(row.metric))
        ),
    )


def get_manual_entry_defaults(*, today: date | None = None) -> dict[str, MetricReading]:
    """The latest stored reading for each metric, keyed by metric.

    Shown beside each input so the person entering a number can see what it is
    replacing before they type, rather than after they have submitted.
    """
    today = today or timezone.localdate()
    # One query, like the summary: the staff form shows every metric at once, so
    # asking per metric cost the same fourteen round trips the page used to.
    return _readings_by_metric(tuple(spec.key for spec in METRICS), today=today)


def latest_observation_date() -> date | None:
    """The newest date any metric was observed on, across all of them."""
    newest = (
        VisibilityObservation.objects.filter(is_current_for_date=True)
        .order_by("-observation_date")
        .values_list("observation_date", flat=True)
        .first()
    )
    return newest


def has_any_observation() -> bool:
    return VisibilityObservation.objects.exists()


def newsletter_metric_keys() -> tuple[str, ...]:
    return NEWSLETTER_METRICS


def social_metric_keys() -> tuple[str, ...]:
    return SOCIAL_METRICS


__all__ = [
    "GA4_NOT_CONNECTED_MESSAGE",
    "MANUAL_METHOD_LABEL",
    "EntryHistoryRow",
    "MetricReading",
    "NewsletterSummary",
    "ReadingState",
    "VisibilityMetric",
    "VisibilitySummary",
    "get_batch_detail",
    "get_latest_visibility_observation",
    "get_manual_entry_defaults",
    "get_newsletter_summary",
    "get_previous_visibility_observation",
    "get_visibility_entry_history",
    "get_visibility_history",
    "get_visibility_series",
    "get_visibility_summary",
    "has_any_observation",
    "latest_observation_date",
    "newsletter_metric_keys",
    "social_metric_keys",
]


@dataclass(frozen=True)
class WebsiteTraffic:
    """The newest website reading, and the one before it.

    Separate from `MetricReading` because that describes a figure a person
    typed: its vocabulary says `Käsitsi sisestatud` and its staleness is
    measured against a cadence somebody is expected to keep. Website traffic is
    the one automated series, arrives daily on its own, and has to say so.
    """

    period_end: date | None = None
    sessions: int | None = None
    active_users: int | None = None
    page_views: int | None = None
    previous_period_end: date | None = None
    previous_sessions: int | None = None

    @property
    def has_data(self) -> bool:
        # `is not None`, not truthiness: a day with no visits is a real reading.
        return self.sessions is not None

    @property
    def change(self) -> int | None:
        """Against the previous reading, when both exist."""
        if self.sessions is None or self.previous_sessions is None:
            return None
        return self.sessions - self.previous_sessions


def get_website_traffic() -> WebsiteTraffic:
    """The two most recent published readings, newest first.

    Two rather than one because a single number answers "how many" and nothing
    else; the board reads this band for movement. Only `is_current` rows are
    considered, so a superseded correction never becomes the comparison.
    """
    from .models import WebsiteTrafficObservation

    rows = list(
        WebsiteTrafficObservation.objects.filter(is_current=True)
        .order_by("-period_end")
        .values("period_end", "sessions", "active_users", "page_views")[:2]
    )
    if not rows:
        return WebsiteTraffic()

    latest = rows[0]
    earlier = rows[1] if len(rows) > 1 else {}
    return WebsiteTraffic(
        period_end=latest["period_end"],
        sessions=latest["sessions"],
        active_users=latest["active_users"],
        page_views=latest["page_views"],
        previous_period_end=earlier.get("period_end"),
        previous_sessions=earlier.get("sessions"),
    )
