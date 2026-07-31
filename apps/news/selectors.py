"""Read paths for the news dashboard. Reads the current snapshot only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.conf import settings

from apps.core.feeds import FeedSummaryMixin

from .models import NewsFeedState, NewsItem, NewsSnapshot

DEFAULT_LIMIT = 10


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


def count_published_since(snapshot: NewsSnapshot | None, since: datetime) -> int:
    """Items in the current snapshot published at or after `since`.

    Bounded by what the feed publishes: it carries a limited number of recent
    items, so this counts what the Chamber's own feed still lists, not every
    article it has ever published.
    """
    snapshot = snapshot or get_current_news_snapshot()
    if snapshot is None:
        return 0
    return NewsItem.objects.filter(snapshot=snapshot, published_at__gte=since).count()


@dataclass(frozen=True)
class NewsSummary(FeedSummaryMixin):
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


def get_news_summary() -> NewsSummary:
    return NewsSummary(
        snapshot=get_current_news_snapshot(),
        feed_state=(
            NewsFeedState.objects.filter(source__slug=settings.KODA_NEWS_SOURCE_SLUG)
            .select_related("source")
            .first()
        ),
    )
