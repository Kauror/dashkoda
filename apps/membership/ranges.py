"""How much history a membership trend draws, decided once for both pages.

The overview card and the Liikmeskond page each let a reader choose a window,
and before this module existed they had no vocabulary in common: the card drew a
fixed 365 days it never named, and the page offered "Viimased 5 aastat" against
"Kogu ajalugu". Two controls, two sets of words, one dataset.

The choices, their labels, the query parameter, the validation and the rule for
which windows may be offered all live here. A page decides two things only:
which subset of the choices it shows, and which one it opens on.

Three rules make a window honest, and none of them belongs in a view:

- **the window is measured from the newest observation, not from today.** The
  board report arrives when it arrives; anchoring to today would let a report
  four days late shorten every window by four days and silently drop its oldest
  point;
- **a window the history cannot fill is not offered.** Two buttons drawing the
  identical line invite a reader to believe the second one failed. The first
  window that covers the whole history is offered — that one shows all of it —
  and everything longer is left out;
- **an unknown key is not an error.** A stale bookmark or a typed URL falls back
  to the page's default rather than raising, and the fallback is always a window
  the data can actually fill.

Nothing here reads the database. A caller passes in the span it already knows
from its own selectors, which is what keeps this module testable without
PostgreSQL.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

# The name the control submits under, on both pages.
QUERY_PARAM = "vahemik"


@dataclass(frozen=True)
class TrendRange:
    """One offered window.

    `months` is `None` for the whole history. That is deliberately not a very
    large number of months: "everything there is" and "the last two hundred
    years" are different statements, and only the first one is true.
    """

    key: str
    months: int | None
    label: str

    @property
    def is_everything(self) -> bool:
        return self.months is None

    def start_from(self, latest: date | None) -> date | None:
        """The first day the window covers, or `None` for the whole history."""
        if self.months is None or latest is None:
            return None
        return months_before(latest, self.months)


# Labelled as the board says them: months up to a year, years beyond it. A
# reader asked for "kaks aastat" and was offered "24 kuud", which is the same
# window described in the wrong unit.
RANGE_6 = TrendRange("6", 6, "6 kuud")
RANGE_12 = TrendRange("12", 12, "12 kuud")
RANGE_24 = TrendRange("24", 24, "2 aastat")
RANGE_36 = TrendRange("36", 36, "3 aastat")
RANGE_60 = TrendRange("60", 60, "5 aastat")
RANGE_ALL = TrendRange("koik", None, "Kogu ajalugu")

CHOICES: tuple[TrendRange, ...] = (RANGE_6, RANGE_12, RANGE_24, RANGE_36, RANGE_60, RANGE_ALL)

# The overview card draws a server-rendered polyline inside a card, so its
# longest window is three years — beyond that the points crowd into a smudge at
# card width. The Liikmeskond page draws the same data at full width and keeps
# the long windows the board already had.
CARD_CHOICES: tuple[TrendRange, ...] = (RANGE_6, RANGE_12, RANGE_24, RANGE_36)
PAGE_CHOICES: tuple[TrendRange, ...] = CHOICES

# What each page opens on. The card's default is the year it already drew; the
# page's is the five years it already drew. Adding finer windows is not a reason
# to change what either one shows to a reader who chooses nothing.
CARD_DEFAULT = RANGE_12
PAGE_DEFAULT = RANGE_60


def months_before(day: date, months: int) -> date:
    """The same day of the month, `months` earlier, clamped to a real date.

    Clamping matters at one boundary and is invisible everywhere else: one month
    before 31 March is 28 or 29 February, not a date that does not exist.
    """
    total = (day.year * 12 + day.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def available(
    offered: Sequence[TrendRange],
    *,
    earliest: date | None,
    latest: date | None,
) -> tuple[TrendRange, ...]:
    """The windows this history can actually fill.

    Walks the offered windows from shortest to longest and stops at the first
    one that reaches past the oldest observation — that window is included,
    because it is the one that draws the whole history, and every longer window
    would draw the identical line.

    Returns nothing when there is no history to bound, and may return a single
    window, which callers treat as "no choice to offer" rather than as a control
    with one button.
    """
    if earliest is None or latest is None:
        return ()

    chosen: list[TrendRange] = []
    for choice in offered:
        chosen.append(choice)
        start = choice.start_from(latest)
        if start is None or start <= earliest:
            break
    return tuple(chosen)


def resolve(
    key: str | None,
    *,
    available: Sequence[TrendRange],
    default: TrendRange,
) -> TrendRange:
    """The requested window, or the nearest sensible one.

    Named `resolve` rather than `parse` because it never fails: an unknown key,
    a key for a window this history cannot fill, and no key at all all end at a
    window that can be drawn.
    """
    for choice in available:
        if choice.key == key:
            return choice
    if default in available:
        return default
    return available[-1] if available else default
