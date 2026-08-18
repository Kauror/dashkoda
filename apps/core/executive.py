"""The vocabulary a domain uses to speak to the executive overview.

Six intelligence dashboards decide, each in its own terms, what is worth a
manager's attention. The main page collects those decisions, orders them and
draws five of them. For that to work the domains need *one* small shared shape —
and only one, because everything else about them differs.

This module holds that shape and nothing else. It lives in `apps.core` rather
than in `apps.dashboard` so a domain can describe itself without importing the
page that renders it: `apps.shop` knowing about `apps.dashboard` would make the
shop module unusable without the overview and would invert the dependency every
other part of this repository maintains.

## What a domain decides, and what it does not

A domain decides **whether** something is worth saying, **how it is worded**,
and **how urgent it is**. Those are judgements about legislation, membership or
Commerce, and the overview has no basis for making them.

The overview decides **how many** signals fit, **which order** they read in and
**what they look like**. Those are judgements about a page.

So `DomainSignal` carries evidence and priority, and carries no colour, no icon,
no position and no CSS class. A domain that wanted to make its own signal red
would be deciding something about the page.

## Why priority is three words and not a number

A score would let two domains' signals be compared arithmetically, and they
cannot be: eight legal deadlines inside a week and a 24% fall in acquisitions
are not positions on one scale. Three named levels can be ordered without
implying the gap between them means anything.

`CRITICAL` is reserved. A signal is critical when the reader would be wrong to
close the page without acting — an imminent deadline under a stated rule, or a
source failure that invalidates a figure the page is showing. Ordinary bad news
is `ATTENTION`. Something merely worth knowing, good or bad, is `NOTABLE`.

## Direction is not priority

`direction` says which way a number moved; `priority` says how much it matters.
They are deliberately separate fields because they are not correlated: a rise in
opinion output is `up` and `notable`, a rise in unanswered deadlines is `up` and
`attention`, and a page that coloured both green because both rose would be
telling the reader the opposite of what happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class SignalPriority(StrEnum):
    """How much a signal matters, in three orderable steps."""

    CRITICAL = "critical"
    ATTENTION = "attention"
    NOTABLE = "notable"


#: Read order. Explicit rather than derived from the enum's declaration order,
#: because reordering an enum for any other reason must not silently reorder the
#: page.
PRIORITY_ORDER: dict[str, int] = {
    SignalPriority.CRITICAL: 0,
    SignalPriority.ATTENTION: 1,
    SignalPriority.NOTABLE: 2,
}


class SignalDirection(StrEnum):
    """Which way the measured figure moved, where that applies."""

    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    #: A state rather than a movement — a count of deadlines, a stale source.
    NONE = "none"


class SignalTone(StrEnum):
    """Whether the domain calls this good news, and only the domain may.

    Direction is the arithmetic sign and priority is how much it matters;
    neither says whether a reader should be pleased. A rise in unanswered
    deadlines and a rise in the paid share are both `UP` and both `NOTABLE`
    would be the same badge for opposite news.

    `NEUTRAL` is the default and the honest one for most signals: a change worth
    knowing is not automatically a change worth celebrating, and a page that
    marked every rise positive would be as useless as one that marked none.
    Only a domain that can say "this is the direction we want" sets `POSITIVE`.
    """

    POSITIVE = "positive"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class DomainSignal:
    """One thing a domain thinks is worth the reader's attention.

    `headline` is the claim in a few words. `evidence` is the measurement that
    supports it and must state its own period, because the overview has no
    single period to state on the domains' behalf.

    `href` may be empty. A signal whose exact records have no page listing
    exactly them is still worth showing; a link landing on an approximate page
    is worse than no link, which is the rule the current overview already
    follows for its counts.

    `is_data_quality` separates a statement about the data from a statement
    about the business. A stale feed belongs in `Andmete seis` unless it
    invalidates a figure the page is showing, and only the domain knows which of
    those it is.
    """

    key: str
    headline: str
    evidence: str
    priority: SignalPriority = SignalPriority.NOTABLE
    direction: SignalDirection = SignalDirection.NONE
    #: Whether the domain calls this good news. Neutral unless it says so.
    tone: SignalTone = SignalTone.NEUTRAL
    href: str = ""
    #: The measurement's own as-of date, where it has one.
    as_of: date | None = None
    is_data_quality: bool = False

    @property
    def has_link(self) -> bool:
        return bool(self.href)

    @property
    def order(self) -> int:
        return PRIORITY_ORDER[self.priority]


__all__ = [
    "PRIORITY_ORDER",
    "DomainSignal",
    "SignalDirection",
    "SignalPriority",
    "SignalTone",
]
