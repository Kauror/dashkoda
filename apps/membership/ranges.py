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

# What the overview card opens on when a reader has chosen nothing.
#
# Six, once, because six was asked for outright. Twelve now, for the reason the
# page moved to twelve and for one the page does not have: the card and the page
# draw the same two series, and a reader who looks at the card and then opens the
# page has to be looking at the same stretch of history. Two different default
# windows made the same line look like two different stories.
#
# Deliberately the same number as `PAGE_DEFAULT_MONTHS` rather than a reference
# to it. They answer for different surfaces and either may be asked to change on
# its own; what must not happen is one of them drifting silently.
CARD_DEFAULT_MONTHS = 12

# The page opens on the last twelve months.
#
# It opened on sixty, which drew five years of an annual cycle at once: the paid
# line dives every February and recovers by December, so five repetitions of
# that swing squeezed the movement of the current year into a fifth of the plot.
# A year shows one cycle, which is the shape a reader is actually comparing
# against.
#
# Counted back from the newest observation rather than from today, so the window
# rolls forward on its own: when a report arrives the window ends on it and
# starts twelve months earlier, with no date anywhere to keep up to date.
PAGE_DEFAULT_MONTHS = 12


@dataclass(frozen=True)
class DateWindow:
    """One resolved window: two dates, both inside the history."""

    start: date
    end: date


def window_start(end: date, months: int) -> date:
    """Where a window of `months` months ending in `end`'s month begins.

    The first day of the month `months - 1` earlier, so "the last twelve months"
    means twelve calendar months — July 2025 through June 2026 — and draws
    twelve monthly points.

    The same day of the month a year earlier, which is what this used to be,
    reaches back into June 2025 and picks up that month's report as well. The
    card then drew thirteen points and labelled itself `viimased 13 kuud`, under
    a control that offers `1 aasta`. The window was a year long and the chart
    was not a year of reports, which is the mismatch a reader actually sees.
    """
    total = (end.year * 12 + end.month - 1) - (months - 1)
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)


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
        start = earliest if months is None else window_start(latest, months)
        end = latest
    else:
        if end is None:
            end = latest
        if start is None:
            start = window_start(end, default_months)
        if end < start:
            start, end = end, start

    start = min(max(start, earliest), latest)
    end = min(max(end, earliest), latest)
    return DateWindow(start=start, end=end)


# --------------------------------------------------------------------------
# Presets
#
# The control is still a pair of dates. A preset is a shortcut that fills them
# in, not a third vocabulary: each one resolves to `alates` and `kuni` and links
# to a URL carrying exactly those, so a shared link says what it shows and a
# bookmark keeps meaning what it meant.
#
# The suppression rule from the top of this module applies here too, and matters
# more with buttons than it did with fields: two presets drawing the identical
# line invite a reader to believe the second one failed. A preset is offered only
# when it covers less history than there is; the first that covers all of it is
# offered as "Kõik" and everything longer is left out.
# --------------------------------------------------------------------------

PRESET_MONTHS: tuple[tuple[str, int], ...] = (
    ("1 aasta", 12),
    ("3 aastat", 36),
    ("5 aastat", 60),
)

PRESET_ALL = "Kõik"


@dataclass(frozen=True)
class RangePreset:
    """One offered window, and whether it is the one being drawn."""

    label: str
    window: DateWindow
    is_active: bool

    @property
    def query(self) -> str:
        """The query string this preset links to."""
        return f"{PARAM_FROM}={self.window.start:%Y-%m-%d}&{PARAM_TO}={self.window.end:%Y-%m-%d}"


def range_presets(
    *,
    earliest: date | None,
    latest: date | None,
    active: DateWindow | None,
) -> tuple[RangePreset, ...]:
    """The windows worth offering for a history running `earliest`–`latest`.

    Returns nothing at all when the history cannot fill even the shortest
    preset, because a row of buttons that all draw the same line is a control
    that does not control anything.
    """
    if earliest is None or latest is None or earliest == latest:
        return ()

    presets: list[RangePreset] = []
    for label, months in PRESET_MONTHS:
        start = window_start(latest, months)
        if start <= earliest:
            # This preset reaches past the beginning, so it and everything
            # longer draw the whole history. "Kõik" says that honestly.
            break
        presets.append(
            RangePreset(
                label=label,
                window=DateWindow(start=start, end=latest),
                is_active=active is not None and active.start == start and active.end == latest,
            )
        )

    whole = DateWindow(start=earliest, end=latest)
    presets.append(
        RangePreset(
            label=PRESET_ALL,
            window=whole,
            is_active=active is not None and active.start == earliest and active.end == latest,
        )
    )
    return tuple(presets)
