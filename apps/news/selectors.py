"""Read paths for the news dashboard. Reads the current snapshot only."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from apps.core.feeds import FeedResult

from .models import NewsFeedState, NewsItem, NewsSnapshot

DEFAULT_LIMIT = 10
OVERVIEW_LIMIT = 5


def get_current_news_snapshot() -> NewsSnapshot | None:
    return (
        NewsSnapshot.objects.filter(source__slug=settings.KODA_NEWS_SOURCE_SLUG, is_current=True)
        .select_related("source")
        .first()
    )


def get_latest_news(snapshot: NewsSnapshot | None = None, limit: int = DEFAULT_LIMIT):
    snapshot = snapshot or get_current_news_snapshot()
    if snapshot is None:
        return NewsItem.objects.none()
    return NewsItem.objects.filter(snapshot=snapshot).order_by("-published_at", "guid")[:limit]


@dataclass(frozen=True)
class NewsSummary:
    snapshot: NewsSnapshot | None
    feed_state: NewsFeedState | None

    @property
    def has_data(self) -> bool:
        return self.snapshot is not None

    @property
    def item_count(self) -> int:
        return self.snapshot.item_count if self.snapshot else 0

    @property
    def observed_at(self):
        return self.snapshot.observed_at if self.snapshot else None

    @property
    def last_checked_at(self):
        return self.feed_state.last_checked_at if self.feed_state else None

    @property
    def last_successful_sync_at(self):
        return self.feed_state.last_successful_sync_at if self.feed_state else None

    @property
    def last_result(self) -> str:
        return self.feed_state.last_result if self.feed_state else FeedResult.NEVER_RUN

    @property
    def last_sync_failed(self) -> bool:
        return self.last_result == FeedResult.FAILED

    @property
    def is_stale_after_failure(self) -> bool:
        return self.last_sync_failed and self.has_data

    @property
    def state_label(self) -> str:
        if not self.has_data:
            return "Ühendamata"
        return "Vananenud" if self.last_sync_failed else "Ühendatud"

    @property
    def state_variant(self) -> str:
        if not self.has_data:
            return "neutral"
        return "warning" if self.last_sync_failed else "success"


def get_news_summary() -> NewsSummary:
    return NewsSummary(
        snapshot=get_current_news_snapshot(),
        feed_state=(
            NewsFeedState.objects.filter(source__slug=settings.KODA_NEWS_SOURCE_SLUG)
            .select_related("source")
            .first()
        ),
    )
