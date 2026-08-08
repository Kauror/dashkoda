"""Which public address an event actually links to, when one is shown.

Two sources can supply one, and they are not equal:

1. the **workbook's own `public_url`**, entered by hand by someone at the
   Chamber who knew which page they meant;
2. a **matched** `PublicEventResource`, decided by the matcher.

The workbook always wins. A person naming a page is better evidence than a
score, even a score of 1.0 — and it means turning the matcher off, or changing
its constants, can never take away a link the Chamber entered itself. Matching
only fills the gap where nobody has said anything, which on current data is the
majority: 859 of 1,188 events have no workbook URL.

Only `matched` decisions are used. `ambiguous` deliberately yields nothing:
where two sessions of one series fall on one day the matcher declines, and
showing a coin-toss link would be worse than showing none.

Resolution is **bulk and read-only**. One query fetches the current match
snapshot's matched rows joined to their pages; nothing is written, no field on
an `EventProgrammeItem` is touched, and a page render never reaches the matcher.
"""

from __future__ import annotations

from dataclasses import dataclass

from .event_match_models import EventPublicMatch, EventPublicMatchSnapshot
from .event_matching import MatchDecision

#: Where a shown link came from. Reaches the template so a reader's link and the
#: page's own counts can never disagree about what is linked.
WORKBOOK = "workbook"
MATCHED = "matched"
NONE = ""


@dataclass(frozen=True)
class PublicLink:
    """The address to use for one event, and where it came from."""

    url: str = ""
    source: str = NONE

    def __bool__(self) -> bool:
        return bool(self.url)

    @property
    def is_matched(self) -> bool:
        """True only for a link the matcher supplied, never a hand-entered one."""
        return self.source == MATCHED


def matched_urls_by_event() -> dict[str, str]:
    """Every matched page in the current match snapshot, keyed by `event_id`.

    Returns `{}` when no snapshot is current, which is the ordinary state before
    the matcher has ever run and must not be an error: the workbook's own links
    keep working exactly as before.
    """
    snapshot = EventPublicMatchSnapshot.objects.filter(is_current=True).only("id").first()
    if snapshot is None:
        return {}
    rows = (
        EventPublicMatch.objects.filter(
            snapshot=snapshot, decision=MatchDecision.MATCHED, resource__isnull=False
        )
        .values_list("event_id", "resource__canonical_url")
        .iterator(chunk_size=2000)
    )
    return dict(rows)


def matched_event_ids() -> frozenset[str]:
    """The events the current match snapshot gave a page.

    Kept separate from `matched_urls_by_event` because the counts and the
    linked/unlinked filter need only membership, and pulling every URL to
    answer "how many" would be wasteful on a page that shows fourteen rows.
    """
    snapshot = EventPublicMatchSnapshot.objects.filter(is_current=True).only("id").first()
    if snapshot is None:
        return frozenset()
    return frozenset(
        EventPublicMatch.objects.filter(
            snapshot=snapshot, decision=MatchDecision.MATCHED, resource__isnull=False
        ).values_list("event_id", flat=True)
    )


def resolve(item, matched: dict[str, str]) -> PublicLink:
    """The effective link for one item, given the matched set."""
    workbook_url = (item.public_url or "").strip()
    if workbook_url:
        return PublicLink(workbook_url, WORKBOOK)
    url = matched.get(item.event_id, "")
    return PublicLink(url, MATCHED) if url else PublicLink()


def attach_public_links(items) -> list:
    """Give every item a `.public_link`, in one query for the whole page.

    Deliberately a list rather than a generator: a template iterates the rows
    more than once, and a generator would silently render an empty second pass.
    """
    rows = list(items)
    if not rows:
        return rows
    matched = matched_urls_by_event()
    for item in rows:
        item.public_link = resolve(item, matched)
    return rows
