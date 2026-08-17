"""How the executive overview shows a figure. Never what the figure means.

Every type here describes **presentation**: a label, a formatted value, a period
to print beside it, a link to follow. None of them computes a membership total,
an opinion count, a session or an acquisition, and none of them decides whether
a change is large. Those are domain judgements and they arrive already made,
from the six `executive.py` modules in the domain apps.

The split matters because the overview is the one page that touches every
domain. If it were allowed to define a KPI, DashKoda would have two definitions
of that KPI — the domain's and the front page's — and they would drift within a
month. So the rule is absolute: a number reaches this module already computed,
already compared and already worded by whoever owns it.

## Why every metric carries its own period

There is deliberately no global period control on this page, because the domains
do not share a period and cannot be made to. Membership is a latest observation
against a year-ago one; legal work is year-to-date against the same calendar day;
the website is thirty measured days; Commerce is thirty days anchored to a manual
export's own end date. A single `30 p` control across those would manufacture the
appearance of comparability.

The consequence is that **the period travels with the number**, in the metric
rather than in the section heading, and `ExecutiveMetric` cannot be constructed
without one. A figure whose period is unknown is a figure nobody can check.

## Availability is three states, not two

`is_available` false with an `unavailable_note` is a source that has nothing to
say. It is not zero, and the templates render an em dash and the note. A metric
that measured zero is available and carries `"0"`, which reads quite differently
and must.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from apps.core.executive import DomainSignal, SignalDirection, SignalPriority


@dataclass(frozen=True)
class ExecutiveLink:
    """One drill-through. The label says where it goes, not "read more"."""

    label: str
    url: str
    #: Set for a link leaving DashKoda, so the template can mark it.
    is_external: bool = False


@dataclass(frozen=True)
class ExecutiveComparison:
    """One figure set against one earlier figure, already worded.

    `text` is the delta as it will be printed — `+8,4%`, `−124` — and carries
    its own sign, so direction is never conveyed by colour alone. `basis` names
    what it was compared with, in the domain's own words.

    `unavailable_note` is how a domain declines to compare. The website's
    coverage rules are the worked example: two windows measured to different
    completeness are not subtractable, and the page prints the reason where the
    delta would have been rather than a delta nobody should trust.
    """

    text: str = ""
    basis: str = ""
    direction: str = SignalDirection.NONE
    unavailable_note: str = ""

    @property
    def is_available(self) -> bool:
        return bool(self.text) and not self.unavailable_note

    @property
    def has_note(self) -> bool:
        return bool(self.unavailable_note)


@dataclass(frozen=True)
class ExecutiveMetric:
    """One figure, with everything needed to check it.

    `value` is a string because it is already formatted by
    `apps.core.formatting` — grouped thousands, an Estonian decimal comma, a
    real minus sign. Handing the template a number would let one page format a
    thousand differently from another.

    `value` of `None` means unavailable. `"0"` means measured zero.
    """

    label: str
    period: str
    source: str
    value: str | None = None
    unit: str = ""
    as_of: date | datetime | None = None
    comparison: ExecutiveComparison | None = None

    @property
    def is_available(self) -> bool:
        return self.value is not None

    @property
    def has_comparison(self) -> bool:
        return self.comparison is not None and self.comparison.is_available


@dataclass(frozen=True)
class ExecutiveFact:
    """One supporting figure under a headline. Smaller, and still sourced.

    A fact carries its own source and date because a card may mix them: the
    Liikmeskond card's headline is the public directory and its paid share is
    the internal board report, and a reader must be able to tell which is which
    without being told twice.
    """

    label: str
    value: str | None = None
    source: str = ""
    as_of: date | datetime | None = None
    #: Where exactly these rows are listed, when such a page exists.
    url: str = ""

    @property
    def is_available(self) -> bool:
        return self.value is not None

    @property
    def has_link(self) -> bool:
        return bool(self.url)


@dataclass(frozen=True)
class ExecutiveDomainCard:
    """One of the six domains, compact enough that six fit in two rows.

    The successor to `ExecutivePillar`, which was a tall card built for a strip
    of four or five strategic areas. Six of those did not fit a screen, and a
    front page that has to be scrolled to see its sixth domain has stopped being
    a cockpit. What came off is everything that was not a number or a label: the
    meaning sentence, the sparkline, the per-fact source captions.

    The order inside the card is fixed and is what makes six of them scannable —
    a reader's eye lands in the same place on every one:

    ```text
    DOMAIN
    BIG NUMBER  unit
    comparison

    fact label            value
    fact label            value

    period/as-of          Vaata … →
    ```

    Deliberately absent:

    - **a meaning sentence.** `ExecutivePillar.meaning` restated the comparison
      immediately above it in words. The domains still compose one — the domain
      pages use it — and this card does not print it;
    - **a trend.** A sparkline two centimetres wide cannot be read and the
      comparison says what it was for;
    - **a status colour for the card as a whole.** There is no red/amber/green
      verdict on a domain here. What needs attention is decided by the domains
      and collected in `Tähelepanu`, one section up, where it arrives with the
      evidence behind it.
    """

    key: str
    label: str
    headline: ExecutiveMetric | None = None
    facts: tuple[ExecutiveFact, ...] = ()
    #: The one period or as-of line at the foot of the card, in the domain's own
    #: vocabulary. Not assembled from the metrics by the template: a card whose
    #: figures come from two sources — Liikmeskond is the worked example — has to
    #: be able to name both currencies in one short line.
    period_line: str = ""
    links: tuple[ExecutiveLink, ...] = ()
    #: Why this card has nothing to show. Empty when it does.
    unavailable_note: str = ""

    @property
    def is_available(self) -> bool:
        return self.headline is not None and self.headline.is_available

    @property
    def available_facts(self) -> tuple[ExecutiveFact, ...]:
        return tuple(fact for fact in self.facts if fact.is_available)


@dataclass(frozen=True)
class ExecutiveSignal:
    """One domain's signal, placed on the page.

    The domain supplied everything except `domain_label` and `position`: what it
    says, how urgent it is and where it links. This adds only which domain it
    belongs to and where it ended up in the order, because those are facts about
    the page rather than about legislation or Commerce.
    """

    signal: DomainSignal
    domain_label: str
    domain_key: str

    @property
    def key(self) -> str:
        return self.signal.key

    @property
    def headline(self) -> str:
        return self.signal.headline

    @property
    def evidence(self) -> str:
        return self.signal.evidence

    @property
    def priority(self) -> str:
        return self.signal.priority

    @property
    def direction(self) -> str:
        return self.signal.direction

    @property
    def href(self) -> str:
        return self.signal.href

    @property
    def as_of(self):
        return self.signal.as_of

    @property
    def has_link(self) -> bool:
        return self.signal.has_link

    @property
    def priority_label(self) -> str:
        """The urgency in words, because colour is never the only signal."""
        return _PRIORITY_LABELS[self.signal.priority]


_PRIORITY_LABELS: dict[str, str] = {
    SignalPriority.CRITICAL: "Kiireloomuline",
    SignalPriority.ATTENTION: "Tähelepanu",
    SignalPriority.NOTABLE: "Tähelepanuväärne",
}


@dataclass(frozen=True)
class ExecutiveUpcomingItem:
    """One dated thing coming up, in the shared timeline.

    `url` is empty unless the destination identifies **this record**. A row
    pointing at a page that merely mentions the same domain is worse than a row
    that does not link at all, because it promises a detail view and delivers a
    list.
    """

    when: date
    domain_label: str
    domain_key: str
    title: str
    context: str = ""
    url: str = ""
    #: An address outside DashKoda — a public event page.
    is_external: bool = False

    @property
    def has_link(self) -> bool:
        return bool(self.url)


@dataclass(frozen=True)
class ExecutiveInterestItem:
    """One column of `Praegu enim huvi`.

    Three of these appear side by side and their metrics are **not comparable**:
    page views, article views and acquired units. Each states its own metric name
    and its own period for exactly that reason, and nothing ranks them against
    one another or puts them on a shared axis.

    There were four. The fourth was the next scheduled event, and it left on
    2026-08-17: this section answers "what are people paying attention to", and a
    date in the future is not an answer to it. Events are on the page twice
    already — the Sündmused card's headline and the timeline's own lane — and
    a scheduled date beside three measured figures invited exactly the comparison
    the rest of this docstring forbids.
    """

    domain_label: str
    domain_key: str
    title: str
    metric_value: str | None = None
    metric_label: str = ""
    period: str = ""
    #: Secondary context — a publication date, an event date, a product type.
    context: str = ""
    url: str = ""
    is_external: bool = False
    unavailable_note: str = ""

    @property
    def is_available(self) -> bool:
        return self.metric_value is not None and bool(self.title)

    @property
    def has_link(self) -> bool:
        return bool(self.url)


@dataclass(frozen=True)
class ExecutiveDataStatus:
    """What one business domain's source is doing, in that source's own terms.

    Not one freshness rule applied six times. A monthly board report is not
    stale because it is older than yesterday, and a dated Commerce export is not
    a failed feed. `state_label` is the domain's own vocabulary and `limitation`
    is what a reader should know before trusting the figures above.
    """

    domain_label: str
    domain_key: str
    source_label: str
    state: str
    state_label: str
    state_variant: str = "neutral"
    as_of: date | datetime | None = None
    coverage: str = ""
    limitation: str = ""

    @property
    def has_limitation(self) -> bool:
        return bool(self.limitation)


@dataclass(frozen=True)
class ExecutiveOverviewPage:
    """Everything the executive template renders.

    Assembled once per request by `executive.build_executive_overview`. The
    template reads it and nothing else: there is no second data path, no
    template tag reaching into a selector, and no JavaScript fetching a figure.
    """

    #: The six domain cards of `Põhinäitajad`, in reading order.
    cards: tuple[ExecutiveDomainCard, ...] = ()
    signals: tuple[ExecutiveSignal, ...] = ()
    upcoming: tuple[ExecutiveUpcomingItem, ...] = ()
    interest: tuple[ExecutiveInterestItem, ...] = ()
    #: The audience strip, built by `apps.visibility` as it always was — minus
    #: the website slot, whose sessions are the `Koduleht ja uudised` card's
    #: headline and must not appear on one page twice.
    channels: tuple = ()
    data_status: tuple[ExecutiveDataStatus, ...] = ()

    @property
    def has_signals(self) -> bool:
        return bool(self.signals)

    @property
    def has_upcoming(self) -> bool:
        return bool(self.upcoming)

    @property
    def available_interest(self) -> tuple[ExecutiveInterestItem, ...]:
        return tuple(item for item in self.interest if item.is_available)

    @property
    def has_any_source(self) -> bool:
        """Whether any business source has published anything at all."""
        return any(row.state != STATE_NOT_CONNECTED for row in self.data_status)

    @property
    def warning_count(self) -> int:
        """Sources worth disclosing in the header chip.

        Counts *connected* sources whose state or limitation a reader should
        know about before trusting a figure above. A source nobody has connected
        yet is not a warning — it is an absence, and a fresh deployment
        announcing "7 andmemärkust" would be reporting its own emptiness as
        seven problems.

        Deliberately **not** a business KPI. The header never prints a
        connected-source ratio: how much plumbing is attached is not a measure
        of how the Chamber is doing. The overview stopped printing this count on
        2026-08-16 for exactly that reason; `/haldus/` reads it, and the property
        stays because that is where the question belongs.
        """
        return sum(
            1
            for row in self.data_status
            if row.state != STATE_NOT_CONNECTED
            and (row.has_limitation or row.state != STATE_AVAILABLE)
        )


#: Source states, in the vocabulary the brief names. Kept here rather than in
#: `connections.py` because that module describes a *collected feed* and half of
#: these are not one: a manual snapshot and a monthly report are neither
#: connected nor broken.
STATE_AVAILABLE = "available"
STATE_STALE = "stale"
STATE_MANUAL = "manual"
STATE_PARTIAL = "partial"
STATE_NOT_CONNECTED = "not_connected"

STATE_LABELS: dict[str, str] = {
    STATE_AVAILABLE: "Andmed olemas",
    STATE_STALE: "Vananenud pärast ebaõnnestunud uuendust",
    STATE_MANUAL: "Käsitsi lisatud seis",
    STATE_PARTIAL: "Osaliselt mõõdetud",
    STATE_NOT_CONNECTED: "Ühendamata",
}

STATE_VARIANTS: dict[str, str] = {
    STATE_AVAILABLE: "success",
    STATE_STALE: "warning",
    STATE_MANUAL: "neutral",
    STATE_PARTIAL: "neutral",
    STATE_NOT_CONNECTED: "neutral",
}


__all__ = [
    "STATE_AVAILABLE",
    "STATE_LABELS",
    "STATE_MANUAL",
    "STATE_NOT_CONNECTED",
    "STATE_PARTIAL",
    "STATE_STALE",
    "STATE_VARIANTS",
    "ExecutiveComparison",
    "ExecutiveDataStatus",
    "ExecutiveDomainCard",
    "ExecutiveFact",
    "ExecutiveInterestItem",
    "ExecutiveLink",
    "ExecutiveMetric",
    "ExecutiveOverviewPage",
    "ExecutiveSignal",
    "ExecutiveUpcomingItem",
]
