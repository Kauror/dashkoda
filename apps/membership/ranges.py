"""How much history a membership trend draws, decided once for both pages.

The overview card and the Liikmeskond page each let a reader choose a window,
and before this module existed they had no vocabulary in common: the card drew a
fixed 365 days it never named, and the page offered "Viimased 5 aastat" against
"Kogu ajalugu". Two controls, two sets of words, one dataset.

The control is a pair of plain date fields — `alates` and `kuni` — submitted by
an ordinary GET form. It used to be a row of fixed-window buttons submitting
`?vahemik=`; those keys are still honoured so a stale bookmark keeps meaning
what it meant, but they are a spelling of a window, not the vocabulary. The
vocabulary is two dates.

Three rules make a window honest, and none of them belongs in a view:

- **the default window is measured from the newest observation, not from
  today.** The board report arrives when it arrives; anchoring to today would
  let a report four days late shorten the window by four days and silently drop
  its oldest point;
- **a window is clamped to the history.** The fields advertise the span with
  `min`/`max`, but attributes are advice and a URL is typed by hand, so whatever
  arrives is folded back inside the observations. The control cannot be used to
  ask for an unbounded or arbitrary query;
- **unreadable input is not an error.** A malformed date, an unknown legacy key
  and no input at all all end at a window that can be drawn, so a stale bookmark
  or a typed URL still renders the page.

Nothing here reads the database. A caller passes in the span it already knows
from its own selectors, which is what keeps this module testable without
PostgreSQL.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date

# The names the two date fields submit under, on both pages.
PARAM_FROM = "alates"
PARAM_TO = "kuni"

# The name the retired button control submitted under. Still read, never
# rendered: a bookmarked `?vahemik=24` keeps drawing the two years it always
# drew, it simply arrives as dates in the fields now.
LEGACY_PARAM = "vahemik"

# What each legacy key meant, in months back from the newest observation.
# `None` is the whole history — deliberately not a very large number of months,
# because "everything there is" and "the last two hundred years" are different
# statements and only the first one is true.
LEGACY_WINDOW_MONTHS: dict[str, int | None] = {
    "6": 6,
    "12": 12,
    "24": 24,
    "36": 36,
    "60": 60,
    "koik": None,
}

# What each page opens on when a reader has chosen nothing. The card's default
# was asked for outright: the last six months. The page keeps the five years it
# already drew — adding a finer control is not a reason to change what a reader
# who chooses nothing is shown.
CARD_DEFAULT_MONTHS = 6
PAGE_DEFAULT_MONTHS = 60


@dataclass(frozen=True)
class DateWindow:
    """One resolved window: two dates, both inside the history."""

    start: date
    end: date


def months_before(day: date, months: int) -> date:
    """The same day of the month, `months` earlier, clamped to a real date.

    Clamping matters at one boundary and is invisible everywhere else: one month
    before 31 March is 28 or 29 February, not a date that does not exist.
    """
    total = (day.year * 12 + day.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def parse_iso_date(raw: str | None) -> date | None:
    """The date a form field submitted, or `None` for anything else.

    A date input submits `YYYY-MM-DD` and nothing but, so that is the one shape
    read. Anything else — an empty field, a hand-typed URL, an injection
    attempt — is not a date and resolves to "no date given" rather than to an
    error page.
    """
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def offers_choice(*, earliest: date | None, latest: date | None) -> bool:
    """Whether there is a choice worth rendering a control for.

    A history of one date has one drawable window; a control over it would be
    two fields that cannot change anything, which reads as a control that is
    broken.
    """
    return earliest is not None and latest is not None and earliest < latest


def resolve_window(
    raw_from: str | None,
    raw_to: str | None,
    *,
    earliest: date | None,
    latest: date | None,
    legacy_key: str | None = None,
    default_months: int,
) -> DateWindow | None:
    """The window the request asked for, folded inside the history.

    Named `resolve` rather than `parse` because it never fails. In order:

    - no history at all resolves to `None`, and the caller renders its empty
      state rather than a control over nothing;
    - two readable dates are taken as given, swapped if reversed — a reader who
      filled the fields backwards asked for that span, not for an error;
    - one readable date keeps the other side of the default window, so touching
      a single field never silently moves both ends;
    - no readable date falls back to the legacy `?vahemik=` key when one
      arrived, and otherwise to the page's default months, measured back from
      the newest observation;
    - finally both ends are clamped into the observation span, so nothing a URL
      can say produces a query the history cannot answer.
    """
    if earliest is None or latest is None:
        return None

    start = parse_iso_date(raw_from)
    end = parse_iso_date(raw_to)

    if start is None and end is None:
        if legacy_key in LEGACY_WINDOW_MONTHS:
            months = LEGACY_WINDOW_MONTHS[legacy_key]
        else:
            months = default_months
        start = earliest if months is None else months_before(latest, months)
        end = latest
    else:
        if end is None:
            end = latest
        if start is None:
            start = months_before(end, default_months)
        if end < start:
            start, end = end, start

    start = min(max(start, earliest), latest)
    end = min(max(end, earliest), latest)
    return DateWindow(start=start, end=end)
