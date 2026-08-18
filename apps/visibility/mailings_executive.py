"""What the Otsepostitused domain tells the main dashboard.

The newsletter card of `Põhinäitajad`, and nothing else. It exists because the
newsletters became their own section — `/otsepostitused/` — and a domain with its
own dashboard needs its own executive summary rather than a rate borrowed from
the website's.

## Nothing here is a new definition

Every figure is `smaily_selectors.get_newsletter_aggregate` over the selector's
own default block of sends, which is the same call `mailings_page` makes for the
section's own comparison table. So the rate on the front page and the rate on
`/otsepostitused/` are the same arithmetic over the same sends, and a
disagreement between them is a defect rather than a second definition.

The e-Teataja open rate on this card is the figure that used to live on the
website card as `newsletter_open_rate`. It moved here whole, with its own
`NEWSLETTER_ISSUES` block size unchanged — what changed is which domain owns it,
now that the newsletters have a dashboard of their own to own it from.

## Two rules this must never break

- **a rate is summed counts over summed counts.** `NewsletterAggregate` derives
  both, so this module never divides anything and never averages a percentage.
  A mean over three lists of 8 008, 755 and 527 would drift towards the smallest
  one and describe no send;
- **audiences are never totalled.** This card carries no subscriber count at
  all. The list sizes are the `Auditooriumid` strip's job, one per list, and the
  overlap between the three is unmeasured — so there is no field here capable of
  holding a sum.

## The cadence figure is a count of letters, not of readers

`sends_recent` answers "how much did we send", which is the one thing on this
card the rates cannot say: a month with no letters and a month with four both
have an open rate, and only one of them is a month of work. It is deliberately
**not** weighted, deduplicated or turned into a per-list figure — a letter is a
letter, and the two campaigns one e-Teataja issue goes out as are two sends
because that is how the Chamber posts it.

## Why e-Teataja leads

It is the flagship: a regular cadence, by far the largest audience, and two
segments the Chamber sends every issue to separately. The other two go out
irregularly — e-Vestnik can go months between sends — so a card leading with
whichever letter went out most recently would change subject without saying so.
The other two appear as supporting rates, each under its own name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from .registry import VisibilityMetric, spec_for
from .smaily_segments import NEWSLETTERS
from .smaily_selectors import (
    DEFAULT_AGGREGATE_ISSUES,
    count_sends_between,
    get_newsletter_aggregate,
)

#: How many recent sends every rate on this card is weighted over — the
#: selector's own default, so this card and the Otsepostitused page cannot state
#: different rates for the same letter.
NEWSLETTER_ISSUES = DEFAULT_AGGREGATE_ISSUES

#: The letter the card leads with, for the reason in the module docstring.
FLAGSHIP_METRIC = VisibilityMetric.NEWSLETTER_ETEATAJA

#: The cadence window, and the length of the block it is compared against.
#: Thirty days rather than a calendar month, so the comparison is two equal
#: spans and not February against March.
CADENCE_DAYS = 30


@dataclass(frozen=True)
class NewsletterRates:
    """One newsletter's weighted rates over one block of sends."""

    metric: str
    label: str
    campaigns: int = 0
    open_rate: float | None = None
    click_rate: float | None = None

    @property
    def has_open_rate(self) -> bool:
        return self.open_rate is not None


@dataclass(frozen=True)
class MailingsExecutive:
    """The Otsepostitused card's figures. Rates only, never an audience."""

    #: e-Teataja, whose open rate is the headline.
    flagship: NewsletterRates | None = None
    #: The same letter's previous block of sends, for the movement in points.
    flagship_previous_open_rate: float | None = None
    #: The other letters, in registry order, each on its own.
    others: tuple[NewsletterRates, ...] = ()
    issues: int = NEWSLETTER_ISSUES
    #: Letters posted in the last `CADENCE_DAYS`, and in the equal span before.
    #: Every newsletter and every one-off, because the question is how much went
    #: out rather than how much of one list went out.
    sends_recent: int | None = None
    sends_previous: int | None = None
    cadence_days: int = CADENCE_DAYS

    @property
    def sends_change(self) -> int | None:
        """The movement in letters, or `None` with nothing to compare against."""
        if self.sends_recent is None or self.sends_previous is None:
            return None
        return self.sends_recent - self.sends_previous

    @property
    def has_headline(self) -> bool:
        return self.flagship is not None and self.flagship.has_open_rate

    @property
    def open_rate_change_points(self) -> float | None:
        """The movement against the previous block, in percentage points.

        Points rather than percent, because two rates differ by points and
        saying `+8%` of a percentage is the commonest way to overstate a
        newsletter by an order of magnitude. `None` when either block is
        unmeasured — a first-ever block has nothing behind it, and a block of
        sends whose statistics were never read is not a block that scored zero.
        """
        if self.flagship is None or self.flagship.open_rate is None:
            return None
        if self.flagship_previous_open_rate is None:
            return None
        return (self.flagship.open_rate - self.flagship_previous_open_rate) * 100.0


def get_mailings_executive() -> MailingsExecutive:
    """Read each newsletter's block of sends once and shape the card.

    Four aggregate reads: the flagship's recent block, the block before it, and
    one for each of the other two letters. Each is bounded by the block size
    rather than by the send history, so nothing here grows with the fourteen
    years of e-Teataja in the archive.
    """
    flagship = _rates(FLAGSHIP_METRIC)
    if flagship is None:
        return MailingsExecutive()

    previous = get_newsletter_aggregate(
        FLAGSHIP_METRIC, limit=NEWSLETTER_ISSUES, offset=NEWSLETTER_ISSUES
    )
    now = timezone.now()
    window = timedelta(days=CADENCE_DAYS)
    others = tuple(
        rates
        for spec in NEWSLETTERS
        if spec.metric != FLAGSHIP_METRIC
        for rates in (_rates(spec.metric),)
        if rates is not None
    )
    return MailingsExecutive(
        flagship=flagship,
        flagship_previous_open_rate=previous.open_rate if previous.has_data else None,
        others=others,
        issues=NEWSLETTER_ISSUES,
        sends_recent=count_sends_between(start=now - window, end=now),
        sends_previous=count_sends_between(start=now - window - window, end=now - window),
        cadence_days=CADENCE_DAYS,
    )


def _rates(metric: str) -> NewsletterRates | None:
    """One newsletter's block, or `None` when nothing has been collected.

    `None` rather than a row of zeros: a letter whose sends have never been read
    has an unknown open rate, not one of nought, and the card's own presence test
    depends on being able to tell those apart.
    """
    aggregate = get_newsletter_aggregate(metric, limit=NEWSLETTER_ISSUES)
    if not aggregate.has_data:
        return None
    registry_spec = spec_for(metric)
    return NewsletterRates(
        metric=metric,
        label=aggregate.label or (registry_spec.label if registry_spec else ""),
        campaigns=aggregate.campaigns,
        open_rate=aggregate.open_rate,
        click_rate=aggregate.click_rate,
    )


__all__ = [
    "CADENCE_DAYS",
    "FLAGSHIP_METRIC",
    "NEWSLETTER_ISSUES",
    "MailingsExecutive",
    "NewsletterRates",
    "get_mailings_executive",
]
