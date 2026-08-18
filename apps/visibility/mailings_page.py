"""What the Otsepostitused page says.

`Otsepostitused` is the Chamber's newsletter intelligence: how each list
performs, how one newsletter's month-by-month open rate has moved, which sends
did best, and — on the same screen now — every campaign ever sent.

## Where this came from

The material was the fifth focus of `/uudised/`, and its composition lived in
`apps/news/page.py` while the presenters and every Smaily query lived here. It
is one section now, at `/otsepostitused/` under Koduleht, and the composition
has come to sit beside the data it composes. `smaily_selectors` answers the
questions it always answered, plus the date-windowed ones this round added —
see `apps/visibility/mailings_period.py`.

## One period, since 2026-08-18

The mockup this round rebuilt the page to shows one `Periood` picker governing
the whole page, the same interface merge `apps/news/page.py` made for
`/uudised/` the same day, for the same reason: a hidden second window the
reader never asked for is worse than one range applied consistently. Before
this the comparison table read "the twelve most recent sends" — a **count**
window, chosen because newsletter cadence is irregular and an equal date span
compares e-Teataja's weekly issues against e-Vestnik's occasional ones unfairly.
That reasoning has not stopped being true; it has stopped being what this page
shows by default. `get_newsletter_aggregate` (the count-window function) is
still here, unused by this module, for whichever future view still needs the
fairer footing.

## A newsletter is always selected now

The landing state used to show only the three-row comparison and nothing that
needed one newsletter picked. The mockup shows the fuller page — the chart, the
rankings — without a click first, so the page now defaults to the first
newsletter in the registry (e-Teataja, the largest list) rather than to
nothing. Picking eNews or e-Vestnik still switches every one-newsletter section
to it.

## Two things this must never do

- **average percentages.** A rate is summed opens over summed delivered. Taking
  the mean of per-issue percentages would weight a send to 755 people the same
  as one to 20 616, and the headline would drift towards the smallest list;
- **total the newsletters.** Three lists, three audiences, and a reader on two
  of them is one person. Nothing here adds them up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from django.utils import timezone

from apps.core.change import share_percent
from apps.core.formatting import integer

from . import smaily_charts
from .mailings_period import ResolvedMailingsPeriod, resolve_period
from .smaily_segments import NEWSLETTERS
from .smaily_selectors import (
    count_sends_between,
    get_monthly_open_rate,
    get_newsletter_aggregate_between,
    get_subscriber_series,
)

#: The newsletter shown when none is chosen. The first (and largest) list in
#: the registry, matching the mockup's default e-Teataja chart.
DEFAULT_NEWSLETTER = NEWSLETTERS[0].metric

#: How many recent sends the rate history draws, when a caller still wants the
#: per-send chart rather than the monthly one this round added.
SENDS_LIMIT = 24

#: How many sends a ranking lists.
RANKING_LIMIT = 5

#: How deep the ranking looks before sorting. A bound rather than the whole
#: history: fourteen years of e-Teataja is thousands of rows, and the strongest
#: five sends of the selected period is the question anybody is actually
#: asking.
RANKING_DEPTH = 200

#: The click-rate benchmark's own window, independent of the page's period
#: picker. A twelve-month trailing average is what "vs keskmine" compares
#: every send against — including a send from three years ago, if `Kõik` is
#: selected — so the benchmark cannot itself be the selected window without
#: becoming circular for anyone comparing a send against the very average it
#: is part of.
BENCHMARK_WINDOW_DAYS = 365


@dataclass(frozen=True)
class NewsletterRow:
    """One newsletter's own line in the comparison. Never added to another's."""

    metric: str
    label: str
    subscribers: str = ""
    open_rate: str = ""
    click_rate: str = ""
    sends_per_year: str = ""


@dataclass(frozen=True)
class MailingsPage:
    """Everything the Otsepostitused overview renders."""

    period: ResolvedMailingsPeriod
    #: One line per newsletter, never summed — see this module's own docstring
    #: for why no field on this page adds the three lists together.
    comparison: tuple[NewsletterRow, ...] = field(default_factory=tuple)
    chart: object | None = None
    rankings: dict = field(default_factory=dict)
    selected_newsletter: str = ""
    selected_label: str = ""
    #: Each of the three newsletters' own trailing 12-month click rate, keyed
    #: by metric — what the archive's `vs keskmine` column reads.
    click_benchmarks: dict[str, float] = field(default_factory=dict)

    @property
    def has_selection(self) -> bool:
        return bool(self.selected_newsletter)

    @property
    def has_rankings(self) -> bool:
        return bool(self.rankings.get("by_open") or self.rankings.get("by_click"))

    @property
    def selected_benchmark(self) -> float | None:
        return self.click_benchmarks.get(self.selected_newsletter)

    @property
    def selected_benchmark_label(self) -> str:
        benchmark = self.selected_benchmark
        return share_percent(benchmark) if benchmark is not None else ""


def _newsletter_row(
    spec, *, period: ResolvedMailingsPeriod, sends_since: date, today: date
) -> NewsletterRow:
    aggregate = get_newsletter_aggregate_between(spec.metric, start=period.start, end=period.end)
    series = get_subscriber_series(spec.metric)
    sends_per_year = count_sends_between(
        start=sends_since, end=today + timedelta(days=1), metric=spec.metric
    )
    return NewsletterRow(
        metric=spec.metric,
        label=aggregate.label or spec.label,
        subscribers=integer(series.latest.subscribers) if series.latest else "",
        open_rate=share_percent(aggregate.open_rate) if aggregate.has_data else "",
        click_rate=share_percent(aggregate.click_rate) if aggregate.has_data else "",
        sends_per_year=integer(sends_per_year),
    )


def click_benchmarks_for(*, today: date) -> dict[str, float]:
    """Every newsletter's own trailing 12-month click rate, where it exists."""
    since = today - timedelta(days=BENCHMARK_WINDOW_DAYS - 1)
    benchmarks = {}
    for spec in NEWSLETTERS:
        aggregate = get_newsletter_aggregate_between(spec.metric, start=since, end=today)
        if aggregate.has_data and aggregate.click_rate is not None:
            benchmarks[spec.metric] = aggregate.click_rate
    return benchmarks


def _rankings(metric: str, *, period: ResolvedMailingsPeriod, limit: int = RANKING_LIMIT) -> dict:
    """The strongest sends by rate, with the audience size beside each.

    A rate without its denominator misleads: 62% of 30 recipients and 41% of
    20 000 are not the same achievement, and only one of them is a newsletter.
    """
    from .smaily_selectors import campaign_queryset, describe_campaigns

    start, end = period.bounds()
    campaigns = campaign_queryset(metric=metric, start=start, end=end)[:RANKING_DEPTH]
    measured = [send for send in describe_campaigns(campaigns) if send.delivered]
    by_open = sorted(
        (send for send in measured if send.open_rate is not None),
        key=lambda send: send.open_rate,
        reverse=True,
    )[:limit]
    by_click = sorted(
        (send for send in measured if send.click_rate is not None),
        key=lambda send: send.click_rate,
        reverse=True,
    )[:limit]
    return {"by_open": tuple(by_open), "by_click": tuple(by_click)}


def build_mailings_page(
    *,
    newsletter_key: str = "",
    period_key: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    today: date | None = None,
) -> MailingsPage:
    """The overview, over the one period the page's header picks.

    `newsletter_key` empty means the default newsletter, not "none" — see this
    module's own docstring for why the landing state changed on 2026-08-18.
    """
    today = today or timezone.localdate()
    period = resolve_period(period_key, date_from, date_to, today=today)
    selected = newsletter_key or DEFAULT_NEWSLETTER
    sends_since = today - timedelta(days=BENCHMARK_WINDOW_DAYS - 1)

    comparison = tuple(
        _newsletter_row(spec, period=period, sends_since=sends_since, today=today)
        for spec in NEWSLETTERS
    )

    monthly = get_monthly_open_rate(selected, start=period.start, end=period.end)
    selected_spec = next((spec for spec in NEWSLETTERS if spec.metric == selected), None)
    selected_label = selected_spec.label if selected_spec else ""

    return MailingsPage(
        period=period,
        comparison=comparison,
        chart=smaily_charts.monthly_open_rate(monthly, newsletter_label=selected_label)
        if monthly
        else None,
        rankings=_rankings(selected, period=period),
        selected_newsletter=selected,
        selected_label=selected_label,
        click_benchmarks=click_benchmarks_for(today=today),
    )


__all__ = [
    "BENCHMARK_WINDOW_DAYS",
    "DEFAULT_NEWSLETTER",
    "RANKING_LIMIT",
    "SENDS_LIMIT",
    "MailingsPage",
    "NewsletterRow",
    "build_mailings_page",
    "click_benchmarks_for",
]
