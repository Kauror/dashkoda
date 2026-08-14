"""Age-normalised news performance, computed from stored GA4 facts.

This module exists to make one comparison honest: **an article published nine
days ago and an article published in 2024 cannot be ranked against each other by
their totals.** The older one has had two more years to collect views, and a
ranking that ignores that measures age rather than performance.

So the analytical unit here is a *window of an article's own life* — its first
seven days, its first thirty — which every article passes through exactly once
and which therefore compares like with like.

## The two time questions never meet in one control

`apps/news/periods.py` owns the **publication** window ("which articles did we
publish"). This module owns the **measurement** window ("which pages were read"),
and the two are separate parameters with separate labels. An article published in
2024 is eligible for `Loetakse praegu` and invisible to `Avaldatud: 30 p`; that is
not a bug in either, it is the distinction working.

## What counts as an article

Catalogue membership, minus the listing pages — and the second half is not
optional. `NewsResource` holds a row for `/et/uudised` (26 315 measured views),
`/en/news` (13 568) and three more section indexes, because the discovery crawl
met them where it met the articles. Counting them as articles inflated the
Chamber's measured news reading by 21% in the window this was written against
(1 651 against a true 1 359), and the inflation is invisible in the total: it
looks like a well-read article, because in a sense it is one.

`section_of` cannot help here — it answers "is this path in the news section",
and a listing page is. The exclusion is by whole path, from a registry built by
reading the stored catalogue rather than by guessing at shapes.

## Missing is not zero, and neither is zero missing

The distinction the rest of DashKoda holds — an unmeasured page is absent, never
`0` — has one careful exception here, and it is a fact about the data rather
than a convenience:

- every one of the 3 619 catalogue paths appears in GA4 at least once, so a
  catalogued article is never a page GA4 has not heard of;
- GA4 coverage has **no missing reporting days** across its 1 155-day span, and
  `missing_days_within` re-checks that for the exact window rather than trusting
  it;
- a day with a collected snapshot and no row for a page means nobody opened that
  page that day. That is a measurement, not a gap.

So when an article's whole first-30-day window lies inside collected coverage and
GA4 recorded nothing for it, the honest figure is **0 views, measured** — 41
articles are in exactly that state. When any part of the window is outside
coverage, or the window has not finished, the figure is `None` and the interface
prints `—`. `ArticleWindow.is_eligible` is the one place that decides which.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import (
    Count,
    DateField,
    ExpressionWrapper,
    F,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
)
from django.db.models.functions import TruncDate

from apps.visibility.ga4_selectors import (
    Coverage,
    current_days,
    current_pages,
    get_coverage,
)

from .categories import NewsCategory
from .public_models import NewsResource

#: The first-window lengths, in days. An article's first week is its publication
#: day plus the six after it; its first month is publication plus twenty-nine.
#: Both are inclusive of the publication day, which is why the arithmetic below
#: subtracts one rather than adding a whole span.
FIRST_WEEK_DAYS = 7
FIRST_MONTH_DAYS = 30

#: Catalogue rows that are section indexes rather than articles.
#:
#: Read out of the stored catalogue on 2026-08-14, not guessed: each of these is
#: a real `NewsResource` row that the discovery crawl recorded alongside the
#: articles, and each carries substantial traffic that is *browsing towards* news
#: rather than reading of it. `/et/uudised` alone holds 26 315 measured views.
#:
#: Matching is by **whole path**, never by prefix. `/et/uudised/arhiiv` is a
#: listing and `/et/uudised/arhiivinduse-seadus-muutub` would be an article, and
#: a `startswith` rule cannot tell them apart.
NEWS_LISTING_PATHS: frozenset[str] = frozenset(
    {
        "/et/uudised",
        "/et/uudised/meie_uudised",
        "/et/uudised/soprade_uudised",
        "/et/uudised/arhiiv",
        "/en/news",
        "/en/news/our-news",
        "/en/news/soprade_uudised",
        "/en/news/archive",
        "/en/news/artiklid",
    }
)

#: How many comparable articles a benchmark needs before it is worth quoting.
#:
#: A median of three articles is a coincidence with a decimal point. Eight is the
#: floor; in practice both real categories clear it by two orders of magnitude
#: (Koja 973 eligible articles, Sõprade 285), so this guards the empty and the
#: early-days cases rather than the ordinary one.
MIN_BENCHMARK_COHORT = 8

#: How far back the benchmark cohort reaches. Twelve months of eligible articles
#: was 372 rows when this was written — large enough for a stable median, recent
#: enough that it describes how the Chamber publishes now rather than in 2023.
BENCHMARK_COHORT_DAYS = 365

#: How old an article must be before continued reading is *evergreen* rather than
#: simply the tail of its launch. Ninety days is past both first windows, so
#: nothing here is still collecting its opening attention.
EVERGREEN_MIN_AGE_DAYS = 90


def article_resources() -> QuerySet[NewsResource]:
    """Catalogue rows that are articles: everything except the section indexes.

    The one definition of "a news article" in this application. Every figure on
    the Uudised dashboard — the view totals, the medians, the concentration, the
    share of site traffic — filters through here, so no two of them can disagree
    about what they are counting.
    """
    return NewsResource.objects.exclude(path__in=NEWS_LISTING_PATHS)


def published_day() -> TruncDate:
    """The article's publication *day* in the application's timezone.

    `published_at` is a moment, and every window in this module is a range of
    dates. Truncating in Europe/Tallinn rather than UTC is what keeps an article
    posted at half past midnight on the fourteenth from being filed under the
    thirteenth — which is the date its own editor would give it, and the date the
    archive's publication filter already uses.
    """
    return TruncDate("published_at")


#: The annotations `annotate_first_window` puts on the outer queryset so the
#: subquery has an article's own window boundaries to compare against.
PUBLISHED_DAY_ANNOTATION = "published_day"
WINDOW_END_ANNOTATION = "window_end"


def with_window_bounds(queryset: QuerySet[NewsResource], *, days: int) -> QuerySet[NewsResource]:
    """Annotate each row with its own first-`days` window, as two dates.

    Applied once, by whichever helper builds the population. The boundaries have
    to exist on the **outer** row before a correlated subquery can compare
    against them: computing them inside the subquery would resolve
    `published_at` against `Ga4PageDaily`, which has no such column.
    """
    return queryset.annotate(
        **{
            PUBLISHED_DAY_ANNOTATION: published_day(),
            WINDOW_END_ANNOTATION: ExpressionWrapper(
                published_day() + timedelta(days=days - 1),
                output_field=DateField(),
            ),
        }
    )


def annotate_first_window(queryset: QuerySet[NewsResource], *, name: str) -> QuerySet[NewsResource]:
    """Give each row the views inside its own first window, as `name`.

    The whole population, ranked and aggregated **in PostgreSQL**. One query
    answers for twelve hundred articles; the alternative — a `Q` object per
    article OR-ed into one filter, or worse a query each — is what a page of
    thirty rows cannot afford and a cohort of a thousand certainly cannot.

    Expects `with_window_bounds` to have run, so the window is whatever length
    that call chose. The sum is `None` where GA4 recorded nothing, never `0`:
    whether that absence means "not measured" or "measured, and nobody read it"
    is a question about *coverage*, and `resolve_window` is where it is answered.
    """
    return queryset.annotate(
        **{
            name: Subquery(
                current_pages()
                .filter(
                    path=OuterRef("path"),
                    report_date__gte=OuterRef(PUBLISHED_DAY_ANNOTATION),
                    report_date__lte=OuterRef(WINDOW_END_ANNOTATION),
                )
                .values("path")
                .annotate(total=Sum("page_views"))
                .values("total")[:1],
                output_field=IntegerField(),
            )
        }
    )


def window_views_subquery(start: date, end: date) -> Subquery:
    """Views inside one shared measurement window, as an annotation.

    The same shape as `first_window_subquery` for a range that is the same for
    every article — "how much was this read during the last thirty days" — which
    is the *measurement* question rather than the age-normalised one.
    """
    return Subquery(
        current_pages()
        .filter(path=OuterRef("path"), report_date__gte=start, report_date__lte=end)
        .values("path")
        .annotate(total=Sum("page_views"))
        .values("total")[:1],
        output_field=IntegerField(),
    )


def missing_days_within(start: date, end: date) -> int:
    """Reporting days in `[start, end]` that were never collected.

    One aggregate query. This is what lets an empty window be read as a measured
    zero: a window with no missing days and no page rows was watched throughout
    and saw nobody, which is a finding. A window with a hole in it is not.
    """
    if start > end:
        return 0
    collected = current_days().filter(report_date__gte=start, report_date__lte=end).count()
    return max(0, (end - start).days + 1 - collected)


@dataclass(frozen=True)
class ArticleWindow:
    """One article's first-window result, with the reason it may not have one.

    `views` is `None` for every article the window cannot fairly describe, and
    the interface prints `—` rather than a number. There are three separate ways
    to earn that, and `reason` keeps them apart so an empty state can say which:
    the article predates measurement, its window has not finished yet, or the
    window has a collection gap in it.
    """

    days: int
    views: int | None = None
    reason: str = ""

    @property
    def is_eligible(self) -> bool:
        return self.views is not None


#: Why a first-window figure is unavailable. Rendered as a title attribute and in
#: the methodology disclosure; never as a number.
REASON_BEFORE_COVERAGE = "enne mõõtmise algust"
REASON_NOT_ELAPSED = "aken pole veel täis"
REASON_COVERAGE_GAP = "mõõtmises on lünk"


def resolve_window(
    *,
    published_on: date | None,
    raw_views: int | None,
    days: int,
    coverage: Coverage,
    gap_check=missing_days_within,
) -> ArticleWindow:
    """Turn a raw window sum into a figure the page may print, or a reason.

    The eligibility rules, in the order they can disqualify an article:

    1. **no publication date** — the window has no beginning;
    2. **published before collection started** — GA4 never saw the article's
       opening days, so the sum is of whatever part happened to be measured;
    3. **the window has not finished inside coverage** — a six-day-old article
       has no thirty-day result, and printing its partial sum as one would
       overstate nothing and understate everything;
    4. **the window contains an uncollected day** — the sum is missing an unknown
       amount.

    An article that survives all four gets its sum, and gets `0` if GA4 recorded
    nothing: the window was watched from end to end and nobody arrived.
    """
    if published_on is None:
        return ArticleWindow(days=days, reason=REASON_BEFORE_COVERAGE)
    if not coverage.has_data or coverage.earliest is None or coverage.latest is None:
        return ArticleWindow(days=days, reason=REASON_BEFORE_COVERAGE)
    if published_on < coverage.earliest:
        return ArticleWindow(days=days, reason=REASON_BEFORE_COVERAGE)

    window_end = published_on + timedelta(days=days - 1)
    if window_end > coverage.latest:
        return ArticleWindow(days=days, reason=REASON_NOT_ELAPSED)
    if gap_check(published_on, window_end):
        return ArticleWindow(days=days, reason=REASON_COVERAGE_GAP)

    return ArticleWindow(days=days, views=raw_views or 0)


def eligible_cohort(
    *,
    days: int,
    coverage: Coverage,
    since: date | None = None,
    category: str = "",
) -> QuerySet[NewsResource]:
    """Articles whose whole first-`days` window lies inside collected coverage.

    Expressed as a queryset filter rather than as a Python loop, because the
    cohort is the population a median is taken over and it has to be counted
    before it is sliced. The gap rule is not applied per row here — coverage is
    contiguous, `missing_days_within` verifies it for the span once, and a cohort
    is withheld entirely rather than silently thinned if that ever stops holding.

    **The window bounds are annotated even when the cohort is empty.** Returning a
    bare `none()` for a property with no GA4 data at all would satisfy every test
    that seeds some, and then raise `FieldError` in production the moment
    `annotate_first_window` looked for the `OuterRef` columns that were never
    added. An empty cohort is still a cohort of this shape.
    """
    empty = not coverage.has_data or coverage.earliest is None or coverage.latest is None
    if empty:
        return with_window_bounds(NewsResource.objects.none(), days=days)

    latest_publication = coverage.latest - timedelta(days=days - 1)
    rows = with_window_bounds(
        article_resources().filter(published_at__isnull=False), days=days
    ).filter(
        **{
            f"{PUBLISHED_DAY_ANNOTATION}__gte": coverage.earliest,
            f"{PUBLISHED_DAY_ANNOTATION}__lte": latest_publication,
        }
    )
    if since is not None:
        rows = rows.filter(**{f"{PUBLISHED_DAY_ANNOTATION}__gte": since})
    if category:
        rows = rows.filter(category=category)
    return rows


@dataclass(frozen=True)
class CohortStats:
    """What "normal" looks like for a set of comparable articles.

    Quartiles rather than a mean, because the distribution is heavily skewed —
    a median of 28 against a mean of 66 and a maximum of 1 875. A mean describes
    none of these articles; the median describes the middle one and the quartiles
    describe the spread it sits in.
    """

    label: str
    count: int = 0
    p25: int | None = None
    median: int | None = None
    p75: int | None = None

    @property
    def is_usable(self) -> bool:
        """Whether this cohort may be quoted as a benchmark at all."""
        return self.count >= MIN_BENCHMARK_COHORT and self.median is not None


def percentile(values: list[int], fraction: float) -> int | None:
    """Linear-interpolated percentile over an already-sorted list.

    The same method PostgreSQL's `percentile_cont` uses, so a figure computed
    here matches one checked with SQL against the same rows. Written out rather
    than imported because `statistics.quantiles` cuts a distribution into equal
    parts and does not answer for an arbitrary fraction of a short list.
    """
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    position = fraction * (len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight)


def cohort_stats(
    values: list[int],
    *,
    label: str = "",
) -> CohortStats:
    """Quartiles for a bounded cohort, computed in Python.

    The cohort is bounded by construction — one category's eligible articles from
    the last twelve months, 372 rows across both categories when this was written
    — so one column of integers reaches this process and the arithmetic is
    trivial. A population-wide ranking is a different problem and stays in
    PostgreSQL; see `first_window_subquery`.
    """
    ordered = sorted(values)
    return CohortStats(
        label=label,
        count=len(ordered),
        p25=percentile(ordered, 0.25),
        median=percentile(ordered, 0.5),
        p75=percentile(ordered, 0.75),
    )


#: The annotation name a first-window sum arrives under.
FIRST_WINDOW_ANNOTATION = "first_window_views"
#: The annotation name a measurement-window sum arrives under.
WINDOW_ANNOTATION = "window_views"


def cohort_values(
    *,
    days: int,
    coverage: Coverage,
    since: date | None = None,
    category: str = "",
) -> list[int]:
    """Every eligible article's first-window result, as a plain list of counts.

    An article GA4 recorded nothing for contributes `0`, not nothing: it is an
    article that was published, watched for its whole first month and read by
    nobody, and dropping it from the median would quietly describe only the
    articles that worked.
    """
    rows = annotate_first_window(
        eligible_cohort(days=days, coverage=coverage, since=since, category=category),
        name=FIRST_WINDOW_ANNOTATION,
    )
    return [row or 0 for row in rows.values_list(FIRST_WINDOW_ANNOTATION, flat=True)]


def benchmark_cohorts(
    *,
    coverage: Coverage,
    today: date | None = None,
    days: int = FIRST_MONTH_DAYS,
) -> dict[str, CohortStats]:
    """Normal first-month performance, per category and overall.

    Keyed by category value, with `""` holding the all-news cohort that a
    category too small to speak for itself falls back to.

    **The categories are benchmarked separately because they perform very
    differently**: Koja uudised have a median first month of 36 views and
    Sõprade uudised of 10. Judging a partner's news against the combined median
    of 28 would mark most of them below normal, which is not a finding about
    those articles — it is a finding about which cohort they were compared with.
    """
    if not coverage.has_data or coverage.latest is None:
        return {}
    today = today or coverage.latest
    since = today - timedelta(days=BENCHMARK_COHORT_DAYS - 1)

    cohorts: dict[str, CohortStats] = {
        "": cohort_stats(
            cohort_values(days=days, coverage=coverage, since=since),
            label="Kõik uudised",
        )
    }
    for value, label in NewsCategory.choices:
        cohorts[value] = cohort_stats(
            cohort_values(days=days, coverage=coverage, since=since, category=value),
            label=label,
        )
    return cohorts


def benchmark_for(cohorts: dict[str, CohortStats], category: str) -> CohortStats | None:
    """The cohort an article should be compared against.

    Its own category when that cohort is large enough to mean something, all news
    when it is not, and nothing at all when even that is too small — in which
    case the comparison is **unavailable**, which is a different statement from a
    comparison that came out at zero.
    """
    own = cohorts.get(category)
    if own is not None and own.is_usable:
        return own
    everything = cohorts.get("")
    if everything is not None and everything.is_usable:
        return everything
    return None


@dataclass(frozen=True)
class Benchmark:
    """One article measured against comparable articles.

    A ratio and a difference, both of which the reader can check against the two
    numbers beside them. Deliberately **not** a score: a 0–100 index would hide
    which cohort it came from, and the cohort is the part that decides the
    answer.
    """

    cohort: CohortStats
    views: int
    #: `views / median`, or `None` where the cohort median is zero and the ratio
    #: would divide by it.
    ratio: float | None = None

    @property
    def difference(self) -> int | None:
        if self.cohort.median is None:
            return None
        return self.views - self.cohort.median

    @property
    def is_below_normal(self) -> bool:
        """Whether this sits in the weakest quarter of comparable articles.

        The 25th percentile, taken from the cohort's own distribution, rather
        than a percentage of the median chosen by hand. It is a description of
        where the article falls — not a verdict: the data does not know what the
        article was for, who it was aimed at or whether anybody promoted it.
        """
        return self.cohort.p25 is not None and self.views < self.cohort.p25

    @property
    def is_above_normal(self) -> bool:
        return self.cohort.p75 is not None and self.views > self.cohort.p75


def benchmark(views: int, cohort: CohortStats | None) -> Benchmark | None:
    if cohort is None or not cohort.is_usable or cohort.median is None:
        return None
    return Benchmark(
        cohort=cohort,
        views=views,
        ratio=(views / cohort.median) if cohort.median else None,
    )


# ---------------------------------------------------------------------------
# Measurement-window questions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewsTrafficSummary:
    """How much news was read in a measurement window, and how much of the site.

    Both figures are additive GA4 page views over the same days, which is the
    only way the share below means anything. Active users are not used and
    cannot be: they are distinct people per day and do not add up across a
    period, so a share built from them would have a denominator nobody can
    define.
    """

    start: date | None = None
    end: date | None = None
    news_views: int | None = None
    site_views: int | None = None
    articles_read: int = 0

    @property
    def has_data(self) -> bool:
        return self.news_views is not None

    @property
    def share(self) -> float | None:
        """News page views as a share of all page views in the same window."""
        if not self.site_views or self.news_views is None:
            return None
        return self.news_views / self.site_views


def news_traffic(*, start: date, end: date) -> NewsTrafficSummary:
    """News reading and site reading over one window, in two aggregates.

    The numerator joins GA4 page rows to the article catalogue, so a page counts
    as news because `NewsResource` says it is an article — never because its path
    contains `uudised`. The denominator is the site's own daily page-view total.

    Both are `screenPageViews` over the same days. A numerator of sessions over a
    denominator of page views would produce a percentage that looks fine and
    means nothing.
    """
    article_paths = article_resources().values("path")
    news = (
        current_pages()
        .filter(report_date__gte=start, report_date__lte=end, path__in=Subquery(article_paths))
        .aggregate(views=Sum("page_views"))
    )
    site = (
        current_days()
        .filter(report_date__gte=start, report_date__lte=end)
        .aggregate(views=Sum("page_views"))
    )
    read = (
        current_pages()
        .filter(report_date__gte=start, report_date__lte=end, path__in=Subquery(article_paths))
        .values("path")
        .distinct()
        .count()
    )
    return NewsTrafficSummary(
        start=start,
        end=end,
        news_views=news["views"],
        site_views=site["views"],
        articles_read=read,
    )


@dataclass(frozen=True)
class Concentration:
    """Whether news attention is spread across the archive or held by a few stories."""

    total_views: int | None = None
    articles_read: int = 0
    top_5: int | None = None
    top_10: int | None = None

    @property
    def has_data(self) -> bool:
        return bool(self.total_views) and self.articles_read > 0

    def _share(self, part: int | None) -> float | None:
        if not self.total_views or part is None:
            return None
        return part / self.total_views

    @property
    def top_5_share(self) -> float | None:
        return self._share(self.top_5)

    @property
    def top_10_share(self) -> float | None:
        return self._share(self.top_10)


def concentration(*, start: date, end: date) -> Concentration:
    """The share of news reading held by the five and ten most-read articles.

    The ranking runs across every article measured in the window before anything
    is sliced, so the top five are the top five of the population rather than of
    a page.
    """
    article_paths = article_resources().values("path")
    rows = list(
        current_pages()
        .filter(report_date__gte=start, report_date__lte=end, path__in=Subquery(article_paths))
        .values("path")
        .annotate(views=Sum("page_views"))
        .order_by("-views")
        .values_list("views", flat=True)
    )
    if not rows:
        return Concentration()
    return Concentration(
        total_views=sum(rows),
        articles_read=len(rows),
        top_5=sum(rows[:5]),
        top_10=sum(rows[:10]),
    )


def most_read(*, start: date, end: date, limit: int = 6) -> QuerySet[NewsResource]:
    """`Loetakse praegu` — articles by views inside the measurement window.

    **Publication date is not a filter here.** An article from 2024 that is being
    read this month belongs at the top of this list, and a quarter of current
    news reading goes to articles over a year old. Ordering happens in
    PostgreSQL across the whole catalogue, so the top six are the top six.
    """
    return (
        article_resources()
        .annotate(**{WINDOW_ANNOTATION: window_views_subquery(start, end)})
        .filter(**{f"{WINDOW_ANNOTATION}__gt": 0})
        .order_by(F(WINDOW_ANNOTATION).desc(nulls_last=True), "-published_at", "path")[:limit]
    )


def evergreen(*, start: date, end: date, limit: int = 6, today: date | None = None):
    """Older articles still being read.

    Published at least ninety days before the window ends — past both first
    windows, so nothing here is still collecting its launch attention — and read
    during it. The question is which of the Chamber's older writing keeps earning
    its place, and the answer is not visible in any ranking that sorts by date.
    """
    today = today or end
    cutoff = today - timedelta(days=EVERGREEN_MIN_AGE_DAYS)
    return (
        article_resources()
        .filter(published_at__isnull=False)
        .annotate(
            **{
                PUBLISHED_DAY_ANNOTATION: published_day(),
                WINDOW_ANNOTATION: window_views_subquery(start, end),
            }
        )
        .filter(
            **{
                f"{PUBLISHED_DAY_ANNOTATION}__lt": cutoff,
                f"{WINDOW_ANNOTATION}__gt": 0,
            }
        )
        .order_by(F(WINDOW_ANNOTATION).desc(nulls_last=True), "-published_at", "path")[:limit]
    )


def first_week_leaders(*, coverage: Coverage, limit: int = 5, today: date | None = None):
    """Recent articles ranked by their first seven days.

    Only articles whose first week has **fully elapsed inside coverage**, which
    is what makes the comparison fair: a two-day-old article has not had a first
    week yet and cannot be ranked against articles that have.

    Recent means the last ninety days of publication, so this describes what the
    Chamber has published lately rather than the best opening week on record.
    """
    if not coverage.has_data or coverage.latest is None:
        return NewsResource.objects.none()
    today = today or coverage.latest
    since = today - timedelta(days=90)
    return (
        annotate_first_window(
            eligible_cohort(days=FIRST_WEEK_DAYS, coverage=coverage, since=since),
            name=FIRST_WINDOW_ANNOTATION,
        )
        .filter(**{f"{FIRST_WINDOW_ANNOTATION}__gt": 0})
        .order_by(F(FIRST_WINDOW_ANNOTATION).desc(nulls_last=True), "-published_at", "path")[:limit]
    )


# ---------------------------------------------------------------------------
# Publication-window questions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishingCount:
    """How much was published in a window, split by whose news it was.

    `unknown` is a count of articles DashKoda has not been able to classify, and
    it is reported rather than folded into either category. A share is quoted
    against classified articles only, and says so.
    """

    total: int = 0
    chamber: int = 0
    partner: int = 0
    unknown: int = 0

    @property
    def classified(self) -> int:
        return self.chamber + self.partner

    @property
    def chamber_share(self) -> float | None:
        if not self.classified:
            return None
        return self.chamber / self.classified


def published_between(start: date | None, end: date | None) -> PublishingCount:
    """One conditional-aggregate query for a publication window."""
    rows = (
        article_resources()
        .filter(published_at__isnull=False)
        .annotate(**{PUBLISHED_DAY_ANNOTATION: published_day()})
    )
    if start is not None:
        rows = rows.filter(**{f"{PUBLISHED_DAY_ANNOTATION}__gte": start})
    if end is not None:
        rows = rows.filter(**{f"{PUBLISHED_DAY_ANNOTATION}__lte": end})
    totals = rows.aggregate(
        total=Count("pk"),
        chamber=Count("pk", filter=Q(category=NewsCategory.CHAMBER)),
        partner=Count("pk", filter=Q(category=NewsCategory.PARTNER)),
        unknown=Count("pk", filter=Q(category="")),
    )
    return PublishingCount(
        total=totals["total"],
        chamber=totals["chamber"],
        partner=totals["partner"],
        unknown=totals["unknown"],
    )


#: Where the publication cadence chart switches from weeks to months. A hundred
#: and twenty days of weekly bars is seventeen bars, which is readable; two years
#: of them is a picket fence.
MONTHLY_FROM_DAYS = 130


def publishing_grain(days: int) -> str:
    """Weekly for a season, monthly for a year or more. Never daily.

    A daily publication chart is a row of ones separated by a fortnight of
    zeroes — it draws the calendar rather than the output.
    """
    return "month" if days >= MONTHLY_FROM_DAYS else "week"


def publishing_series(
    *, start: date, end: date, grain: str | None = None
) -> list[tuple[date, int, int, int]]:
    """Articles published per period, split by category, aggregated in the database.

    Returns one tuple per bucket that **has** articles. A period with no
    publishing contributes no row rather than a zero: the chart draws the buckets
    it is given, and the difference matters at the ends of a range where a zero
    would assert that the Chamber published nothing on days the catalogue simply
    does not reach.
    """
    from django.db.models.functions import TruncMonth, TruncWeek

    grain = grain or publishing_grain((end - start).days + 1)
    truncate = TruncMonth if grain == "month" else TruncWeek
    rows = (
        article_resources()
        .filter(published_at__isnull=False)
        .annotate(**{PUBLISHED_DAY_ANNOTATION: published_day()})
        .filter(
            **{
                f"{PUBLISHED_DAY_ANNOTATION}__gte": start,
                f"{PUBLISHED_DAY_ANNOTATION}__lte": end,
            }
        )
        .annotate(bucket=truncate(PUBLISHED_DAY_ANNOTATION))
        .values("bucket")
        .annotate(
            chamber=Count("pk", filter=Q(category=NewsCategory.CHAMBER)),
            partner=Count("pk", filter=Q(category=NewsCategory.PARTNER)),
            unknown=Count("pk", filter=Q(category="")),
        )
        .order_by("bucket")
    )
    return [(row["bucket"], row["chamber"], row["partner"], row["unknown"]) for row in rows]


@dataclass(frozen=True)
class CategoryPerformance:
    """One category's publishing output and its fair performance, side by side.

    The two answer different questions and are shown together because either
    alone misleads: Koja uudised take most of the news traffic largely because
    there are nearly three times as many of them, and the median first month is
    what says whether one of them is read more than one partner article.
    """

    key: str
    label: str
    published: int = 0
    median_first_month: int | None = None
    cohort_size: int = 0
    window_views: int | None = None

    @property
    def has_benchmark(self) -> bool:
        return self.median_first_month is not None and self.cohort_size >= MIN_BENCHMARK_COHORT


def category_performance(
    *,
    cohorts: dict[str, CohortStats],
    cohort_start: date | None,
    cohort_end: date | None,
    reading_start: date | None,
    reading_end: date | None,
) -> tuple[CategoryPerformance, ...]:
    """Output, fair performance and current attention, per category.

    **Three figures governed by two different windows, and they are passed in
    separately on purpose.** Output and the median describe the same publication
    cohort — the last twelve months — so the count and the median are about the
    same articles. Attention is measured over the reading window, because "what
    is being read now" is a question about now and not about when anything was
    written.

    An earlier version of this function took one `start`/`end` pair and used it
    for both, which counted the articles published *during the reading window*
    and reported it beside a median over a different set entirely. The two
    windows have separate parameters so that cannot happen again.

    Current attention is a **traffic share**, not a quality measure, and the
    interface says so beside it: a category that publishes three times as much
    will collect more traffic without any of its articles being read more.
    """
    rows = []
    counted = published_between(cohort_start, cohort_end)
    for value, label in NewsCategory.choices:
        published = counted.chamber if value == NewsCategory.CHAMBER else counted.partner
        stats = cohorts.get(value)
        views = None
        if reading_start is not None and reading_end is not None:
            measured = (
                current_pages()
                .filter(
                    report_date__gte=reading_start,
                    report_date__lte=reading_end,
                    path__in=Subquery(article_resources().filter(category=value).values("path")),
                )
                .aggregate(views=Sum("page_views"))
            )
            views = measured["views"]
        rows.append(
            CategoryPerformance(
                key=value,
                label=label,
                published=published,
                median_first_month=stats.median if stats and stats.is_usable else None,
                cohort_size=stats.count if stats else 0,
                window_views=views,
            )
        )
    return tuple(rows)


def previous_window(start: date, end: date) -> tuple[date, date]:
    """The equal-length window immediately before this one, with no overlap.

    `[2026-07-16, 2026-08-14]` compares against `[2026-06-16, 2026-07-15]`: the
    same number of days, ending the day before this window starts. Inclusive
    arithmetic throughout, which is why the length is `+1` and the previous end
    is `-1`.
    """
    length = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    return previous_end - timedelta(days=length - 1), previous_end


def catalogue_facts(coverage: Coverage | None = None) -> dict:
    """The aggregate description of what the catalogue holds.

    Reported in the methodology disclosure so the limits of every figure above
    are discoverable without being repeated under each one.
    """
    coverage = coverage if coverage is not None else get_coverage()
    articles = article_resources()
    row = articles.aggregate(
        total=Count("pk"),
        dated=Count("pk", filter=Q(published_at__isnull=False)),
        undated=Count("pk", filter=Q(published_at__isnull=True)),
        chamber=Count("pk", filter=Q(category=NewsCategory.CHAMBER)),
        partner=Count("pk", filter=Q(category=NewsCategory.PARTNER)),
        unclassified=Count("pk", filter=Q(category="")),
        earliest=Min("published_at"),
        latest=Max("published_at"),
    )
    row["listings"] = NewsResource.objects.filter(path__in=NEWS_LISTING_PATHS).count()
    row["coverage"] = coverage
    return row


__all__ = [
    "BENCHMARK_COHORT_DAYS",
    "EVERGREEN_MIN_AGE_DAYS",
    "FIRST_MONTH_DAYS",
    "FIRST_WEEK_DAYS",
    "FIRST_WINDOW_ANNOTATION",
    "MIN_BENCHMARK_COHORT",
    "NEWS_LISTING_PATHS",
    "PUBLISHED_DAY_ANNOTATION",
    "REASON_BEFORE_COVERAGE",
    "REASON_COVERAGE_GAP",
    "REASON_NOT_ELAPSED",
    "WINDOW_ANNOTATION",
    "MONTHLY_FROM_DAYS",
    "ArticleWindow",
    "Benchmark",
    "CategoryPerformance",
    "CohortStats",
    "Concentration",
    "NewsTrafficSummary",
    "PublishingCount",
    "annotate_first_window",
    "category_performance",
    "publishing_grain",
    "publishing_series",
    "article_resources",
    "benchmark",
    "benchmark_cohorts",
    "benchmark_for",
    "catalogue_facts",
    "cohort_stats",
    "cohort_values",
    "concentration",
    "eligible_cohort",
    "evergreen",
    "first_week_leaders",
    "missing_days_within",
    "most_read",
    "news_traffic",
    "percentile",
    "previous_window",
    "published_between",
    "published_day",
    "resolve_window",
    "window_views_subquery",
    "with_window_bounds",
]
