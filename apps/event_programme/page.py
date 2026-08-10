"""Everything the Sündmused page renders, assembled in one readable place.

The view renders. What the reader sees — which period is the default, which
figures lead, how a filter state becomes a URL, how the table paginates — is
decided here, so the template holds layout and no business rule.

Two properties are deliberate:

- **Every filter value is validated against the current snapshot** before it
  reaches a query. An unknown tag, a year the Chamber ran nothing in or a
  nonsense status falls back to "not filtered" rather than producing an
  unexplained empty table, and no arbitrary value from a query string ever
  reaches the database.
- **Links are rebuilt from the validated state, never echoed from the request.**
  A pagination link therefore carries exactly the filters that were applied, and
  a bogus parameter cannot travel from page to page.

Nothing here reaches outside PostgreSQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.urls import reverse

from apps.visibility.ga4_paths import canonical_path
from apps.visibility.ga4_selectors import get_coverage, get_page_view_totals
from apps.visibility.item_analytics import attach_page_views, event_url

from .models import EventStatus
from .public_links import attach_public_links
from .selectors import (
    LINK_ALL,
    LINK_LINKED,
    LINK_UNLINKED,
    LINK_VALUES,
    NEAR_TERM_DAYS,
    PAGE_SIZE,
    REVIEW_ALL,
    REVIEW_CLEAR,
    REVIEW_REQUIRED,
    REVIEW_VALUES,
    SORT_CHOICES,
    SORT_DATE,
    SORT_VIEWS,
    YEAR_ALL,
    EventProgrammeSummary,
    FilterOptions,
    Option,
    ProgrammeFilters,
    count_events_for_year,
    count_events_started_within,
    count_events_starting_within,
    count_review_required_events,
    count_unknown_date_events,
    get_event_programme_filter_options,
    get_filtered_event_programme_items,
)

ALL_YEARS_LABEL = "Kõik aastad"

# How many numbered page links surround the current one. Enough to move a few
# pages at a time without turning a long history into a wall of numbers.
PAGE_WINDOW = 3

PUBLIC_LINK_OPTIONS = (
    Option(value=LINK_ALL, label="Kõik"),
    Option(value=LINK_LINKED, label="Avaliku lehega"),
    Option(value=LINK_UNLINKED, label="Avaliku leheta"),
)

REVIEW_OPTIONS = (
    Option(value=REVIEW_ALL, label="Kõik"),
    Option(value=REVIEW_REQUIRED, label="Vajab ülevaatust"),
    Option(value=REVIEW_CLEAR, label="Ülevaadatud"),
)


@dataclass(frozen=True)
class Figure:
    """One compact figure above the table."""

    label: str
    value: int | None
    note: str = ""


@dataclass(frozen=True)
class PageLink:
    number: int
    url: str
    is_current: bool


@dataclass(frozen=True)
class Pagination:
    """Server-side paging that keeps the active filters in every link."""

    number: int = 1
    count: int = 1
    total: int = 0
    first_index: int = 0
    last_index: int = 0
    previous_url: str = ""
    next_url: str = ""
    links: tuple[PageLink, ...] = ()

    @property
    def has_pages(self) -> bool:
        return self.count > 1


@dataclass(frozen=True)
class SortOption:
    """One ordering choice, as a link that keeps every active filter.

    Rebuilt from validated state rather than echoed from the request, which is
    the same rule the filters follow: nothing a reader types is reflected back
    into a URL this page emits.
    """

    label: str
    url: str
    is_active: bool


@dataclass(frozen=True)
class QualityLink:
    """A data-quality count that opens the table on exactly those records."""

    label: str
    value: int
    url: str


@dataclass(frozen=True)
class ProgrammePage:
    summary: EventProgrammeSummary
    filters: ProgrammeFilters
    options: FilterOptions
    period_label: str
    figures: tuple[Figure, ...]
    items: tuple = ()
    pagination: Pagination = Pagination()
    result_count: int = 0
    sort_options: tuple[SortOption, ...] = ()
    #: Stated once under the table, never per row.
    coverage_start: object = None
    quality: tuple[QualityLink, ...] = ()
    clear_url: str = ""
    all_years_value: str = YEAR_ALL
    all_years_label: str = ALL_YEARS_LABEL
    public_link_options: tuple[Option, ...] = PUBLIC_LINK_OPTIONS
    review_options: tuple[Option, ...] = REVIEW_OPTIONS

    @property
    def has_data(self) -> bool:
        return self.summary.has_data

    @property
    def all_years_selected(self) -> bool:
        return self.filters.year is None

    @property
    def has_narrowed(self) -> bool:
        """Whether anything but the default period is applied."""
        return self.filters.is_active or self.filters.year is None


def parse_filters(params, options: FilterOptions) -> ProgrammeFilters:
    """Turn one request's query string into a validated filter state.

    The year is the only parameter with a meaningful default: absent means "the
    current period", `all` means the whole history, and anything the snapshot
    does not contain falls back to the default rather than emptying the table
    without explanation.

    `event_type` and `delivery_mode` are deliberately not read. The page has no
    control for either any more, and a filter that can still be switched on from
    a query string but has nowhere on screen to say it is on would shrink the
    table with no visible reason. `ProgrammeFilters` still carries the fields and
    the selector still honours them, so the capability is intact for whatever
    asks for it explicitly — this page simply never asks.
    """
    return ProgrammeFilters(
        q=params.get("q", "").strip()[:100],
        year=_parse_year(params.get("year"), options),
        month=_allowed(params.get("month"), options.months),
        quarter=_allowed(params.get("quarter"), options.quarters),
        tag=_allowed(params.get("tag"), options.tags),
        status=_allowed(params.get("status"), options.statuses),
        public_link=_one_of(params.get("public_link"), LINK_VALUES, LINK_ALL),
        review=_one_of(params.get("review"), REVIEW_VALUES, REVIEW_ALL),
        # Chronological unless asked otherwise, and an unknown value is
        # chronological too rather than an error page.
        sort=_one_of(params.get("sort"), SORT_CHOICES, SORT_DATE),
    )


def _parse_year(raw, options: FilterOptions) -> int | None:
    if raw == YEAR_ALL:
        return None
    if raw:
        try:
            candidate = int(raw)
        except TypeError, ValueError:
            candidate = None
        if candidate in options.years:
            return candidate
    return options.default_year()


def _allowed(raw, choices: tuple[Option, ...]) -> str:
    values = {option.value for option in choices}
    return raw if raw in values else ""


def _one_of(raw, values: tuple[str, ...], default: str) -> str:
    return raw if raw in values else default


def build_programme_page(summary: EventProgrammeSummary, params) -> ProgrammePage:
    """Read the programme once and shape it for the page.

    `params` is the request's `GET`. The options are derived first, because every
    other decision — which year is the default, whether a supplied tag exists at
    all — is a question about what this snapshot contains.
    """
    snapshot = summary.snapshot
    options = get_event_programme_filter_options(snapshot)
    filters = parse_filters(params, options)

    if snapshot is None:
        return ProgrammePage(
            summary=summary,
            filters=filters,
            options=options,
            period_label=ALL_YEARS_LABEL,
            figures=(),
            clear_url=reverse("events"),
        )

    rows = get_filtered_event_programme_items(snapshot, filters=filters)
    if filters.sort == SORT_VIEWS:
        rows = _ranked_by_views(rows)
    paginator = Paginator(rows, PAGE_SIZE)
    page = paginator.get_page(params.get("page"))

    return ProgrammePage(
        summary=summary,
        filters=filters,
        options=options,
        period_label=str(filters.year) if filters.year is not None else ALL_YEARS_LABEL,
        figures=_figures(snapshot, filters),
        items=tuple(attach_page_views(attach_public_links(page.object_list), url_of=event_url)),
        pagination=_pagination(page, filters),
        result_count=paginator.count,
        sort_options=_sort_options(filters),
        coverage_start=get_coverage().earliest,
        quality=_quality_links(snapshot),
        clear_url=reverse("events"),
    )


def _sort_options(filters: ProgrammeFilters) -> tuple[SortOption, ...]:
    """`Kuupäev` and `Enim vaadatud`, each carrying the current filters."""
    from dataclasses import replace

    return tuple(
        SortOption(
            label=label,
            url=_url(replace(filters, sort=mode)),
            is_active=filters.sort == mode,
        )
        for mode, label in ((SORT_DATE, "Kuupäev"), (SORT_VIEWS, "Enim vaadatud"))
    )


def _ranked_by_views(rows) -> list:
    """The whole filtered set ordered by measured traffic, before pagination.

    Ranking a page of results would rank whichever twenty-five rows happened to
    be on it, which is not a ranking. So the population is resolved first — its
    public links, then one bulk total for those paths — and ordered whole.

    Bounded on purpose. The programme is on the order of a thousand rows and
    each carries one link, so this is a small in-memory sort rather than a query
    that reimplements the link precedence in SQL. If the programme ever grows by
    an order of magnitude this is the thing to revisit.

    **An unmeasured event is not a zero-traffic event.** Measured rows come
    first, most-viewed first; unmeasured ones keep their chronological order
    behind them rather than being sorted as though they had scored nothing.
    """
    population = attach_public_links(list(rows))
    totals = get_page_view_totals(event_url(item) for item in population)

    measured, unmeasured = [], []
    for item in population:
        path = canonical_path(event_url(item))
        views = totals.get(path) if path else None
        (measured if views is not None else unmeasured).append((views, item))

    # `event_id` breaks ties so equal-traffic events hold their order between
    # renders and pagination stays stable.
    measured.sort(key=lambda pair: (-pair[0].total, pair[1].event_id))
    return [item for _, item in measured] + [item for _, item in unmeasured]


def _figures(snapshot, filters: ProgrammeFilters) -> tuple[Figure, ...]:
    """Three figures, each stating the period it measures.

    The filtered result count is not among them: the table states its own count,
    and repeating it as a fourth card would put the same number on screen twice.

    Nor is the count of events carrying a confirmed public link. That is a
    property of the workbook's link column rather than of the programme, the
    "Avalik leht" filter is where a reader acts on it, and the board asked for it
    off the figure strip.
    """
    period = str(filters.year) if filters.year is not None else "kogu ajaloos"
    return (
        Figure(
            label="Sündmusi perioodil",
            value=count_events_for_year(snapshot, filters.year),
            note=period,
        ),
        Figure(
            label="Algab lähiajal",
            value=count_events_starting_within(snapshot),
            note=f"järgmise {NEAR_TERM_DAYS} päeva jooksul",
        ),
        Figure(
            label="Algas hiljuti",
            value=count_events_started_within(snapshot),
            note=f"eelmise {NEAR_TERM_DAYS} päeva jooksul",
        ),
    )


def _quality_links(snapshot) -> tuple[QualityLink, ...]:
    """Records a default period would hide, disclosed with a way to reach them.

    An undated event carries no year, so the default period filter excludes it.
    Saying how many there are — and linking to them across every year — is what
    keeps the default from quietly shrinking the programme.
    """
    links = []
    unknown = count_unknown_date_events(snapshot)
    if unknown:
        links.append(
            QualityLink(
                label="Kuupäev teadmata",
                value=unknown,
                url=_url(
                    ProgrammeFilters(year=None, status=EventStatus.DATE_UNKNOWN),
                ),
            )
        )
    review = count_review_required_events(snapshot)
    if review:
        links.append(
            QualityLink(
                label="Vajab ülevaatust",
                value=review,
                url=_url(ProgrammeFilters(year=None, review=REVIEW_REQUIRED)),
            )
        )
    return tuple(links)


def _pagination(page, filters: ProgrammeFilters) -> Pagination:
    paginator = page.paginator
    window = range(
        max(1, page.number - PAGE_WINDOW),
        min(paginator.num_pages, page.number + PAGE_WINDOW) + 1,
    )
    return Pagination(
        number=page.number,
        count=paginator.num_pages,
        total=paginator.count,
        first_index=page.start_index(),
        last_index=page.end_index(),
        previous_url=(
            _url(filters, page=page.previous_page_number()) if page.has_previous() else ""
        ),
        next_url=(_url(filters, page=page.next_page_number()) if page.has_next() else ""),
        links=tuple(
            PageLink(
                number=number,
                url=_url(filters, page=number),
                is_current=number == page.number,
            )
            for number in window
        ),
    )


def _url(filters: ProgrammeFilters, *, page: int | None = None) -> str:
    """The page's own address carrying exactly the filters that were applied.

    The year is always written out, as a number or as `all`, so a link never
    depends on the reader's calendar year to mean what it meant when it was
    built.
    """
    query: list[tuple[str, str]] = [
        ("year", str(filters.year) if filters.year is not None else YEAR_ALL)
    ]
    for name, value in (
        ("q", filters.q),
        ("month", filters.month),
        ("quarter", filters.quarter),
        ("tag", filters.tag),
        ("status", filters.status),
    ):
        if value:
            query.append((name, value))
    if filters.public_link != LINK_ALL:
        query.append(("public_link", filters.public_link))
    if filters.review != REVIEW_ALL:
        query.append(("review", filters.review))
    if filters.sort != SORT_DATE:
        query.append(("sort", filters.sort))
    if page is not None and page > 1:
        query.append(("page", str(page)))
    return f"{reverse('events')}?{urlencode(query)}"
