"""Shape the visibility figures for the pages that show them.

The selectors answer "what is stored". This module answers "what does the board
see", once, so the overview band, the Nähtavus page and the newsletter card on
Uudised cannot end up describing the same number differently. The templates lay
out; neither holds a rule.

## The band is audiences, and the website is not one of them

There was a website slot here — `Kodulehe külastused`, sessions since the
previous reading — and it was removed on 2026-08-17 with the section it lived
in. `Koja töölaud` was its only consumer, and the rebuilt front page states
sessions as the headline of its `Koduleht ja uudised` card over a properly
measured window with a proper comparison. One measure under two labels on one
page invites a reconciliation nobody can perform, and the slot's own comparison
— against whichever reading happened to precede it — was the weaker of the two.

So the band is what `Auditooriumid` claims it is: how large the Chamber's own
audiences are. Sessions are not an audience; they are visits, and the same
person visiting twice is two of them.

Two things are decided here and nowhere else:

- **the newsletter slot lists each newsletter and never totals them.** The
  Chamber sends three, to three lists, and nobody has counted how many people
  are on more than one. A sum would silently claim that overlap is zero, so the
  card shows the lists and no headline figure at all;
- **neither kind ever reads as the other.** The newsletter card is collected and
  says `Automaatselt kogutud`; the four social cards are typed and never say
  synchronised, connected or automatically updated. Every populated card carries
  its observation date whichever it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.urls import reverse

from .models import CollectionMethod, VisibilityMetric
from .registry import SOCIAL_METRICS
from .selectors import (
    MetricReading,
    NewsletterSummary,
    VisibilitySummary,
    get_visibility_summary,
)

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
        # card keeps its `külastust` unit because its title does not say
        # whether the number counts visits or page views.
        unit="",
        secondary=secondary,
        as_of=reading.as_of,
        source_label=reading.source_label,
        method_label=reading.method_label,
        state_label=reading.state_label,
        state_variant=reading.state_variant,
        profile_url=reading.profile_url,
    )


@dataclass(frozen=True)
class AudienceRow:
    """One audience, on its own line, with nothing added to anything.

    The overview's `Auditooriumid` strip since 2026-08-18. It draws one flat
    list where the band drew cells — the three newsletters were a single cell
    holding three sub-rows, which made them look like parts of one audience
    when they are three, and gave the four social channels four times the room
    to say a smaller thing.

    **Sorted by size and never summed.** A subscriber list and a follower count
    are different kinds of audience and the sort does not make them the same
    thing; what it does is put the Chamber's largest audiences first, which is
    the order a reader wants and the order nobody can derive from a fixed
    registry sequence. Every row keeps the word for what it counts — `uudiskiri`
    on a list, the registry's own `jälgijad` or `tellijad` on a profile — so a
    reader can see the two kinds apart while reading them together.

    A row with no reading keeps its name and shows no figure. It sorts last,
    because "we have not entered this" is not a size.
    """

    label: str
    value: int | None = None
    as_of: date | None = None
    profile_url: str = ""
    detail_url: str = ""

    @property
    def has_value(self) -> bool:
        # `is not None`, not truthiness: an entered zero is a real reading.
        return self.value is not None

    @property
    def external_link_note(self) -> str:
        return EXTERNAL_LINK_NOTE


def build_audience_rows(*, summary: VisibilitySummary | None = None) -> tuple[AudienceRow, ...]:
    """Every audience the Chamber owns, largest first.

    Reads the same `VisibilitySummary` the band reads, so a figure here and a
    figure on the Otsepostitused newsletter card cannot disagree — this shapes
    the same readings into rows rather than computing anything of its own.

    The newsletters are named `<list> uudiskiri` because a flat list has no
    surrounding card to say what kind of audience a line is. `e-Teataja 20 616`
    beside `Facebook 12 230` would leave the reader to guess that one counts
    subscriptions and the other follows.
    """
    summary = summary if summary is not None else get_visibility_summary()

    rows = [
        AudienceRow(
            label=f"{reading.label} uudiskiri",
            value=reading.value,
            as_of=reading.as_of,
            detail_url=reverse("mailings"),
        )
        for reading in summary.newsletter.lists
    ]
    rows.extend(
        AudienceRow(
            label=reading.label,
            value=reading.value,
            as_of=reading.as_of,
            profile_url=reading.profile_url,
        )
        for reading in summary.social
    )
    # Largest first; the unread ones keep their names and go last in the order
    # the registry fixes, which is the only order they have.
    rows.sort(key=lambda row: (row.value is None, -(row.value or 0)))
    return tuple(rows)


def build_channel_band(
    *,
    summary: VisibilitySummary | None = None,
    include_newsletter: bool = True,
) -> tuple[ChannelSlot, ...]:
    """The audience slots, in the order the board reads them.

    The newsletter first, which the Chamber owns outright; then the four social
    channels in the order the registry fixes. The website slot that used to lead
    this band went on 2026-08-17 — see the module docstring.

    **Each slot's destination is decided here**, because this is the only place
    that knows which slot is which. It used to take one `detail_url` and hand the
    same address to all six — which meant that address had to be wrong for five
    of them, and quietly became wrong for all six as material moved: the
    newsletter card pointed at a page whose newsletters had gone to Uudised, and
    after the website page became Koduleht the four social cards pointed at a
    page that deliberately shows no social figures at all.

    So: the newsletter card goes to Otsepostitused where
    `Uudiskirjade tulemused` lives, and the social cards nowhere — see
    `_social_slot` for why nowhere is the honest answer rather than a gap.

    The newsletter card has been re-aimed twice, both times one release behind
    the material: it pointed at the website page after the newsletters left it,
    and at Uudised after they left there. The rule it keeps breaking is that a
    card links to wherever its own subject is rendered *now*.

    `include_newsletter` is turned off by a page that shows the newsletter
    material itself one section further on, so its band does not repeat a card
    the reader is about to reach.
    """
    summary = summary if summary is not None else get_visibility_summary()
    return (
        *(
            (build_newsletter_slot(summary.newsletter, detail_url=reverse("mailings")),)
            if include_newsletter
            else ()
        ),
        *(_social_slot(reading) for reading in summary.social),
    )


__all__ = [
    "EXTERNAL_LINK_NOTE",
    "NEWSLETTER_LABEL",
    "SOCIAL_METRICS",
    "AudienceRow",
    "ChannelSlot",
    "VisibilityMetric",
    "build_audience_rows",
    "build_channel_band",
    "build_newsletter_slot",
]
