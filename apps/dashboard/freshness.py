"""Aggregate freshness state for the dashboard shell.

Four data modules publish through their own feed states: legal work,
membership, news and the event programme. This module reduces those to one honest
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

from apps.event_programme.selectors import (
    EventProgrammeSummary,
    get_event_programme_summary,
)
from apps.legal_work.selectors import LegalWorkSummary, get_legal_work_summary
from apps.membership.selectors import MembershipSummary, get_membership_summary
from apps.news.selectors import NewsSummary, get_news_summary

NO_SOURCE_MESSAGE = "Andmeallikas ei ole veel ühendatud."

# The wired data modules, each paired with the selector that reads it. A module
# joins the shell count by being added here, not by being guessed at.
#
# There are four **business domains**, not four collectors. The event domain has
# two feeds — the canonical workbook programme and the public Koda.ee calendar —
# and the programme is the one this row speaks for. Adding the public calendar as
# a fifth entry would tell a board member the dashboard covers five subjects when
# it covers four, and would move the denominator for a reason that has nothing to
# do with what is on the dashboard.
#
# The summary type is what lets a caller hand back a summary it has already
# loaded: a page passes what it read, and the type says which module it speaks
# for. Matching by type rather than by position means a caller cannot
# accidentally supply one module's summary in another's place.
_SUMMARY_SOURCES = (
    (LegalWorkSummary, get_legal_work_summary),
    (MembershipSummary, get_membership_summary),
    (NewsSummary, get_news_summary),
    (EventProgrammeSummary, get_event_programme_summary),
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


def latest_import_at(source_slug: str = ""):
    """When a source — or any source — last published successfully.

    With no slug this answers for the whole application, which is what the front
    page asks: it spans every domain and can only say when data last came in at
    all. With one, it answers for that feed alone, which is a stronger statement
    and the right one for a page built from a single source: every figure on
    Koduleht comes from GA4, so GA4's own last success *is* the date those
    figures are current to.

    The one timestamp the overview prints. It is **not** "the figures above are
    current as of this": the seven sources are collected on seven cadences, and
    `Andmete seis` at `/haldus/` is where each states its own date. What this
    says is narrower and true — the last moment DashKoda finished taking data
    in, from anywhere.

    A run that succeeded without finding anything new still counts, because it
    is still the last time the application looked and finished. `None` before
    anything has ever been imported, which renders as no line at all rather
    than as a date nobody can source.

    One indexed read: `ImportRun` is ordered on its own timestamps and this asks
    for a single row.
    """
    from apps.sources.models import ImportRun, ImportStatus

    runs = ImportRun.objects.filter(status=ImportStatus.SUCCEEDED, finished_at__isnull=False)
    if source_slug:
        runs = runs.filter(source__slug=source_slug)
    latest = runs.order_by("-finished_at").values_list("finished_at", flat=True).first()
    return timezone.localtime(latest) if latest else None


def current_freshness(*preloaded) -> FreshnessState:
    """Reduce every wired module's summary to the one shell freshness row.

    Each module costs two indexed single-row queries, so a page that has already
    read a summary for its own content can hand it back rather than pay for it
    twice: ``current_freshness(summary)``. Anything not supplied is read here,
    so a caller can pass none, one or all four and the row means the same thing.

    Nothing is cached, memoised or stashed on the request. The only way a
    summary is reused is that a caller passes the object it already holds, which
    keeps the data flow visible in the view rather than hidden behind a loader.

    Connected and stale are not restated here: `has_data` and
    `is_stale_after_failure` come from the summary the module's pages already
    render, so the shell row and the module cannot disagree about whether a
    source is publishing or showing older data after a failed check.

    Reading the summary rather than the feed-state row also covers data that was
    published without a feed check — a workbook imported by hand through the
    admin is current data, and a later failed collection makes it stale in
    exactly the way a synchronised source would be.
    """
    supplied = {type(summary): summary for summary in preloaded}
    summaries = [
        supplied[summary_class] if summary_class in supplied else read_summary()
        for summary_class, read_summary in _SUMMARY_SOURCES
    ]
    return FreshnessState(
        checked_at=timezone.localtime(),
        connected_sources=sum(1 for summary in summaries if summary.has_data),
        total_sources=len(_SUMMARY_SOURCES),
        stale_sources=sum(1 for summary in summaries if summary.is_stale_after_failure),
    )
