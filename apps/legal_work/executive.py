"""What the Õigusloome domain tells the main dashboard.

The Õigusloome card's figures and the domain's own view of what deserves a
manager's attention. Everything here is read off the one current workbook
snapshot the Õigusloome page reads, through that page's own analytics, so the
two cannot disagree about what a year-to-date opinion count is.

## The card leads with the stock, not the flow

`open_topics` — `X teemat töös` — is what a manager can act on today, and it is
what the front page states. `sent` is still here and still compared like for
like, one row down: it is a record of work carried, and a year-to-date count is
not a description of the current state of anything.

## No topic lists

This summary used to carry `in_progress` and `recently_sent`, two seven-row
lists the overview rendered as a section of its own. That section is gone — the
front page does not reproduce half of `/oigusloome/` — and the lists went with
it rather than being left built and unread: three selector reads and a
link-resolution pass per overview render, for rows nothing displayed.
`get_open_items_by_deadline`, `get_latest_sent_items` and `present_topics` are
untouched and the Õigusloome page still calls them; what left is this module's
own call to them.

## Output is not impact

`Arvamusi välja saadetud` counts opinions Koda sent. It does not count opinions
that were accepted, provisions that changed, or influence of any kind. The
distinction matters enough that the card's own wording never uses the word
`mõju`: this figure is how much policy work the Chamber carried, and whether
that work succeeded is not in the workbook.

## Both sides of the comparison stop on the same calendar day

`analytics.sent_year_on_year` runs 1 January to the workbook's reporting date,
against 1 January to the same day a year earlier. A part-year against a finished
year is the easiest lie available to this dashboard, and it would understate the
current year for eleven months of every twelve. The comparison's cutoff is
rendered beside the figure rather than left implicit.

## A passed deadline is two different facts

`analytics.deadline_pressure` splits them and this module keeps the split. A
matter still awaiting its opinion is outstanding work and is what the critical
signal is about. A matter whose opinion has gone out and which remains open —
waiting on a committee, waiting to come into force — is ordinary process, and
counting it as late would manufacture a backlog the Chamber does not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.urls import reverse

from apps.core.executive import DomainSignal, SignalDirection, SignalPriority
from apps.core.formatting import integer, percent

from .analytics import YearOnYear, deadline_pressure, sent_year_on_year, topics_year_on_year
from .sections import SECTION_OPEN, anchor
from .selectors import LegalWorkSummary, get_upcoming_deadlines

#: The horizon the card and the critical signal both use for "soon". Seven days
#: is the workbook's own weekly rhythm and the band `deadline_pressure` already
#: counts, so the signal and the Õigusloome page's own pressure chart cannot
#: disagree about what falls inside it.
URGENT_DAYS = 7

#: How many upcoming deadlines the shared timeline may take from this domain.
#: The overview's timeline holds ten rows across two domains; asking for more
#: than this would let one busy week fill it.
TIMELINE_LIMIT = 8


@dataclass(frozen=True)
class LegalWorkExecutive:
    """The Õigusloome card's figures, all from one workbook snapshot."""

    #: Opinions sent 1 January → reporting date, and the same span a year back.
    sent: YearOnYear | None = None
    #: Matters in hand right now — a stock, not a flow.
    open_topics: int | None = None
    #: New matters belonging to the current year, by the register's grouping.
    topics_this_year: YearOnYear | None = None
    due_within_7: int | None = None
    overdue_pending: int | None = None
    #: The workbook's own reporting date. Every figure above stops here.
    reporting_date: date | None = None

    signals: tuple[DomainSignal, ...] = ()

    @property
    def has_headline(self) -> bool:
        """Whether a workbook snapshot exists at all.

        Named for the summary rather than for the card: the card's own headline
        is `open_topics`, and it renders only when that count exists. A snapshot
        with no open matters is a real state of the world and reports zero.
        """
        return self.sent is not None

    @property
    def meaning(self) -> str:
        """The year-on-year sentence, stated as the measurement.

        Without the "Arvamusi on sama kuupäevani" opener it used to carry: the
        headline above it says "arvamust sellel aastal" itself now, and the
        board struck the repeat. A baseline of zero yields no percentage, and
        the sentence then states the fact rather than inventing an infinite
        increase.
        """
        if self.sent is None:
            return ""
        change = self.sent.absolute_change
        if self.sent.previous == 0:
            return "Eelmisel aastal ei olnud sama kuupäevani ühtegi arvamust."
        if change == 0:
            return "Täpselt sama palju kui eelmisel aastal sama kuupäevani."
        word = "rohkem" if change > 0 else "vähem"
        return f"{percent(abs(self.sent.percent_change))} {word} kui eelmisel aastal."


def get_legal_work_executive(summary: LegalWorkSummary) -> LegalWorkExecutive:
    """Shape the card from a summary the caller already read.

    The summary is passed in rather than read again: the overview reads it once
    for the shell freshness row and the data-status section, and this is the
    third place that would otherwise pay for the same two indexed queries.
    """
    snapshot = summary.snapshot
    if snapshot is None:
        return LegalWorkExecutive()

    sent = sent_year_on_year(snapshot)
    pressure = deadline_pressure(snapshot)
    return LegalWorkExecutive(
        sent=sent,
        open_topics=summary.open_count,
        topics_this_year=topics_year_on_year(snapshot),
        due_within_7=pressure.due_within_7,
        overdue_pending=pressure.overdue_pending,
        reporting_date=summary.reporting_date,
        signals=_signals(pressure, reporting_date=summary.reporting_date),
    )


def _signals(pressure, *, reporting_date: date | None) -> tuple[DomainSignal, ...]:
    """At most two: what is late, and what is about to be.

    Both are counts of open matters against the workbook's reporting date, which
    is why both carry it as their as-of. Neither links to a filtered list,
    because the Õigusloome page has no section listing exactly "open and overdue"
    or exactly "open and due this week" — `Hetkel töös` is the nearest thing and
    is a superset of both. The anchor sends the reader to the list the rows live
    in and the evidence tells them what to look for, which is honest; a link
    promising a filtered view that does not exist would not be.
    """
    page = reverse("legal-work")
    signals = []

    if pressure.overdue_pending:
        signals.append(
            DomainSignal(
                key="legal-overdue-pending",
                headline=(
                    f"{integer(pressure.overdue_pending)} teemal on tähtaeg möödas "
                    "ja arvamus saatmata."
                ),
                evidence=(
                    "Avatud teemad, mille arvamuse tähtaeg jäi töövihiku seisu "
                    "kuupäevast varasemaks."
                ),
                priority=SignalPriority.CRITICAL,
                direction=SignalDirection.NONE,
                href=anchor(page, SECTION_OPEN),
                as_of=reporting_date,
            )
        )

    if pressure.due_within_7:
        signals.append(
            DomainSignal(
                key="legal-due-soon",
                headline=(
                    f"{integer(pressure.due_within_7)} tähtaega järgmise "
                    f"{URGENT_DAYS} päeva jooksul."
                ),
                evidence="Avatud teemad, mille arvamuse tähtaeg on käes selle nädala sees.",
                priority=SignalPriority.ATTENTION,
                direction=SignalDirection.NONE,
                href=anchor(page, SECTION_OPEN),
                as_of=reporting_date,
            )
        )

    return tuple(signals)


def get_timeline_deadlines(summary: LegalWorkSummary, *, within_days: int):
    """Dated upcoming deadlines for the shared 30-day timeline.

    Returns the domain's own `Deadline` objects. The overview turns them into
    rows; it does not decide which deadlines are still actionable, because
    `get_upcoming_deadlines` already excludes concluded matters and passed dates
    for reasons that belong to this domain.
    """
    if summary.snapshot is None:
        return ()
    return get_upcoming_deadlines(summary.snapshot, within_days=within_days, limit=TIMELINE_LIMIT)


__all__ = [
    "TIMELINE_LIMIT",
    "URGENT_DAYS",
    "LegalWorkExecutive",
    "get_legal_work_executive",
    "get_timeline_deadlines",
]
