"""Which articles the news archive is about, decided once.

**The period here is a publication window, and that is the whole point of this
module.** On Nähtavus a period is a *measurement* window — "which pages were read
during these ninety days", whoever published them and whenever. Here it is a
*publication* window — "which articles did the Chamber publish during these
ninety days", however much they were read since. The two pages ask different
questions of the same GA4 facts, and the day someone quietly resolves one into
the other is the day both answers become wrong without either page changing.

So `periood=90` on `/uudised/` means `published_at` inside the last ninety days.
It never means "views received in the last ninety days".

Three rules the view is not allowed to hold:

- **a window is inclusive of both its days.** Publication dates are stored as
  moments, so a naive `published_at__lte=<date>` compares against midnight and
  drops everything published after breakfast on the last day. The boundaries are
  built here, as timezone-aware moments in the application's own zone;
- **unreadable input is not an error.** A hand-typed URL, a reversed range, a
  half-filled pair of date fields and no input at all each resolve to a window
  that can be queried, because a stale bookmark should render a page rather than
  a stack trace;
- **an undated article belongs to no window.** The catalogue is mostly built
  from public pages, which do not reliably carry a publication date, and
  `apps/news/discovery.py` refuses to invent one. An article DashKoda cannot
  date cannot honestly be said to have been published in March, so it appears
  only under `Kõik` — where the claim is "everything catalogued", not
  "everything published in a period".

Nothing here reads the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import quote

from django.utils import timezone

from apps.core.query_state import (  # noqa: F401 - re-exported for the view
    parse_iso_date,
    parse_page,
)
from apps.core.query_state import (
    parse_search as core_parse_search,
)
from apps.core.query_state import (
    parse_sort as core_parse_sort,
)

#: The query parameters this page reads.
PARAM_PERIOD = "periood"
PARAM_FROM = "alates"
PARAM_TO = "kuni"
PARAM_SORT = "sort"
PARAM_PAGE = "lk"
PARAM_SEARCH = "otsing"
PARAM_CATEGORY = "kategooria"

#: The custom range's own key. It is a period like the others as far as the
#: chips are concerned, but its dates come from the two fields rather than from
#: a length.
CUSTOM_KEY = "kohandatud"

#: How long a search term may be. It only ever reaches the ORM as a parameter,
#: never as SQL; the cap is here so a multi-megabyte query string cannot become
#: a multi-megabyte `LIKE`.
MAX_SEARCH_LENGTH = 120


@dataclass(frozen=True)
class NewsPeriod:
    """One offered publication window.

    `days` is `None` for `Kõik`, which is the only option whose extent is a
    property of the catalogue rather than of the choice.
    """

    key: str
    label: str
    days: int | None

    @property
    def is_all(self) -> bool:
        return self.days is None and self.key != CUSTOM_KEY

    @property
    def is_custom(self) -> bool:
        return self.key == CUSTOM_KEY


PERIODS: tuple[NewsPeriod, ...] = (
    NewsPeriod(key="30", label="30 päeva", days=30),
    NewsPeriod(key="90", label="90 päeva", days=90),
    NewsPeriod(key="1a", label="1 aasta", days=365),
    NewsPeriod(key="koik", label="Kõik", days=None),
    NewsPeriod(key=CUSTOM_KEY, label="Kohandatud", days=None),
)

DEFAULT_PERIOD = PERIODS[0]

_BY_KEY = {period.key: period for period in PERIODS}


@dataclass(frozen=True)
class ResolvedPeriod:
    """A chosen period with its boundaries worked out.

    `start` and `end` are `None` together only for `Kõik`, which is not a window
    at all: it is the absence of one. `includes_undated` follows from that and is
    stated rather than re-derived, because it is the single fact that decides
    whether 1 194 undated catalogue rows are part of the answer.
    """

    period: NewsPeriod
    start: date | None
    end: date | None

    @property
    def key(self) -> str:
        return self.period.key

    @property
    def label(self) -> str:
        return self.period.label

    @property
    def is_windowed(self) -> bool:
        return self.start is not None or self.end is not None

    @property
    def includes_undated(self) -> bool:
        """Only `Kõik` can honestly hold an article with no publication date."""
        return not self.is_windowed

    @property
    def query(self) -> str:
        """The query fragment that reproduces this period exactly."""
        if self.period.is_custom and self.start and self.end:
            return (
                f"{PARAM_PERIOD}={CUSTOM_KEY}"
                f"&{PARAM_FROM}={self.start:%Y-%m-%d}"
                f"&{PARAM_TO}={self.end:%Y-%m-%d}"
            )
        return f"{PARAM_PERIOD}={self.key}"

    def bounds(self) -> tuple[datetime | None, datetime | None]:
        """The window as two aware moments, half-open at the top.

        `[start 00:00, end+1 day 00:00)` in the application's timezone, so an
        article published at 16:20 on the last day is inside the window. The
        obvious `published_at__lte=end` is the bug this replaces: it compares a
        moment against midnight and silently drops most of the final day.
        """
        zone = timezone.get_current_timezone()
        lower = (
            timezone.make_aware(datetime.combine(self.start, time.min), zone)
            if self.start is not None
            else None
        )
        upper = (
            timezone.make_aware(datetime.combine(self.end + timedelta(days=1), time.min), zone)
            if self.end is not None
            else None
        )
        return lower, upper


#: How the archive is ordered.
SORT_NEWEST = "uusimad"
SORT_VIEWS = "vaadatud"
SORT_KEYS = (SORT_NEWEST, SORT_VIEWS)
SORT_LABELS = {SORT_NEWEST: "Uusimad", SORT_VIEWS: "Enim vaadatud"}


def parse_sort(raw: str | None) -> str:
    """The ordering asked for, or newest-first."""
    return core_parse_sort(raw, allowed=SORT_KEYS, default=SORT_NEWEST)


def parse_search(raw: str | None) -> str:
    """The search term, bounded to what this archive's index is worth."""
    return core_parse_search(raw, limit=MAX_SEARCH_LENGTH)


def resolve_period(
    raw_period: str | None,
    raw_from: str | None = None,
    raw_to: str | None = None,
    *,
    today: date | None = None,
) -> ResolvedPeriod:
    """The publication window a request asked for. Never raises.

    A named preset wins outright, so `?periood=30` means thirty days whatever
    else is in the query string. A custom range is what the two date fields
    submit, and is reached either by naming it or simply by sending a date.

    Reversed dates are swapped rather than refused: a reader who filled the
    fields backwards asked for that span, not for a 400. A half-filled pair
    keeps the other end at the natural boundary — today for a missing end, and
    for a missing start the whole catalogue, since the archive has no fixed
    beginning to count back from.
    """
    today = today or timezone.localdate()
    key = (raw_period or "").strip()
    start = parse_iso_date(raw_from)
    end = parse_iso_date(raw_to)

    named = _BY_KEY.get(key)
    wants_custom = key == CUSTOM_KEY or (named is None and (start is not None or end is not None))

    if wants_custom:
        if start is None and end is None:
            # `Kohandatud` chosen but nothing filled in yet: offer the default
            # window's dates rather than an empty pair, so the fields open on
            # something the reader can adjust.
            start = today - timedelta(days=DEFAULT_PERIOD.days - 1)
            end = today
        elif start is None:
            # Everything up to the given day. There is no catalogue-wide
            # earliest date to count back from — most rows have no date at all —
            # so the open end is genuinely open.
            start = None
        elif end is None:
            end = max(today, start)
        if start is not None and end is not None and end < start:
            start, end = end, start
        return ResolvedPeriod(period=_BY_KEY[CUSTOM_KEY], start=start, end=end)

    period = named if named is not None and not named.is_custom else DEFAULT_PERIOD
    if period.is_all:
        return ResolvedPeriod(period=period, start=None, end=None)
    return ResolvedPeriod(period=period, start=today - timedelta(days=period.days - 1), end=today)


def build_query(
    *,
    period_key: str,
    sort: str,
    search: str = "",
    category: str = "",
    page: int | None = None,
    start: date | None = None,
    end: date | None = None,
    carried: str = "",
) -> str:
    """One URL's worth of state, assembled from validated values only.

    Every control on the page links through here, which is what makes the
    controls compose: changing the window keeps the ordering and the search,
    paging keeps all three. The alternative — copying `request.GET` and editing
    one key — carries whatever else was in the URL, including keys this page
    does not understand and a `lk=7` that no longer exists.

    `carried` is the newsletter section's own state, which shares this page but
    not this vocabulary. It arrives already built from
    `apps.visibility.newsletter_page`, is appended untouched, and is what stops a
    period chip from clearing the newsletter the reader picked. It is still not
    `request.GET`: only a section that owns its parameters may hand one over.
    """
    parts = [f"{PARAM_PERIOD}={quote(period_key)}"]
    if period_key == CUSTOM_KEY:
        if start is not None:
            parts.append(f"{PARAM_FROM}={start:%Y-%m-%d}")
        if end is not None:
            parts.append(f"{PARAM_TO}={end:%Y-%m-%d}")
    if sort != SORT_NEWEST:
        parts.append(f"{PARAM_SORT}={quote(sort)}")
    if search:
        parts.append(f"{PARAM_SEARCH}={quote(search)}")
    if category:
        parts.append(f"{PARAM_CATEGORY}={quote(category)}")
    if page and page > 1:
        parts.append(f"{PARAM_PAGE}={page}")
    if carried:
        parts.append(carried)
    return "&".join(parts)


@dataclass(frozen=True)
class PeriodOption:
    """One period chip, carrying whatever else is in force."""

    period: NewsPeriod
    is_active: bool
    query: str


def period_options(
    active: ResolvedPeriod, *, sort: str, search: str, category: str = "", carried: str = ""
) -> tuple[PeriodOption, ...]:
    """Every period, each linking to itself with the rest of the state kept.

    Changing the window must not silently discard a search or an ordering: the
    reader narrowed the question once and is narrowing it again, not starting
    over.
    """
    options = []
    for period in PERIODS:
        # The custom chip carries the resolved dates when it is already the
        # active one, so clicking away and back does not lose them.
        carries_dates = period.is_custom and active.period.is_custom
        options.append(
            PeriodOption(
                period=period,
                is_active=period.key == active.key,
                query=build_query(
                    period_key=period.key,
                    sort=sort,
                    search=search,
                    category=category,
                    start=active.start if carries_dates else None,
                    end=active.end if carries_dates else None,
                    carried=carried,
                ),
            )
        )
    return tuple(options)


__all__ = [
    "CUSTOM_KEY",
    "DEFAULT_PERIOD",
    "MAX_SEARCH_LENGTH",
    "PARAM_FROM",
    "PARAM_PAGE",
    "PARAM_PERIOD",
    "PARAM_CATEGORY",
    "PARAM_SEARCH",
    "PARAM_SORT",
    "PARAM_TO",
    "PERIODS",
    "SORT_KEYS",
    "SORT_LABELS",
    "SORT_NEWEST",
    "SORT_VIEWS",
    "NewsPeriod",
    "PeriodOption",
    "ResolvedPeriod",
    "build_query",
    "parse_iso_date",
    "parse_page",
    "parse_search",
    "parse_sort",
    "period_options",
    "resolve_period",
]
