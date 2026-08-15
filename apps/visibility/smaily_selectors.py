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
from .smaily_campaigns import AUDIENCE_NON_MEMBERS, OTHER_KEY, label_for
from .smaily_segments import NEWSLETTERS, NEWSLETTERS_BY_METRIC, NewsletterSpec

#: How long a subject search term may be. Bounded so a hand-typed query cannot
#: become an unbounded scan.
MAX_SEARCH_LENGTH = 80


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
    #: Already validated at import; empty when the template was deleted.
    preview_url: str = ""

    @property
    def has_preview(self) -> bool:
        return bool(self.preview_url)

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


def campaign_queryset(*, metric: str | None = None, search: str = ""):
    """Completed sends, newest first, optionally narrowed.

    **Every completed campaign is in here**, whether or not it was recognised as
    an issue of one of the three newsletters. This used to `exclude(newsletter="")`
    and that was wrong: it hid 2 105 of this account's 3 194 sends — every event
    calendar, invitation and one-off letter the Chamber has posted since 2012 —
    behind a classifier that was only ever meant to *label* them.

    `metric` narrows to one newsletter; `OTHER_KEY` narrows to the sends that
    match none of them. `search` matches the stored subject, and never reaches
    Smaily: this runs against PostgreSQL like every other page query.
    """
    campaigns = SmailyCampaign.objects.all()
    if metric == OTHER_KEY:
        campaigns = campaigns.filter(newsletter="")
    elif metric:
        campaigns = campaigns.filter(newsletter=metric)
    term = (search or "").strip()[:MAX_SEARCH_LENGTH]
    if term:
        # `icontains` on the stored name. Bounded above, and a parameterised
        # query — the term never becomes SQL.
        campaigns = campaigns.filter(name__icontains=term)
    return campaigns.order_by("-completed_at", "-campaign_id")


def has_unclassified_campaigns() -> bool:
    """Whether the `Muu` filter has anything behind it.

    The chip is offered only when it leads somewhere. A filter that always
    returns nothing teaches a reader that the section is broken.
    """
    return SmailyCampaign.objects.filter(newsletter="").exists()


def describe_campaigns(campaigns) -> tuple[CampaignPerformance, ...]:
    """Attach the current statistics to an already-narrowed set of campaigns.

    One extra query for the whole page rather than one per row.
    """
    campaigns = list(campaigns)
    current = {
        row.campaign_id: row
        for row in SmailyCampaignStats.objects.filter(campaign__in=campaigns, is_current=True)
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
                newsletter_label=(
                    registry_spec.label if registry_spec else label_for(campaign.newsletter)
                ),
                audience=campaign.audience,
                completed_at=campaign.completed_at.date() if campaign.completed_at else None,
                preview_url=campaign.preview_url,
                delivered=stats.delivered_count if stats else None,
                opened=stats.opened_count if stats else None,
                unique_clicks=stats.unique_click_count if stats else None,
                unsubscribed=stats.unsubscribe_count if stats else None,
                open_rate=stats.open_rate if stats else None,
                click_rate=stats.click_rate if stats else None,
            )
        )
    return tuple(rows)


def get_campaign_performance(
    *, metric: str | None = None, limit: int = 20, search: str = ""
) -> tuple[CampaignPerformance, ...]:
    """The most recent completed sends, described for the page."""
    return describe_campaigns(campaign_queryset(metric=metric, search=search)[: max(limit, 0)])


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


#: How many recent sends an aggregate summarises unless a caller says
#: otherwise. Named once here, the selector's own default: the Uudiskirjad
#: page and the executive overview both quote a rate over this many issues,
#: and two literals could drift into two different rates for the same letter.
DEFAULT_AGGREGATE_ISSUES = 12


def get_newsletter_aggregate(
    metric: str, *, limit: int = DEFAULT_AGGREGATE_ISSUES, offset: int = 0
) -> NewsletterAggregate:
    """One newsletter's totals across a slice of its most recent issues.

    `offset` steps back through the send history in blocks of `limit`, which is
    what lets a caller put the last twelve issues beside the twelve before them.
    It defaults to zero, so every existing caller asks the same question it
    always did: the most recent `limit` issues.

    The slice is taken over **sends**, not over a date range, because newsletter
    cadence is irregular — e-Teataja goes out as two campaigns per issue and the
    Russian-language letter goes months between sends — and equal spans would
    compare four issues against nineteen.
    """
    registry_spec = spec_for(metric)
    label = registry_spec.label if registry_spec else ""

    window = max(limit, 0)
    start = max(offset, 0)
    campaign_ids = list(
        SmailyCampaign.objects.filter(newsletter=metric)
        .order_by("-completed_at", "-campaign_id")
        .values_list("pk", flat=True)[start : start + window]
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


def get_all_aggregates(*, limit: int = DEFAULT_AGGREGATE_ISSUES) -> tuple[NewsletterAggregate, ...]:
    return tuple(get_newsletter_aggregate(spec.metric, limit=limit) for spec in NEWSLETTERS)


__all__ = [
    "MAX_SEARCH_LENGTH",
    "CampaignPerformance",
    "campaign_queryset",
    "describe_campaigns",
    "has_unclassified_campaigns",
    "NewsletterAggregate",
    "SubscriberPoint",
    "SubscriberSeries",
    "get_all_aggregates",
    "get_all_subscriber_series",
    "get_campaign_performance",
    "get_newsletter_aggregate",
    "get_subscriber_series",
]
