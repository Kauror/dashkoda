"""Everything the Uudised page renders, assembled once.

The page is an **archive browser**, not a view of the feed. It answers "what
have we published, and how much was each one read" over the durable catalogue,
and it is deliberately not the same question as the content ranking on Nähtavus:

- `/uudised/?periood=90` — articles **published** in the last ninety days,
  each showing its total measured views however long ago they accrued;
- `/nahtavus/?periood=90&sisu=uudised` — news pages **read** in the last ninety
  days, whenever they were published.

Both are useful and neither substitutes for the other. `apps/news/periods.py`
holds the distinction in the one place that could blur it.

The view parses query parameters and renders. This module decides what the page
says. The selectors own retrieval, and `apps/visibility/ga4_selectors.py` owns
what a view total means.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.core.paginator import EmptyPage, Paginator
from django.db.models import Count, Q

from apps.visibility.ga4_selectors import Coverage, get_coverage

from .categories import NewsCategory
from .periods import (
    SORT_KEYS,
    SORT_LABELS,
    PeriodOption,
    ResolvedPeriod,
    build_query,
    period_options,
    resolve_period,
)
from .public_models import NewsResource
from .selectors import VIEWS_ANNOTATION, news_resources

#: Rows per page. The rows are one line each, so thirty is a screenful and a
#: half rather than the wall of text ten summary-bearing rows used to be.
PER_PAGE = 30

#: What a view figure is called in the column heading. The unit is stated once,
#: at the top of the column, and never repeated on every row — which is what
#: made the old list three lines tall per article.
VIEWS_HEADING = "Lehevaatamised"

#: Shown in place of a number where nothing was measured. Not a zero: nobody
#: having counted an article is not the same as an article nobody read.
NO_VIEWS = "—"

#: Shown in place of a date for a catalogued article DashKoda cannot date.
NO_DATE = "Kuupäev teadmata"


@dataclass(frozen=True)
class NewsRow:
    """One line of the archive."""

    title: str
    url: str
    published_on: date | None
    views: int | None

    @property
    def has_date(self) -> bool:
        return self.published_on is not None

    @property
    def has_views(self) -> bool:
        return self.views is not None


@dataclass(frozen=True)
class SortOption:
    key: str
    label: str
    is_active: bool
    query: str


@dataclass(frozen=True)
class CategoryOption:
    """One `Kõik / Koja / Sõprade` chip."""

    key: str
    label: str
    is_active: bool
    query: str


@dataclass(frozen=True)
class NewsArchive:
    """The whole page."""

    rows: tuple[NewsRow, ...]
    period: ResolvedPeriod
    periods: tuple[PeriodOption, ...]
    sort: str
    sorts: tuple[SortOption, ...]
    category: str
    categories: tuple[CategoryOption, ...]
    search: str
    total: int
    page_number: int
    total_pages: int
    coverage: Coverage
    #: Whether the catalogue holds anything at all, which is a different state
    #: from "this window is empty" and gets a different empty message.
    catalogue_is_empty: bool
    undated_count: int
    _unclassified: int = 0
    #: The newsletter section's state, carried through every link this archive
    #: builds. The two sections share `/uudised/` and neither may reset the
    #: other; see `build_query` in `periods.py`.
    carried: str = ""

    @property
    def has_rows(self) -> bool:
        return bool(self.rows)

    @property
    def has_previous(self) -> bool:
        return self.page_number > 1

    @property
    def has_next(self) -> bool:
        return self.page_number < self.total_pages

    @property
    def is_searching(self) -> bool:
        return bool(self.search)

    @property
    def views_heading(self) -> str:
        return VIEWS_HEADING

    @property
    def result_summary(self) -> str:
        """How many articles the current question found, in words.

        Beside the controls rather than in a card: it is a caption for the list
        below it, not a headline figure. It replaces "Uudiseid voos: 10", which
        described the feed rather than anything the reader had asked for.
        """
        if self.total == 1:
            return "1 uudis"
        return f"{self.total} uudist"

    @property
    def coverage_note(self) -> str:
        """One sentence under the list, never a line under every row.

        The old page printed "Google Analytics andmed alates …" beneath each
        article that predated collection, which is both true and unreadable at
        one line per article. The caveat belongs to the column, so it is stated
        once where the column ends.
        """
        if self.coverage.earliest is None:
            return ""
        return (
            "Lehevaatamised on Google Analyticsi mõõdetud lehevaatamised alates "
            f"{self.coverage.earliest:%d.%m.%Y}."
        )

    @property
    def coverage_caveat(self) -> str:
        """What `Kõik` does and does not claim.

        `Kõik` means every article DashKoda knows about, which is not the same
        as every article the Chamber has ever published — the catalogue is
        built from the feed since collection began and from public pages found
        since. Saying "kõik Koja uudised läbi aegade" would be a claim the
        stored data does not support.
        """
        if self.catalogue_is_empty:
            return ""
        note = "„Kõik“ tähendab kõiki DashKodale teadaolevaid uudiseid, mitte kogu Koja arhiivi."
        if self.undated_count:
            note += (
                f" {self.undated_count} uudisel ei ole teadaolevat avaldamiskuupäeva; "
                "need on nähtavad ainult „Kõik“ all."
            )
        return note

    @property
    def empty_message(self) -> str:
        """Two different nothings, told apart.

        An unconnected source and a filter that matched nothing look identical
        on screen and mean opposite things — one is a broken pipeline, the other
        is a working page answering the question it was asked.
        """
        if self.catalogue_is_empty:
            return "Andmeallikas ei ole veel ühendatud."
        if self.is_searching:
            return "Otsingule vastavaid uudiseid ei leitud."
        return "Valitud perioodil uudiseid ei leitud."

    @property
    def empty_detail(self) -> str:
        if self.catalogue_is_empty:
            return "Uudised ilmuvad siia pärast esimest edukat kontrolli."
        if self.is_searching:
            return "Proovi teist sõna või vali pikem periood."
        return "Vali pikem periood või kohandatud vahemik."

    @property
    def unclassified_count(self) -> int:
        """Rows carrying no category, so the page can say so rather than imply
        the two chips cover everything."""
        return self._unclassified

    def page_query(self, page: int) -> str:
        return build_query(
            period_key=self.period.key,
            sort=self.sort,
            search=self.search,
            category=self.category,
            page=page,
            start=self.period.start,
            end=self.period.end,
            carried=self.carried,
        )

    @property
    def previous_query(self) -> str:
        return self.page_query(max(self.page_number - 1, 1))

    @property
    def next_query(self) -> str:
        return self.page_query(min(self.page_number + 1, max(self.total_pages, 1)))


def _sort_options(
    active: str, period: ResolvedPeriod, search: str, category: str, carried: str = ""
) -> tuple[SortOption, ...]:
    return tuple(
        SortOption(
            key=key,
            label=SORT_LABELS[key],
            is_active=key == active,
            query=build_query(
                period_key=period.key,
                sort=key,
                search=search,
                category=category,
                start=period.start,
                end=period.end,
                carried=carried,
            ),
        )
        for key in SORT_KEYS
    )


def _category_options(
    active: str, period: ResolvedPeriod, sort: str, search: str, carried: str = ""
) -> tuple[CategoryOption, ...]:
    """`Kõik` first, then the two real categories.

    `Kõik` is not a third category — it is the absence of the filter, and it is
    the only option that includes articles DashKoda has not been able to
    classify.
    """
    choices = [("", "Kõik")] + [(value, label) for value, label in NewsCategory.choices]
    return tuple(
        CategoryOption(
            key=key,
            label=label,
            is_active=key == active,
            query=build_query(
                period_key=period.key,
                sort=sort,
                search=search,
                category=key,
                start=period.start,
                end=period.end,
                carried=carried,
            ),
        )
        for key, label in choices
    )


def _describe(resource: NewsResource) -> NewsRow:
    """One catalogue row as the page shows it.

    The title comes from `NewsResource` and nothing re-reads `NewsItem` to get
    it. That is the whole purpose of the catalogue: the feed's ten current rows
    can name ten articles, and the catalogue can name every one it has seen.
    """
    return NewsRow(
        title=resource.title,
        url=resource.canonical_url,
        published_on=resource.published_at.date() if resource.published_at else None,
        views=getattr(resource, VIEWS_ANNOTATION, None),
    )


def build_news_archive(
    *,
    period_key: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = "",
    search: str = "",
    category: str = "",
    page: int = 1,
    today: date | None = None,
    carried: str = "",
) -> NewsArchive:
    """Read the catalogue once and shape it for the page.

    The query count does not depend on the number of rows rendered: the view
    totals arrive as an annotation on the page slice rather than as a lookup per
    article, which is what the old page's `get_news_analytics` avoided for ten
    rows and this has to hold for thirty.
    """
    resolved = resolve_period(period_key, date_from, date_to, today=today)
    queryset = news_resources(period=resolved, search=search, sort=sort, category=category)

    paginator = Paginator(queryset, PER_PAGE)
    try:
        current = paginator.page(page)
    except EmptyPage:
        # A page number past the end is a stale bookmark, not an error. The
        # last page is the closest true answer to what it asked for.
        current = paginator.page(paginator.num_pages)

    return NewsArchive(
        rows=tuple(_describe(resource) for resource in current.object_list),
        period=resolved,
        periods=period_options(
            resolved, sort=sort, search=search, category=category, carried=carried
        ),
        sort=sort,
        sorts=_sort_options(sort, resolved, search, category, carried),
        category=category,
        categories=_category_options(category, resolved, sort, search, carried),
        search=search,
        total=paginator.count,
        page_number=current.number,
        total_pages=paginator.num_pages,
        coverage=get_coverage(),
        carried=carried,
        **_catalogue_facts(),
    )


def _catalogue_facts() -> dict:
    """The three catalogue-wide figures the page states, in one query.

    None of them can be inferred from the filtered count, and each means
    something different to the reader: whether any source is connected at all,
    how many articles are undated, and how many carry no category. They were
    three separate round trips — an `exists()` and two `count()`s over the same
    unfiltered table — which the live-search fragment then paid on every
    keystroke. Conditional aggregation asks the same three questions once.
    """
    row = NewsResource.objects.aggregate(
        total=Count("pk"),
        undated=Count("pk", filter=Q(published_at__isnull=True)),
        unclassified=Count("pk", filter=Q(category="")),
    )
    return {
        "catalogue_is_empty": not row["total"],
        "undated_count": row["undated"],
        "_unclassified": row["unclassified"],
    }


__all__ = [
    "NO_DATE",
    "NO_VIEWS",
    "PER_PAGE",
    "VIEWS_HEADING",
    "CategoryOption",
    "NewsArchive",
    "NewsRow",
    "SortOption",
    "build_news_archive",
]
