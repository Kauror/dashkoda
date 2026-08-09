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

from django.db.models import Count, Max, Min, Q, QuerySet, Sum
from django.db.models.functions import TruncMonth, TruncWeek

from .ga4_paths import canonical_path
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
    page_rows: int = 0

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
    """One aggregate query for the span, one for the page rows."""
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
        page_rows=current_pages().count(),
    )


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


@dataclass(frozen=True)
class PageTotal:
    path: str
    page_views: int
    days_seen: int
    peak_active_users: int | None = None


def get_top_pages(
    *, start: date, end: date, limit: int = 20, prefix: str = ""
) -> tuple[PageTotal, ...]:
    """The most-viewed pages of a period, aggregated in the database.

    `prefix` narrows to a section — `/et/uudised` for news. Matched with
    `startswith` on a canonical path plus an exact match on the section index
    itself, which is what keeps `/et/uudiseks` out of the news list.
    """
    rows = current_pages().filter(report_date__gte=start, report_date__lte=end)
    if prefix:
        section = canonical_path(prefix)
        rows = rows.filter(Q(path=section) | Q(path__startswith=section + "/"))
    aggregated = (
        rows.values("path")
        .annotate(
            page_views=Sum("page_views"),
            days_seen=Count("id"),
            peak_active_users=Max("active_users"),
        )
        .order_by("-page_views")[:limit]
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

    totals = _sum_views(paths)
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
    "TrafficPoint",
    "TrafficSeries",
    "current_channels",
    "current_days",
    "current_pages",
    "get_article_views",
    "get_channel_totals",
    "get_coverage",
    "get_page_series",
    "get_top_pages",
    "get_traffic_series",
    "grain_for",
    "missing_dates",
]
