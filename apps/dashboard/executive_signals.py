"""Collect what the domains flagged, order it, and cut it to what fits.

`Tähelepanu` is the overview's one genuinely cross-domain capability,
and this module is deliberately the least clever part of it. It does four
things — collect, deduplicate, sort, limit — and it decides none of the
following:

- **whether** something is unusual. Eight deadlines inside a week is a fact
  about legislative process; a 24% fall in acquisitions is a fact about a
  template catalogue. Only `apps.legal_work` and `apps.shop` can say what
  counts, and both already do, using the same thresholds their own dashboards
  use;
- **how** it is worded. The evidence sentence arrives written;
- **how urgent** it is. `SignalPriority` comes from the domain.

If this module decided any of those, the overview would hold a second, private
definition of "unusual" for six domains at once, and it would drift from all six.

## No score, no ranking model

The order is priority first, then a fixed domain order, then the domain's own
order within itself. That is a *sort*, not a *ranking*: nothing is scored,
nothing is weighted, and two signals of the same priority are not being claimed
to be equally important — only that the page had to print one of them first.

A weighted cross-domain score would be worse than useless here. It would need
membership, opinions, sessions and acquisitions to share a unit, and the whole
reason this dashboard has six domain cards instead of one number is that they
do not.

## Silence is a real answer

With nothing to say the section is not rendered at all. It does not fill itself
with rows confirming that ordinary things are ordinary, because a reader who
learns to skim `Tähelepanu` when it is full of routine will skim it
on the day it is not.
"""

from __future__ import annotations

from collections.abc import Iterable

from apps.core.executive import DomainSignal

from .executive_models import ExecutiveSignal

#: How many signals the section shows. Five is what a reader takes in before the
#: section stops being an exception list and becomes another dashboard.
SIGNAL_LIMIT = 5

#: How many any one domain may contribute, so a single busy source cannot fill
#: the section and hide the other five domains entirely.
PER_DOMAIN_LIMIT = 2

#: Tie-break order when two signals share a priority. Follows the card order in
#: `Põhinäitajad`, so the section reads in the same sequence as the grid below it.
DOMAIN_ORDER: tuple[str, ...] = (
    "membership",
    "legal_work",
    "events",
    "website",
    "news",
    "shop",
)

_DOMAIN_POSITION = {key: index for index, key in enumerate(DOMAIN_ORDER)}


def collect_signals(sources: Iterable[tuple[str, str, Iterable[DomainSignal]]]):
    """Flatten `(domain_key, domain_label, signals)` into the page's list.

    Deduplication is by `DomainSignal.key`. Two domains cannot currently emit
    the same key — they are prefixed by domain — but a domain asked twice in one
    request would, and the page must never print one condition twice because a
    builder read a summary in two places.
    """
    collected: list[ExecutiveSignal] = []
    seen: set[str] = set()
    per_domain: dict[str, int] = {}

    for domain_key, domain_label, signals in sources:
        for signal in signals or ():
            if signal.key in seen:
                continue
            if per_domain.get(domain_key, 0) >= PER_DOMAIN_LIMIT:
                continue
            seen.add(signal.key)
            per_domain[domain_key] = per_domain.get(domain_key, 0) + 1
            collected.append(
                ExecutiveSignal(
                    signal=signal,
                    domain_label=domain_label,
                    domain_key=domain_key,
                )
            )

    collected.sort(key=_order)
    return tuple(collected[:SIGNAL_LIMIT])


def _order(entry: ExecutiveSignal) -> tuple[int, int, str]:
    """Priority, then page order, then key.

    The key is the final tie-break so the sort is total and the same input
    always renders in the same order — a list that reshuffles between two
    identical requests reads as movement that did not happen.
    """
    return (
        entry.signal.order,
        _DOMAIN_POSITION.get(entry.domain_key, len(DOMAIN_ORDER)),
        entry.signal.key,
    )


__all__ = [
    "DOMAIN_ORDER",
    "PER_DOMAIN_LIMIT",
    "SIGNAL_LIMIT",
    "collect_signals",
]
