"""Read paths for the news dashboard.

Two populations live here, and confusing them is the mistake this module exists
to prevent:

- the **current snapshot** is what the Koda.ee feed says right now. Ten items,
  replaced whole on every sync, retired revisions pruned after a week. It is the
  right source for "is the feed healthy" and for the overview's short preview,
  and it is the wrong source for any question with the word *history* in it;
- the **catalogue** — `NewsResource` — is one durable row per public article,
  written the first time DashKoda sees it and never deleted. It outlives the
  snapshot that introduced it, which is what makes an archive possible at all.

`/uudised/` was built on the first and asked questions only the second can
answer: filtering ten rolling rows by "the last year" cannot work, however the
filter is written. The archive selectors below read the catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import F, Q, QuerySet

from apps.core.feeds import FeedSummaryMixin
from apps.visibility.ga4_selectors import page_view_total_subquery

from .models import NewsFeedState, NewsItem, NewsSnapshot
from .periods import SORT_VIEWS, ResolvedPeriod
from .public_models import NewsResource

DEFAULT_LIMIT = 10

#: The annotation name the archive orders and renders by.
VIEWS_ANNOTATION = "measured_views"


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


def news_resources(
    *,
    period: ResolvedPeriod,
    search: str = "",
    sort: str = "",
    category: str = "",
) -> QuerySet[NewsResource]:
    """The catalogued articles a request is asking about, ordered.

    Everything the archive page shows comes from this one queryset: the count,
    the ordering and the page slice. That matters most for `Enim vaadatud`,
    where the ranking has to run across the **whole** selected population before
    the slice — ranking a page of thirty rows would answer "the most-read of the
    thirty most recent", which is a different and much less useful question.

    So the view total is a subquery annotation rather than a Python dictionary:
    PostgreSQL orders and paginates twelve hundred articles without any of them
    reaching this process. `page_view_total_subquery` is the same definition
    `get_page_view_totals` uses, kept in the visibility module beside it.

    Undated articles are included only when the period is not a window — see
    `apps/news/periods.py` for why an article DashKoda cannot date cannot
    honestly be said to have been published in March.
    """
    queryset = NewsResource.objects.all()

    lower, upper = period.bounds()
    if lower is not None:
        queryset = queryset.filter(published_at__gte=lower)
    if upper is not None:
        # Half-open at the top, so the window's last day is whole.
        queryset = queryset.filter(published_at__lt=upper)
    if period.is_windowed:
        # A windowed question is about publication dates, and a row without one
        # cannot answer it either way.
        queryset = queryset.filter(published_at__isnull=False)

    if category:
        # Blank is "not classified", which is a real state: nothing public on
        # Koda.ee exposes the category, so an article carries one only once it
        # has been read from the site. Filtering to a category therefore
        # excludes the unclassified rather than guessing where they belong.
        queryset = queryset.filter(category=category)

    if search:
        # Title first, because that is what a reader remembers. The path is
        # matched too so a pasted URL finds its article. Both are ORM
        # parameters; no search text is ever concatenated into SQL.
        queryset = queryset.filter(Q(title__icontains=search) | Q(path__icontains=search))

    queryset = queryset.annotate(**{VIEWS_ANNOTATION: page_view_total_subquery()})

    if sort == SORT_VIEWS:
        # Measured first, most-read first. `nulls_last` is what keeps an
        # unmeasured article behind a measured zero: nobody having counted an
        # article is not the same as an article nobody read, and only one of
        # those two is a performance.
        return queryset.order_by(
            F(VIEWS_ANNOTATION).desc(nulls_last=True),
            F("published_at").desc(nulls_last=True),
            "path",
        )
    # Newest first, undated last, then path so equal dates never shuffle
    # between two renders of the same page.
    return queryset.order_by(F("published_at").desc(nulls_last=True), "path")


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
