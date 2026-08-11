"""Shape the visibility figures for the two pages that show them.

The selectors answer "what is stored". This module answers "what does the board
see", once, so the overview band and the Nähtavus page cannot end up describing
the same number differently. The templates lay out; neither holds a rule.

Three things are decided here and nowhere else:

- **the website slot stays planned until data exists.** It becomes a real card
  only when a collected reporting day actually exists — configuration for the
  `sync_ga4` collector alone is not data, so an unconfigured or not-yet-run
  deployment carries no value, links nowhere and says why;
- **the newsletter slot lists each newsletter and never totals them.** The
  Chamber sends three, to three lists, and nobody has counted how many people
  are on more than one. A sum would silently claim that overlap is zero, so the
  card shows the lists and no headline figure at all;
- **neither kind ever reads as the other.** The website and newsletter cards are
  collected and say `Automaatselt kogutud`; the four social cards are typed and
  never say synchronised, connected or automatically updated. Every populated
  card carries its observation date whichever it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.utils import timezone

from apps.core.formatting import short_date, signed_integer
from apps.dashboard.sparkline import Sparkline, build_sparkline

from .ga4 import Ga4ConnectionStatus, get_connection_status
from .models import CollectionMethod, VisibilityMetric
from .newsletter_page import NewsletterSection, build_newsletter_section
from .registry import SOCIAL_METRICS, VisibilityMetricSpec
from .selectors import (
    MetricReading,
    NewsletterSummary,
    VisibilitySummary,
    WebsiteTraffic,
    get_visibility_series,
    get_visibility_summary,
    get_website_traffic,
)
from .traffic_page import TrafficSection, build_traffic_section

WEBSITE_LABEL = "Kodulehe külastused"
NEWSLETTER_LABEL = "Uudiskirjad"

#: Appended to every outbound profile link for screen-reader users, because the
#: link text alone does not say the destination is outside DashKoda.
EXTERNAL_LINK_NOTE = "väline leht, avaneb uuel vahelehel"


@dataclass(frozen=True)
class ChannelDetail:
    """One named figure inside a slot that has several of equal standing."""

    label: str
    value: int | str


@dataclass(frozen=True)
class ChannelSlot:
    """One cell of the overview's channel band.

    Uniform on purpose: six slots that look alike but mean different things would
    be worse than six that look alike and *say* what they are. Every field a card
    can render is on this object, and `is_planned` is what separates "nothing
    collects this" from "nothing has been entered yet".

    `source_label` and `method_label` are the exception: the card footer was
    trimmed to the observation date alone, so the band no longer prints either.
    A card still says a person typed the figure — that is `state_label`, in the
    card's own header — and the Nähtavus page still names every source in full.
    """

    label: str
    value: int | None = None
    unit: str = ""
    secondary: str = ""
    #: Several figures of equal standing, shown instead of a single `value`.
    details: tuple[ChannelDetail, ...] = ()
    as_of: date | None = None
    source_label: str = ""
    method_label: str = ""
    state_label: str = ""
    state_variant: str = "neutral"
    profile_url: str = ""
    detail_url: str = ""
    is_planned: bool = False
    #: Only meaningful for a planned slot, and never a promise about when.
    promise: str = ""
    empty_message: str = "Andmed puuduvad."

    @property
    def has_value(self) -> bool:
        # `is not None`, not truthiness: an entered zero is a real reading.
        return self.value is not None

    @property
    def has_profile_link(self) -> bool:
        return bool(self.profile_url)

    @property
    def external_link_note(self) -> str:
        return EXTERNAL_LINK_NOTE


def _website_slot(status: Ga4ConnectionStatus, traffic: WebsiteTraffic) -> ChannelSlot:
    """The website slot: planned until a reading exists, then the reading.

    The planned branch is what shows before anything has been collected, and it
    is not a placeholder for a number — it is the honest statement that nothing
    has measured this yet. It stayed on the page after collection began, because
    this returned it unconditionally while the docstring claimed otherwise: the
    traffic was collected, stored and audited, and the card went on saying the
    source was not connected.

    It deliberately links nowhere either way. A link to Google Analytics would
    send a board member to a login screen.
    """
    if not (status.is_connected and traffic.has_data):
        return ChannelSlot(
            label=WEBSITE_LABEL,
            is_planned=True,
            state_label="Lisamisel",
            state_variant="neutral",
            promise=f"{status.message} {status.detail}".strip(),
        )

    # Sessions, because the card is labelled `Kodulehe külastused` — visits, not
    # people. Users and page views are a different question and are kept for the
    # Nähtavus page rather than crowded into one cell.
    secondary = ""
    if traffic.change is not None:
        secondary = (
            f"{signed_integer(traffic.change)} võrreldes {short_date(traffic.previous_period_end)}"
        )
    return ChannelSlot(
        label=WEBSITE_LABEL,
        value=traffic.sessions,
        unit="seanssi",
        secondary=secondary,
        as_of=traffic.period_end,
        # One of the two automated figures on this band, the other being the
        # newsletters. Saying it was typed would be false in the opposite
        # direction from the four social cards.
        state_label=CollectionMethod.AUTOMATIC.label,
        state_variant="neutral",
    )


def _newsletter_slot(summary: NewsletterSummary, *, detail_url: str) -> ChannelSlot:
    """Each newsletter under its own name, with no total across them.

    The three lists are separate audiences. Adding them would count a reader
    subscribed to two of them twice, and the number nobody has — how many
    people appear on more than one list — is exactly what a total would have to
    assume was zero. So the slot has no `value`: it lists what was counted.

    A list nobody has entered contributes no row, rather than a zero that would
    read as "this newsletter has no subscribers".
    """
    first = summary.lists[0]
    if not summary.has_any_data:
        return ChannelSlot(
            label=NEWSLETTER_LABEL,
            state_label=first.state_label,
            state_variant=first.state_variant,
            source_label=first.source_label,
            detail_url=detail_url,
        )

    entered = summary.entered
    missing = tuple(reading.label for reading in summary.lists if not reading.has_data)
    secondary = ""
    if missing:
        secondary = f"Sisestamata: {', '.join(missing)}."

    return ChannelSlot(
        label=NEWSLETTER_LABEL,
        details=tuple(
            ChannelDetail(label=reading.label, value=reading.value) for reading in entered
        ),
        secondary=secondary,
        as_of=summary.as_of,
        source_label=first.source_label,
        method_label=first.method_label,
        # Collected, not typed. Saying a person entered these would be false in
        # the same way saying the social figures were synchronised would be.
        state_label=("Vajab uuendamist" if summary.is_stale else CollectionMethod.AUTOMATIC.label),
        state_variant="warning" if summary.is_stale else "neutral",
        detail_url=detail_url,
    )


def _social_slot(reading: MetricReading, *, detail_url: str) -> ChannelSlot:
    secondary = ""
    if reading.has_data and reading.change is not None:
        secondary = f"{reading.change_label} {reading.comparison_period}"
    return ChannelSlot(
        label=reading.label,
        value=reading.value,
        unit=reading.unit if reading.has_data else "",
        secondary=secondary,
        as_of=reading.as_of,
        source_label=reading.source_label,
        method_label=reading.method_label,
        state_label=reading.state_label,
        state_variant=reading.state_variant,
        profile_url=reading.profile_url,
        detail_url=detail_url,
    )


def build_channel_band(
    *,
    summary: VisibilitySummary | None = None,
    ga4_status: Ga4ConnectionStatus | None = None,
    detail_url: str = "",
) -> tuple[ChannelSlot, ...]:
    """The six channel slots, in the order the board reads them.

    Website first because it is the widest audience and the one that is missing;
    then the newsletter, which the Chamber owns outright; then the four social
    channels in the order the registry fixes.
    """
    summary = summary if summary is not None else get_visibility_summary()
    ga4_status = ga4_status if ga4_status is not None else get_connection_status()
    traffic = get_website_traffic()
    return (
        _website_slot(ga4_status, traffic),
        _newsletter_slot(summary.newsletter, detail_url=detail_url),
        *(_social_slot(reading, detail_url=detail_url) for reading in summary.social),
    )


@dataclass(frozen=True)
class ChannelTrend:
    """One channel's history, drawn and tabulated.

    The sparkline is optional and the table is not. A series of fewer than two
    points is not a trend, so nothing is drawn — but the values themselves stay
    in the document either way, which is what makes the figure readable without
    the drawing.
    """

    spec: VisibilityMetricSpec
    reading: MetricReading
    series: tuple[tuple[date, int], ...]
    sparkline: Sparkline | None

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def has_series(self) -> bool:
        return bool(self.series)

    @property
    def point_count(self) -> int:
        return len(self.series)


@dataclass(frozen=True)
class VisibilityPage:
    """Everything the Nähtavus template renders."""

    summary: VisibilitySummary
    newsletter: NewsletterSummary
    social: tuple[MetricReading, ...]
    trends: tuple[ChannelTrend, ...]
    channels: tuple[ChannelSlot, ...]
    ga4: Ga4ConnectionStatus
    traffic: TrafficSection
    newsletters: NewsletterSection
    today: date

    @property
    def has_any_data(self) -> bool:
        return self.summary.has_any_data

    @property
    def drawn_trends(self) -> tuple[ChannelTrend, ...]:
        return tuple(trend for trend in self.trends if trend.has_series)


def _trend(reading: MetricReading) -> ChannelTrend:
    """One channel's whole history, drawn if there is enough of it.

    Deliberately unwindowed. A fixed window would hide the first reading of a
    metric someone entered a year ago, which on a hand-kept series is exactly the
    point worth seeing — and the series is a handful of rows by construction, so
    there is nothing to bound.
    """
    series = get_visibility_series(reading.spec.key)
    return ChannelTrend(
        spec=reading.spec,
        reading=reading,
        series=series,
        # Returns `None` below two points: a single dot on an axis is not a trend.
        sparkline=build_sparkline(series),
    )


def build_visibility_page(
    *,
    detail_url: str = "",
    today: date | None = None,
    period_key: str | None = None,
    section_key: str | None = None,
    newsletter_key: str | None = None,
    search: str | None = None,
    page: str | int | None = None,
) -> VisibilityPage:
    """Read every metric once and shape it for the page."""
    today = today or timezone.localdate()
    summary = get_visibility_summary(today=today)
    ga4_status = get_connection_status()
    trends = tuple(_trend(reading) for reading in summary.social)

    return VisibilityPage(
        summary=summary,
        newsletter=summary.newsletter,
        social=summary.social,
        trends=trends,
        channels=build_channel_band(summary=summary, ga4_status=ga4_status, detail_url=detail_url),
        ga4=ga4_status,
        traffic=build_traffic_section(
            period_key=period_key,
            section_key=section_key,
            search=search,
            page=page,
            today=today,
        ),
        newsletters=build_newsletter_section(newsletter_key=newsletter_key),
        today=today,
    )


__all__ = [
    "EXTERNAL_LINK_NOTE",
    "NEWSLETTER_LABEL",
    "SOCIAL_METRICS",
    "WEBSITE_LABEL",
    "ChannelSlot",
    "ChannelTrend",
    "VisibilityMetric",
    "VisibilityPage",
    "build_channel_band",
    "build_visibility_page",
]
