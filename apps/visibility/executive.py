"""What the Koduleht domain tells the main dashboard.

The website half of the overview's `Nähtavus ja teavitamine` pillar, plus the
newsletter engagement figure that sits beside it. The news half comes from
`apps.news.executive`; the two are deliberately separate modules because they
are separate sources measuring separate things, and the pillar shows them side
by side without ever adding them.

## The window is anchored to measurement, not to the reader's calendar

`website_period.parse_period(None, coverage)` resolves the default thirty days
against GA4 coverage, ending on the newest measured day. Anchoring on today
would quietly include days the collector has not reached — GA4 lags — and the
pillar would report a fall every morning until the overnight sync landed.

## A comparison is offered only when it means something

`website_period.build_comparison` already knows when two windows are too
differently covered to be subtracted, and this module does not second-guess it.
When `can_compare_site` is false the pillar shows the current figure with no
delta and the data-status section carries the reason. That is the rule the brief
calls suppressing the business signal in favour of the data warning, and it is
enforced here rather than in the page, because the page has no way to know what
`MIN_COMPARISON_COVERAGE` means.

## Sessions are not people

A session is a visit. Two visits by one person are two sessions, and no figure
here is ever worded as a count of humans. The same applies to page views, which
are smaller still: neither is a unique reader, and the pillar's wording says
`seansid` and `vaatamised` and never `külastajad`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from django.urls import reverse

from apps.core.executive import DomainSignal, SignalDirection, SignalPriority
from apps.core.formatting import integer, percent

from .content_performance import ContentPerformanceRow, describe_pages
from .content_sections import (
    SECTION_ALL,
    SECTION_EVENTS,
    SECTION_NEWS,
    all_index_paths,
)
from .ga4_selectors import get_coverage, get_top_pages
from .registry import VisibilityMetric
from .smaily_selectors import DEFAULT_AGGREGATE_ISSUES, get_newsletter_aggregate
from .website_analytics import WebsiteTrafficSummary, get_traffic_summary
from .website_period import build_comparison, get_period_coverage, parse_period

#: How far sessions must move against the preceding window before the domain
#: calls it worth a manager's attention. Ordinary week-to-week variation on this
#: property sits well inside it; a fortnight of campaign traffic does not.
SESSION_CHANGE_PCT = 15.0

#: How many issues the newsletter engagement figure is weighted over — the
#: selector's own default, so this figure and the Uudiskirjad page cannot
#: state different rates for the same letter.
NEWSLETTER_ISSUES = DEFAULT_AGGREGATE_ISSUES

#: How many top pages to read before picking one that is neither news nor an
#: event. Bounded, and generous enough that a week where the news dominates
#: still yields an ordinary content page.
TOP_PAGE_SCAN = 12


@dataclass(frozen=True)
class WebsiteExecutive:
    """The website's figures for one measured window, and what they may claim."""

    sessions: int | None = None
    engagement_rate: float | None = None
    page_views: int | None = None
    #: The preceding equal-length window, only when it may be compared.
    previous_sessions: int | None = None
    can_compare: bool = False
    comparison_note: str = ""
    #: The measured window itself. Both dates are inside GA4 coverage.
    start: date | None = None
    end: date | None = None
    days: int = 0
    #: The most-viewed page that is neither a news article nor an event page.
    top_page: ContentPerformanceRow | None = None
    #: e-Teataja's weighted open rate across recent issues.
    newsletter_open_rate: float | None = None
    newsletter_issues: int = 0

    signals: tuple[DomainSignal, ...] = ()

    @property
    def has_headline(self) -> bool:
        return self.sessions is not None

    @property
    def change_pct(self) -> float | None:
        if not self.can_compare or self.sessions is None or not self.previous_sessions:
            return None
        return (self.sessions - self.previous_sessions) / self.previous_sessions * 100.0

    @property
    def meaning(self) -> str:
        """Sessions and engagement in one sentence, each with its own basis.

        Engagement is stated as a level rather than as a movement: the rate's
        own change is a percentage-point figure the pillar has no room for, and
        two percentages moving in one sentence is how a reader ends up
        subtracting one from the other.
        """
        if not self.has_headline:
            return ""
        change = self.change_pct
        if change is None:
            if self.engagement_rate is None:
                return f"Seansse oli {integer(self.sessions)} viimasel mõõdetud perioodil."
            return (
                f"Seansse oli {integer(self.sessions)}, "
                f"kaasatuse määr {percent(self.engagement_rate * 100)}."
            )
        word = "kasvasid" if change > 0 else "langesid" if change < 0 else "püsisid"
        if change == 0:
            body = "Seansid püsisid eelmise võrdlusperioodiga samal tasemel"
        else:
            body = f"Seansid {word} {percent(abs(change))} võrreldes eelmise sama pika perioodiga"
        if self.engagement_rate is None:
            return f"{body}."
        return f"{body}; kaasatuse määr {percent(self.engagement_rate * 100)}."


def get_website_executive() -> WebsiteExecutive:
    """Read the default measured window once and shape the website figures.

    Five bounded reads: coverage, this window's totals, its coverage, the
    previous window's totals, and the top-page slice. None of them grows with
    the number of pages on the site — `get_top_pages` aggregates and slices in
    PostgreSQL.
    """
    coverage = get_coverage()
    if not coverage.has_data:
        return WebsiteExecutive(signals=())

    period = parse_period(None, coverage)
    if not period.has_window:
        return WebsiteExecutive()

    current = get_traffic_summary(start=period.start, end=period.end)
    current_coverage = get_period_coverage(period.start, period.end)
    comparison = build_comparison(period, coverage, current_coverage)

    previous: WebsiteTrafficSummary | None = None
    if comparison.is_available:
        previous = get_traffic_summary(start=comparison.start, end=comparison.end)

    can_compare = comparison.can_compare_site and previous is not None
    executive = WebsiteExecutive(
        sessions=current.sessions,
        engagement_rate=current.engagement_rate,
        page_views=current.page_views,
        previous_sessions=previous.sessions if previous else None,
        can_compare=can_compare,
        comparison_note=_comparison_note(comparison, can_compare=can_compare),
        start=period.start,
        end=period.end,
        days=period.days,
        top_page=_top_ordinary_page(period.start, period.end),
        newsletter_open_rate=_newsletter_open_rate(),
        newsletter_issues=NEWSLETTER_ISSUES,
    )
    return _with_signals(executive)


def _comparison_note(comparison, *, can_compare: bool) -> str:
    """Why no delta is shown, whenever there is no delta to show.

    `build_comparison` fills `unavailable_reason` only for the cases it can name
    up front — no window at all, the whole history, a previous period reaching
    before collection began. It leaves that field **empty** for the other
    refusal: two windows that both exist but are measured to different
    completeness, which `can_compare_site` rejects on the coverage ratios.

    Without this, that case rendered as a pillar with no comparison and a data
    status reading `Andmed olemas` — the page silently declining to compare and
    then reporting nothing wrong. The reason a figure is missing is exactly what
    the reader needs, so the unnamed refusal gets named here.
    """
    if can_compare:
        return ""
    if comparison.unavailable_reason:
        return comparison.unavailable_reason
    return "Kahe perioodi mõõdetus erineb liiga palju, et neid võrrelda."


def _top_ordinary_page(start: date, end: date) -> ContentPerformanceRow | None:
    """The most-viewed page that is neither a news article nor an event page.

    News and events have their own panels on the overview, and a Koduleht panel
    repeating whichever of them happened to win adds nothing. Section listing
    pages are excluded at the query, because an index collects the traffic of
    everyone passing through it and would top every ranking forever.

    Falls back to the leading page of any kind when nothing else qualifies —
    with its section badge, so a reader can see that the site's most-read page
    that fortnight genuinely was an article. What it never does is invent a name
    from a slug: `describe_pages` resolves titles from DashKoda's own content
    catalogues and leaves the path visible when it cannot.
    """
    totals = get_top_pages(start=start, end=end, limit=TOP_PAGE_SCAN, exclude=all_index_paths())
    if not totals:
        return None
    rows = describe_pages(totals, section=SECTION_ALL)
    for row in rows:
        if not SECTION_NEWS.contains(row.path) and not SECTION_EVENTS.contains(row.path):
            return row
    return rows[0] if rows else None


def _newsletter_open_rate() -> float | None:
    """e-Teataja's open rate across recent issues, weighted by delivery.

    One newsletter, not an average across the three: the three lists are
    different audiences of very different sizes, and a mean of their rates would
    be a number about nothing. e-Teataja is the one with a regular cadence.
    """
    aggregate = get_newsletter_aggregate(
        VisibilityMetric.NEWSLETTER_ETEATAJA, limit=NEWSLETTER_ISSUES
    )
    return aggregate.open_rate if aggregate.has_data else None


def _with_signals(executive: WebsiteExecutive) -> WebsiteExecutive:
    """At most one: a session movement large enough to be worth explaining.

    Only when the comparison is allowed. A fall computed across two differently
    covered windows is the collector's gaps rather than the Chamber's audience,
    and the domain refuses to state it as a business fact — which is exactly the
    case where the data-status section carries `comparison_note` instead.
    """
    change = executive.change_pct
    if change is None or abs(change) < SESSION_CHANGE_PCT:
        return executive

    falling = change < 0
    signal = DomainSignal(
        key="website-sessions",
        headline=(
            f"Kodulehe seansid {'langesid' if falling else 'kasvasid'} "
            f"{percent(abs(change))} võrreldes eelmise sama pika perioodiga."
        ),
        evidence=(
            f"{integer(executive.sessions)} seanssi {executive.days} päeva jooksul, "
            f"eelmisel võrdlusperioodil {integer(executive.previous_sessions)}."
        ),
        # A fall is worth attention; a rise is worth knowing. Neither is a
        # verdict — the page states the movement and the reader explains it.
        priority=SignalPriority.ATTENTION if falling else SignalPriority.NOTABLE,
        direction=SignalDirection.DOWN if falling else SignalDirection.UP,
        href=reverse("visibility"),
        as_of=executive.end,
    )
    return replace(executive, signals=(signal,))


__all__ = [
    "NEWSLETTER_ISSUES",
    "SESSION_CHANGE_PCT",
    "WebsiteExecutive",
    "get_website_executive",
]
