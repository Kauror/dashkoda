"""What the Sündmused domain tells the main dashboard.

The Kaasamine pillar's figures, at the programme's own grain, plus the two
things this domain thinks a manager should be told without opening its page.

## The grain is the event, not the occurrence

`EventProgrammeSnapshot.canonical_event_count` counts **programme events**: one
record is one event the Chamber ran, not one occurrence of a repeating series
and not one calendar day. The workbook's occurrence sheet exists and is
deliberately not an analytical source here, exactly as it is not one on the
Sündmused page. The pillar's label therefore says `sündmusi` and never
`toimumiskordi`, because those are different numbers and only one of them is
this one.

## No registrations, on purpose

The Sündmused page can join Commerce to the programme, and it does so on one
focus where the join's own coverage report is displayed beside it. This pillar
does not, for two reasons that both point the same way:

- the join is gated on that page because it is neither cheap nor complete, and
  an executive figure carrying a silent coverage caveat is worse than no figure;
- registration units are Commerce activity, and the overview's Digiteenused
  pillar is built from Commerce. Showing them here as well would present the
  same rows as two separate contributions to two separate pillars.

So Kaasamine is the **programme** and Digiteenused is the **shop minus event
registrations**. The two pillars share no row. What this costs is stated as a
limitation rather than papered over: the overview does not claim to show how
many people registered, and it never claims to show how many attended, which is
not a fact this application holds at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from apps.core.executive import DomainSignal, SignalDirection, SignalPriority
from apps.core.formatting import integer, percent

from .analytics import (
    count_completed_in_year,
    count_year_to_date,
    delivery_mode_distribution,
    population,
)
from .public_links import attach_public_links
from .selectors import (
    NEAR_TERM_DAYS,
    EventProgrammeSummary,
    count_events_starting_within,
    get_upcoming_programme_events,
)

#: How many upcoming events the shared timeline may take from this domain, and
#: the same list the unlinked-event signal is computed over. One read, two uses.
TIMELINE_LIMIT = 8


@dataclass(frozen=True)
class EventsExecutive:
    """The Kaasamine pillar's figures, all at the programme's event grain."""

    #: Events started 1 January → today, and the same span a year earlier.
    events_ytd: int | None = None
    events_ytd_previous: int | None = None
    #: Events beginning inside the near-term horizon.
    starting_soon: int | None = None
    completed_ytd: int | None = None
    #: The delivery mode the current year's programme used most, and its share.
    top_delivery_mode: str = ""
    top_delivery_share_pct: int | None = None
    #: The next scheduled events, public links already attached, soonest first.
    #: One bounded read serving four consumers: the pillar fact, the unlinked
    #: signal, the "coming interest" panel and the shared timeline.
    upcoming: tuple = ()
    #: The workbook export's own refresh moment.
    observed_at: object = None

    signals: tuple[DomainSignal, ...] = ()

    @property
    def has_headline(self) -> bool:
        return self.events_ytd is not None

    @property
    def next_event(self):
        """The soonest scheduled event. The overview's panel shows this rather
        than a most-viewed event, because a completed event cannot be what is
        coming next."""
        return self.upcoming[0] if self.upcoming else None

    @property
    def change(self) -> int | None:
        if self.events_ytd is None or self.events_ytd_previous is None:
            return None
        return self.events_ytd - self.events_ytd_previous

    @property
    def change_pct(self) -> float | None:
        """`None` against a zero baseline, where a ratio would mean nothing."""
        if self.change is None or not self.events_ytd_previous:
            return None
        return self.change / self.events_ytd_previous * 100.0

    @property
    def meaning(self) -> str:
        """The like-for-like sentence, without the opener it used to carry.

        "Sündmusi on sama ajaks" repeated what the headline already says, and
        the board struck it. The zero-baseline branch keeps the count in the
        sentence, because a comparison with no percentage needs something
        concrete to hang on.
        """
        if not self.has_headline or self.change is None:
            return ""
        if self.events_ytd_previous == 0:
            return (
                "Eelmisel aastal ei olnud selleks ajaks ühtegi sündmust; "
                f"tänavu on {integer(self.events_ytd)}."
            )
        if self.change == 0:
            return "Täpselt sama palju kui eelmisel aastal samaks ajaks."
        word = "rohkem" if self.change > 0 else "vähem"
        return f"{percent(abs(self.change_pct))} {word} kui eelmisel aastal."


def get_events_executive(summary: EventProgrammeSummary) -> EventsExecutive:
    """Shape the pillar from a summary the caller already read."""
    snapshot = summary.snapshot
    if snapshot is None:
        return EventsExecutive()

    today = timezone.localdate()
    upcoming = attach_public_links(
        list(get_upcoming_programme_events(snapshot, limit=TIMELINE_LIMIT))
    )
    mode, share = _top_delivery_mode(snapshot, year=today.year)
    return EventsExecutive(
        events_ytd=count_year_to_date(snapshot, year=today.year, today=today),
        events_ytd_previous=count_year_to_date(snapshot, year=today.year - 1, today=today),
        starting_soon=count_events_starting_within(snapshot),
        completed_ytd=count_completed_in_year(snapshot, year=today.year, today=today),
        top_delivery_mode=mode,
        top_delivery_share_pct=share,
        # `get_upcoming_programme_events` orders by start date, so the first
        # row is the soonest and the timeline can clip without re-sorting.
        upcoming=tuple(upcoming),
        observed_at=summary.observed_at,
        signals=_signals(upcoming, observed_at=summary.observed_at),
    )


def _top_delivery_mode(snapshot, *, year: int) -> tuple[str, int | None]:
    """The current year's most-used delivery mode, and its share.

    The unclassified row is excluded from the contest but **not** from the
    share's denominator, which `Category.share` already takes against the whole
    population. So a year that classified a third of its events reports the
    leading mode with the modest share it genuinely holds, rather than a share
    of the classified subset dressed up as a share of the programme.

    Empty when nothing is classified: a "most common" drawn from an
    unclassified population names whichever label the coverage gap left
    standing.
    """
    distribution = delivery_mode_distribution(population(snapshot, year=year))
    rows = [row for row in distribution.rows if row.count and not row.is_unknown]
    if not rows:
        return "", None
    leader = max(rows, key=lambda row: row.count)
    return leader.label, leader.share_pct


def _signals(upcoming, *, observed_at) -> tuple[DomainSignal, ...]:
    """One: upcoming events the public site has no page for.

    Computed over the same bounded list the timeline draws, so it costs nothing
    extra and cannot describe a different set of events than the rows below it.

    It is a business signal rather than a data-quality note: an event a member
    cannot find a page for is a gap in the Chamber's own communication, not a
    defect in a feed.
    """
    unlinked = [item for item in upcoming if not item.public_link.url]
    if not unlinked:
        return ()
    return (
        DomainSignal(
            key="events-unlinked",
            headline=(
                f"{integer(len(unlinked))} tulemas olevat sündmust ei ole seotud "
                "avaliku sündmuse lehega."
            ),
            # No evidence sentence. It restated the headline with the cohort
            # size added, and the board struck it; the headline carries the
            # count and the Sündmused page carries the detail.
            evidence="",
            priority=SignalPriority.NOTABLE,
            direction=SignalDirection.NONE,
            as_of=observed_at.date() if hasattr(observed_at, "date") else observed_at,
        ),
    )


def get_timeline_events(executive: EventsExecutive, *, within_days: int):
    """Dated upcoming events for the shared 30-day timeline.

    Reads the bounded, link-attached list the executive summary already holds
    — the same rows the pillar and the unlinked-event signal were computed
    over, so the timeline cannot describe a different set of events — and
    clips it to the horizon here. No query of its own.
    """
    horizon = timezone.localdate() + timedelta(days=within_days)
    return [item for item in executive.upcoming if item.start_date and item.start_date <= horizon]


__all__ = [
    "NEAR_TERM_DAYS",
    "TIMELINE_LIMIT",
    "EventsExecutive",
    "get_events_executive",
    "get_timeline_events",
]
