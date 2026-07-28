"""Read paths for the legal-work dashboard.

Every query lives here rather than in a template or a view, so the definition
of "currently open" or "latest sent" has exactly one home.

All of them read the current snapshot only. When an older snapshot exists but
none is current, the answer is an empty state: showing retired data as if it
were live would be worse than showing nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from .models import LegalWorkFeedState, LegalWorkItem, LegalWorkSnapshot, SentStatus, SyncResult

DEFAULT_RECENT_LIMIT = 15
# The open list is bounded so a workbook that grows cannot produce an
# unbounded page.
MAX_OPEN_ITEMS = 200


def get_current_snapshot() -> LegalWorkSnapshot | None:
    return (
        LegalWorkSnapshot.objects.filter(
            source__slug=settings.LEGAL_WORK_SOURCE_SLUG,
            is_current=True,
        )
        .select_related("source")
        .first()
    )


def _items(snapshot: LegalWorkSnapshot | None):
    if snapshot is None:
        return LegalWorkItem.objects.none()
    return LegalWorkItem.objects.filter(snapshot=snapshot)


def get_open_items(snapshot: LegalWorkSnapshot | None = None, limit: int | None = MAX_OPEN_ITEMS):
    """Topics still being worked on, most recently received first."""
    snapshot = snapshot or get_current_snapshot()
    # PostgreSQL sorts NULLs last in a descending order, so dated records lead
    # and undated ones trail deterministically by topic.
    queryset = (
        _items(snapshot).filter(is_open=True).order_by("-received_date", "topic", "record_id")
    )
    return queryset[:limit] if limit else queryset


def get_latest_sent_items(
    snapshot: LegalWorkSnapshot | None = None, limit: int = DEFAULT_RECENT_LIMIT
):
    """Most recently sent opinions.

    `not_sent` never appears here: a record that was explicitly not sent is not
    a recent send, and the model constraint already guarantees it carries no
    date to sort by.
    """
    snapshot = snapshot or get_current_snapshot()
    return (
        _items(snapshot)
        .filter(sent_status=SentStatus.SENT, sent_date__isnull=False)
        .order_by("-sent_date", "topic", "record_id")[:limit]
    )


def get_newest_received_items(
    snapshot: LegalWorkSnapshot | None = None, limit: int = DEFAULT_RECENT_LIMIT
):
    """Most recently received topics.

    A received date in the future is a known workbook data problem, flagged by
    the generator's `received_date_in_future` warning. Such a record would
    otherwise sit permanently at the top of this list, so it is excluded here
    while remaining fully present in the imported data.
    """
    snapshot = snapshot or get_current_snapshot()
    today = timezone.localdate()
    return (
        _items(snapshot)
        .filter(received_date__isnull=False, received_date__lte=today)
        .order_by("-received_date", "topic", "record_id")[:limit]
    )


@dataclass(frozen=True)
class LegalWorkSummary:
    """Everything the dashboard needs to describe the data's state honestly."""

    snapshot: LegalWorkSnapshot | None
    feed_state: LegalWorkFeedState | None

    @property
    def has_data(self) -> bool:
        return self.snapshot is not None

    @property
    def open_count(self) -> int:
        return self.snapshot.open_record_count if self.snapshot else 0

    @property
    def total_count(self) -> int:
        return self.snapshot.total_record_count if self.snapshot else 0

    @property
    def reporting_date(self):
        return self.snapshot.reporting_date if self.snapshot else None

    @property
    def generated_at(self):
        return self.snapshot.workbook_generated_at if self.snapshot else None

    @property
    def last_checked_at(self):
        return self.feed_state.last_checked_at if self.feed_state else None

    @property
    def last_successful_sync_at(self):
        return self.feed_state.last_successful_sync_at if self.feed_state else None

    @property
    def last_result(self) -> str:
        return self.feed_state.last_result if self.feed_state else SyncResult.NEVER_RUN

    @property
    def last_sync_failed(self) -> bool:
        return self.last_result == SyncResult.FAILED

    @property
    def is_stale_after_failure(self) -> bool:
        """Showing older data because the newest check did not succeed."""
        return self.last_sync_failed and self.has_data

    @property
    def state_label(self) -> str:
        if not self.has_data:
            return "Ühendamata"
        if self.last_sync_failed:
            return "Vananenud"
        return "Ühendatud"

    @property
    def state_variant(self) -> str:
        if not self.has_data:
            return "neutral"
        if self.last_sync_failed:
            return "warning"
        return "success"


def get_legal_work_summary() -> LegalWorkSummary:
    snapshot = get_current_snapshot()
    feed_state = (
        LegalWorkFeedState.objects.filter(source__slug=settings.LEGAL_WORK_SOURCE_SLUG)
        .select_related("source")
        .first()
    )
    return LegalWorkSummary(snapshot=snapshot, feed_state=feed_state)
