"""Reading the stored Smaily history back out.

Every function here reads PostgreSQL and nothing else. No page render contacts
Smaily — see `apps.visibility.smaily`, which is only ever called from a
scheduled command.

Three rules shape what these return, and all three are about not inventing
numbers the source never reported:

- **a newsletter with no reading has no point**, not a zero. A chart that padded
  the days before collection started would show three newsletters being founded
  on the morning the collector was deployed;
- **a rate is never stored and never averaged from rates.** Open rate is opens
  over *delivered*, and averaging per-campaign percentages would weight a send
  to eight hundred people the same as one to twenty thousand. Aggregates here
  divide summed counts by summed counts, which is the only form that means what
  the label says;
- **the three newsletters are never combined.** A reader subscribed to two of
  them is one person and two subscriptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db.models import Sum

from apps.core.formatting import group_thousands, percent

from .models import SmailyCampaign, SmailyCampaignStats, SmailySegmentDaily
from .registry import spec_for
from .smaily_campaigns import AUDIENCE_NON_MEMBERS
from .smaily_segments import NEWSLETTERS, NEWSLETTERS_BY_METRIC, NewsletterSpec


@dataclass(frozen=True)
class SubscriberPoint:
    """One newsletter's audience on one day."""

    observed_on: date
    subscribers: int


@dataclass(frozen=True)
class SubscriberSeries:
    """A newsletter's audience over time, as far back as it was collected."""

    metric: str
    label: str
    points: tuple[SubscriberPoint, ...] = ()

    @property
    def has_points(self) -> bool:
        return bool(self.points)

    @property
    def is_drawable(self) -> bool:
        """One reading is a figure, not a trend, and is not drawn as one."""
        return len(self.points) >= 2

    @property
    def latest(self) -> SubscriberPoint | None:
        return self.points[-1] if self.points else None

    @property
    def earliest(self) -> SubscriberPoint | None:
        return self.points[0] if self.points else None

    @property
    def change(self) -> int | None:
        """Growth across the whole window, or `None` with nothing to compare."""
        if len(self.points) < 2:
            return None
        return self.points[-1].subscribers - self.points[0].subscribers


def _segment_ids(spec: NewsletterSpec) -> tuple[int, ...]:
    return tuple(segment.segment_id for segment in spec.segments)


def get_subscriber_series(
    metric: str, *, start: date | None = None, end: date | None = None
) -> SubscriberSeries:
    """One newsletter's audience per day, summed across its own segments.

    e-Teataja is two segments and the two are added, for the reason
    `apps.visibility.smaily_segments` sets out: they are members and
    non-members, disjoint by construction.

    A day on which **any** of a newsletter's segments was missing produces no
    point at all. Summing what was read would draw a cliff on the chart the day
    a segment was renamed, and a cliff is how a reader sees a collapse.
    """
    spec = NEWSLETTERS_BY_METRIC.get(metric)
    registry_spec = spec_for(metric)
    if spec is None or registry_spec is None:
        return SubscriberSeries(metric=metric, label="")

    ids = _segment_ids(spec)
    rows = SmailySegmentDaily.objects.filter(
        segment_id__in=ids,
        snapshot__is_current_for_date=True,
    )
    if start is not None:
        rows = rows.filter(observed_on__gte=start)
    if end is not None:
        rows = rows.filter(observed_on__lte=end)

    totals: dict[date, list[int]] = {}
    for observed_on, subscribers in rows.values_list("observed_on", "subscribers"):
        totals.setdefault(observed_on, []).append(subscribers)

    points = tuple(
        SubscriberPoint(observed_on=day, subscribers=sum(values))
        for day, values in sorted(totals.items())
        # Every segment, or no point. A partial sum is a smaller newsletter.
        if len(values) == len(ids)
    )
    return SubscriberSeries(metric=metric, label=registry_spec.label, points=points)


def get_all_subscriber_series(
    *, start: date | None = None, end: date | None = None
) -> tuple[SubscriberSeries, ...]:
    """Every newsletter's audience series, in registry order."""
    return tuple(get_subscriber_series(spec.metric, start=start, end=end) for spec in NEWSLETTERS)


@dataclass(frozen=True)
class CampaignPerformance:
    """One completed issue, with the rates derived at the point of display."""

    campaign_id: int
    name: str
    newsletter: str
    newsletter_label: str
    audience: str
    completed_at: date | None
    delivered: int | None = None
    opened: int | None = None
    unique_clicks: int | None = None
    unsubscribed: int | None = None
    open_rate: float | None = None
    click_rate: float | None = None

    @property
    def is_to_non_members(self) -> bool:
        return self.audience == AUDIENCE_NON_MEMBERS

    @property
    def has_statistics(self) -> bool:
        return self.delivered is not None

    # Formatted here rather than in the template, because Django's
    # `floatformat` has no percentage form: `floatformat:"-1%"` renders the
    # number and a literal `%` sign without multiplying by a hundred, so a
    # 50,9% open rate would have shown as `0,5%`.

    @property
    def delivered_label(self) -> str:
        return group_thousands(self.delivered) if self.delivered is not None else ""

    @property
    def open_rate_label(self) -> str:
        return percent(100 * self.open_rate) if self.open_rate is not None else ""

    @property
    def click_rate_label(self) -> str:
        return percent(100 * self.click_rate) if self.click_rate is not None else ""


def get_campaign_performance(
    *, metric: str | None = None, limit: int = 20
) -> tuple[CampaignPerformance, ...]:
    """Recent completed issues, newest first.

    With `metric`, only that newsletter's issues. Without it, only issues of the
    three newsletters — never the event calendars and one-off letters, which are
    catalogued but are not newsletter performance and would drag an average
    towards a different kind of mailing entirely.
    """
    campaigns = SmailyCampaign.objects.exclude(newsletter="")
    if metric:
        campaigns = campaigns.filter(newsletter=metric)
    campaigns = campaigns.order_by("-completed_at", "-campaign_id")[: max(limit, 0)]

    current = {
        row.campaign_id: row
        for row in SmailyCampaignStats.objects.filter(campaign__in=list(campaigns), is_current=True)
    }

    rows = []
    for campaign in campaigns:
        stats = current.get(campaign.pk)
        registry_spec = spec_for(campaign.newsletter)
        rows.append(
            CampaignPerformance(
                campaign_id=campaign.campaign_id,
                name=campaign.name,
                newsletter=campaign.newsletter,
                newsletter_label=registry_spec.label if registry_spec else "",
                audience=campaign.audience,
                completed_at=campaign.completed_at.date() if campaign.completed_at else None,
                delivered=stats.delivered_count if stats else None,
                opened=stats.opened_count if stats else None,
                unique_clicks=stats.unique_click_count if stats else None,
                unsubscribed=stats.unsubscribe_count if stats else None,
                open_rate=stats.open_rate if stats else None,
                click_rate=stats.click_rate if stats else None,
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class NewsletterAggregate:
    """How one newsletter performs across several issues.

    Rates are summed counts over summed counts, never the mean of per-campaign
    percentages: a send to eight hundred people and one to twenty thousand are
    not two equally weighted observations of the same thing.
    """

    metric: str
    label: str
    campaigns: int = 0
    delivered: int | None = None
    opened: int | None = None
    unique_clicks: int | None = None
    unsubscribed: int | None = None

    @property
    def has_data(self) -> bool:
        return bool(self.campaigns) and self.delivered is not None

    @property
    def open_rate(self) -> float | None:
        """Opens as a share of delivered."""
        if not self.delivered or self.opened is None:
            return None
        return self.opened / self.delivered

    @property
    def click_rate(self) -> float | None:
        """Unique clicks as a share of delivered."""
        if not self.delivered or self.unique_clicks is None:
            return None
        return self.unique_clicks / self.delivered

    @property
    def click_to_open_rate(self) -> float | None:
        """Unique clicks as a share of opens — a different denominator."""
        if not self.opened or self.unique_clicks is None:
            return None
        return self.unique_clicks / self.opened


def get_newsletter_aggregate(metric: str, *, limit: int = 12) -> NewsletterAggregate:
    """One newsletter's totals across its most recent measured issues."""
    registry_spec = spec_for(metric)
    label = registry_spec.label if registry_spec else ""

    campaign_ids = list(
        SmailyCampaign.objects.filter(newsletter=metric)
        .order_by("-completed_at", "-campaign_id")
        .values_list("pk", flat=True)[: max(limit, 0)]
    )
    if not campaign_ids:
        return NewsletterAggregate(metric=metric, label=label)

    measured = SmailyCampaignStats.objects.filter(campaign_id__in=campaign_ids, is_current=True)
    totals = measured.aggregate(
        delivered=Sum("delivered_count"),
        opened=Sum("opened_count"),
        clicks=Sum("unique_click_count"),
        unsubscribed=Sum("unsubscribe_count"),
    )
    # Campaigns that actually have figures, which is not the same as campaigns
    # asked for: a send whose statistics have never been read has none.
    measured_count = measured.count()
    return NewsletterAggregate(
        metric=metric,
        label=label,
        campaigns=measured_count,
        delivered=totals["delivered"],
        opened=totals["opened"],
        unique_clicks=totals["clicks"],
        unsubscribed=totals["unsubscribed"],
    )


def get_all_aggregates(*, limit: int = 12) -> tuple[NewsletterAggregate, ...]:
    return tuple(get_newsletter_aggregate(spec.metric, limit=limit) for spec in NEWSLETTERS)


__all__ = [
    "CampaignPerformance",
    "NewsletterAggregate",
    "SubscriberPoint",
    "SubscriberSeries",
    "get_all_aggregates",
    "get_all_subscriber_series",
    "get_campaign_performance",
    "get_newsletter_aggregate",
    "get_subscriber_series",
]
