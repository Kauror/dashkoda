"""How much public attention an event's own koda.ee page received.

GA4 can answer exactly one question about an event: **how many times was its
public page viewed, on the days it was measured.** Everything else people want
from it — who attended, how many people came, whether anyone registered, whether
they enjoyed it — is not in this data and is not derived here.

A page view is not a person. One reader opening a page four times is four of
these, and no figure below is ever labelled with a word that implies otherwise.

Three different time questions live here, and keeping them apart is the whole
job of this module:

============================  ====================================================
`recent_views`                the last 30 measured days. "What is getting
                              attention **now**", and only meaningful for events
                              that have not happened yet
`pre_event_views`             the 30 days ending on the event's own start date.
                              An equal-length window per event, which is the only
                              way two events months apart can be compared fairly
`total_views`                 everything GA4 has measured for that page. Never
                              "lifetime": most event pages predate collection
============================  ====================================================

Collapsing them into one "views" number is the mistake this module exists to
prevent — a page measured for three years and a page measured for three weeks
would sit in the same ranking.

**Bulk by construction.** Every window is one grouped query across the whole
population, using the same `(path, date range)` disjunction
`apps.visibility.ga4_selectors` uses for articles. A hundred events cost three
queries, not three hundred.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Q, Sum

from apps.visibility.ga4_paths import canonical_path
from apps.visibility.ga4_selectors import Coverage, current_pages, get_coverage

from .public_links import attach_public_links

#: The fair-comparison window, in days ending on the event's start date
#: inclusive. Thirty days is long enough to contain the announcement and the
#: reminder, and short enough that two events in the same season do not overlap
#: each other's windows.
PRE_EVENT_DAYS = 30

#: A second, tighter window. It reveals late interest, and it is deliberately
#: the only companion metric: five windows of the same measurement is not
#: analysis, it is a menu.
LATE_INTEREST_DAYS = 7

#: The trailing window that answers "what is being looked at now".
RECENT_DAYS = 30

#: The smallest page-view total a **rate** may be built on. Below it, one extra
#: view moves the answer by percentage points. Chosen against the real
#: programme, where the eligible events run from 4 to 1 403 views with a median
#: of 171: at fifty, 265 of 301 eligible events still qualify, so the floor
#: removes the genuinely unmeasurable without discarding the population.
MIN_VIEWS_FOR_RATE = 50

#: How many comparable events a benchmark needs. Matches
#: `analytics.MIN_SAMPLE`; the two are the same judgement about the same
#: programme.
MIN_BENCHMARK_SAMPLE = 8


@dataclass(frozen=True)
class EventAttention:
    """One event's measured web attention, with the honesty attached.

    Every count is `None` rather than `0` when it was not measured. The two look
    alike in a table and mean opposite things: `0` is "we measured, nobody came"
    and `None` is "nobody measured".
    """

    event_id: str
    path: str = ""
    start_date: date | None = None
    total_views: int | None = None
    pre_event_views: int | None = None
    late_views: int | None = None
    recent_views: int | None = None
    #: Whether GA4 covers every day of this event's 30-day pre-event window. A
    #: partial window produces no figure at all — a half-covered window is a
    #: smaller number for the same event, which is worse than no number.
    window_is_complete: bool = False

    @property
    def is_measured(self) -> bool:
        return self.total_views is not None

    @property
    def has_fair_window(self) -> bool:
        return self.window_is_complete and self.pre_event_views is not None


@dataclass(frozen=True)
class AttentionDistribution:
    """What normal looks like, so one event's figure has something to mean.

    A Top 10 alone tells a reader which events did best and nothing about
    whether their own event did badly. The quartiles are what turn 43 views into
    "below the first quartile" instead of "a small number".
    """

    median: float | None = None
    p25: float | None = None
    p75: float | None = None
    eligible: int = 0
    measured: int = 0

    @property
    def has_data(self) -> bool:
        return self.measured >= MIN_BENCHMARK_SAMPLE


@dataclass(frozen=True)
class Benchmark:
    """One event against comparable events, stated without a cause.

    The comparison is to the median 30-day pre-event views of the same event
    type, falling back to every eligible event when the type is too thin. What
    it never does is explain the gap: this data cannot show that a headline was
    weak, a price too high or a theme unpopular, and saying so would be an
    invention with a number attached.
    """

    value: int
    reference: float
    sample: int
    scope: str
    scope_label: str

    @property
    def difference_pct(self) -> float | None:
        if not self.reference:
            return None
        return (self.value - self.reference) / self.reference * 100


@dataclass(frozen=True)
class AttentionCoverage:
    """The denominators, stated once and never quietly changed."""

    earliest: date | None = None
    latest: date | None = None
    population: int = 0
    with_page: int = 0
    measured: int = 0
    complete_window: int = 0

    @property
    def has_data(self) -> bool:
        return self.earliest is not None


def _paths_for(items) -> dict[str, str]:
    """`event_id -> canonical path`, for the events that resolve to a page.

    Resolution goes through `attach_public_links`, which applies the workbook →
    matcher precedence in one query. This module never re-derives which page an
    event is on: two answers to that question is exactly how one event's traffic
    ends up on another event's row.
    """
    resolved: dict[str, str] = {}
    for item in attach_public_links(items):
        url = getattr(getattr(item, "public_link", None), "url", "") or ""
        path = canonical_path(url)
        if path:
            resolved[item.event_id] = path
    return resolved


def _sum_windows(windows: list[tuple[str, date, date]]) -> dict[tuple[str, date, date], int]:
    """One grouped query for many different per-event windows.

    Each event has its own date range, so the filter is a disjunction of
    `(path, start, end)` triples rather than one shared range — the same shape
    `apps.visibility.ga4_selectors._sum_views_from_publication` uses, and for
    the same reason: it is still a single round trip.

    The result is keyed by the whole triple rather than by path, because two
    programme events can share one public page while sitting months apart, and
    their windows are then genuinely different questions about the same page.
    """
    if not windows:
        return {}
    distinct = {window for window in windows}
    condition = Q()
    for path, start, end in distinct:
        condition |= Q(path=path, report_date__gte=start, report_date__lte=end)

    # Bucketed by path before the windows are applied. Scanning every returned
    # row once per window is quadratic, and the whole-history distribution asks
    # for three hundred windows over nine thousand rows.
    by_path: dict[str, list[tuple[date, int]]] = {}
    for row in current_pages().filter(condition).values("path", "report_date", "page_views"):
        by_path.setdefault(row["path"], []).append((row["report_date"], row["page_views"] or 0))

    totals: dict[tuple[str, date, date], int] = {}
    for path, start, end in distinct:
        totals[(path, start, end)] = sum(
            views for day, views in by_path.get(path, ()) if start <= day <= end
        )
    return totals


def _totals_for(paths: set[str]) -> dict[str, int]:
    if not paths:
        return {}
    return {
        row["path"]: row["total"] or 0
        for row in current_pages()
        .filter(path__in=paths)
        .values("path")
        .annotate(total=Sum("page_views"))
    }


def attach_attention(
    items,
    *,
    coverage: Coverage | None = None,
    today: date | None = None,
) -> dict[str, EventAttention]:
    """Every window for every event, keyed by `event_id`, in three queries.

    Events with no resolvable public page are **absent from the result**, not
    present with zeros. An event nobody linked has not been measured at zero
    views; it has not been measured.
    """
    rows = list(items)
    if not rows:
        return {}

    coverage = coverage if coverage is not None else get_coverage()
    if not coverage.has_data:
        return {}

    paths = _paths_for(rows)
    if not paths:
        return {}

    today = today or coverage.latest
    recent_start = coverage.latest - timedelta(days=RECENT_DAYS - 1)

    pre_windows: list[tuple[str, date, date]] = []
    late_windows: list[tuple[str, date, date]] = []
    recent_windows: list[tuple[str, date, date]] = []
    starts: dict[str, date | None] = {}

    for item in rows:
        path = paths.get(item.event_id)
        if not path:
            continue
        start = item.start_date
        starts[item.event_id] = start
        recent_windows.append((path, recent_start, coverage.latest))
        if start is None:
            continue
        pre_windows.append((path, start - timedelta(days=PRE_EVENT_DAYS - 1), start))
        late_windows.append((path, start - timedelta(days=LATE_INTEREST_DAYS - 1), start))

    pre = _sum_windows(pre_windows)
    late = _sum_windows(late_windows)
    recent = _sum_windows(recent_windows)
    totals = _totals_for(set(paths.values()))

    result: dict[str, EventAttention] = {}
    for item in rows:
        path = paths.get(item.event_id)
        if not path:
            continue
        start = item.start_date
        measured = path in totals
        complete = False
        pre_views = late_views = None
        if start is not None:
            window_start = start - timedelta(days=PRE_EVENT_DAYS - 1)
            complete = coverage.earliest <= window_start and start <= coverage.latest
            if complete and measured:
                pre_views = pre.get((path, window_start, start))
                late_views = late.get((path, start - timedelta(days=LATE_INTEREST_DAYS - 1), start))
        result[item.event_id] = EventAttention(
            event_id=item.event_id,
            path=path,
            start_date=start,
            total_views=totals.get(path),
            pre_event_views=pre_views,
            late_views=late_views,
            recent_views=(recent.get((path, recent_start, coverage.latest)) if measured else None),
            window_is_complete=complete,
        )
    return result


def distribution_of(attention: dict[str, EventAttention]) -> AttentionDistribution:
    """Quartiles of the fair 30-day window across every eligible event."""
    eligible = [row for row in attention.values() if row.window_is_complete]
    values = sorted(row.pre_event_views for row in eligible if row.pre_event_views is not None)
    if len(values) < MIN_BENCHMARK_SAMPLE:
        return AttentionDistribution(eligible=len(eligible), measured=len(values))
    quartiles = statistics.quantiles(values, n=4)
    return AttentionDistribution(
        median=float(statistics.median(values)),
        p25=float(quartiles[0]),
        p75=float(quartiles[2]),
        eligible=len(eligible),
        measured=len(values),
    )


def benchmark_for(
    item,
    attention: dict[str, EventAttention],
    *,
    by_type: dict[str, list[int]],
    overall: list[int],
) -> Benchmark | None:
    """One event against its own type, or against everything if the type is thin.

    A category represented by one or two events cannot produce a median worth
    ranking against, so the comparison widens rather than pretending. Which
    comparison was used travels with the answer in `scope_label`, because a
    reader has to know whether "the median" meant seminars or the whole
    programme.
    """
    row = attention.get(item.event_id)
    if row is None or not row.has_fair_window:
        return None

    typed = by_type.get(item.event_type_key or "", [])
    if len(typed) >= MIN_BENCHMARK_SAMPLE:
        return Benchmark(
            value=row.pre_event_views,
            reference=float(statistics.median(typed)),
            sample=len(typed),
            scope="type",
            scope_label=(item.event_type_label or "").strip() or "sama tüüpi sündmused",
        )
    if len(overall) >= MIN_BENCHMARK_SAMPLE:
        return Benchmark(
            value=row.pre_event_views,
            reference=float(statistics.median(overall)),
            sample=len(overall),
            scope="all",
            scope_label="kõik võrreldavad sündmused",
        )
    return None


def benchmark_pools(
    items, attention: dict[str, EventAttention]
) -> tuple[dict[str, list[int]], list[int]]:
    """The comparison pools, built once for a whole page.

    Only events with a **complete** 30-day window enter a pool. An event whose
    window is half-covered would drag every median down with a number that is
    small only because the measurement was short.
    """
    by_type: dict[str, list[int]] = {}
    overall: list[int] = []
    for item in items:
        row = attention.get(item.event_id)
        if row is None or not row.has_fair_window:
            continue
        overall.append(row.pre_event_views)
        by_type.setdefault(item.event_type_key or "", []).append(row.pre_event_views)
    return by_type, overall


def coverage_report(
    items, attention: dict[str, EventAttention], *, coverage: Coverage | None = None
) -> AttentionCoverage:
    """The denominators every attention figure on the page is a share of."""
    coverage = coverage if coverage is not None else get_coverage()
    rows = list(items)
    return AttentionCoverage(
        earliest=coverage.earliest,
        latest=coverage.latest,
        population=len(rows),
        with_page=len(attention),
        measured=sum(1 for row in attention.values() if row.is_measured),
        complete_window=sum(1 for row in attention.values() if row.has_fair_window),
    )


__all__ = [
    "LATE_INTEREST_DAYS",
    "MIN_BENCHMARK_SAMPLE",
    "MIN_VIEWS_FOR_RATE",
    "PRE_EVENT_DAYS",
    "RECENT_DAYS",
    "AttentionCoverage",
    "AttentionDistribution",
    "Benchmark",
    "EventAttention",
    "attach_attention",
    "benchmark_for",
    "benchmark_pools",
    "coverage_report",
    "distribution_of",
]
