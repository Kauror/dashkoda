"""What the Koduleht domain tells the main dashboard.

The website half of the overview's `Koduleht ja uudised` card. The news half
comes from `apps.news.executive`; the two are deliberately separate modules
because they are separate sources measuring separate things, and the card shows
them side by side without ever adding them.

The newsletter open rate used to be here as well, because the newsletters had no
dashboard of their own. They have one now — `/otsepostitused/` — and the figure
moved whole to `apps.visibility.mailings_executive`, which owns the
Otsepostitused card. Nothing about the rate changed: same selector, same block
of sends, same weighting. What changed is that a Koduleht summary no longer
carries a figure about something other than the website, and this module no
longer runs a Smaily query for a card it does not build.

## The window is anchored to measurement, not to the reader's calendar

`website_period.parse_period(None, coverage)` resolves the default thirty days
against GA4 coverage, ending on the newest measured day. Anchoring on today
would quietly include days the collector has not reached — GA4 lags — and the
card would report a fall every morning until the overnight sync landed.

## A comparison is offered only when it means something

`website_period.build_comparison` already knows when two windows are too
differently covered to be subtracted, and this module does not second-guess it.
When `can_compare_site` is false the card shows the current figure with no
delta and the data-status section carries the reason. That is the rule the brief
calls suppressing the business signal in favour of the data warning, and it is
enforced here rather than in the page, because the page has no way to know what
`MIN_COMPARISON_COVERAGE` means.

## Sessions are not people

A session is a visit. Two visits by one person are two sessions, and no figure
here is ever worded as a count of humans. The same applies to page views, which
are smaller still: neither is a unique reader, and the card's wording says
`külastused` and `vaatamised` and never `külastajad`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from django.urls import reverse

from apps.core.executive import DomainSignal, SignalDirection, SignalPriority, SignalTone
from apps.core.formatting import integer, percent

from .ga4_selectors import get_coverage
from .website_analytics import WebsiteTrafficSummary, get_traffic_summary
from .website_period import build_comparison, get_period_coverage, parse_period

#: How far sessions must move against the preceding window before the domain
#: calls it worth a manager's attention. Ordinary week-to-week variation on this
#: property sits well inside it; a fortnight of campaign traffic does not.
SESSION_CHANGE_PCT = 15.0


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
        """The sessions sentence, and only sessions.

        The engagement clause it used to end with was the same figure the
        `Kaasatuse määr` fact states one row below, and the board struck the
        repeat. One sentence, one measure.
        """
        if not self.has_headline:
            return ""
        change = self.change_pct
        if change is None:
            return f"Külastusi oli {integer(self.sessions)} viimasel mõõdetud perioodil."
        if change == 0:
            return "Külastused püsisid eelmise võrdlusperioodiga samal tasemel."
        word = "kasvasid" if change > 0 else "langesid"
        return f"Külastused {word} {percent(abs(change))} võrreldes eelmise sama pika perioodiga."


def get_website_executive() -> WebsiteExecutive:
    """Read the default measured window once and shape the website figures.

    Four bounded reads: coverage, this window's totals, its coverage and the
    previous window's totals. There was a fifth — the leading ordinary content
    page, for the overview's `Praegu enim huvi` — until that section left the
    front page on 2026-08-18, and it went with the section rather than being
    left as a query nobody renders.
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
    )
    return _with_signals(executive)


def _comparison_note(comparison, *, can_compare: bool) -> str:
    """Why no delta is shown, whenever there is no delta to show.

    `build_comparison` fills `unavailable_reason` only for the cases it can name
    up front — no window at all, the whole history, a previous period reaching
    before collection began. It leaves that field **empty** for the other
    refusal: two windows that both exist but are measured to different
    completeness, which `can_compare_site` rejects on the coverage ratios.

    Without this, that case rendered as a card with no comparison and a data
    status reading `Andmed olemas` — the page silently declining to compare and
    then reporting nothing wrong. The reason a figure is missing is exactly what
    the reader needs, so the unnamed refusal gets named here.
    """
    if can_compare:
        return ""
    if comparison.unavailable_reason:
        return comparison.unavailable_reason
    return "Kahe perioodi mõõdetus erineb liiga palju, et neid võrrelda."


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
            f"Kodulehe külastused {'langesid' if falling else 'kasvasid'} "
            f"{percent(abs(change))} võrreldes eelmise sama pika perioodiga."
        ),
        evidence=(
            f"{integer(executive.sessions)} külastust {executive.days} päeva jooksul, "
            f"eelmisel võrdlusperioodil {integer(executive.previous_sessions)}."
        ),
        # A fall is worth attention; a rise is worth knowing. Neither is a
        # verdict — the page states the movement and the reader explains it.
        priority=SignalPriority.ATTENTION if falling else SignalPriority.NOTABLE,
        direction=SignalDirection.DOWN if falling else SignalDirection.UP,
        tone=SignalTone.NEUTRAL if falling else SignalTone.POSITIVE,
        href=reverse("visibility"),
        as_of=executive.end,
    )
    return replace(executive, signals=(signal,))


__all__ = [
    "SESSION_CHANGE_PCT",
    "WebsiteExecutive",
    "get_website_executive",
]
