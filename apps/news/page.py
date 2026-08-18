"""What the Uudised page says.

`views.py` reads query parameters and renders; this module decides what the page
holds. It carried a `focus` split until 2026-08-17 — the overview must not pay
for the publishing view's monthly series, and the archive must not pay for the
impact view's cohort medians, so each built only what it rendered. The page is
one view now and `build_news_page` always runs every builder below; the split
stays because each function is still independently readable and testable, and
because the day this page grows a second view again, the "only what it
renders" rule is exactly what should govern it.

## One window, since 2026-08-18

Until this date the page held two separate time controls: `periood=` — a
**publication** window, "articles published during this span" — and `loetud=`
— a **measurement** window, "pages read during this span". `apps/news/periods.py`
still carries the reasoning for why those are different questions; that
reasoning is still true. What changed is the interface: the mockup this round
rebuilt the page to shows one picker, and a second hidden one the reader never
asked for was worse than one picker used consistently. So every section on this
page now reads the **same** calendar window — `periood=`'s — and applies it
according to its own question: `Avaldatud uudiseid` still counts what was
*published* inside it, `Lehevaatamised` still counts what was *read* inside it,
clipped to what GA4 has actually collected. The distinction the old two-control
design protected is preserved in what each figure's own label says it counts,
not in which control moved it. `apps/news/measurement.py` and its `loetud=`
parameter retired with the second control; a bookmark that still carries one is
simply an unread parameter, the same as a stray `fookus=`.

## Newsletters are not here

They were, as a fifth focus. The Smaily material is now `Otsepostitused` at
`/otsepostitused/`, composed by `apps.visibility.mailings_page` — which is the
app that owned the models, the collectors and the selectors all along. This
module holds no newsletter builder, and `build_overview` no longer closes with
a comparison strip: one concept, one home.

## Numbers arrive formatted

A readout carries strings, not values. The alternative is a template deciding how
to write a signed percentage, which makes the template a second place that
decision lives, and the two drift the first time either changes. The vocabulary
is `apps/core/formatting.py` and nothing here spells a number by hand.

## What is never built

No score. No index. No sentence that claims a cause. `Tähelepanu` states what the
data shows — "42% under the twelve-month median for this kind of article" — and
stops there, because whether that is a problem depends on who the article was
for, whether anybody promoted it and what it was trying to do, none of which is
in GA4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from apps.core.change import direction_of, share_percent
from apps.core.formatting import (
    integer,
    percentage_points,
    short_date,
    signed_integer,
    signed_percent,
)
from apps.visibility.ga4_selectors import Coverage, get_coverage

from . import analytics
from .periods import ResolvedPeriod, resolve_period

#: Shown where a figure exists but its comparison does not.
NO_COMPARISON = "võrdlust pole"

#: Shown in place of a number that was never measured.
NO_VALUE = "—"


@dataclass(frozen=True)
class Headline:
    """One of the page's four primary measures.

    A headline with no value is not rendered. Three honest figures beat four
    where the fourth is a placeholder — a card reading `0` because a cohort was
    too small is worse than a card that is not there, because it looks like a
    measurement.
    """

    key: str
    label: str
    value: str
    #: What the figure is of, in a few words: the population, the window, the
    #: denominator. This is where a KPI stops being a number without a question.
    detail: str = ""
    change: str = ""
    change_label: str = ""
    direction: str = ""
    note: str = ""
    #: The category split, where the measure has one.
    parts: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_value(self) -> bool:
        return bool(self.value)

    @property
    def has_change(self) -> bool:
        return bool(self.change)


def _reading_window(
    period: ResolvedPeriod, *, coverage: Coverage
) -> tuple[date | None, date | None, bool]:
    """The **same** calendar window as `period`, clipped to what GA4 collected.

    `periood=` is a publication window and may be open at either end — `Kõik`
    is both ends open, a half-filled custom range is one. A read-based figure
    has no catalogue to fall back on for an open end, so an open publication
    bound becomes the corresponding coverage bound here, and a bound outside
    coverage is pulled in to the nearest collected day. `is_truncated` says
    when that happened, the same distinction `apps/news/measurement.py` used to
    draw for its own window.
    """
    if not coverage.has_data or coverage.earliest is None or coverage.latest is None:
        return None, None, False

    start = period.start or coverage.earliest
    end = period.end or coverage.latest
    truncated = (period.start is not None and period.start < coverage.earliest) or (
        period.end is not None and period.end > coverage.latest
    )
    start = max(start, coverage.earliest)
    end = min(end, coverage.latest)
    if start > end:
        return None, None, truncated
    return start, end, truncated


def _headline_published(period: ResolvedPeriod) -> Headline:
    """How much was published in the window, against the one before.

    The comparison is the equal-length window immediately before this one. It is
    a count of articles, never annualised: eleven articles in thirty days is not
    "134 per year", and a rate extrapolated from a fortnight of summer would say
    the Chamber had stopped publishing.
    """
    if not period.is_windowed or period.start is None or period.end is None:
        counted = analytics.published_between(None, None)
        return Headline(key="published", label="Avaldatud uudiseid", value=integer(counted.total))

    current = analytics.published_between(period.start, period.end)
    previous_start, previous_end = analytics.previous_window(period.start, period.end)
    previous = analytics.published_between(previous_start, previous_end)
    difference = current.total - previous.total
    return Headline(
        key="published",
        label="Avaldatud uudiseid",
        value=integer(current.total),
        change=f"{signed_integer(difference)} vs eelmine periood",
        change_label=f"{signed_integer(difference)} võrreldes eelmise perioodiga",
        direction=direction_of(difference),
    )


def _headline_news_views(
    current: analytics.NewsTrafficSummary,
    previous: analytics.NewsTrafficSummary,
) -> Headline | None:
    """News page views in the window.

    Page views, not readers: one person opening an article twice is two of these
    and one of those, and the word on the page is `lehevaatamist` throughout.
    Active users are not summed here or anywhere — they are distinct people per
    day and there is no arithmetic over daily distinct counts that yields a
    period distinct count.
    """
    if not current.has_data:
        return None
    change = ""
    change_label = ""
    direction = ""
    if previous.has_data and previous.news_views:
        difference = current.news_views - previous.news_views
        ratio = difference / previous.news_views
        change = f"{signed_percent(ratio * 100)} vs eelmine periood"
        change_label = f"{signed_percent(ratio * 100)} võrreldes eelmise perioodiga"
        direction = direction_of(difference)
    return Headline(
        key="news_views",
        label="Lehevaatamised",
        value=integer(current.news_views),
        change=change,
        change_label=change_label,
        direction=direction,
        note="" if previous.has_data else NO_COMPARISON,
    )


def _headline_news_share(
    current: analytics.NewsTrafficSummary,
    previous: analytics.NewsTrafficSummary,
) -> Headline | None:
    """News reading as a share of all website reading.

    Both sides are additive page views over the same days. A numerator of
    sessions over a denominator of page views would produce a number that looks
    like a percentage and answers nothing.

    The change is in **percentage points**: a share that moved from 12,0% to
    9,7% did not fall by 2,3% of itself.
    """
    if current.share is None:
        return None
    change = ""
    change_label = ""
    direction = ""
    if previous.share is not None:
        difference = (current.share - previous.share) * 100
        change = f"{percentage_points(difference)} vs eelmine periood"
        change_label = f"{percentage_points(difference)} võrreldes eelmise perioodiga"
        direction = direction_of(difference)
    return Headline(
        key="news_share",
        label="Osakaal kodulehe külastustest",
        value=share_percent(current.share),
        change=change,
        change_label=change_label,
        direction=direction,
        note="" if previous.share is not None else NO_COMPARISON,
    )


def _headline_typical_first_month(cohorts: dict[str, analytics.CohortStats]) -> Headline | None:
    """The fourth measure: what a normal article's first month looks like.

    Independent of the window above it on purpose. This is the same trailing
    twelve-month cohort `Kuidas uudised jõuavad` draws its shape from, so the
    two say the same "normal" — a card that answered from the window picker
    instead would call two different populations "typical" on one screen.
    """
    everything = cohorts.get("")
    if everything is None or not everything.is_usable:
        return None
    detail = (
        f"keskmine pool {integer(everything.p25)} – {integer(everything.p75)}"
        if everything.p25 is not None and everything.p75 is not None
        else ""
    )
    note = f"{integer(everything.count)} uudise põhjal"
    if detail:
        note = f"{detail} · {note}"
    return Headline(
        key="typical_first_month",
        label="Tüüpiline uudis esimese 30 päevaga",
        value=f"{integer(everything.median)} vaatamist",
        note=note,
    )


@dataclass(frozen=True)
class NewsPage:
    """One rendering of `/uudised/` — every field, on every render.

    The `#: fookus=...` comments below name which now-retired focus's
    builder populates each group of fields, not which one renders it —
    `moju` and `avaldamine` both merged into the one view on 2026-08-17 (or
    2026-08-16, for `avaldamine`), and everything below composes now.
    """

    period: ResolvedPeriod
    coverage: Coverage
    #: The window the read-based headlines and the distribution chart actually
    #: queried — `period`'s own bounds, clipped to GA4 coverage.
    read_start: date | None = None
    read_end: date | None = None
    read_is_truncated: bool = False

    headlines: tuple[Headline, ...] = field(default_factory=tuple)

    #: `fookus=moju`
    distribution: object | None = None
    cohorts: dict = field(default_factory=dict)

    #: `fookus=avaldamine`
    cadence: object | None = None
    counted: analytics.PublishingCount | None = None
    series_start: date | None = None
    series_end: date | None = None
    grain: str = ""

    #: Facts about the catalogue and the coverage, for `Andmete kohta`.
    facts: dict = field(default_factory=dict)

    @property
    def coverage_note(self) -> str:
        if not self.coverage.has_data:
            return ""
        return (
            "Lehevaatamised on Google Analyticsi mõõdetud alates "
            f"{short_date(self.coverage.earliest)}."
        )

    @property
    def read_is_windowed(self) -> bool:
        return self.read_start is not None and self.read_end is not None


def build_overview(
    *,
    period: ResolvedPeriod,
    coverage: Coverage,
    read_start: date | None,
    read_end: date | None,
    cohorts: dict[str, analytics.CohortStats],
) -> dict:
    """The default view: four measures, and what changed.

    Ordered so the first screen answers the questions somebody opens this page
    with — how much did we publish, how much was read, how much of the site is
    news, what does a normal article do — before offering anything to
    investigate.
    """
    has_window = read_start is not None and read_end is not None
    traffic = (
        analytics.news_traffic(start=read_start, end=read_end)
        if has_window
        else analytics.NewsTrafficSummary()
    )
    previous_traffic = (
        analytics.previous_traffic_within(read_start, read_end, coverage)
        if has_window
        else analytics.NewsTrafficSummary()
    )

    headlines = [
        _headline_published(period),
        _headline_news_views(traffic, previous_traffic),
        _headline_news_share(traffic, previous_traffic),
        _headline_typical_first_month(cohorts),
    ]

    return {"headlines": tuple(headline for headline in headlines if headline is not None)}


def build_impact(*, coverage: Coverage) -> dict:
    """The one section `Uudiste mõju` retired with: the first-month shape.

    Three separate rankings and a per-lens control used to live here. All of it
    left this view between 2026-08-16 and 2026-08-17 except the distribution,
    which is still the only honest way to say whether a figure is remarkable —
    see `apps/news/charts.py::first_month_distribution`.
    """
    from . import charts

    cohorts = analytics.benchmark_cohorts(coverage=coverage)
    everything = cohorts.get("")

    distribution = None
    if everything is not None and everything.is_usable:
        cohort_start = (
            coverage.latest - timedelta(days=analytics.BENCHMARK_COHORT_DAYS - 1)
            if coverage.latest
            else None
        )
        values = analytics.cohort_values(
            days=analytics.FIRST_MONTH_DAYS, coverage=coverage, since=cohort_start
        )
        distribution = charts.first_month_distribution(values, everything)

    return {"distribution": distribution, "cohorts": cohorts}


def build_publishing(*, period: ResolvedPeriod, coverage: Coverage) -> dict:
    """What and how much the Chamber publishes, over the same window.

    Everything here is a **publication** question — `published_at` inside
    `period`, whatever GA4 does or does not know about it yet.
    """
    from . import charts

    start = period.start
    end = period.end
    if start is None or end is None:
        # `Kõik` — the whole catalogue, from the first article it holds.
        bounds = analytics.article_resources().filter(published_at__isnull=False)
        earliest = bounds.order_by("published_at").values_list("published_at", flat=True).first()
        latest = bounds.order_by("-published_at").values_list("published_at", flat=True).first()
        if earliest is None or latest is None:
            return {"cadence": None, "counted": analytics.PublishingCount()}
        from django.utils import timezone

        start = timezone.localtime(earliest).date()
        end = timezone.localtime(latest).date()

    grain = analytics.publishing_grain((end - start).days + 1)
    buckets = analytics.publishing_series(start=start, end=end, grain=grain)
    counted = analytics.published_between(start, end)

    # The final bucket is nearly always still running. Saying so stops a month
    # that is a third over from reading as a collapse in output.
    partial_from = buckets[-1][0] if buckets else None
    if partial_from is not None and grain == "month":
        partial_from = partial_from if end.day < 28 else None
    elif partial_from is not None:
        partial_from = partial_from if (end - partial_from).days < 6 else None

    return {
        "cadence": charts.publishing_cadence(buckets, grain=grain, partial_from=partial_from),
        "counted": counted,
        "series_start": start,
        "series_end": end,
        "grain": grain,
    }


def build_news_page(
    *,
    period_key: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    today: date | None = None,
) -> NewsPage:
    """Assemble the one view — every section this page draws.

    `focus_key`, `read_key` and `lens_key` are no longer accepted: the three
    focuses merged on 2026-08-17 and the reading window merged into the
    publication window on 2026-08-18. A bookmark carrying `fookus=`, `loetud=`
    or `vaade=` still opens this page — Django simply never reads them.
    """
    coverage = get_coverage()
    period = resolve_period(period_key, date_from, date_to, today=today)
    read_start, read_end, read_truncated = _reading_window(period, coverage=coverage)

    page = {
        "period": period,
        "coverage": coverage,
        "read_start": read_start,
        "read_end": read_end,
        "read_is_truncated": read_truncated,
        "facts": analytics.catalogue_facts(coverage),
    }

    impact = build_impact(coverage=coverage)
    page.update(impact)
    page.update(
        build_overview(
            period=period,
            coverage=coverage,
            read_start=read_start,
            read_end=read_end,
            cohorts=impact["cohorts"],
        )
    )
    page.update(build_publishing(period=period, coverage=coverage))

    return NewsPage(**page)


__all__ = [
    "NO_COMPARISON",
    "NO_VALUE",
    "Headline",
    "NewsPage",
    "build_impact",
    "build_news_page",
    "build_overview",
    "build_publishing",
]
