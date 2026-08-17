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

## The article panel is about now, not about lifetime

`analytics.most_read` ranks by views **inside the measurement window** and does
not filter by publication date. An article from two years ago that is being read
this month legitimately leads the panel — on this property roughly a quarter of
current news reading goes to articles over a year old. The publication date is
shown separately so a reader can see that is what happened, which is a different
statement from "this is the newest article".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from django.urls import reverse

from apps.core.executive import DomainSignal, SignalDirection, SignalPriority
from apps.core.formatting import integer, percent
from apps.visibility.ga4_selectors import get_coverage
from apps.visibility.website_period import parse_period

from .analytics import (
    WINDOW_ANNOTATION,
    NewsTrafficSummary,
    most_read,
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
    #: The most-read article inside the window, whenever it was published.
    top_article: object = None
    top_article_views: int | None = None

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
    leader = _leader(period.start, period.end)

    executive = NewsExecutive(
        news_views=current.news_views,
        previous_news_views=previous.news_views,
        site_share=current.share,
        articles_read=current.articles_read,
        published=_published(summary, period.start, period.end),
        start=period.start,
        end=period.end,
        top_article=leader,
        top_article_views=_window_views(leader),
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


def _leader(start: date, end: date):
    """The single most-read article in the window, or `None`."""
    rows = list(most_read(start=start, end=end, limit=1))
    return rows[0] if rows else None


def _window_views(article) -> int | None:
    """The annotated window view count `most_read` attached, if any."""
    if article is None:
        return None
    return getattr(article, WINDOW_ANNOTATION, None)


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
        href=reverse("news"),
        as_of=executive.end,
    )
    return replace(executive, signals=(signal,))


__all__ = [
    "NEWS_CHANGE_PCT",
    "NewsExecutive",
    "get_news_executive",
]
