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

from dataclasses import dataclass, replace
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.urls import reverse

from apps.visibility.ga4_paths import canonical_path
from apps.visibility.ga4_selectors import get_coverage, get_page_view_totals
from apps.visibility.item_analytics import attach_page_views, event_url

from . import commerce
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
    SORT_REGISTRATIONS,
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

#: Which focus of `/sundmused/` the register is. It lives here rather than in
#: `intelligence.py` because this module owns every link the register emits and
#: is the lighter of the two: `intelligence` reaches into the shop and GA4, and
#: the search fragment has no business importing either.
REGISTER_FOCUS = "programm"

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
    #: The same question over the whole history: this filter state with the year
    #: widened and everything else — the term above all — kept.
    all_years_url: str = ""
    all_years_value: str = YEAR_ALL
    all_years_label: str = ALL_YEARS_LABEL
    public_link_options: tuple[Option, ...] = PUBLIC_LINK_OPTIONS
    review_options: tuple[Option, ...] = REVIEW_OPTIONS
    #: Whether the Commerce export carries any event-registration product at
    #: all. False hides the whole column rather than filling it with dashes.
    shows_registrations: bool = False

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

    @property
    def is_searching(self) -> bool:
        return bool(self.filters.q)

    @property
    def has_refinements(self) -> bool:
        """Whether any filter behind the `Täpsem valik` disclosure is set.

        Eight controls stacked two rows deep pushed the programme itself below
        the fold, and six of them are asked for rarely: the search box and the
        year answer almost every visit. Those two stay out; the rest fold away.

        Deliberately not `filters.is_active`, which also counts the search. A
        reader typing a name has not asked about quarters, and opening the
        disclosure on every keystroke would undo the compaction exactly when
        the reader is busiest.
        """
        return bool(
            self.filters.month
            or self.filters.quarter
            or self.filters.tag
            or self.filters.event_type
            or self.filters.delivery_mode
            or self.filters.status
            or self.filters.public_link != LINK_ALL
            or self.filters.review != REVIEW_ALL
        )

    @property
    def refinement_count(self) -> int:
        """How many are set, so the closed disclosure can say so.

        A collapsed control that hides an applied filter is how a reader ends up
        mistrusting a count they cannot explain.
        """
        return sum(
            (
                bool(self.filters.month),
                bool(self.filters.quarter),
                bool(self.filters.tag),
                bool(self.filters.event_type),
                bool(self.filters.delivery_mode),
                bool(self.filters.status),
                self.filters.public_link != LINK_ALL,
                self.filters.review != REVIEW_ALL,
            )
        )

    @property
    def search_is_year_bound(self) -> bool:
        """Whether a search is answering inside a year the reader did not type.

        The year defaults to the current one, which is right for a page whose
        standing job is this year's programme — and surprising the moment
        somebody searches, because a search reads as a question about the
        register. `eksport` finds one event in 2026 and thirteen in the whole
        programme, and nothing on screen said which had been asked.

        So the page says it. The filter is not overridden: a reader may have set
        the year deliberately, and a search that silently widened it would be
        the same failure in the other direction.
        """
        return self.is_searching and self.filters.year is not None

    @property
    def year_bound_note(self) -> str:
        if not self.search_is_year_bound:
            return ""
        return f"Otsitakse ainult {self.filters.year}. aasta sündmustest."


def parse_filters(params, options: FilterOptions) -> ProgrammeFilters:
    """Turn one request's query string into a validated filter state.

    The year is the only parameter with a meaningful default: absent means "the
    current period", `all` means the whole history, and anything the snapshot
    does not contain falls back to the default rather than emptying the table
    without explanation.

    `event_type` and `delivery_mode` are read again. They were dropped when the
    page had no control for either — a filter switchable from a query string
    with nowhere on screen to say it is on shrinks the table for no visible
    reason. The intelligence dashboard gives both a legitimate visible use: a
    reader who has just seen that a third of the programme is a seminar wants
    the register to show exactly those, and `Täpsem valik` now carries and counts
    both.
    """
    return ProgrammeFilters(
        q=params.get("q", "").strip()[:100],
        year=_parse_year(params.get("year"), options),
        month=_allowed(params.get("month"), options.months),
        quarter=_allowed(params.get("quarter"), options.quarters),
        tag=_allowed(params.get("tag"), options.tags),
        event_type=_allowed(params.get("event_type"), options.event_types),
        delivery_mode=_allowed(params.get("delivery_mode"), options.delivery_modes),
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

    # Registration columns exist only where the Commerce export actually carries
    # event-registration products. On the Chamber's current dataset it carries
    # none, so the column is absent rather than a wall of dashes claiming to be
    # a measurement.
    registration_pages = commerce.registration_pages()

    rows = get_filtered_event_programme_items(snapshot, filters=filters)
    if filters.sort == SORT_VIEWS:
        rows = _ranked_by_views(rows)
    elif filters.sort == SORT_REGISTRATIONS and registration_pages:
        rows = _ranked_by_registrations(rows, registration_pages)
    paginator = Paginator(rows, PAGE_SIZE)
    page = paginator.get_page(params.get("page"))

    items = attach_page_views(attach_public_links(page.object_list), url_of=event_url)
    if registration_pages:
        registrations = commerce.attach_registrations(items, pages=registration_pages)
        for item in items:
            item.registrations = registrations.get(item.event_id)

    return ProgrammePage(
        summary=summary,
        filters=filters,
        options=options,
        period_label=str(filters.year) if filters.year is not None else ALL_YEARS_LABEL,
        figures=_figures(snapshot, filters),
        items=tuple(items),
        pagination=_pagination(page, filters),
        result_count=paginator.count,
        sort_options=_sort_options(filters, offer_registrations=bool(registration_pages)),
        coverage_start=get_coverage().earliest,
        quality=_quality_links(snapshot),
        clear_url=f"{reverse('events')}?{urlencode({'fookus': REGISTER_FOCUS})}",
        all_years_url=_url(replace(filters, year=None)),
        shows_registrations=bool(registration_pages),
    )


def _sort_options(
    filters: ProgrammeFilters, *, offer_registrations: bool = False
) -> tuple[SortOption, ...]:
    """The orderings, each carrying the current filters.

    A choice is only offered when it can change the picture: ranking by
    registrations on a dataset with no registration products would be a control
    that does nothing, which is how a reader learns to mistrust the page.
    """
    modes = [(SORT_DATE, "Kuupäev"), (SORT_VIEWS, "Enim vaadatud")]
    if offer_registrations:
        modes.append((SORT_REGISTRATIONS, "Enim registreerimisühikuid"))
    return tuple(
        SortOption(
            label=label,
            url=_url(replace(filters, sort=mode)),
            is_active=filters.sort == mode,
        )
        for mode, label in modes
    )


def _ranked_by_registrations(rows, pages) -> list:
    """The whole filtered set ordered by Commerce registration units.

    The same rule the traffic ranking follows, for the same reason: ranking a
    page of results ranks whichever fifty rows happened to be on it, which is
    not a ranking. And **an event with no Commerce product is not an event with
    zero registrations** — those rows keep their chronological order behind the
    measured ones rather than being sorted as though they had sold nothing.
    """
    population = list(rows)
    registrations = commerce.attach_registrations(population, pages=pages)

    measured, unmeasured = [], []
    for item in population:
        row = registrations.get(item.event_id)
        (measured if row is not None else unmeasured).append((row, item))
    measured.sort(key=lambda pair: (-pair[0].units, pair[1].event_id))
    return [item for _row, item in measured] + [item for _row, item in unmeasured]


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
    # The focus travels in every link this page emits, so a pagination click, a
    # sort chip or a data-quality link lands the reader back on the register
    # rather than dropping them on the overview with their filters intact and
    # nothing visibly filtered.
    query: list[tuple[str, str]] = [
        ("fookus", REGISTER_FOCUS),
        ("year", str(filters.year) if filters.year is not None else YEAR_ALL),
    ]
    for name, value in (
        ("q", filters.q),
        ("month", filters.month),
        ("quarter", filters.quarter),
        ("tag", filters.tag),
        ("event_type", filters.event_type),
        ("delivery_mode", filters.delivery_mode),
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
