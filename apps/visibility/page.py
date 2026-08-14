"""Shape the visibility figures for the pages that show them.

The selectors answer "what is stored". This module answers "what does the board
see", once, so the overview band, the Nähtavus page and the newsletter card on
Uudised cannot end up describing the same number differently. The templates lay
out; neither holds a rule.

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

from django.urls import reverse
from django.utils import timezone

from apps.core.formatting import short_date, signed_integer

from .ga4 import Ga4ConnectionStatus, get_connection_status
from .models import CollectionMethod, VisibilityMetric
from .registry import SOCIAL_METRICS
from .selectors import (
    MetricReading,
    NewsletterSummary,
    VisibilitySummary,
    WebsiteTraffic,
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
    trimmed to the observation date alone, so the band no longer prints either,
    and the Nähtavus page's definition list has since gone too. What a card still
    carries is `state_label` in its own header, saying whether the figure was
    typed or collected.
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

    It used to link nowhere, and the reason given was that a link to Google
    Analytics would send a board member to a login screen. That is still true and
    is no longer the only option: **Koduleht** answers this card's question in
    DashKoda, so the heading goes there. `channel_card` links a heading only when
    the slot is not planned, so the unconnected branch still links nowhere on its
    own — which is right, because there would be nothing at the other end.
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
    # people. Users and page views are a different question and are kept for
    # Koduleht rather than crowded into one cell.
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
        detail_url=reverse("visibility"),
    )


def build_newsletter_slot(summary: NewsletterSummary, *, detail_url: str = "") -> ChannelSlot:
    """Each newsletter under its own name, with no total across them.

    Public because two pages render this one card: the overall dashboard's
    channel band, and the Uudised page, where the newsletter material moved from
    Nähtavus. Both take the same `ChannelSlot` from here rather than growing a
    second card under `apps/news` that would drift from this one.

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


def _social_slot(reading: MetricReading) -> ChannelSlot:
    """One hand-entered channel. Its heading is text, not a link.

    There is nowhere in DashKoda for it to go. The four social figures used to
    have a page — the old Nähtavus — and Koduleht deliberately does not show
    them, because a page named after the website should not open with four
    figures about something else.

    The remaining place they can be read whole is
    `/admin/data-entry/visibility/`, and that is staff-only. An ordinary viewer
    holds the shared PIN and no Django account, so linking there would advertise
    a door they cannot open — the same rule that keeps `Lisa andmed` off a
    viewer's page. A heading that is plain text is the honest state, and the
    figure, its date and `Käsitsi sisestatud` are all on the card already.
    """
    secondary = ""
    if reading.has_data and reading.change is not None:
        secondary = f"{reading.change_label} {reading.comparison_period}"
    return ChannelSlot(
        label=reading.label,
        value=reading.value,
        # No unit beside the figure. `Facebooki jälgijad · 12 230 jälgijat`
        # says "followers" twice; the card's own title is the unit. The website
        # card keeps `seanssi` because sessions are not visits and its title
        # does not say which of the two the number is.
        unit="",
        secondary=secondary,
        as_of=reading.as_of,
        source_label=reading.source_label,
        method_label=reading.method_label,
        state_label=reading.state_label,
        state_variant=reading.state_variant,
        profile_url=reading.profile_url,
    )


def build_channel_band(
    *,
    summary: VisibilitySummary | None = None,
    ga4_status: Ga4ConnectionStatus | None = None,
    include_newsletter: bool = True,
) -> tuple[ChannelSlot, ...]:
    """The six channel slots, in the order the board reads them.

    Website first because it is the widest audience; then the newsletter, which
    the Chamber owns outright; then the four social channels in the order the
    registry fixes.

    **Each slot's destination is decided here**, because this is the only place
    that knows which slot is which. It used to take one `detail_url` and hand the
    same address to all six — which meant that address had to be wrong for five
    of them, and quietly became wrong for all six as material moved: the
    newsletter card pointed at a page whose newsletters had gone to Uudised, and
    after the website page became Koduleht the four social cards pointed at a
    page that deliberately shows no social figures at all.

    So: the website card goes to Koduleht, the newsletter card to Uudised where
    `Uudiskirjade tulemused` lives, and the social cards nowhere — see
    `_social_slot` for why nowhere is the honest answer rather than a gap.

    `include_newsletter` is turned off by a page that shows the newsletter
    material itself one section further on, so its band does not repeat a card
    the reader is about to reach.
    """
    summary = summary if summary is not None else get_visibility_summary()
    ga4_status = ga4_status if ga4_status is not None else get_connection_status()
    traffic = get_website_traffic()
    return (
        _website_slot(ga4_status, traffic),
        *(
            (build_newsletter_slot(summary.newsletter, detail_url=reverse("news")),)
            if include_newsletter
            else ()
        ),
        *(_social_slot(reading) for reading in summary.social),
    )


@dataclass(frozen=True)
class VisibilityPage:
    """Everything the Nähtavus template renders.

    No `newsletters` section any more: `Uudiskirjade tulemused` is rendered by
    the Uudised page, so building it here would run the campaign-performance
    queries on every Nähtavus visit for a section that page no longer shows.
    """

    summary: VisibilitySummary
    newsletter: NewsletterSummary
    social: tuple[MetricReading, ...]
    channels: tuple[ChannelSlot, ...]
    ga4: Ga4ConnectionStatus
    traffic: TrafficSection
    today: date

    @property
    def has_any_data(self) -> bool:
        return self.summary.has_any_data


def build_visibility_page(
    *,
    today: date | None = None,
    period_key: str | None = None,
    section_key: str | None = None,
    search: str | None = None,
    page: str | int | None = None,
) -> VisibilityPage:
    """Read every metric once and shape it for the page.

    `search` matches website pages, and it is the only search this page has now.
    It used to sit beside a `newsletter_search` that matched campaign subjects,
    named apart so neither could be fed the other's term; the subject search
    moved to Uudised with the section it belongs to.
    """
    today = today or timezone.localdate()
    summary = get_visibility_summary(today=today)
    ga4_status = get_connection_status()

    return VisibilityPage(
        summary=summary,
        newsletter=summary.newsletter,
        social=summary.social,
        channels=build_channel_band(
            summary=summary,
            ga4_status=ga4_status,
            # Shown on Uudised now, one section below the news archive.
            include_newsletter=False,
        ),
        ga4=ga4_status,
        traffic=build_traffic_section(
            period_key=period_key,
            section_key=section_key,
            search=search,
            page=page,
            today=today,
        ),
        today=today,
    )


__all__ = [
    "EXTERNAL_LINK_NOTE",
    "NEWSLETTER_LABEL",
    "SOCIAL_METRICS",
    "WEBSITE_LABEL",
    "ChannelSlot",
    "VisibilityMetric",
    "VisibilityPage",
    "build_channel_band",
    "build_newsletter_slot",
    "build_visibility_page",
]
