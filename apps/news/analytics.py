"""Page views for the news items the Uudised page is about to render.

The join between two things that never touch each other in the database: an
imported `NewsItem`, which holds a `canonical_url`, and GA4's page rows, which
hold a path. `apps.visibility.ga4_paths` is what makes them one key.

Three decisions worth stating, because each has a tempting wrong version:

- **nothing is stored on the item.** A `NewsItem` belongs to an immutable
  snapshot and is re-imported whole every time the feed is read, so a view count
  written onto it would be a mutable figure inside a frozen record and would
  have to be rewritten on every sync. The association is computed instead;
- **the match is exact.** No prefix guessing, no fuzzy title matching, no
  "closest path". A wrong match attributes one article's readership to another
  and there is nothing in the numbers that would ever reveal it;
- **an article with no analytics renders normally.** Absence is absence: an
  article nobody has measured has no view count, not a zero, and the page shows
  nothing rather than `0 lehevaatamist`.

The whole page costs a fixed handful of queries whatever the item count.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from apps.core.formatting import group_thousands
from apps.visibility.ga4_paths import canonical_path
from apps.visibility.ga4_selectors import ArticleViews, Coverage, get_article_views, get_coverage

#: What a view count is called. Never "lugejad": one person reading an article
#: twice is two page views and one reader, and the figure is the first of those.
VIEWS_LABEL = "lehevaatamist"


@dataclass(frozen=True)
class ItemAnalytics:
    """One article's measured performance, with its caveats attached.

    `total_is_lifetime` is the field that keeps this honest. GA4 collection
    began on a particular day; for anything published before it, the total is
    views *within the measured period* and the interface has to say so rather
    than printing a number that reads like a lifetime count.
    """

    views: ArticleViews
    coverage: Coverage

    @property
    def has_data(self) -> bool:
        return self.views.has_data

    @property
    def total(self) -> int | None:
        return self.views.total

    @property
    def total_is_lifetime(self) -> bool:
        return self.views.covers_publication

    @property
    def first_7_days(self) -> int | None:
        return self.views.first_7_days if self.views.covers_publication else None

    @property
    def first_30_days(self) -> int | None:
        return self.views.first_30_days if self.views.covers_publication else None

    @property
    def last_30_days(self) -> int | None:
        return self.views.last_30_days

    @property
    def total_label(self) -> str:
        """The count and its unit, or an empty string when nothing was measured."""
        if self.total is None:
            return ""
        return f"{group_thousands(self.total)} {VIEWS_LABEL}"

    @property
    def coverage_note(self) -> str:
        """The sentence that stops a partial total from reading as a whole one."""
        if self.total is None or self.coverage.earliest is None:
            return ""
        if self.total_is_lifetime:
            return ""
        return f"Google Analytics andmed alates {self.coverage.earliest:%d.%m.%Y}"


@dataclass(frozen=True)
class NewsAnalytics:
    """The lookup a template holds, keyed by canonical path.

    Empty when GA4 has collected nothing, which is a real state and not an
    error: the page renders exactly as it did before any of this existed.
    """

    by_path: dict[str, ItemAnalytics]
    coverage: Coverage

    @property
    def has_any(self) -> bool:
        return bool(self.by_path)

    def for_item(self, item) -> ItemAnalytics | None:
        """This item's analytics, or `None` if it has none."""
        path = canonical_path(getattr(item, "canonical_url", None))
        if not path:
            return None
        return self.by_path.get(path)

    def ranked(self, items: Sequence, *, limit: int | None = None) -> tuple:
        """`items` ordered by measured views, most-read first.

        Items with no analytics keep their feed order **after** the measured
        ones rather than being dropped or treated as zero: an unmeasured article
        is not a badly performing one.
        """
        measured = []
        unmeasured = []
        for item in items:
            analytics = self.for_item(item)
            if analytics is not None and analytics.total is not None:
                measured.append((analytics.total, item))
            else:
                unmeasured.append(item)
        measured.sort(key=lambda pair: pair[0], reverse=True)
        ordered = tuple(item for _, item in measured) + tuple(unmeasured)
        return ordered[:limit] if limit else ordered


def get_news_analytics(
    items: Iterable, *, today: date | None = None, coverage: Coverage | None = None
) -> NewsAnalytics:
    """Views for every item, in a fixed number of queries.

    `items` is consumed once into a list, so a queryset is not iterated twice.
    """
    items = list(items)
    coverage = coverage if coverage is not None else get_coverage()
    if not coverage.has_data or not items:
        return NewsAnalytics(by_path={}, coverage=coverage)

    views = get_article_views(items, today=today, coverage=coverage)
    return NewsAnalytics(
        by_path={
            path: ItemAnalytics(views=article, coverage=coverage) for path, article in views.items()
        },
        coverage=coverage,
    )


__all__ = ["VIEWS_LABEL", "ItemAnalytics", "NewsAnalytics", "get_news_analytics"]
