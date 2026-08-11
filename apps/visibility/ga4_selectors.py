"""What the stored GA4 history can answer, and what it must refuse to.

Every query here reads only **current** revisions: a superseded reading of a day
is provenance, not arithmetic, and summing both would count that Tuesday twice.

## The one rule worth stating twice

Sessions, page views and engaged sessions are **event counts**. They belong to
exactly one day, so adding a week of them gives that week's total, and every
aggregate below does that in the database.

Active users are **distinct people**. Monday's 400 and Tuesday's 380 are not
780 — most of them are the same people. There is no arithmetic that turns daily
distinct counts into a period distinct count; the only honest source of "how
many people in March" is a GA4 query whose date range *is* March.

So this module never sums `active_users`. Where a period figure is wanted, it
reports the **daily peak** and says that is what it is. A `users` column that
could be `SUM()`-ed would eventually be, and the resulting number would be
larger than the Chamber's audience and impossible to spot as wrong.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Count, IntegerField, Max, Min, OuterRef, Q, QuerySet, Subquery, Sum
from django.db.models.functions import TruncMonth, TruncWeek

from .content_ranking import (
    CONTENT_RANKING_EXACT_EXCLUSIONS,
    CONTENT_RANKING_PREFIX_EXCLUSIONS,
    ERROR_DOCUMENT_PREFIXES,
)
from .ga4_paths import canonical_path, canonical_paths
from .models import Ga4ChannelDaily, Ga4DailySnapshot, Ga4PageDaily

#: A period is drawn at one of these grains. Which one is chosen by span, not by
#: taste: 1 800 daily points on a five-year chart is a smear, and twelve monthly
#: points on a thirty-day chart is four bars.
GRAIN_DAY = "day"
GRAIN_WEEK = "week"
GRAIN_MONTH = "month"

#: Where each grain takes over, in days.
WEEKLY_FROM_DAYS = 120
MONTHLY_FROM_DAYS = 400


def grain_for(days: int) -> str:
    """The grain a span of `days` should be drawn at."""
    if days >= MONTHLY_FROM_DAYS:
        return GRAIN_MONTH
    if days >= WEEKLY_FROM_DAYS:
        return GRAIN_WEEK
    return GRAIN_DAY


def current_days() -> QuerySet[Ga4DailySnapshot]:
    """Every reporting day's current revision. The base of everything here."""
    return Ga4DailySnapshot.objects.filter(is_current_for_date=True)


def current_pages() -> QuerySet[Ga4PageDaily]:
    return Ga4PageDaily.objects.filter(snapshot__is_current_for_date=True)


def current_channels() -> QuerySet[Ga4ChannelDaily]:
    return Ga4ChannelDaily.objects.filter(snapshot__is_current_for_date=True)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Coverage:
    """What history exists, so the interface never implies more than there is.

    A five-year selector on a property with two years of data must show two
    years and say so, not draw three years of flat nothing before the tracking
    started. `missing_days` is the honest gap count inside the covered span.
    """

    earliest: date | None = None
    latest: date | None = None
    days_covered: int = 0
    days_with_pages: int = 0

    @property
    def has_data(self) -> bool:
        return self.earliest is not None and self.latest is not None

    @property
    def span_days(self) -> int:
        if not self.has_data:
            return 0
        return (self.latest - self.earliest).days + 1

    @property
    def missing_days(self) -> int:
        """Days inside the covered span with no current revision at all.

        Not the same as a day with no traffic: a day GA4 reported nothing for
        still has a revision, with absent figures. This counts days never
        collected.
        """
        return max(0, self.span_days - self.days_covered)


def get_coverage() -> Coverage:
    """One aggregate query. Called on every page that shows analytics."""
    span = current_days().aggregate(
        earliest=Min("report_date"),
        latest=Max("report_date"),
        days=Count("id"),
        with_pages=Count("id", filter=Q(has_page_detail=True)),
    )
    if span["earliest"] is None:
        return Coverage()
    return Coverage(
        earliest=span["earliest"],
        latest=span["latest"],
        days_covered=span["days"] or 0,
        days_with_pages=span["with_pages"] or 0,
    )


def count_page_rows() -> int:
    """How many page/day rows are stored. **Not** part of `get_coverage`.

    It was, and that was a `COUNT` over every page row — a hundred thousand of
    them once the history is filled — on every render of both the Nähtavus page
    and the news list, to produce a number neither page shows. It is an
    operator's figure, so `ga4_status` asks for it and nothing else does.
    """
    return current_pages().count()


def missing_dates(start: date, end: date) -> tuple[date, ...]:
    """Which days in a range have no current revision.

    For the status command and for a backfill that wants to know what is left,
    rather than re-reading a range that is already complete.
    """
    have = set(
        current_days()
        .filter(report_date__gte=start, report_date__lte=end)
        .values_list("report_date", flat=True)
    )
    span = (end - start).days
    return tuple(
        start + timedelta(days=offset)
        for offset in range(span + 1)
        if (start + timedelta(days=offset)) not in have
    )


# ---------------------------------------------------------------------------
# Site-wide series
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrafficPoint:
    """One point of a drawn series.

    `active_users` is the **peak day** inside the bucket, never a sum. On a
    daily grain the two are the same thing; on a monthly one only the peak is a
    number that means anything.
    """

    period_start: date
    sessions: int | None
    page_views: int | None
    engaged_sessions: int | None
    peak_active_users: int | None
    days: int

    @property
    def engagement_rate(self) -> float | None:
        if not self.sessions or self.engaged_sessions is None:
            return None
        return self.engaged_sessions / self.sessions


@dataclass(frozen=True)
class TrafficSeries:
    points: tuple[TrafficPoint, ...]
    grain: str
    start: date | None
    end: date | None

    @property
    def has_points(self) -> bool:
        return bool(self.points)

    @property
    def is_drawable(self) -> bool:
        """One point is a reading, not a trend, and is not drawn as one."""
        return len(self.points) >= 2

    @property
    def total_sessions(self) -> int | None:
        values = [point.sessions for point in self.points if point.sessions is not None]
        return sum(values) if values else None

    @property
    def total_page_views(self) -> int | None:
        values = [point.page_views for point in self.points if point.page_views is not None]
        return sum(values) if values else None

    @property
    def peak_active_users(self) -> int | None:
        """The busiest single day in the period. **Not** a period user count.

        Deliberately named for what it is. "Users in March" is a question only
        GA4 can answer, with March as its date range, because distinct people
        cannot be added up across days.
        """
        values = [
            point.peak_active_users for point in self.points if point.peak_active_users is not None
        ]
        return max(values) if values else None


def get_traffic_series(*, start: date, end: date, grain: str | None = None) -> TrafficSeries:
    """The site-wide series for a period, aggregated in the database.

    Grouping happens in PostgreSQL rather than in Python: five years is about
    1 800 rows, and pulling them into the process to bucket them by month is
    work the database does better and a habit that stops scaling exactly when
    the history gets interesting.
    """
    grain = grain or grain_for((end - start).days + 1)
    rows = current_days().filter(report_date__gte=start, report_date__lte=end)

    if grain == GRAIN_DAY:
        points = [
            TrafficPoint(
                period_start=row["report_date"],
                sessions=row["sessions"],
                page_views=row["page_views"],
                engaged_sessions=row["engaged_sessions"],
                peak_active_users=row["active_users"],
                days=1,
            )
            for row in rows.order_by("report_date").values(
                "report_date", "sessions", "page_views", "engaged_sessions", "active_users"
            )
        ]
        return TrafficSeries(points=tuple(points), grain=grain, start=start, end=end)

    bucket = TruncWeek("report_date") if grain == GRAIN_WEEK else TruncMonth("report_date")
    aggregated = (
        rows.annotate(bucket=bucket)
        .values("bucket")
        .annotate(
            sessions=Sum("sessions"),
            page_views=Sum("page_views"),
            engaged_sessions=Sum("engaged_sessions"),
            # Max, never Sum. See the module docstring.
            peak_active_users=Max("active_users"),
            days=Count("id"),
        )
        .order_by("bucket")
    )
    points = [
        TrafficPoint(
            period_start=row["bucket"],
            sessions=row["sessions"],
            page_views=row["page_views"],
            engaged_sessions=row["engaged_sessions"],
            peak_active_users=row["peak_active_users"],
            days=row["days"],
        )
        for row in aggregated
    ]
    return TrafficSeries(points=tuple(points), grain=grain, start=start, end=end)


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelTotal:
    channel: str
    sessions: int
    engaged_sessions: int | None

    def share_of(self, total: int | None) -> float | None:
        if not total:
            return None
        return self.sessions / total


def get_channel_totals(*, start: date, end: date, limit: int = 12) -> tuple[ChannelTotal, ...]:
    """Sessions by acquisition channel over a period, largest first.

    Additive because a session belongs to exactly one channel, which is the
    property that makes this table safe to sum and the users table not.
    """
    rows = (
        current_channels()
        .filter(report_date__gte=start, report_date__lte=end)
        .values("channel")
        .annotate(sessions=Sum("sessions"), engaged=Sum("engaged_sessions"))
        .order_by("-sessions")[:limit]
    )
    return tuple(
        ChannelTotal(
            channel=row["channel"], sessions=row["sessions"] or 0, engaged_sessions=row["engaged"]
        )
        for row in rows
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def only_rankable(rows: QuerySet[Ga4PageDaily]) -> QuerySet[Ga4PageDaily]:
    """Drop the utility paths from a page queryset, in the database.

    Applied to the **content ranking and content search only**. Site totals,
    the traffic series and the channel breakdown all read the untouched rows:
    `/et` is 133 588 real page views and stays in every figure that claims to
    count the website. What it may not do is compete with articles in a list of
    content — see `apps.visibility.content_ranking` for the whole rationale and
    for where the list came from.

    Excluding here rather than after the slice is what makes the Top 20 a top
    twenty *of content*. Filtering afterwards would leave a ranking of
    seventeen.
    """
    rows = rows.exclude(path__in=CONTENT_RANKING_EXACT_EXCLUSIONS)
    for prefix in ERROR_DOCUMENT_PREFIXES:
        rows = rows.exclude(path__startswith=prefix)
    for prefix in CONTENT_RANKING_PREFIX_EXCLUSIONS:
        # Whole segments: the prefix itself, or something beneath it. A plain
        # `startswith` would take `/en/services/search-cooperation-partner`,
        # which is a service the Chamber sells.
        rows = rows.exclude(Q(path=prefix) | Q(path__startswith=prefix + "/"))
    return rows


@dataclass(frozen=True)
class PageTotal:
    path: str
    page_views: int
    days_seen: int
    peak_active_users: int | None = None


def get_top_pages(
    *,
    start: date,
    end: date,
    limit: int = 20,
    prefix: str | Sequence[str] = "",
    exclude: Sequence[str] = (),
) -> tuple[PageTotal, ...]:
    """The most-viewed pages of a period, aggregated in the database.

    `exclude` drops exact paths — the section listing pages, which are not
    content and would top every ranking of it.

    `prefix` narrows to a section, and takes either one prefix or several: a
    section is `/et/uudised` **and** `/en/news`, because a translated article is
    the same content and dropping one of them undercounts it.

    Matching is by whole path segment — the section root, or something beneath
    it — which is what keeps `/et/uudiseks` out of the news list. Aggregation
    and ordering both happen in PostgreSQL; only the bounded top slice is
    returned, so enriching it with titles later touches a handful of rows rather
    than a year of them.
    """
    rows = current_pages().filter(report_date__gte=start, report_date__lte=end)

    wanted = (prefix,) if isinstance(prefix, str) else tuple(prefix or ())
    section_filter = Q()
    for one in wanted:
        if not one:
            continue
        section = canonical_path(one)
        # Whole segments: the section root itself, or something under it.
        # `startswith` alone would file `/et/uudiseks` under `/et/uudised`.
        section_filter |= Q(path=section) | Q(path__startswith=section + "/")
    if section_filter:
        rows = rows.filter(section_filter)

    # Listing pages are left out by exact path. A section index collects the
    # traffic of everyone on their way to an article and would otherwise sit
    # permanently above the articles themselves, which is not a ranking of
    # content — it is the same page winning every time.
    left_out = tuple(canonical_path(path) for path in exclude if path)
    if left_out:
        rows = rows.exclude(path__in=left_out)

    rows = only_rankable(rows)

    aggregated = (
        rows.values("path")
        .annotate(
            page_views=Sum("page_views"),
            days_seen=Count("id"),
            peak_active_users=Max("active_users"),
        )
        # `path` breaks ties, so equal-view pages keep a stable order between
        # renders and a test cannot pass or fail on row ordering luck.
        .order_by("-page_views", "path")[:limit]
    )
    return tuple(
        PageTotal(
            path=row["path"],
            page_views=row["page_views"] or 0,
            days_seen=row["days_seen"],
            peak_active_users=row["peak_active_users"],
        )
        for row in aggregated
    )


#: How long a search term may be. Bounded so a pasted essay cannot become a
#: `LIKE` over forty thousand paths.
MAX_SEARCH_LENGTH = 120


@dataclass(frozen=True)
class PageMatch:
    """One page a search found, with both figures a reader needs.

    The two are different questions and are never the same number by
    construction: `period_views` is what this page did inside the window the
    reader chose, `total_views` is everything GA4 has ever measured for it. A
    page can be busy this month and rare overall, or the reverse, and collapsing
    them into one column would hide whichever the reader was actually asking
    about.
    """

    path: str
    period_views: int
    total_views: int
    days_seen: int


def search_pages(
    *,
    term: str,
    start: date,
    end: date,
    extra_paths: Iterable[str] = (),
    prefix: str | Sequence[str] = "",
    exclude: Sequence[str] = (),
    limit: int = 50,
    offset: int = 0,
) -> tuple[tuple[PageMatch, ...], int]:
    """Pages whose path matches `term`, or which `extra_paths` names.

    Returns the requested slice and the **total** number of matches, so a caller
    can paginate without counting rows itself.

    Searched over the whole measured population rather than over a ranking:
    a page sitting at #347 is exactly the kind of page somebody searches for,
    and searching the Top 20 would answer only for pages the reader could
    already see.

    `extra_paths` is how a title search reaches here. The catalogues know that
    "islandi" names `/et/sundmused/eesti-islandi-arifoorum`; this module knows
    nothing about titles and does not need to — it is handed the paths and
    unions them with its own path matches.

    Three queries whatever the result count: one for the period figures, one for
    the totals, one for the count. Nothing here runs per row.
    """
    term = (term or "").strip()[:MAX_SEARCH_LENGTH]
    extra = tuple(dict.fromkeys(canonical_paths(extra_paths)))
    if not term and not extra:
        return (), 0

    rows = current_pages().filter(report_date__gte=start, report_date__lte=end)

    wanted = (prefix,) if isinstance(prefix, str) else tuple(prefix or ())
    section_filter = Q()
    for one in wanted:
        if not one:
            continue
        section = canonical_path(one)
        section_filter |= Q(path=section) | Q(path__startswith=section + "/")
    if section_filter:
        rows = rows.filter(section_filter)

    left_out = tuple(canonical_path(path) for path in exclude if path)
    if left_out:
        rows = rows.exclude(path__in=left_out)
    # A search is a search of *content*, so the same utility paths stay out.
    # `/et/search/node` is not a result for the word "search".
    rows = only_rankable(rows)

    matching = Q()
    if term:
        # `icontains` on the stored canonical path. Parameterised by the ORM —
        # the term never becomes SQL, and it is never used as a regex.
        matching |= Q(path__icontains=term)
        # A reader pastes what the browser gave them. `canonical_path` turns
        # `https://www.koda.ee/et/liikmed/liikmemaks` into the form actually
        # stored; without this the paste matches nothing, because no stored
        # path contains the host.
        canonical_term = canonical_path(term)
        if canonical_term and canonical_term != term:
            matching |= Q(path__icontains=canonical_term)
    if extra:
        matching |= Q(path__in=extra)
    rows = rows.filter(matching)

    aggregated = (
        rows.values("path")
        .annotate(period_views=Sum("page_views"), days_seen=Count("id"))
        # Busiest first inside the chosen period, which is the question the
        # reader asked; path breaks ties so the order is stable between renders.
        .order_by("-period_views", "path")
    )
    total_matches = aggregated.count()
    page = list(aggregated[offset : offset + limit])

    # One grouped query for the totals of exactly the rows being shown, rather
    # than one `SUM` per result.
    totals = get_page_view_totals(row["path"] for row in page)

    return (
        tuple(
            PageMatch(
                path=row["path"],
                period_views=row["period_views"] or 0,
                total_views=totals[row["path"]].total if row["path"] in totals else 0,
                days_seen=row["days_seen"],
            )
            for row in page
        ),
        total_matches,
    )


@dataclass(frozen=True)
class PageViews:
    """Total measured page views for one canonical path.

    **All of GA4's coverage, not a chosen period.** This is the figure that sits
    beside an item — an article, an event — and answers "how much traffic has
    this page had". The period-filtered figure is a different question with a
    different answer, and `get_top_pages` is what asks it.

    `coverage_start` travels with the number because the two are only meaningful
    together: 291 views on a page published in 2019 is 291 views *since GA4
    started measuring*, which is not the same claim as 291 views ever.
    """

    path: str
    total: int
    coverage_start: date | None = None
    coverage_end: date | None = None

    def covers(self, published_on: date | None) -> bool:
        """Whether measurement was already running when this page appeared.

        False makes `total` a partial figure, and the interface has to stop
        short of calling it a lifetime.
        """
        if published_on is None or self.coverage_start is None:
            return False
        return self.coverage_start <= published_on


def get_page_view_totals(
    urls_or_paths: Iterable[str], *, coverage: Coverage | None = None
) -> dict[str, PageViews]:
    """Total measured views for many pages, in **one** grouped query.

    The shared answer to "how much traffic has this page had", used by news,
    events, the overview and anything added later. Each module resolving its own
    `SUM(page_views)` is how two surfaces end up printing different totals for
    one article.

    Input may be canonical URLs or bare paths, in any mixture: both go through
    `canonical_path`, which is the only join key this application uses. A path
    with no stored rows is **absent from the result**, never present with a
    zero — nobody measuring a page is not the same as a page nobody visited.
    """
    paths = canonical_paths(urls_or_paths)
    if not paths:
        return {}

    coverage = coverage if coverage is not None else get_coverage()
    rows = current_pages().filter(path__in=paths).values("path").annotate(total=Sum("page_views"))
    return {
        row["path"]: PageViews(
            path=row["path"],
            # `Sum` of an empty group cannot happen here — a row exists — but a
            # measured zero can, and it is a reading rather than an absence.
            total=row["total"] or 0,
            coverage_start=coverage.earliest,
            coverage_end=coverage.latest,
        )
        for row in rows
    }


def page_view_total_subquery(path_field: str = "path"):
    """The figure `get_page_view_totals` returns, as an annotation.

    The same total, the same rows, the same definition — expressed so a caller
    can *order and paginate by it in PostgreSQL* instead of pulling the whole
    population into Python to sort it. The news archive needs exactly that: its
    "Enim vaadatud" ordering has to run across every catalogued article before
    the page slice, and there are twelve hundred of them.

    It lives here, beside the dictionary version, for the reason stated there:
    each module resolving its own `SUM(page_views)` is how two surfaces end up
    printing different totals for one article. Two spellings of one definition
    are acceptable; two definitions are not, and
    `tests/visibility/test_page_view_totals.py` holds them to each other.

    `None` where nothing was measured, never `0`, so a caller can order with
    `nulls_last=True` and keep "unmeasured" behind "measured zero" — which are
    different facts.
    """
    return Subquery(
        current_pages()
        .filter(path=OuterRef(path_field))
        .values("path")
        .annotate(total=Sum("page_views"))
        .values("total")[:1],
        output_field=IntegerField(),
    )


def get_page_series(
    *, path: str, start: date, end: date, grain: str | None = None
) -> TrafficSeries:
    """One page's own history, at the grain its span deserves."""
    grain = grain or grain_for((end - start).days + 1)
    rows = current_pages().filter(
        path=canonical_path(path), report_date__gte=start, report_date__lte=end
    )

    if grain == GRAIN_DAY:
        points = [
            TrafficPoint(
                period_start=row["report_date"],
                sessions=None,
                page_views=row["page_views"],
                engaged_sessions=None,
                peak_active_users=row["active_users"],
                days=1,
            )
            for row in rows.order_by("report_date").values(
                "report_date", "page_views", "active_users"
            )
        ]
        return TrafficSeries(points=tuple(points), grain=grain, start=start, end=end)

    bucket = TruncWeek("report_date") if grain == GRAIN_WEEK else TruncMonth("report_date")
    aggregated = (
        rows.annotate(bucket=bucket)
        .values("bucket")
        .annotate(
            page_views=Sum("page_views"),
            peak_active_users=Max("active_users"),
            days=Count("id"),
        )
        .order_by("bucket")
    )
    points = [
        TrafficPoint(
            period_start=row["bucket"],
            sessions=None,
            page_views=row["page_views"],
            engaged_sessions=None,
            peak_active_users=row["peak_active_users"],
            days=row["days"],
        )
        for row in aggregated
    ]
    return TrafficSeries(points=tuple(points), grain=grain, start=start, end=end)


# ---------------------------------------------------------------------------
# Article performance
# ---------------------------------------------------------------------------

#: The two windows an article is judged on. Seven days is the news cycle; thirty
#: is whether anything kept finding it.
FIRST_WINDOW_DAYS = 7
SECOND_WINDOW_DAYS = 30
RECENT_WINDOW_DAYS = 30


@dataclass(frozen=True)
class ArticleViews:
    """One article's page views, with the honesty about coverage attached.

    `total` is views inside GA4's coverage, which is **not** a lifetime figure
    for anything published before collection began. `covers_publication` is what
    the interface must consult before using the word "kokku" without a caveat.
    """

    path: str
    published_on: date | None = None
    first_7_days: int | None = None
    first_30_days: int | None = None
    last_30_days: int | None = None
    total: int | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None

    @property
    def has_data(self) -> bool:
        return self.total is not None

    @property
    def covers_publication(self) -> bool:
        """Whether GA4 was already collecting when this was published.

        False makes every "first week" figure meaningless and the total a
        partial one, and the interface has to say so rather than printing a
        number that reads like a lifetime count.
        """
        if self.published_on is None or self.coverage_start is None:
            return False
        return self.coverage_start <= self.published_on

    @property
    def first_windows_are_complete(self) -> bool:
        """Whether the first-week and first-month windows have fully elapsed
        inside coverage. A two-day-old article has no thirty-day figure."""
        if not self.covers_publication or self.coverage_end is None:
            return False
        return self.published_on + timedelta(days=SECOND_WINDOW_DAYS - 1) <= self.coverage_end


def get_article_views(
    items: Iterable, *, today: date | None = None, coverage: Coverage | None = None
) -> dict[str, ArticleViews]:
    """Views for many articles at once, keyed by canonical path.

    **Bulk by construction.** `items` is anything with `canonical_url` and
    `published_at`; every window for every article is answered by one grouped
    query per window, not one query per article. A news list of fifty items
    costs four queries, not two hundred.

    Articles whose URL cannot be canonicalised are absent from the result rather
    than present with zeros — an unmatchable article has not been measured at
    zero views.
    """
    coverage = coverage if coverage is not None else get_coverage()
    if not coverage.has_data:
        return {}

    published: dict[str, date] = {}
    for item in items:
        path = canonical_path(getattr(item, "canonical_url", None))
        if not path:
            continue
        moment = getattr(item, "published_at", None)
        day = moment.date() if hasattr(moment, "date") else moment
        # The earliest publication wins when the same path appears twice: a
        # later snapshot repeats the article, it does not republish it.
        if day is not None and (path not in published or day < published[path]):
            published[path] = day
        published.setdefault(path, day)

    if not published:
        return {}

    paths = tuple(published)
    today = today or coverage.latest

    # The total comes from the shared selector rather than from a second `SUM`
    # written here. One source of truth: the figure beside an article on the
    # news page, on the overview and in any later module is the same number
    # produced by the same query, because there is only one query.
    totals = {
        path: views.total for path, views in get_page_view_totals(paths, coverage=coverage).items()
    }
    recent_start = today - timedelta(days=RECENT_WINDOW_DAYS - 1)
    recent = _sum_views(paths, start=recent_start, end=today)

    # One query per window across every article, with each article's own date
    # range expressed as a (path, report_date) pair inside a single filter.
    first_7 = _sum_views_from_publication(published, days=FIRST_WINDOW_DAYS)
    first_30 = _sum_views_from_publication(published, days=SECOND_WINDOW_DAYS)

    return {
        path: ArticleViews(
            path=path,
            published_on=published[path],
            first_7_days=first_7.get(path),
            first_30_days=first_30.get(path),
            last_30_days=recent.get(path),
            total=totals.get(path),
            coverage_start=coverage.earliest,
            coverage_end=coverage.latest,
        )
        for path in paths
    }


def _sum_views(
    paths: Sequence[str], *, start: date | None = None, end: date | None = None
) -> dict[str, int]:
    rows = current_pages().filter(path__in=paths)
    if start is not None:
        rows = rows.filter(report_date__gte=start)
    if end is not None:
        rows = rows.filter(report_date__lte=end)
    return {
        row["path"]: row["views"] or 0
        for row in rows.values("path").annotate(views=Sum("page_views"))
    }


def _sum_views_from_publication(published: dict[str, date], *, days: int) -> dict[str, int]:
    """Views in the first `days` days of each article's own life, in one query.

    The per-article window is different for every article, so the filter is a
    disjunction of (path, date range) pairs rather than one shared range. It is
    still a single round trip, which is the property that matters on a list
    page; the alternative — a query per article — is the N+1 this exists to
    avoid.
    """
    if not published:
        return {}
    condition = Q()
    for path, day in published.items():
        if day is None:
            continue
        condition |= Q(
            path=path, report_date__gte=day, report_date__lte=day + timedelta(days=days - 1)
        )
    if not condition:
        return {}
    rows = current_pages().filter(condition).values("path").annotate(views=Sum("page_views"))
    return {row["path"]: row["views"] or 0 for row in rows}


__all__ = [
    "FIRST_WINDOW_DAYS",
    "GRAIN_DAY",
    "GRAIN_MONTH",
    "GRAIN_WEEK",
    "RECENT_WINDOW_DAYS",
    "SECOND_WINDOW_DAYS",
    "ArticleViews",
    "ChannelTotal",
    "Coverage",
    "PageTotal",
    "PageViews",
    "TrafficPoint",
    "TrafficSeries",
    "current_channels",
    "current_days",
    "current_pages",
    "get_article_views",
    "get_channel_totals",
    "count_page_rows",
    "get_coverage",
    "get_page_series",
    "get_page_view_totals",
    "page_view_total_subquery",
    "get_top_pages",
    "get_traffic_series",
    "grain_for",
    "missing_dates",
]
