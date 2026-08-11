"""The newsletter-analytics section of the Nähtavus page.

The band above answers "how many people can the Chamber reach". This answers the
two questions a card cannot: is that number growing, and does anybody read what
is sent.

Three things it must never do, and each has a specific way of going wrong:

- **imply history that does not exist.** Smaily reports what a list holds now
  and nothing about last year, so the audience chart starts on the day
  collection started and says so. Nothing pads the days before it;
- **average percentages.** A newsletter's open rate here is summed opens over
  summed delivered. Taking the mean of per-issue percentages would weight a send
  to 755 people the same as one to 20 616, and the headline figure would drift
  towards whichever list is smallest;
- **mix the newsletters.** Three separate lists, three separate audiences, and
  a reader on two of them is one person. Nothing here totals across them.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.core.formatting import group_thousands, percent
from apps.dashboard.sparkline import TrendChart, TrendSource, build_trend_chart

from .registry import spec_for
from .smaily_campaigns import AUDIENCE_MEMBERS, AUDIENCE_NON_MEMBERS, OTHER_KEY, OTHER_LABEL
from .smaily_segments import NEWSLETTERS
from .smaily_selectors import (
    CampaignPerformance,
    NewsletterAggregate,
    SubscriberSeries,
    campaign_queryset,
    get_all_subscriber_series,
    get_campaign_performance,
    get_newsletter_aggregate,
    has_unclassified_campaigns,
)

#: The query parameter the newsletter filter carries.
PARAM_NEWSLETTER = "uudiskiri"

#: How many issues the performance table lists. Long enough to show a pattern,
#: short enough that nobody scrolls past the point of interest.
TOP_ISSUES = 15

#: How many recent issues an aggregate rate is computed over. About a quarter of
#: e-Teataja's cadence, which is recent enough to describe how the newsletter
#: performs now rather than how it performed two years ago.
AGGREGATE_ISSUES = 12

#: The filter value meaning "all three".
ALL_NEWSLETTERS = "koik"

_AUDIENCE_LABELS = {
    AUDIENCE_MEMBERS: "Liikmed",
    AUDIENCE_NON_MEMBERS: "Mitteliikmed",
}


def parse_newsletter(raw: str | None) -> str:
    """The newsletter asked for, or all of them. Never raises.

    Validated against a closed set — the three newsletters plus `Muu` — so a
    hand-typed value falls back to "all" rather than reaching a query.
    """
    value = (raw or "").strip()
    if value == OTHER_KEY or any(spec.metric == value for spec in NEWSLETTERS):
        return value
    return ALL_NEWSLETTERS


@dataclass(frozen=True)
class NewsletterOption:
    """One filter button: what it says, where it goes, whether it is active."""

    key: str
    label: str
    is_active: bool

    @property
    def query(self) -> str:
        return f"{PARAM_NEWSLETTER}={self.key}"


def newsletter_options(active: str, *, with_other: bool = False) -> tuple[NewsletterOption, ...]:
    """`Kõik`, the three newsletters, and `Muu` when it leads somewhere.

    `Muu` is offered only when unclassified sends actually exist. A filter that
    always returns nothing teaches a reader that the section is broken; on this
    account it is the largest group there is, so it is nearly always shown.
    """
    options = [
        NewsletterOption(
            key=ALL_NEWSLETTERS,
            label="Kõik",
            is_active=active == ALL_NEWSLETTERS,
        )
    ]
    for spec in NEWSLETTERS:
        registry_spec = spec_for(spec.metric)
        options.append(
            NewsletterOption(
                key=spec.metric,
                label=registry_spec.label if registry_spec else spec.metric,
                is_active=active == spec.metric,
            )
        )
    if with_other:
        options.append(
            NewsletterOption(key=OTHER_KEY, label=OTHER_LABEL, is_active=active == OTHER_KEY)
        )
    return tuple(options)


@dataclass(frozen=True)
class AudienceCard:
    """One newsletter's audience: the figure, its trend and its split."""

    series: SubscriberSeries
    chart: TrendChart | None
    #: `("Liikmed", "8 008")` and so on, only where the list is genuinely split.
    parts: tuple[tuple[str, str], ...] = ()

    @property
    def label(self) -> str:
        return self.series.label

    @property
    def has_data(self) -> bool:
        return self.series.has_points

    @property
    def value(self) -> str:
        latest = self.series.latest
        return group_thousands(latest.subscribers) if latest else ""

    @property
    def as_of(self):
        latest = self.series.latest
        return latest.observed_on if latest else None

    @property
    def change_label(self) -> str:
        """Growth over the collected window, spelled, or empty.

        Empty rather than "0" when there is only one reading: a newsletter read
        once has not failed to grow, it has not been measured twice.
        """
        change = self.series.change
        if change is None:
            return ""
        earliest = self.series.earliest
        sign = "+" if change > 0 else ("−" if change < 0 else "±")
        return f"{sign}{group_thousands(abs(change))} alates {earliest.observed_on:%d.%m.%Y}"


@dataclass(frozen=True)
class PerformanceFigure:
    """One aggregate rate, already spelled, with its denominator named."""

    label: str
    value: str
    note: str = ""

    @property
    def has_value(self) -> bool:
        return bool(self.value)


@dataclass(frozen=True)
class NewsletterSection:
    """Everything the newsletter-analytics section renders."""

    active: str
    options: tuple[NewsletterOption, ...]
    audience: tuple[AudienceCard, ...]
    figures: tuple[PerformanceFigure, ...]
    issues: tuple[CampaignPerformance, ...]
    aggregate: NewsletterAggregate | None
    #: How many completed sends exist in total, for the "see all" link.
    total_sends: int = 0

    @property
    def has_more_sends(self) -> bool:
        return self.total_sends > len(self.issues)

    @property
    def has_audience(self) -> bool:
        return any(card.has_data for card in self.audience)

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    @property
    def has_any_data(self) -> bool:
        return self.has_audience or self.has_issues

    @property
    def is_filtered(self) -> bool:
        return self.active != ALL_NEWSLETTERS

    @property
    def coverage_note(self) -> str:
        """What the audience chart actually covers, always stated.

        Smaily has no historical endpoint, so this is the whole of the
        Chamber's newsletter history and the reader is told where it starts
        rather than left to infer it from where the line begins.
        """
        starts = [
            card.series.earliest.observed_on for card in self.audience if card.series.earliest
        ]
        if not starts:
            return ""
        return (
            f"Smaily andmed alates {min(starts):%d.%m.%Y}. Varasemat ajalugu ei ole "
            "võimalik koguda: Smaily näitab nimekirja praegust suurust, mitte selle ajalugu."
        )


def build_newsletter_section(*, newsletter_key: str | None = None) -> NewsletterSection:
    """Read the stored Smaily history once and shape it for the page."""
    active = parse_newsletter(newsletter_key)
    metric = None if active == ALL_NEWSLETTERS else active

    # The audience cards describe subscriber *lists*, which is a different thing
    # from campaign classification: `Muu` sends went to ad-hoc audiences and
    # there is no list behind them, so filtering to `Muu` narrows the sends and
    # leaves the three lists as they were.
    audience = tuple(
        _audience_card(series)
        for series in get_all_subscriber_series()
        if metric is None or metric == OTHER_KEY or series.metric == metric
    )
    issues = get_campaign_performance(metric=metric, limit=TOP_ISSUES)
    aggregate = (
        get_newsletter_aggregate(metric, limit=AGGREGATE_ISSUES)
        if metric is not None and metric != OTHER_KEY
        else None
    )

    return NewsletterSection(
        active=active,
        options=newsletter_options(active, with_other=has_unclassified_campaigns()),
        audience=audience,
        figures=_figures(aggregate) if aggregate is not None else (),
        issues=issues,
        aggregate=aggregate,
        total_sends=campaign_queryset().count(),
    )


def _audience_card(series: SubscriberSeries) -> AudienceCard:
    return AudienceCard(series=series, chart=_chart(series))


def _chart(series: SubscriberSeries) -> TrendChart | None:
    """One line: how many people are on this list.

    Drawn only from two readings onward. A single point is a figure, and a chart
    with one dot on it reads as a trend that happens to be flat.
    """
    if not series.is_drawable:
        return None
    points = tuple((point.observed_on, point.subscribers) for point in series.points)
    return build_trend_chart(
        [TrendSource(label=series.label, style="solid", source="Smaily", series=points)]
    )


def _figures(aggregate: NewsletterAggregate) -> tuple[PerformanceFigure, ...]:
    """The rates above the table, each naming what it divided by."""
    if not aggregate.has_data:
        return ()

    figures = [
        PerformanceFigure(
            label="Kohale toimetatud",
            value=group_thousands(aggregate.delivered or 0),
            note=f"Kokku {aggregate.campaigns} viimase numbri peale.",
        )
    ]
    if aggregate.open_rate is not None:
        figures.append(
            PerformanceFigure(
                label="Avamismäär",
                value=percent(100 * aggregate.open_rate),
                # Named, because Smaily's own percentage means this and a reader
                # comparing it with another tool's figure needs to know.
                note="Avamised kohale toimetatud kirjadest.",
            )
        )
    if aggregate.click_rate is not None:
        figures.append(
            PerformanceFigure(
                label="Klikimäär",
                value=percent(100 * aggregate.click_rate),
                note="Unikaalsed klikid kohale toimetatud kirjadest.",
            )
        )
    if aggregate.click_to_open_rate is not None:
        figures.append(
            PerformanceFigure(
                label="Klikke avajate seas",
                value=percent(100 * aggregate.click_to_open_rate),
                # A different denominator from the one above it, so it says so.
                note="Unikaalsed klikid avamistest.",
            )
        )
    return tuple(figures)


def audience_label(audience: str) -> str:
    return _AUDIENCE_LABELS.get(audience, "")


__all__ = [
    "AGGREGATE_ISSUES",
    "ALL_NEWSLETTERS",
    "PARAM_NEWSLETTER",
    "TOP_ISSUES",
    "AudienceCard",
    "NewsletterOption",
    "NewsletterSection",
    "PerformanceFigure",
    "audience_label",
    "build_newsletter_section",
    "newsletter_options",
    "parse_newsletter",
]
