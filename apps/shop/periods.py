"""Which period the E-pood pages are about, decided once.

**The presets are anchored to the dataset, not to the wall clock.** That is the
one thing this module does differently from `apps/news/periods.py`, and it
follows from the source being a manual export that stops on a stated day.

"30 päeva" against a frozen August export must mean the last thirty days *the
export covers*, not the last thirty days of the reader's calendar. Anchoring on
`timezone.localdate()` would select a window that drifts further past the data
every day and eventually selects nothing at all — a product page reading
"0 soetatud" for a month nobody has imported.

The maximum selectable end is therefore Commerce coverage end. When automation
arrives later and coverage end starts moving forward on its own, this needs no
change.

Nothing here reads the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import quote

PARAM_PERIOD = "periood"
PARAM_FROM = "alates"
PARAM_TO = "kuni"
PARAM_TYPE = "liik"
PARAM_CATEGORY = "kategooria"
PARAM_SEARCH = "otsing"
PARAM_SORT = "sort"
PARAM_PAGE = "lk"
PARAM_MEMBER = "liikmestaatus"

CUSTOM_KEY = "kohandatud"
ALL_KEY = "koik"

MAX_SEARCH_LENGTH = 120


@dataclass(frozen=True)
class ShopPeriod:
    key: str
    label: str
    days: int | None
    #: Whether this belongs in the first row of controls. Seven equal-weight
    #: buttons is a menu, not a control: the reader has to read all of them
    #: before choosing any. Five is a glance.
    is_primary: bool = True

    @property
    def is_all(self) -> bool:
        return self.days is None and self.key != CUSTOM_KEY

    @property
    def is_custom(self) -> bool:
        return self.key == CUSTOM_KEY


PERIODS: tuple[ShopPeriod, ...] = (
    ShopPeriod(key="30", label="30 p", days=30),
    ShopPeriod(key="90", label="90 p", days=90),
    ShopPeriod(key="1a", label="1 a", days=365),
    ShopPeriod(key="3a", label="3 a", days=1095),
    ShopPeriod(key=ALL_KEY, label="Kõik", days=None),
    # Kept, and kept out of the first row. Five years is a rare question on a
    # dataset whose whole history is under six, and a custom range is a second
    # thought rather than a first one.
    ShopPeriod(key="5a", label="5 aastat", days=1825, is_primary=False),
    ShopPeriod(key=CUSTOM_KEY, label="Kohandatud", days=None, is_primary=False),
)

DEFAULT_PERIOD = PERIODS[2]  # 1 aasta: a shop question is rarely about a month

_BY_KEY = {period.key: period for period in PERIODS}


@dataclass(frozen=True)
class ResolvedShopPeriod:
    period: ShopPeriod
    start: date | None
    end: date | None

    @property
    def key(self) -> str:
        return self.period.key

    @property
    def label(self) -> str:
        return self.period.label

    @property
    def query(self) -> str:
        if self.period.is_custom and self.start and self.end:
            return (
                f"{PARAM_PERIOD}={CUSTOM_KEY}"
                f"&{PARAM_FROM}={self.start:%Y-%m-%d}"
                f"&{PARAM_TO}={self.end:%Y-%m-%d}"
            )
        return f"{PARAM_PERIOD}={self.key}"


def parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError, AttributeError:
        return None


def parse_search(raw: str | None) -> str:
    return (raw or "").strip()[:MAX_SEARCH_LENGTH]


def parse_page(raw: str | int | None) -> int:
    try:
        return max(int(raw), 1)
    except TypeError, ValueError:
        return 1


def parse_int_list(values) -> tuple[int, ...]:
    """Whatever of a repeated query parameter is actually an integer.

    A rotted bookmark carrying `kategooria=abc` narrows to nothing rather than
    raising; the page still renders and the reader can pick again.
    """
    out: list[int] = []
    for value in values or ():
        try:
            out.append(int(value))
        except TypeError, ValueError:
            continue
    return tuple(dict.fromkeys(out))


def resolve_period(
    raw_period: str | None,
    raw_from: str | None = None,
    raw_to: str | None = None,
    *,
    anchor: date | None,
) -> ResolvedShopPeriod:
    """The window a request asked for, anchored on the dataset. Never raises.

    `anchor` is Commerce coverage end. With no data at all it is `None`, and
    every period resolves to the open window — there is nothing to count back
    from, and inventing today as an anchor would imply data that is not there.
    """
    key = (raw_period or "").strip()
    start = parse_iso_date(raw_from)
    end = parse_iso_date(raw_to)

    named = _BY_KEY.get(key)
    wants_custom = key == CUSTOM_KEY or (named is None and (start is not None or end is not None))

    if wants_custom and anchor is not None:
        if start is None and end is None:
            start = anchor - timedelta(days=DEFAULT_PERIOD.days - 1)
            end = anchor
        elif start is None:
            start = None
        elif end is None:
            end = max(anchor, start)
        if start is not None and end is not None and end < start:
            start, end = end, start
        return ResolvedShopPeriod(period=_BY_KEY[CUSTOM_KEY], start=start, end=end)

    period = named if named is not None and not named.is_custom else DEFAULT_PERIOD
    if period.is_all or anchor is None:
        return ResolvedShopPeriod(
            period=period if period.is_all else DEFAULT_PERIOD, start=None, end=None
        )
    return ResolvedShopPeriod(
        period=period, start=anchor - timedelta(days=period.days - 1), end=anchor
    )


def build_query(
    *,
    period_key: str,
    start: date | None = None,
    end: date | None = None,
    product_type: str = "",
    categories: tuple[int, ...] = (),
    search: str = "",
    sort: str = "",
    member_status: str = "",
    page: int | None = None,
) -> str:
    """One URL's worth of validated state.

    Every control links through here, which is what makes the controls compose:
    changing the period keeps the type, the categories and the search, and
    paging keeps all of them.
    """
    parts = [f"{PARAM_PERIOD}={quote(period_key)}"]
    if period_key == CUSTOM_KEY:
        if start is not None:
            parts.append(f"{PARAM_FROM}={start:%Y-%m-%d}")
        if end is not None:
            parts.append(f"{PARAM_TO}={end:%Y-%m-%d}")
    if product_type:
        parts.append(f"{PARAM_TYPE}={quote(product_type)}")
    for term_id in categories:
        parts.append(f"{PARAM_CATEGORY}={term_id}")
    if search:
        parts.append(f"{PARAM_SEARCH}={quote(search)}")
    if sort:
        parts.append(f"{PARAM_SORT}={quote(sort)}")
    if member_status:
        parts.append(f"{PARAM_MEMBER}={quote(member_status)}")
    if page and page > 1:
        parts.append(f"{PARAM_PAGE}={page}")
    return "&".join(parts)


@dataclass(frozen=True)
class PeriodOption:
    period: ShopPeriod
    is_active: bool
    query: str


def period_options(active: ResolvedShopPeriod, **state) -> tuple[PeriodOption, ...]:
    options = []
    for period in PERIODS:
        carries_dates = period.is_custom and active.period.is_custom
        options.append(
            PeriodOption(
                period=period,
                is_active=period.key == active.key,
                query=build_query(
                    period_key=period.key,
                    start=active.start if carries_dates else None,
                    end=active.end if carries_dates else None,
                    **state,
                ),
            )
        )
    return tuple(options)


#: How a product ranking may be ordered.
SORT_UNITS = "soetatud"
SORT_VALUE = "vaartus"
SORT_VIEWS = "vaatamised"
SORT_CONVERSION = "maar"
SORT_TITLE = "nimi"
SORT_KEYS = (SORT_UNITS, SORT_VALUE, SORT_VIEWS, SORT_CONVERSION, SORT_TITLE)
#: Column headings, not sentences. The table is the place these are read, and a
#: heading of four words in a right-aligned numeric column wraps to three lines
#: at 320 pixels. What each one means is in `Andmete kohta`.
SORT_LABELS = {
    SORT_TITLE: "Toode",
    SORT_UNITS: "Soetatud",
    SORT_VALUE: "Väärtus",
    SORT_VIEWS: "Vaatamised",
    SORT_CONVERSION: "/ 100",
}

#: The order the explorer's columns appear in, which is also the order the
#: sortable headers are built in.
SORT_COLUMNS = (SORT_TITLE, SORT_UNITS, SORT_VALUE, SORT_VIEWS, SORT_CONVERSION)


def parse_sort(raw: str | None) -> str:
    value = (raw or "").strip()
    return value if value in SORT_KEYS else SORT_UNITS


__all__ = [
    "ALL_KEY",
    "CUSTOM_KEY",
    "DEFAULT_PERIOD",
    "MAX_SEARCH_LENGTH",
    "PARAM_CATEGORY",
    "PARAM_FROM",
    "PARAM_MEMBER",
    "PARAM_PAGE",
    "PARAM_PERIOD",
    "PARAM_SEARCH",
    "PARAM_SORT",
    "PARAM_TO",
    "PARAM_TYPE",
    "PERIODS",
    "SORT_COLUMNS",
    "SORT_CONVERSION",
    "SORT_KEYS",
    "SORT_LABELS",
    "SORT_TITLE",
    "SORT_UNITS",
    "SORT_VALUE",
    "SORT_VIEWS",
    "PeriodOption",
    "ResolvedShopPeriod",
    "ShopPeriod",
    "build_query",
    "parse_int_list",
    "parse_iso_date",
    "parse_page",
    "parse_search",
    "parse_sort",
    "period_options",
    "resolve_period",
]
