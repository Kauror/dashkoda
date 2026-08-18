"""Which sends `/otsepostitused/` is about, decided once.

The same shape as `apps/news/periods.py`'s publication window, and the same
kind of question: `periood=90` means a campaign's `completed_at` falls inside
the last ninety days, however long ago it was drafted or however much it has
been read since. It is a publication window here too — `Kõik` is every
completed send this account has ever sent, not a measurement window with an
implicit "since collection began".

Unlike news, there is no undated-row problem: `SmailyCampaign.completed_at` is
set the moment Smaily finishes a send, and a campaign with none has not
completed and is not in `campaign_queryset` at all. So every period here,
`Kõik` included, is unambiguous — there is no population a window could
silently exclude.

Own module rather than an import from `apps/news/periods.py`: the two pages
belong to different apps, and a page under `apps/visibility` importing from
`apps/news` would run the ownership backwards — `apps/news/page.py` already
imports GA4 coverage *from* `apps/visibility`, not the other way around.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import quote

from django.utils import timezone

from apps.core.query_state import parse_iso_date

#: The query parameters this page reads.
PARAM_PERIOD = "periood"
PARAM_FROM = "alates"
PARAM_TO = "kuni"

#: The custom range's own key.
CUSTOM_KEY = "kohandatud"


@dataclass(frozen=True)
class MailingsPeriod:
    """One offered window.

    `days` is `None` for `Kõik`, which is the only option whose extent is a
    property of the send history rather than of the choice.
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


PERIODS: tuple[MailingsPeriod, ...] = (
    MailingsPeriod(key="30", label="30 päeva", days=30),
    MailingsPeriod(key="90", label="90 päeva", days=90),
    MailingsPeriod(key="1a", label="1 aasta", days=365),
    MailingsPeriod(key="koik", label="Kõik", days=None),
    MailingsPeriod(key=CUSTOM_KEY, label="Kohandatud", days=None),
)

DEFAULT_PERIOD = PERIODS[0]

_BY_KEY = {period.key: period for period in PERIODS}


@dataclass(frozen=True)
class ResolvedMailingsPeriod:
    """A chosen period with its boundaries worked out.

    `start` and `end` are `None` together only for `Kõik`.
    """

    period: MailingsPeriod
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

        `[start 00:00, end+1 day 00:00)` in the application's timezone, so a
        campaign completed at 23:40 on the last day is inside the window —
        the same boundary rule `apps/news/periods.py` uses, for the same
        reason: a naive `completed_at__lte=<date>` compares against midnight.
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


def resolve_period(
    raw_period: str | None,
    raw_from: str | None = None,
    raw_to: str | None = None,
    *,
    today: date | None = None,
) -> ResolvedMailingsPeriod:
    """The window a request asked for. Never raises.

    Follows `apps/news/periods.py::resolve_period` exactly: a named preset
    wins outright, reversed custom dates are swapped rather than refused, and
    a half-filled pair keeps the other end at the natural boundary.
    """
    today = today or timezone.localdate()
    key = (raw_period or "").strip()
    start = parse_iso_date(raw_from)
    end = parse_iso_date(raw_to)

    named = _BY_KEY.get(key)
    wants_custom = key == CUSTOM_KEY or (named is None and (start is not None or end is not None))

    if wants_custom:
        if start is None and end is None:
            start = today - timedelta(days=DEFAULT_PERIOD.days - 1)
            end = today
        elif start is None:
            start = None
        elif end is None:
            end = max(today, start)
        if start is not None and end is not None and end < start:
            start, end = end, start
        return ResolvedMailingsPeriod(period=_BY_KEY[CUSTOM_KEY], start=start, end=end)

    period = named if named is not None and not named.is_custom else DEFAULT_PERIOD
    if period.is_all:
        return ResolvedMailingsPeriod(period=period, start=None, end=None)
    return ResolvedMailingsPeriod(
        period=period, start=today - timedelta(days=period.days - 1), end=today
    )


def build_query(
    *,
    period_key: str,
    newsletter: str = "",
    search: str = "",
    page: int | None = None,
    start: date | None = None,
    end: date | None = None,
) -> str:
    """One URL's worth of state, assembled from validated values only."""
    parts = [f"{PARAM_PERIOD}={quote(period_key)}"]
    if period_key == CUSTOM_KEY:
        if start is not None:
            parts.append(f"{PARAM_FROM}={start:%Y-%m-%d}")
        if end is not None:
            parts.append(f"{PARAM_TO}={end:%Y-%m-%d}")
    if newsletter:
        parts.append(f"uudiskiri={quote(newsletter)}")
    if search:
        parts.append(f"otsi={quote(search)}")
    if page and page > 1:
        parts.append(f"lk={page}")
    return "&".join(parts)


@dataclass(frozen=True)
class MailingsPeriodOption:
    """One period chip, carrying whatever else is in force."""

    period: MailingsPeriod
    is_active: bool
    query: str


def period_options(
    active: ResolvedMailingsPeriod, *, newsletter: str = "", search: str = ""
) -> tuple[MailingsPeriodOption, ...]:
    """Every period, each linking to itself with the rest of the state kept."""
    options = []
    for period in PERIODS:
        carries_dates = period.is_custom and active.period.is_custom
        options.append(
            MailingsPeriodOption(
                period=period,
                is_active=period.key == active.key,
                query=build_query(
                    period_key=period.key,
                    newsletter=newsletter,
                    search=search,
                    start=active.start if carries_dates else None,
                    end=active.end if carries_dates else None,
                ),
            )
        )
    return tuple(options)


__all__ = [
    "CUSTOM_KEY",
    "DEFAULT_PERIOD",
    "PARAM_FROM",
    "PARAM_PERIOD",
    "PARAM_TO",
    "PERIODS",
    "MailingsPeriod",
    "MailingsPeriodOption",
    "ResolvedMailingsPeriod",
    "build_query",
    "period_options",
    "resolve_period",
]
