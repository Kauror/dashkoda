"""Period-scoped user counts: the one GA4 figure that cannot be derived here.

Every other website number on Koduleht is arithmetic over stored daily rows.
This one is not, and the reason is worth restating where the code lives rather
than only where it is explained:

    Sessions, page views and engaged sessions are **event counts**. They belong
    to a day, so a period's total is the sum of its days.

    Users are **distinct people**. Monday's 400 and Tuesday's 380 are not 780.
    No arithmetic over daily counts produces a period's distinct count, and a
    sum of them would exceed the Chamber's real audience while looking like an
    ordinary total.

So the period figure is asked of GA4 directly, once per range, with **no date
dimension** — the query whose date range *is* the period. The answers are cached
in `Ga4PeriodUsers` and the page reads only that table, which keeps the rule
that no page render ever reaches Google.

## Which ranges are fetched

Exactly the ones a reader can produce by clicking: each period preset resolved
against current coverage, plus each one's comparison window. They are resolved
through `parse_period` and `build_comparison` — the same functions the page
uses — rather than recomputed here, because a stored range that is one day off
from the one the page asks for is a cache that never hits and a card that is
permanently blank.

**Custom ranges are deliberately not fetched.** They are unbounded, each one
costs a request, and a hand-picked window is a question asked once. The card
says so rather than showing a number for a different period.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .ga4 import Ga4PeriodUserCollector
from .ga4_selectors import Coverage, get_coverage
from .models import Ga4PeriodUsers
from .website_period import PERIOD_PRESETS, build_comparison, get_period_coverage, parse_period


@dataclass(frozen=True)
class DateRange:
    """One inclusive window, as both GA4 and the cache key understand it."""

    start: date
    end: date


@dataclass
class PeriodUserSync:
    """What one run did. Counted, never estimated."""

    fetched: int = 0
    stored: int = 0
    unchanged: int = 0
    empty: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.stored)


def periods_to_fetch(coverage: Coverage | None = None) -> tuple[DateRange, ...]:
    """Every window the controls can produce, and each one's comparison.

    De-duplicated: short presets on a young property resolve to the same clamped
    window, and one range is one request no matter how many buttons reach it.
    """
    coverage = coverage if coverage is not None else get_coverage()
    if not coverage.has_data:
        return ()

    ordered: dict[tuple[date, date], None] = {}
    for preset in PERIOD_PRESETS:
        period = parse_period(preset.key, coverage)
        if not period.has_window:
            continue
        ordered[(period.start, period.end)] = None

        comparison = build_comparison(
            period, coverage, get_period_coverage(period.start, period.end)
        )
        if comparison.is_available and comparison.start and comparison.end:
            ordered[(comparison.start, comparison.end)] = None

    return tuple(DateRange(start=start, end=end) for start, end in ordered)


def get_period_users(start: date | None, end: date | None) -> int | None:
    """The stored count for exactly this window, or `None` if it was never asked.

    `None` is not zero. A range nobody fetched and a range that genuinely had no
    users are different statements, and only the second one is a measurement.
    """
    if start is None or end is None:
        return None
    row = Ga4PeriodUsers.objects.filter(start_date=start, end_date=end).only("active_users").first()
    return row.active_users if row is not None else None


def record_period_users(start: date, end: date, users: int) -> bool:
    """Store one answer, replacing any older one. True when the value moved.

    Replacement rather than a new revision: this table is a cache of a
    deterministic question, not a published history. The daily snapshots are
    versioned because a revised figure must stay auditable; nothing here was
    ever published as a fact of a particular day.
    """
    existing = Ga4PeriodUsers.objects.filter(start_date=start, end_date=end).first()
    if existing is None:
        Ga4PeriodUsers.objects.create(start_date=start, end_date=end, active_users=users)
        return True
    if existing.active_users == users:
        # Touch `fetched_at` anyway: "asked today, same answer" is a different
        # operational state from "not asked since June".
        existing.save(update_fields=["fetched_at"])
        return False
    existing.active_users = users
    existing.save(update_fields=["active_users", "fetched_at"])
    return True


def synchronize_period_users(
    collector: Ga4PeriodUserCollector,
    *,
    coverage: Coverage | None = None,
    dry_run: bool = False,
) -> PeriodUserSync:
    """Ask GA4 for each reachable window and cache the answers.

    One request per window, and the window list is short by construction: six
    presets and their comparisons, de-duplicated, on a property whose history
    clamps several of them together.
    """
    summary = PeriodUserSync()
    for window in periods_to_fetch(coverage):
        users = collector.collect_period_users(start=window.start, end=window.end)
        summary.fetched += 1
        if users is None:
            # The property answered with no row. An absence, not a zero — and
            # storing it as one would put a fabricated measurement on the page.
            summary.empty += 1
            continue
        if dry_run:
            continue
        if record_period_users(window.start, window.end, users):
            summary.stored += 1
        else:
            summary.unchanged += 1
    return summary


__all__ = [
    "DateRange",
    "PeriodUserSync",
    "get_period_users",
    "periods_to_fetch",
    "record_period_users",
    "synchronize_period_users",
]
