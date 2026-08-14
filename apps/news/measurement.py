"""When news was **read** — the other time question on this page.

`apps/news/periods.py` owns the publication window: which articles the Chamber
published during a span. This module owns the measurement window: which pages
were read during a span, whenever they were written.

They are separate modules, separate parameters and separate controls because
they are separate questions, and the failure mode of merging them is silent. One
`30 päeva` control above a page of charts, interpreted as publication by some of
them and as readership by others, produces a screen where every number is
defensible on its own and the page as a whole means nothing. An article
published in 2024 is *absent* from `Avaldatud: 30 p` and *top of*
`Loetud: 30 p`, and both are correct.

    periood=90   → published in the last ninety days
    loetud=90    → read in the last ninety days

## The window ends where the data ends

A measurement window is anchored to GA4's **latest collected day**, not to
today. Today is never collected — a partial day publishes a figure that is wrong
by construction — so a window ending today would end in a day that has no
figures and quietly understate the most recent period every time.

## A window longer than the history is truncated, and says so

Asking for a year of readership from a property with eight months of data cannot
produce a year. The window is clipped to what exists and `is_truncated` is set,
so the interface can state the real span rather than implying eight months of
data covers twelve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import quote

from apps.visibility.ga4_selectors import Coverage

#: The query parameter this window reads. Deliberately not `periood`: that one is
#: taken, by the other question.
PARAM_READ = "loetud"


@dataclass(frozen=True)
class ReadPeriod:
    """One offered measurement window."""

    key: str
    label: str
    days: int


READ_PERIODS: tuple[ReadPeriod, ...] = (
    ReadPeriod(key="30", label="30 päeva", days=30),
    ReadPeriod(key="90", label="90 päeva", days=90),
    ReadPeriod(key="1a", label="1 aasta", days=365),
)

DEFAULT_READ_PERIOD = READ_PERIODS[0]

_BY_KEY = {period.key: period for period in READ_PERIODS}


@dataclass(frozen=True)
class ResolvedReading:
    """A measurement window with its boundaries settled against real coverage."""

    period: ReadPeriod
    start: date | None = None
    end: date | None = None
    #: Whether the window had to be clipped because the property is younger than
    #: the span asked for.
    is_truncated: bool = False

    @property
    def key(self) -> str:
        return self.period.key

    @property
    def label(self) -> str:
        return self.period.label

    @property
    def has_window(self) -> bool:
        return self.start is not None and self.end is not None

    @property
    def days(self) -> int:
        """The window's real length, which is not always the length asked for."""
        if not self.has_window:
            return 0
        return (self.end - self.start).days + 1

    @property
    def query(self) -> str:
        return f"{PARAM_READ}={quote(self.key)}"


def resolve_reading(
    raw: str | None, *, coverage: Coverage, period: ReadPeriod | None = None
) -> ResolvedReading:
    """The measurement window a request asked for, clipped to what was collected.

    Never raises: an unreadable value is the default window, because a rotted
    bookmark should render the page it names.
    """
    chosen = period or _BY_KEY.get((raw or "").strip(), DEFAULT_READ_PERIOD)
    if not coverage.has_data or coverage.latest is None or coverage.earliest is None:
        return ResolvedReading(period=chosen)

    end = coverage.latest
    start = end - timedelta(days=chosen.days - 1)
    if start < coverage.earliest:
        return ResolvedReading(period=chosen, start=coverage.earliest, end=end, is_truncated=True)
    return ResolvedReading(period=chosen, start=start, end=end)


@dataclass(frozen=True)
class ReadPeriodOption:
    """One measurement-window chip.

    `is_offered` is false for a span the property cannot fill at all. The chip is
    still rendered — a period that exists but has no data is worth seeing as
    unavailable rather than silently missing, which is the rule the Nähtavus page
    already follows.
    """

    period: ReadPeriod
    is_active: bool
    is_offered: bool
    query: str


def read_period_options(
    active: ResolvedReading, *, coverage: Coverage, state: str = ""
) -> tuple[ReadPeriodOption, ...]:
    """Every measurement window, each linking to itself with the state kept."""
    span = coverage.span_days if coverage.has_data else 0
    options = []
    for period in READ_PERIODS:
        query = f"{PARAM_READ}={quote(period.key)}"
        if state:
            query = f"{query}&{state}"
        options.append(
            ReadPeriodOption(
                period=period,
                is_active=period.key == active.key,
                # A window is offered when any of it can be filled. The shortest
                # is always offered, so a new property is never a page of
                # disabled controls.
                is_offered=span > 0,
                query=query,
            )
        )
    return tuple(options)


__all__ = [
    "DEFAULT_READ_PERIOD",
    "PARAM_READ",
    "READ_PERIODS",
    "ReadPeriod",
    "ReadPeriodOption",
    "ResolvedReading",
    "read_period_options",
    "resolve_reading",
]
