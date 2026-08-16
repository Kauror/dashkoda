"""What the Uudised page says, assembled per focus.

`views.py` reads query parameters and renders; this module decides what the page
holds. The split matters because the page has four faces and each reads a
different amount: the overview must not pay for the publishing view's monthly
series, and the archive must not pay for the impact view's cohort medians.

**Every focus builds only what it renders.** Building all four would run three
views' worth of queries behind whichever one is on screen.

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

from django.utils import timezone

from apps.core.change import ChangeRow, direction_of, share_percent
from apps.core.formatting import (
    integer,
    percent,
    percentage_points,
    short_date,
    signed_integer,
    signed_percent,
)
from apps.visibility.ga4_selectors import Coverage, get_coverage

from . import analytics
from .categories import NewsCategory
from .focus import (
    FOCUS_ARCHIVE,
    FOCUS_IMPACT,
    FOCUS_OVERVIEW,
    PARAM_FOCUS,
    Focus,
    FocusOption,
    focus_options,
    parse_focus,
)
from .measurement import (
    ReadPeriodOption,
    ResolvedReading,
    read_period_options,
    resolve_reading,
)
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


@dataclass(frozen=True)
class ArticleRow:
    """One ranked article, with the figure that ranked it.

    `views` is already the answer to the question the list asked — views in the
    measurement window, or views in the article's first week — so the list never
    has to explain which number it sorted by.
    """

    title: str
    url: str
    published_on: date | None
    category: str
    views: str
    #: The comparison against comparable articles, where one is available.
    benchmark: str = ""
    benchmark_direction: str = ""
    note: str = ""

    @property
    def has_date(self) -> bool:
        return self.published_on is not None

    @property
    def category_label(self) -> str:
        return dict(NewsCategory.choices).get(self.category, "")

    @property
    def published_label(self) -> str:
        return short_date(self.published_on) if self.published_on else "Kuupäev teadmata"


@dataclass(frozen=True)
class CategoryRow:
    """One category's output, fair performance and current attention, formatted.

    The three come from two different windows and the template must not have to
    know that; the builder passes the windows in separately and the section's
    caption names both.
    """

    key: str
    label: str
    published: str
    median: str
    window_views: str


@dataclass(frozen=True)
class ConcentrationView:
    """How much of the reading the few most-read articles hold, formatted."""

    articles_read: int
    top_5: str
    top_10: str
    has_data: bool = True


@dataclass(frozen=True)
class Signal:
    """One item under `Tähelepanu`.

    The evidence, never the explanation. `Sellel uudisel oli halb pealkiri` is a
    claim the data cannot support; `30 päeva vaatamisi 42% alla sarnaste uudiste
    mediaani` is what was actually measured, and the communications team is far
    better placed than a dashboard to say why.
    """

    label: str
    evidence: str
    url: str = ""


def _headline_published(period: ResolvedPeriod, *, today: date | None = None) -> Headline:
    """How much was published in the publication window, against the one before.

    The comparison is the equal-length window immediately before this one. It is
    a count of articles, never annualised: eleven articles in thirty days is not
    "134 per year", and a rate extrapolated from a fortnight of summer would say
    the Chamber had stopped publishing.
    """
    if not period.is_windowed or period.start is None or period.end is None:
        counted = analytics.published_between(None, None)
        return Headline(
            key="published",
            label="Avaldatud uudiseid",
            value=integer(counted.total),
        )

    current = analytics.published_between(period.start, period.end)
    previous_start, previous_end = analytics.previous_window(period.start, period.end)
    previous = analytics.published_between(previous_start, previous_end)
    difference = current.total - previous.total
    return Headline(
        key="published",
        label="Avaldatud uudiseid",
        value=integer(current.total),
        change=f"{signed_integer(difference)} vs eelmine {period.label.lower()}",
        change_label=f"{signed_integer(difference)} võrreldes eelmise perioodiga",
        direction=direction_of(difference),
    )


def _headline_news_views(
    current: analytics.NewsTrafficSummary,
    previous: analytics.NewsTrafficSummary,
    reading: ResolvedReading,
) -> Headline | None:
    """News page views in the measurement window.

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
        label="Uudiste lehevaatamised",
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
        label="Uudiste osakaal",
        value=share_percent(current.share),
        change=change,
        change_label=change_label,
        direction=direction,
        note="" if previous.share is not None else NO_COMPARISON,
    )


def _describe_ranked(
    resources,
    *,
    annotation: str,
    cohorts: dict[str, analytics.CohortStats] | None = None,
    unit: str = "vaatamist",
) -> tuple[ArticleRow, ...]:
    """Ranked catalogue rows as the page shows them."""
    rows = []
    for resource in resources:
        views = getattr(resource, annotation, None)
        benchmark_text = ""
        benchmark_direction = ""
        if cohorts is not None and views is not None:
            cohort = analytics.benchmark_for(cohorts, resource.category)
            result = analytics.benchmark(views, cohort)
            if result is not None and result.ratio is not None:
                benchmark_text = f"{signed_percent((result.ratio - 1) * 100)} vs mediaan"
                benchmark_direction = direction_of(result.difference)
        rows.append(
            ArticleRow(
                title=resource.title,
                url=resource.canonical_url,
                # Localised before the date is taken, so an article posted at
                # half past midnight is dated the day its editor would date it
                # — the same day `analytics.published_day` filters it under.
                published_on=(
                    timezone.localtime(resource.published_at).date()
                    if resource.published_at
                    else None
                ),
                category=resource.category,
                views=f"{integer(views)} {unit}" if views is not None else NO_VALUE,
                benchmark=benchmark_text,
                benchmark_direction=benchmark_direction,
            )
        )
    return tuple(rows)


def _opportunities(
    *,
    coverage: Coverage,
    reading: ResolvedReading,
    cohorts: dict[str, analytics.CohortStats],
    traffic: analytics.NewsTrafficSummary,
    previous: analytics.NewsTrafficSummary,
) -> tuple[Signal, ...]:
    """Transparent rules over stored figures. No model, no prose, no score.

    Each signal names the evidence and links to the article. Whether any of them
    is worth acting on is a judgement about audience and intent that the
    dashboard cannot make and does not try to.
    """
    signals: list[Signal] = []

    if reading.has_window:
        # An older article collecting current attention. The reader's question is
        # "why is this being read now", which is worth asking and which the data
        # cannot answer.
        for resource in analytics.evergreen(
            start=reading.start, end=reading.end, limit=1, today=reading.end
        ):
            views = getattr(resource, analytics.WINDOW_ANNOTATION, None)
            if views:
                signals.append(
                    Signal(
                        label="Vanem uudis kogub praegu lugejaid",
                        evidence=(
                            f"„{resource.title}“ — {integer(views)} vaatamist "
                            f"{reading.label.lower()} jooksul, avaldatud "
                            f"{short_date(timezone.localtime(resource.published_at).date())}."
                        ),
                        url=resource.canonical_url,
                    )
                )

    # A recent article in the weakest quarter of comparable articles. Stated as a
    # position in a distribution, never as a failure.
    weak = _below_normal(coverage=coverage, cohorts=cohorts, limit=1)
    signals.extend(weak)

    if traffic.share is not None and previous.share is not None:
        difference = (traffic.share - previous.share) * 100
        if abs(difference) >= 2:
            signals.append(
                Signal(
                    label="Uudiste osakaal kodulehe vaatamistest muutus",
                    evidence=(
                        f"{share_percent(traffic.share)} vs "
                        f"{share_percent(previous.share)} eelmisel võrdsel perioodil "
                        f"({percentage_points(difference)})."
                    ),
                )
            )
    return tuple(signals)


def _below_normal(
    *,
    coverage: Coverage,
    cohorts: dict[str, analytics.CohortStats],
    limit: int = 5,
) -> tuple[Signal, ...]:
    """Recently published articles sitting in their cohort's lowest quarter.

    Eligible articles only — a complete first month inside coverage — so nothing
    here is merely young. The threshold is the cohort's own 25th percentile
    rather than a percentage chosen by hand, and the wording says where the
    article sits rather than what it did wrong.
    """
    if not coverage.has_data or coverage.latest is None:
        return ()
    since = coverage.latest - timedelta(days=180)
    rows = analytics.annotate_first_window(
        analytics.eligible_cohort(days=analytics.FIRST_MONTH_DAYS, coverage=coverage, since=since),
        name=analytics.FIRST_WINDOW_ANNOTATION,
    ).order_by("-published_at")[:200]

    signals: list[Signal] = []
    for resource in rows:
        views = getattr(resource, analytics.FIRST_WINDOW_ANNOTATION, None) or 0
        cohort = analytics.benchmark_for(cohorts, resource.category)
        result = analytics.benchmark(views, cohort)
        if result is None or not result.is_below_normal:
            continue
        shortfall = (1 - result.ratio) * 100 if result.ratio is not None else None
        signals.append(
            Signal(
                label="Alla tavapärase",
                evidence=(
                    f"„{resource.title}“ — {integer(views)} vaatamist esimese 30 päevaga, "
                    f"{percent(shortfall)} alla {cohort.label.lower()} mediaani "
                    f"({integer(cohort.median)})."
                ),
                url=resource.canonical_url,
            )
        )
        if len(signals) >= limit:
            break
    return tuple(signals)


@dataclass(frozen=True)
class NewsPage:
    """One rendering of `/uudised/`, in whichever focus was asked for."""

    focus: Focus
    focuses: tuple[FocusOption, ...]
    reading: ResolvedReading
    read_periods: tuple[ReadPeriodOption, ...]
    period: ResolvedPeriod
    coverage: Coverage

    headlines: tuple[Headline, ...] = field(default_factory=tuple)
    changes: tuple[ChangeRow, ...] = field(default_factory=tuple)
    most_read: tuple[ArticleRow, ...] = field(default_factory=tuple)
    first_week: tuple[ArticleRow, ...] = field(default_factory=tuple)
    signals: tuple[Signal, ...] = field(default_factory=tuple)

    #: `fookus=moju`
    lens: str = ""
    lenses: tuple[LensOption, ...] = field(default_factory=tuple)
    lens_question: str = ""
    ranked: tuple[ArticleRow, ...] = field(default_factory=tuple)
    distribution: object | None = None
    evergreen: tuple[ArticleRow, ...] = field(default_factory=tuple)
    below_normal: tuple[Signal, ...] = field(default_factory=tuple)
    concentration: ConcentrationView | None = None
    categories: tuple[CategoryRow, ...] = field(default_factory=tuple)
    cohorts: dict = field(default_factory=dict)
    #: The publication window the cohort figures describe, so the interface can
    #: name it rather than leaving "median" and "published" undated.
    cohort_start: date | None = None

    #: `fookus=avaldamine`
    cadence: object | None = None
    counted: analytics.PublishingCount | None = None
    #: The **publication** window chips. Labelled `Avaldatud:` on the page,
    #: because the same three lengths mean readership one focus away.
    periods: tuple = field(default_factory=tuple)
    series_start: date | None = None
    series_end: date | None = None
    grain: str = ""

    #: Facts about the catalogue and the coverage, for `Andmete kohta`.
    facts: dict = field(default_factory=dict)

    @property
    def is_overview(self) -> bool:
        return self.focus.key == FOCUS_OVERVIEW

    @property
    def is_impact(self) -> bool:
        return self.focus.key == FOCUS_IMPACT

    @property
    def is_archive(self) -> bool:
        return self.focus.key == FOCUS_ARCHIVE

    @property
    def coverage_note(self) -> str:
        if not self.coverage.has_data:
            return ""
        return (
            "Lehevaatamised on Google Analyticsi mõõdetud alates "
            f"{short_date(self.coverage.earliest)}."
        )


def build_overview(
    *,
    reading: ResolvedReading,
    period: ResolvedPeriod,
    coverage: Coverage,
) -> dict:
    """The default view: four measures, what changed, and what to look at.

    Ordered so the first screen answers the questions somebody opens this page
    with — how much did we publish, how much was read, how much of the site is
    news, what does a normal article do — before offering anything to
    investigate.
    """
    traffic = (
        analytics.news_traffic(start=reading.start, end=reading.end)
        if reading.has_window
        else analytics.NewsTrafficSummary()
    )
    previous_traffic = (
        analytics.previous_traffic_within(reading.start, reading.end, coverage)
        if reading.has_window
        else analytics.NewsTrafficSummary()
    )

    # `benchmark_cohorts` is no longer walked here. It was the fourth card's
    # median and the input to `signals`, and both went on 2026-08-16 — it is
    # still built on `Uudiste mõju`, which is the focus that uses it.
    headlines = [
        _headline_published(period),
        _headline_news_views(traffic, previous_traffic, reading),
        _headline_news_share(traffic, previous_traffic),
    ]

    most_read = ()
    if reading.has_window:
        most_read = _describe_ranked(
            analytics.most_read(start=reading.start, end=reading.end, limit=6),
            annotation=analytics.WINDOW_ANNOTATION,
        )

    # The newsletter comparison strip that used to close this view moved to
    # `/otsepostitused/` with the rest of the Smaily material. It is not
    # summarised here in its place: a second copy of three rates on a page whose
    # subject is articles is exactly the duplication that move was for.
    # `changes`, `first_week` and `signals` are no longer computed. Their three
    # sections left this view on 2026-08-16, and this module's rule is that a
    # focus builds only what it renders — the cohort walk behind `signals` and
    # the second ranking behind `first_week` were not free. The fields stay on
    # `NewsPage` with their empty defaults, so restoring a section is putting
    # its key back here rather than reassembling it.
    return {
        "headlines": tuple(headline for headline in headlines if headline is not None),
        "most_read": most_read,
    }


def _changes(
    traffic: analytics.NewsTrafficSummary,
    previous: analytics.NewsTrafficSummary,
    period: ResolvedPeriod,
) -> tuple[ChangeRow, ...]:
    """`Mis muutus?` — only comparisons both of whose sides exist."""
    rows: list[ChangeRow] = []

    if traffic.has_data and previous.has_data and previous.news_views:
        difference = traffic.news_views - previous.news_views
        rows.append(
            ChangeRow(
                label="Uudiste lehevaatamised",
                current=integer(traffic.news_views),
                previous=integer(previous.news_views),
                change=signed_percent((difference / previous.news_views) * 100),
                direction=direction_of(difference),
            )
        )

    if traffic.share is not None and previous.share is not None:
        difference = (traffic.share - previous.share) * 100
        rows.append(
            ChangeRow(
                label="Uudiste osakaal kodulehe vaatamistest",
                current=share_percent(traffic.share),
                previous=share_percent(previous.share),
                change=percentage_points(difference),
                direction=direction_of(difference),
            )
        )

    if period.is_windowed and period.start is not None and period.end is not None:
        current = analytics.published_between(period.start, period.end)
        previous_start, previous_end = analytics.previous_window(period.start, period.end)
        earlier = analytics.published_between(previous_start, previous_end)
        rows.append(
            ChangeRow(
                label="Avaldatud uudiseid",
                current=integer(current.total),
                previous=integer(earlier.total),
                change=signed_integer(current.total - earlier.total),
                direction=direction_of(current.total - earlier.total),
            )
        )

    return tuple(rows)


#: The impact view's own control.
PARAM_LENS = "vaade"

#: The three lenses the impact view offers, and the question each answers.
LENS_NOW = "praegu"
LENS_WEEK = "nadal"
LENS_MONTH = "kuu"

LENSES: tuple[tuple[str, str, str], ...] = (
    (LENS_NOW, "Loetakse praegu", "Mis huvitab lugejaid praegu, olenemata avaldamisajast?"),
    (LENS_WEEK, "Esimene nädal", "Mis äratas kohe tähelepanu?"),
    (LENS_MONTH, "Esimene 30 päeva", "Mis toimis võrreldaval esimese kuu alusel?"),
)

#: The default lens for comparing articles across publication dates. The first
#: month is the fairest of the three: every article passes through one, and a
#: total measured figure compares an article's age as much as its reach.
DEFAULT_LENS = LENS_MONTH

_LENS_KEYS = {key for key, _, _ in LENSES}


def parse_lens(raw: str | None) -> str:
    value = (raw or "").strip()
    return value if value in _LENS_KEYS else DEFAULT_LENS


@dataclass(frozen=True)
class LensOption:
    key: str
    label: str
    is_active: bool
    query: str


def build_impact(
    *,
    reading: ResolvedReading,
    coverage: Coverage,
    lens: str,
    state: str = "",
) -> dict:
    """The deep content-performance view.

    Three separate rankings rather than one `Enim vaadatud` table, because
    "what is being read now" and "what performed best on a comparable first
    month" are different questions and one list cannot answer both. Which is on
    screen is a real URL, so a colleague can be sent the exact lens.
    """
    from . import charts

    cohorts = analytics.benchmark_cohorts(coverage=coverage)
    everything = cohorts.get("")
    cohort_start = (
        coverage.latest - timedelta(days=analytics.BENCHMARK_COHORT_DAYS - 1)
        if coverage.latest
        else None
    )

    ranked: tuple[ArticleRow, ...] = ()
    if lens == LENS_NOW and reading.has_window:
        ranked = _describe_ranked(
            analytics.most_read(start=reading.start, end=reading.end, limit=20),
            annotation=analytics.WINDOW_ANNOTATION,
        )
    elif lens == LENS_WEEK:
        ranked = _describe_ranked(
            analytics.first_week_leaders(coverage=coverage, limit=20),
            annotation=analytics.FIRST_WINDOW_ANNOTATION,
        )
    elif lens == LENS_MONTH:
        rows = analytics.annotate_first_window(
            analytics.eligible_cohort(days=analytics.FIRST_MONTH_DAYS, coverage=coverage),
            name=analytics.FIRST_WINDOW_ANNOTATION,
        ).order_by(f"-{analytics.FIRST_WINDOW_ANNOTATION}", "-published_at", "path")[:20]
        ranked = _describe_ranked(
            rows, annotation=analytics.FIRST_WINDOW_ANNOTATION, cohorts=cohorts
        )

    distribution = None
    values: list[int] = []
    if everything is not None and everything.is_usable:
        values = analytics.cohort_values(
            days=analytics.FIRST_MONTH_DAYS, coverage=coverage, since=cohort_start
        )
        distribution = charts.first_month_distribution(values, everything)

    evergreen_rows: tuple[ArticleRow, ...] = ()
    if reading.has_window:
        evergreen_rows = _describe_ranked(
            analytics.evergreen(start=reading.start, end=reading.end, limit=8, today=reading.end),
            annotation=analytics.WINDOW_ANNOTATION,
        )

    return {
        "lens": lens,
        "lenses": tuple(
            LensOption(
                key=key,
                label=label,
                is_active=key == lens,
                query=_lens_query(key, state),
            )
            for key, label, _ in LENSES
        ),
        # `lens_question`, `concentration` and `categories` are no longer
        # computed — the lens sentence and both sections went on 2026-08-16, and
        # `analytics.concentration` and `analytics.category_performance` are a
        # query each. Both selectors are untouched and still tested; nothing on
        # this focus calls them.
        "ranked": ranked,
        "distribution": distribution,
        "evergreen": evergreen_rows,
        "below_normal": _below_normal(coverage=coverage, cohorts=cohorts, limit=6),
        "cohort_start": cohort_start,
        "cohorts": cohorts,
    }


def _describe_concentration(result: analytics.Concentration) -> ConcentrationView:
    return ConcentrationView(
        articles_read=result.articles_read,
        top_5=share_percent(result.top_5_share),
        top_10=share_percent(result.top_10_share),
        has_data=result.has_data,
    )


def _lens_query(key: str, state: str) -> str:
    parts = [f"{PARAM_FOCUS}={FOCUS_IMPACT}"]
    if key != DEFAULT_LENS:
        parts.append(f"{PARAM_LENS}={key}")
    if state:
        parts.append(state)
    return "&".join(parts)


def build_publishing(*, period: ResolvedPeriod, coverage: Coverage, state: str = "") -> dict:
    """What and how much the Chamber publishes.

    Everything here is a **publication** question, so the control above it is the
    publication window and the measurement window has no effect on any of it.
    """
    from . import charts
    from .periods import period_options

    # On the overview since 2026-08-16, which is the default focus and emits no
    # `fookus` parameter — the chips carry only the rest of the page state.
    periods = period_options(period, sort="", search="", carried=state)

    start = period.start
    end = period.end
    if start is None or end is None:
        # `Kõik` — the whole catalogue, from the first article it holds.
        bounds = analytics.article_resources().filter(published_at__isnull=False)
        earliest = bounds.order_by("published_at").values_list("published_at", flat=True).first()
        latest = bounds.order_by("-published_at").values_list("published_at", flat=True).first()
        if earliest is None or latest is None:
            return {
                "cadence": None,
                "counted": analytics.PublishingCount(),
                "periods": periods,
            }
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
        "periods": periods,
        "series_start": start,
        "series_end": end,
        "grain": grain,
    }


def build_news_page(
    *,
    focus_key: str | None = None,
    read_key: str | None = None,
    period_key: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    lens_key: str | None = None,
    state: str = "",
    today: date | None = None,
) -> NewsPage:
    """Assemble whichever focus was asked for, **and only that one**.

    Each branch runs the queries its own view renders and no others. Building all
    four would put the publishing series and three cohort medians on every render
    of a page showing one of them.
    """
    coverage = get_coverage()
    focus = parse_focus(focus_key)
    reading = resolve_reading(read_key, coverage=coverage)
    period = resolve_period(period_key, date_from, date_to, today=today)

    page = {
        "focus": focus,
        "focuses": focus_options(focus, state=state),
        "reading": reading,
        "read_periods": read_period_options(reading, coverage=coverage, state=state),
        "period": period,
        "coverage": coverage,
        "facts": analytics.catalogue_facts(coverage),
    }

    if focus.key == FOCUS_OVERVIEW:
        page.update(build_overview(reading=reading, period=period, coverage=coverage))
        # The publishing material joined the overview when `avaldamine`
        # retired: how much the Chamber publishes belongs on the same first
        # screen as what is being read.
        page.update(build_publishing(period=period, coverage=coverage, state=state))
    elif focus.key == FOCUS_IMPACT:
        page.update(
            build_impact(
                reading=reading,
                coverage=coverage,
                lens=parse_lens(lens_key),
                state=state,
            )
        )

    return NewsPage(**page)


__all__ = [
    "DEFAULT_LENS",
    "LENSES",
    "LENS_MONTH",
    "LENS_NOW",
    "LENS_WEEK",
    "NO_COMPARISON",
    "NO_VALUE",
    "PARAM_LENS",
    "ArticleRow",
    "Headline",
    "LensOption",
    "NewsPage",
    "Signal",
    "build_impact",
    "build_news_page",
    "build_overview",
    "build_publishing",
    "parse_lens",
]
