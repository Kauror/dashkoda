"""What the Uudised domain tells the main dashboard.

The news half of the overview's `Koduleht ja uudised` card, and the news
panel of `Praegu enim huvi`. The website half lives in
`apps.visibility.executive`; the card shows both and never adds them.

## Why news views are not added to website views

They are a **subset**. `analytics.news_traffic` reads both figures as GA4 page
views over the same days precisely so that the one can be stated as a share of
the other: news reading is part of site reading, and a "total reach" summing
them would count every article view twice. The card therefore carries a share,
never a sum, and the share is computed by this domain rather than by the page.

## The leading article left with the section that showed it

This summary carried the most-read article of the window for the overview's
`Praegu enim huvi` panel. That section left the front page on 2026-08-18 —
which single article happened to lead is a browsing question, and `/uudised/`
ranks them properly — so the field and its query went with it rather than being
left built and unread. `analytics.most_read` is untouched and the Uudised page
still calls it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from django.urls import reverse

from apps.core.executive import DomainSignal, SignalDirection, SignalPriority, SignalTone
from apps.core.formatting import integer, percent
from apps.visibility.ga4_selectors import get_coverage
from apps.visibility.website_period import parse_period

from .analytics import (
    NewsTrafficSummary,
    news_traffic,
    previous_traffic_within,
    published_between,
)
from .selectors import NewsSummary

#: How far news reading must move against the preceding equal window before the
#: domain states it as a signal. Higher than the website's threshold: news
#: traffic is far spikier than site traffic — one widely shared article moves it
#: — and a lower bar would fire most weeks.
NEWS_CHANGE_PCT = 25.0


@dataclass(frozen=True)
class NewsExecutive:
    """News reading and publishing over the same window the website uses."""

    news_views: int | None = None
    previous_news_views: int | None = None
    #: News views as a share of all site page views in the same window.
    site_share: float | None = None
    articles_read: int = 0
    published: int | None = None
    start: date | None = None
    end: date | None = None

    signals: tuple[DomainSignal, ...] = ()

    @property
    def has_headline(self) -> bool:
        return self.news_views is not None

    @property
    def change_pct(self) -> float | None:
        if self.news_views is None or not self.previous_news_views:
            return None
        return (self.news_views - self.previous_news_views) / self.previous_news_views * 100.0

    @property
    def meaning(self) -> str:
        """Reading volume and its share of the site, in one sentence."""
        if not self.has_headline:
            return ""
        if self.site_share is None:
            return f"Uudiseid vaadati {integer(self.news_views)} korda."
        return (
            f"Uudiseid vaadati {integer(self.news_views)} korda, "
            f"{percent(self.site_share * 100)} kogu kodulehe vaatamistest."
        )


def get_news_executive(summary: NewsSummary) -> NewsExecutive:
    """Shape the news figures over the website's own measured window.

    The window comes from `apps.visibility` rather than from this domain's own
    period presets, because the card puts news reading beside site reading and
    two different thirty-day windows in one sentence would not be comparable.
    """
    coverage = get_coverage()
    if not coverage.has_data:
        return NewsExecutive()

    period = parse_period(None, coverage)
    if not period.has_window:
        return NewsExecutive()

    current = news_traffic(start=period.start, end=period.end)
    # The same refusal the news page applies: a previous window reaching before
    # collection began yields no comparison rather than a partial denominator.
    previous = previous_traffic_within(period.start, period.end, coverage)

    executive = NewsExecutive(
        news_views=current.news_views,
        previous_news_views=previous.news_views,
        site_share=current.share,
        articles_read=current.articles_read,
        published=_published(summary, period.start, period.end),
        start=period.start,
        end=period.end,
    )
    return _with_signals(executive, current)


def _published(summary: NewsSummary, start: date, end: date) -> int | None:
    """Articles published inside the window, or `None` with no catalogue.

    `None` rather than `0`: an unconnected news feed has not observed a quiet
    fortnight, it has observed nothing.
    """
    if not summary.has_data:
        return None
    return published_between(start, end).total


def _with_signals(executive: NewsExecutive, current: NewsTrafficSummary) -> NewsExecutive:
    """At most one: news reading that moved materially against the window before.

    The coverage guard sits in `previous_traffic_within`: when the previous
    window reaches before collection began, `previous_news_views` is `None`,
    `change_pct` is `None`, and no signal can state a comparison the news page
    itself would refuse.
    """
    change = executive.change_pct
    if change is None or abs(change) < NEWS_CHANGE_PCT:
        return executive

    falling = change < 0
    signal = DomainSignal(
        key="news-views",
        headline=(
            f"Uudiste vaatamised {'langesid' if falling else 'kasvasid'} "
            f"{percent(abs(change))} võrreldes eelmise sama pika perioodiga."
        ),
        evidence=(
            f"{integer(executive.news_views)} vaatamist, "
            f"eelmisel perioodil {integer(executive.previous_news_views)}. "
            f"Loetud artikleid {integer(current.articles_read)}."
        ),
        priority=SignalPriority.ATTENTION if falling else SignalPriority.NOTABLE,
        direction=SignalDirection.DOWN if falling else SignalDirection.UP,
        tone=SignalTone.NEUTRAL if falling else SignalTone.POSITIVE,
        href=reverse("news"),
        as_of=executive.end,
    )
    return replace(executive, signals=(signal,))


__all__ = [
    "NEWS_CHANGE_PCT",
    "NewsExecutive",
    "get_news_executive",
]
