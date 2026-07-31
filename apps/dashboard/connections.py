"""What each data source is, how often it updates, and whether it is connected.

The dashboard has to answer "where did this number come from and how current is
it?" next to every figure, and "is this connected yet?" for the parts that are
not. Both answers used to live in whichever template happened to need them.
This module holds them once.

It deliberately holds no query of its own. Whether a source publishes data is
already decided by that module's own summary — `has_data` on the objects in
`apps/core/feeds.py` — and restating the rule here would let the two drift.

A source that is **planned** has no state to read: nothing collects it, so its
state is a fact about the product, not about the database. Saying so plainly is
the point. AGENTS.md: an empty module says so rather than filling itself with
something plausible.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# How often a connected source is refreshed. Shown beside a figure whenever two
# sources appear near each other, because "3 412 counted today" and "2 798
# reported last month" are different kinds of claim and the reader has to be
# able to tell them apart.
CADENCE_DAILY = "iga päev"
CADENCE_MONTHLY = "kord kuus"


class ConnectionState(StrEnum):
    """What can honestly be said about a source right now."""

    CONNECTED = "connected"
    STALE = "stale"
    NOT_CONNECTED = "not_connected"
    PLANNED = "planned"


_LABELS = {
    ConnectionState.CONNECTED: "Ühendatud",
    ConnectionState.STALE: "Vananenud",
    ConnectionState.NOT_CONNECTED: "Ühendamata",
    ConnectionState.PLANNED: "Lisamisel",
}

_VARIANTS = {
    ConnectionState.CONNECTED: "success",
    ConnectionState.STALE: "warning",
    ConnectionState.NOT_CONNECTED: "neutral",
    ConnectionState.PLANNED: "neutral",
}


@dataclass(frozen=True)
class Connection:
    """One source, described for display."""

    label: str
    state: ConnectionState
    cadence: str = ""
    # What the reader would see here once the source exists. Only meaningful
    # for a planned source, and never a promise about when.
    promise: str = ""

    @property
    def state_label(self) -> str:
        return _LABELS[self.state]

    @property
    def state_variant(self) -> str:
        return _VARIANTS[self.state]

    @property
    def is_connected(self) -> bool:
        return self.state in (ConnectionState.CONNECTED, ConnectionState.STALE)


def from_summary(summary, *, label: str, cadence: str = "") -> Connection:
    """Describe a wired feed from the summary that already knows its state.

    Accepts any of the per-module summaries, which all share
    `apps.core.feeds.FeedSummaryMixin`, so "stale" here means exactly what it
    means on the module's own page.
    """
    if not summary.has_data:
        state = ConnectionState.NOT_CONNECTED
    elif summary.last_sync_failed:
        state = ConnectionState.STALE
    else:
        state = ConnectionState.CONNECTED
    return Connection(label=label, state=state, cadence=cadence)


def planned(label: str, *, promise: str = "", cadence: str = "") -> Connection:
    """A source the product intends to have and does not yet collect."""
    return Connection(label=label, state=ConnectionState.PLANNED, cadence=cadence, promise=promise)


# The communication channels the design reserves a band for. None of them is
# collected: there is no analytics, mailing-list or social integration in this
# repository, and there is no field anywhere capable of holding one of these
# numbers. They are listed so the band shows the intended shape and says, for
# each slot, that nothing feeds it.
CHANNEL_STATISTICS: tuple[Connection, ...] = (
    planned("Kodulehe külastused", promise="Veebistatistika allikas."),
    planned("Uudiskirja saajad", promise="Uudiskirja saatja allikas."),
    planned("Facebooki jälgijad", promise="Sotsiaalmeedia allikas."),
    planned("LinkedIni jälgijad", promise="Sotsiaalmeedia allikas."),
    # Typographic apostrophe, not the ASCII one: Django escapes `'` to `&#x27;`,
    # and the digits inside that entity would survive a plain tag-strip and read
    # as a number on a page that is asserting it shows none.
    planned("YouTube’i tellijad", promise="Sotsiaalmeedia allikas."),
)
