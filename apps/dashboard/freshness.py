"""Aggregate freshness state for the dashboard shell.

Four data modules publish through their own feed states: legal work,
membership, news and events. This module reduces those to one honest
shell-level line — how many of the wired sources currently publish data,
whether any of them is showing older data after a failed check, and when this
was computed. It reads PostgreSQL only.

What it deliberately does not do: invent an as-of date for the business data
(each module states its own), or count a source as connected merely because it
is registered. Connected means "has current published data", exactly as the
per-module summaries define it — which is why it asks the summaries rather than
reading the feed-state rows and deciding again.

Since the overview stopped carrying a per-card freshness badge, this row is
where a failed check is disclosed on that page. It has to speak for every wired
module, however that module's data was published.
"""

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from apps.events.selectors import get_event_summary
from apps.legal_work.selectors import get_legal_work_summary
from apps.membership.selectors import get_membership_summary
from apps.news.selectors import get_news_summary

NO_SOURCE_MESSAGE = "Andmeallikas ei ole veel ühendatud."

# The wired data modules, each read through its own summary selector. A module
# joins the shell count by being added here, not by being guessed at.
_SUMMARIES = (
    get_legal_work_summary,
    get_membership_summary,
    get_news_summary,
    get_event_summary,
)


@dataclass(frozen=True)
class FreshnessState:
    checked_at: datetime
    connected_sources: int = 0
    total_sources: int = 0
    stale_sources: int = 0

    @property
    def has_sources(self) -> bool:
        return self.connected_sources > 0

    @property
    def state_label(self) -> str:
        if not self.has_sources:
            return "Ühendamata"
        return "Vananenud" if self.stale_sources else "Ühendatud"

    @property
    def state_variant(self) -> str:
        if not self.has_sources:
            return "neutral"
        return "warning" if self.stale_sources else "success"

    @property
    def message(self) -> str:
        if not self.has_sources:
            return NO_SOURCE_MESSAGE
        base = f"Ühendatud andmeallikaid: {self.connected_sources}/{self.total_sources}."
        if self.stale_sources:
            return f"{base} Vananenud: {self.stale_sources}."
        return base


def freshness_from(summaries) -> FreshnessState:
    """Count the connected and stale sources from already-loaded summaries.

    For a view that has just read the module summaries for its own content —
    the overview reads all four — this avoids fetching every one of them a
    second time. `summaries` must hold one summary per wired module, in any
    order.

    Connected and stale are not restated here: `has_data` and
    `is_stale_after_failure` come from the summary the module's pages already
    render, so the shell row and the module cannot disagree about whether a
    source is publishing or showing older data after a failed check.

    Reading the summary rather than the feed-state row also covers data that was
    published without a feed check — a workbook imported by hand through the
    admin is current data, and a later failed collection makes it stale in
    exactly the way a synchronised source would be.
    """
    summaries = list(summaries)
    return FreshnessState(
        checked_at=timezone.localtime(),
        connected_sources=sum(1 for summary in summaries if summary.has_data),
        total_sources=len(_SUMMARIES),
        stale_sources=sum(1 for summary in summaries if summary.is_stale_after_failure),
    )


def current_freshness() -> FreshnessState:
    """Read every wired module's summary and reduce it to the shell row.

    Two indexed single-row queries per wired module.
    """
    return freshness_from(read_summary() for read_summary in _SUMMARIES)
