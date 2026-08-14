"""The Sündmused intelligence dashboard: one page, six focus views.

`/sundmused/` answers different questions for different readers, and it answers
them in one place rather than in six routes. `?fookus=` chooses which analytical
surface is on screen; the programme itself, the year control and the provenance
block are constant.

    ulevaade       what does the programme look like right now
    maht           how much, and when
    formaadid      what kinds of events, and how that has shifted
    huvi           which events the public looked at
    planeerimine   how far ahead events are arranged, and what they cost
    programm       the exact register, searchable and filterable

Two properties are load-bearing.

**Only the active focus is computed.** A reader on `Ülevaade` does not pay for
the seasonality medians, and a reader on `Programm` does not pay for GA4. This
is why the builders below are separate functions rather than one dataclass that
fills every field.

**The year is an event cohort, never a measurement window.** `2026` means
"events whose own start date falls in 2026". Their public pages may have been
viewed in 2025 and their registrations bought in 2025 too; those windows are
chosen by the analyses that need them and are labelled where they appear. One
control that silently meant three different periods at once is the specific
confusion this page is built to avoid.

Nothing here writes an interpretation. Every line of `Mis muutus?` is a
comparison of two figures the reader can find elsewhere on the page, and no
metric on this dashboard explains *why* a number moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

from apps.core.formatting import (
    integer,
    percent,
    percentage_points,
    short_date,
    signed_integer,
)
from apps.visibility.ga4_selectors import get_coverage

from . import analytics, attention, charts, commerce
from .page import REGISTER_FOCUS
from .public_links import attach_public_links
from .selectors import (
    NEAR_TERM_DAYS,
    YEAR_ALL,
    EventProgrammeSummary,
    count_events_starting_within,
    count_linked_events,
    count_review_required_events,
    count_unknown_date_events,
    count_workbook_linked_events,
    get_event_programme_filter_options,
)

FOCUS_OVERVIEW = "ulevaade"
FOCUS_VOLUME = "maht"
FOCUS_FORMATS = "formaadid"
FOCUS_ATTENTION = "huvi"
FOCUS_PLANNING = "planeerimine"
#: Owned by `page.py`, which builds every link the register emits. One spelling,
#: so a pagination link and a focus tab can never disagree about the register's
#: own name.
FOCUS_REGISTER = REGISTER_FOCUS

#: The navigation, in reading order: the answer, then the two structural
#: analyses, then the public-response analysis, then planning, then the register.
FOCUS_LABELS: tuple[tuple[str, str], ...] = (
    (FOCUS_OVERVIEW, "Ülevaade"),
    (FOCUS_VOLUME, "Maht ja kalender"),
    (FOCUS_FORMATS, "Formaadid ja teemad"),
    (FOCUS_ATTENTION, "Huvi"),
    (FOCUS_PLANNING, "Planeerimine"),
    (FOCUS_REGISTER, "Programm"),
)

FOCUS_VALUES = tuple(key for key, _label in FOCUS_LABELS)

ALL_YEARS_LABEL = "Kõik aastad"

#: How many events the overview previews. Five is a glance; twenty is a list the
#: reader has to work through, and `Programm` is where a list belongs.
PREVIEW_LIMIT = 5

#: How many themes the mix-over-time chart tracks. Beyond this a stacked bar
#: becomes a colour puzzle, and the ranking beside it carries the detail.
MIX_KEYS = 5


@dataclass(frozen=True)
class FocusLink:
    key: str
    label: str
    url: str
    is_active: bool


@dataclass(frozen=True)
class YearLink:
    value: str
    label: str
    url: str
    is_active: bool


@dataclass(frozen=True)
class Headline:
    """One primary answer. `value` is `None` when the source cannot supply it."""

    label: str
    value: str | None
    note: str = ""
    detail: str = ""


@dataclass(frozen=True)
class Change:
    """One deterministic comparison for `Mis muutus?`.

    Never an interpretation: the label names the two things compared and the
    note states the period. Nothing here says a change is good, bad, caused by
    anything, or likely to continue.
    """

    label: str
    value: str
    change: str = ""
    direction: str = ""
    note: str = ""


@dataclass(frozen=True)
class Notice:
    """One actionable, source-backed observation. Never a recommendation."""

    text: str
    url: str = ""
    link_label: str = ""


@dataclass(frozen=True)
class EventPreview:
    """One event as it appears in a short list."""

    event_id: str
    name: str
    date_label: str
    type_label: str
    tag_label: str
    delivery_label: str
    url: str = ""
    metric: str = ""


@dataclass(frozen=True)
class DataCoverage:
    """Everything `Andmete kohta` states, computed once.

    Every denominator any figure on this page divides by appears here, so a
    reader can check a share against the population it was taken from rather
    than trusting that the two agree.
    """

    export_refreshed_at: object = None
    schema_version: str = ""
    generator_version: str = ""
    canonical_events: int = 0
    dated_events: int = 0
    undated_events: int = 0
    repeated_service_codes: int = 0
    excluded_rows: int = 0
    review_required: int = 0
    warnings: int = 0
    workbook_links: int = 0
    effective_links: int = 0
    years: tuple[int, ...] = ()
    type_coverage: int = 0
    tag_coverage: int = 0
    delivery_coverage: int = 0
    planning_coverage: int = 0
    price_coverage: int = 0
    retroactive_planning: int = 0
    ga4_start: date | None = None
    ga4_end: date | None = None
    commerce: commerce.JoinReport | None = None

    @property
    def link_gap(self) -> int:
        return self.canonical_events - self.effective_links


@dataclass(frozen=True)
class OverviewView:
    headline: tuple[Headline, ...] = ()
    changes: tuple[Change, ...] = ()
    notices: tuple[Notice, ...] = ()
    types: analytics.Distribution | None = None
    tags: analytics.Distribution | None = None
    delivery: analytics.Distribution | None = None
    upcoming: tuple[EventPreview, ...] = ()
    watched: tuple[EventPreview, ...] = ()
    state: analytics.TemporalState | None = None


@dataclass(frozen=True)
class VolumeView:
    volume: analytics.VolumeSummary | None = None
    year_chart: charts.ChartPayload | None = None
    month_chart: charts.ChartPayload | None = None
    seasonality_chart: charts.ChartPayload | None = None
    duration_chart: charts.ChartPayload | None = None
    quarters: tuple[analytics.Category, ...] = ()


@dataclass(frozen=True)
class FormatsView:
    types: analytics.Distribution | None = None
    tags: analytics.Distribution | None = None
    delivery: analytics.Distribution | None = None
    state: analytics.TemporalState | None = None
    type_chart: charts.ChartPayload | None = None
    tag_chart: charts.ChartPayload | None = None
    delivery_chart: charts.ChartPayload | None = None
    delivery_over_time: charts.ChartPayload | None = None
    tag_over_time: charts.ChartPayload | None = None


@dataclass(frozen=True)
class AttentionView:
    coverage: attention.AttentionCoverage | None = None
    distribution: attention.AttentionDistribution | None = None
    watched: charts.ChartPayload | None = None
    strongest: charts.ChartPayload | None = None
    registrations: commerce.JoinReport | None = None
    registration_rows: tuple[EventPreview, ...] = ()
    lead_bands: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class PlanningView:
    planning: analytics.PlanningSummary | None = None
    prices: analytics.PriceSummary | None = None
    bands_chart: charts.ChartPayload | None = None
    type_chart: charts.ChartPayload | None = None
    price_chart: charts.ChartPayload | None = None


@dataclass(frozen=True)
class IntelligencePage:
    """One rendered state of `/sundmused/`."""

    summary: EventProgrammeSummary
    focus: str = FOCUS_OVERVIEW
    focus_links: tuple[FocusLink, ...] = ()
    year: int | None = None
    year_links: tuple[YearLink, ...] = ()
    period_label: str = ALL_YEARS_LABEL
    quality: DataCoverage = field(default_factory=DataCoverage)
    overview: OverviewView | None = None
    volume: VolumeView | None = None
    formats: FormatsView | None = None
    attention: AttentionView | None = None
    planning: PlanningView | None = None

    @property
    def has_data(self) -> bool:
        return self.summary.has_data

    @property
    def draws_charts(self) -> bool:
        """Whether this focus loads the chart bundle at all.

        The register and the overview are read without ECharts, and shipping a
        large module to a page that draws nothing is a cost with no reader on
        the other end of it.
        """
        return self.focus in (FOCUS_VOLUME, FOCUS_FORMATS, FOCUS_ATTENTION, FOCUS_PLANNING)


# ---------------------------------------------------------------------------
# Query state
# ---------------------------------------------------------------------------


def parse_focus(raw) -> str:
    """A known focus, or `Ülevaade`. An unknown value is never an error page."""
    return raw if raw in FOCUS_VALUES else FOCUS_OVERVIEW


def focus_url(focus: str, *, year: int | None) -> str:
    query = [("fookus", focus), ("year", str(year) if year is not None else YEAR_ALL)]
    return f"{reverse('events')}?{urlencode(query)}"


def _focus_links(focus: str, *, year: int | None) -> tuple[FocusLink, ...]:
    return tuple(
        FocusLink(key=key, label=label, url=focus_url(key, year=year), is_active=key == focus)
        for key, label in FOCUS_LABELS
    )


def _year_links(options, *, focus: str, year: int | None) -> tuple[YearLink, ...]:
    links = [
        YearLink(
            value=str(value),
            label=str(value),
            url=focus_url(focus, year=value),
            is_active=year == value,
        )
        for value in options.years
    ]
    links.append(
        YearLink(
            value=YEAR_ALL,
            label=ALL_YEARS_LABEL,
            url=focus_url(focus, year=None),
            is_active=year is None,
        )
    )
    return tuple(links)


def _preview(item, *, metric: str = "") -> EventPreview:
    link = getattr(item, "public_link", None)
    if item.start_date is None:
        date_label = "Kuupäev teadmata"
    elif item.end_date and item.end_date != item.start_date:
        date_label = f"{short_date(item.start_date)}–{short_date(item.end_date)}"
    else:
        date_label = short_date(item.start_date)
    return EventPreview(
        event_id=item.event_id,
        name=item.event_name,
        date_label=date_label,
        type_label=(item.event_type_label or "").strip(),
        tag_label=(item.tag_label or "").strip(),
        delivery_label=item.get_delivery_mode_display() if item.delivery_mode else "",
        url=getattr(link, "url", "") or "",
        metric=metric,
    )


# ---------------------------------------------------------------------------
# Data coverage
# ---------------------------------------------------------------------------


def build_coverage(snapshot, *, include_commerce: bool = False) -> DataCoverage:
    """The provenance block. One pass of small aggregates, never per row."""
    if snapshot is None:
        return DataCoverage()
    rows = analytics.items_for(snapshot)
    total = rows.count()
    ga4 = get_coverage()
    return DataCoverage(
        export_refreshed_at=snapshot.export_refreshed_at,
        schema_version=snapshot.schema_version,
        generator_version=snapshot.generator_version,
        canonical_events=snapshot.canonical_event_count,
        dated_events=snapshot.dated_event_count,
        undated_events=count_unknown_date_events(snapshot),
        repeated_service_codes=snapshot.repeated_service_code_count,
        excluded_rows=snapshot.excluded_event_count,
        review_required=count_review_required_events(snapshot),
        warnings=snapshot.warning_count,
        workbook_links=count_workbook_linked_events(snapshot),
        effective_links=count_linked_events(snapshot),
        years=tuple(sorted(analytics.counts_by_year(snapshot), reverse=True)),
        type_coverage=total - rows.filter(event_type_key="").count(),
        tag_coverage=total - rows.filter(tag_key="").count(),
        delivery_coverage=total - rows.filter(delivery_mode="").count(),
        planning_coverage=rows.exclude(planning_lead_days=None).count(),
        price_coverage=total - rows.filter(price_status="").count(),
        retroactive_planning=rows.filter(planning_lead_days__lt=0).count(),
        ga4_start=ga4.earliest,
        ga4_end=ga4.latest,
        commerce=commerce.join_report(rows) if include_commerce else None,
    )


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def _direction(value) -> str:
    """The non-colour signal for a movement.

    A change distinguished only by hue does not exist for a reader who cannot
    separate the hues, so every `Change` carries a direction the template turns
    into a glyph as well as a colour.
    """
    if not value:
        return "flat"
    return "up" if value > 0 else "down"


def _changes(snapshot, *, year: int | None, today: date) -> tuple[Change, ...]:
    """Deterministic year-on-year comparisons. No causes, no adjectives.

    The whole-programme comparison and the year-to-date one are **separate
    lines**, because they answer different questions and averaging them would
    compare a full schedule against eight months of one.
    """
    if year is None:
        return ()
    by_year = analytics.counts_by_year(snapshot)
    previous = year - 1
    if previous not in by_year:
        return ()

    rows: list[Change] = []
    current = by_year.get(year, 0)
    prior = by_year[previous]
    delta = current - prior
    rows.append(
        Change(
            label="Sündmusi programmis",
            value=integer(current),
            change=signed_integer(delta) if delta else "muutumatu",
            direction=_direction(delta),
            note=f"kogu {year}. aasta programm vs {previous}",
        )
    )

    if year == today.year:
        ytd_now = analytics.count_year_to_date(snapshot, year=year, today=today)
        ytd_then = analytics.count_year_to_date(snapshot, year=previous, today=today)
        ytd_delta = ytd_now - ytd_then
        rows.append(
            Change(
                label="Alanud 1. jaanuarist tänaseni",
                value=integer(ytd_now),
                change=signed_integer(ytd_delta) if ytd_delta else "muutumatu",
                direction=_direction(ytd_delta),
                note=f"sama periood {previous}. aastal",
            )
        )

    # Delivery-mode shift, stated as a share of each year's own programme.
    modes = analytics.delivery_mode_by_year(snapshot, years=(previous, year))
    online_now = next((row for row in modes[year].all_rows if row.key == "online"), None)
    online_then = next((row for row in modes[previous].all_rows if row.key == "online"), None)
    if online_now and online_then and modes[year].total and modes[previous].total:
        shift = online_now.share - online_then.share
        rows.append(
            Change(
                label="Veebis toimuvate osakaal",
                value=percent(online_now.share),
                # Percentage **points**, not percent. A share moving from 20% to
                # 25% has risen five points and by a quarter, and writing the
                # first as "+5%" is how the two get confused.
                change=percentage_points(shift),
                direction=_direction(shift),
                note=f"{previous}. aastal {percent(online_then.share)}",
            )
        )

    # The theme that grew most, by count. Ranked, never explained.
    tags_now = analytics.labelled_distribution(
        analytics.population(snapshot, year=year),
        key_field="tag_key",
        label_field="tag_label",
        dimension="tag",
        top=None,
    )
    tags_then = analytics.labelled_distribution(
        analytics.population(snapshot, year=previous),
        key_field="tag_key",
        label_field="tag_label",
        dimension="tag",
        top=None,
    )
    prior_counts = {row.key: row.count for row in tags_then.all_rows}
    growth = [
        (row.count - prior_counts.get(row.key, 0), row)
        for row in tags_now.all_rows
        if not row.is_unknown
    ]
    growth.sort(key=lambda pair: (-pair[0], pair[1].label.casefold()))
    if growth and growth[0][0] > 0:
        gain, row = growth[0]
        rows.append(
            Change(
                label="Enim kasvanud teema",
                value=row.label,
                change=signed_integer(gain),
                direction="up",
                note=f"{integer(prior_counts.get(row.key, 0))} → {integer(row.count)} sündmust",
            )
        )

    planning_now = analytics.build_planning(snapshot, year=year)
    planning_then = analytics.build_planning(snapshot, year=previous)
    if planning_now.median_lead is not None and planning_then.median_lead is not None:
        shift = planning_now.median_lead - planning_then.median_lead
        rows.append(
            Change(
                label="Mediaan planeerimisvaru",
                value=f"{integer(round(planning_now.median_lead))} päeva",
                change=signed_integer(round(shift)) if shift else "muutumatu",
                direction=_direction(shift),
                note=f"{previous}. aastal {integer(round(planning_then.median_lead))} päeva",
            )
        )
    return tuple(rows)


def _notices(snapshot, *, today: date) -> tuple[Notice, ...]:
    """Source-backed things worth acting on. Each names a count and a way to see it."""
    rows: list[Notice] = []
    horizon = today + timedelta(days=NEAR_TERM_DAYS)
    soon = list(
        analytics.items_for(snapshot).filter(start_date__gte=today, start_date__lte=horizon)
    )
    if soon:
        linked = attach_public_links(soon)
        without = sum(1 for item in linked if not getattr(item.public_link, "url", ""))
        if without:
            rows.append(
                Notice(
                    text=(
                        f"{integer(without)} järgmise {NEAR_TERM_DAYS} päeva sündmust ei ole "
                        "seotud ühegi avaliku koda.ee lehega."
                    ),
                    url=(
                        f"{reverse('events')}?"
                        + urlencode(
                            {"fookus": FOCUS_REGISTER, "year": YEAR_ALL, "public_link": "unlinked"}
                        )
                    ),
                    link_label="Vaata programmist",
                )
            )
    unknown = count_unknown_date_events(snapshot)
    if unknown:
        rows.append(
            Notice(
                text=(
                    f"{integer(unknown)} sündmuse kuupäeva ei õnnestunud lähtefailist lugeda. "
                    "Need on programmis olemas, kuid ei kuulu ühessegi kuu- ega aastavaatesse."
                ),
                url=(
                    f"{reverse('events')}?"
                    + urlencode(
                        {"fookus": FOCUS_REGISTER, "year": YEAR_ALL, "status": "date_unknown"}
                    )
                ),
                link_label="Vaata neid",
            )
        )
    review = count_review_required_events(snapshot)
    if review:
        rows.append(
            Notice(
                text=f"{integer(review)} sündmust on lähtefailis märgitud ülevaatust vajavaks.",
                url=(
                    f"{reverse('events')}?"
                    + urlencode({"fookus": FOCUS_REGISTER, "year": YEAR_ALL, "review": "required"})
                ),
                link_label="Vaata neid",
            )
        )
    return tuple(rows)


def build_overview(snapshot, *, year: int | None, today: date) -> OverviewView:
    cohort = analytics.population(snapshot, year=year)
    state = analytics.temporal_state(cohort, today=today)
    starting = count_events_starting_within(snapshot)
    planning = analytics.build_planning(snapshot, year=year)

    period = str(year) if year is not None else "kogu programmis"
    headline = [
        Headline(
            label="Sündmusi programmis",
            value=integer(cohort.count()),
            note=period,
            detail="üks kirje = üks programmi sündmus, mitte toimumiskord",
        ),
        Headline(
            label="Algab lähiajal",
            value=integer(starting),
            note=f"järgmise {NEAR_TERM_DAYS} päeva jooksul",
        ),
        Headline(
            label="Juba toimunud",
            value=integer(state.past),
            note=period,
        ),
    ]
    if planning.has_data and planning.median_lead is not None:
        headline.append(
            Headline(
                label="Mediaan planeerimisvaru",
                value=f"{integer(round(planning.median_lead))} päeva",
                note=period,
                detail=f"{integer(planning.measured)} sündmuse kohta",
            )
        )

    upcoming = list(
        analytics.items_for(snapshot)
        .filter(start_date__gte=today)
        .order_by("start_date", "event_name", "service_code")[:PREVIEW_LIMIT]
    )
    upcoming = attach_public_links(upcoming)

    # What is being looked at now — upcoming events only, over the last 30
    # measured days. Never mixed with all-time popularity.
    horizon = list(
        analytics.items_for(snapshot)
        .filter(start_date__gte=today)
        .order_by("start_date", "event_name", "service_code")[:60]
    )
    watched: list[EventPreview] = []
    if horizon:
        measured = attention.attach_attention(horizon)
        ranked = sorted(
            (
                (measured[item.event_id].recent_views, item)
                for item in horizon
                if item.event_id in measured and measured[item.event_id].recent_views is not None
            ),
            key=lambda pair: (-pair[0], pair[1].event_name),
        )[:PREVIEW_LIMIT]
        linked = {
            item.event_id: item for item in attach_public_links([item for _v, item in ranked])
        }
        watched = [
            _preview(linked[item.event_id], metric=f"{integer(views)} vaatamist")
            for views, item in ranked
        ]

    return OverviewView(
        headline=tuple(headline),
        changes=_changes(snapshot, year=year, today=today),
        notices=_notices(snapshot, today=today),
        types=analytics.labelled_distribution(
            cohort,
            key_field="event_type_key",
            label_field="event_type_label",
            dimension="type",
            top=5,
        ),
        tags=analytics.labelled_distribution(
            cohort, key_field="tag_key", label_field="tag_label", dimension="tag", top=5
        ),
        delivery=analytics.delivery_mode_distribution(cohort),
        upcoming=tuple(_preview(item) for item in upcoming),
        watched=tuple(watched),
        state=state,
    )


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


def build_volume_view(snapshot, *, year: int | None, today: date) -> VolumeView:
    volume = analytics.build_volume(snapshot, year=year, today=today)
    return VolumeView(
        volume=volume,
        year_chart=charts.events_by_year_chart(volume),
        month_chart=(charts.events_by_month_chart(volume, year=year) if year is not None else None),
        seasonality_chart=charts.seasonality_chart(volume),
        duration_chart=charts.ranking_chart(
            volume.durations,
            payload_id="events-duration",
            title="Sündmuste kestus",
            question="Kui pikad Koja sündmused on?",
            footnotes=(
                "Kestus on kalendripäevades algusest lõpuni, mitte koolitustundides.",
                "Üks mitmepäevane programm on üks sündmus, mitte mitu.",
            ),
            empty_message="Sel perioodil pole kuupäevaga sündmusi.",
        ),
        quarters=volume.quarters,
    )


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------


def build_formats(snapshot, *, year: int | None, today: date) -> FormatsView:
    cohort = analytics.population(snapshot, year=year)
    types = analytics.labelled_distribution(
        cohort, key_field="event_type_key", label_field="event_type_label", dimension="type"
    )
    tags = analytics.labelled_distribution(
        cohort, key_field="tag_key", label_field="tag_label", dimension="tag"
    )
    delivery = analytics.delivery_mode_distribution(cohort)

    years = tuple(sorted(analytics.counts_by_year(snapshot)))
    delivery_years = analytics.delivery_mode_by_year(snapshot, years=years)
    top_tags = tuple((row.key, row.label) for row in tags.rows[:MIX_KEYS] if not row.is_unknown)
    tag_years = analytics.tag_mix_by_year(
        snapshot, years=years, keys=tuple(key for key, _label in top_tags)
    )

    return FormatsView(
        types=types,
        tags=tags,
        delivery=delivery,
        state=analytics.temporal_state(cohort, today=today),
        type_chart=charts.ranking_chart(
            types,
            payload_id="events-types",
            title="Sündmused tüübi järgi",
            question="Mis liiki sündmusi Koda korraldab?",
            footnotes=(
                "Tüübid tulevad lähtefaili käsitsi hooldatavast klassifikatsioonist. "
                "Uus tüüp ilmub siia ilma koodimuudatuseta.",
            ),
        ),
        tag_chart=charts.ranking_chart(
            tags,
            payload_id="events-tags",
            title="Teemad",
            question="Millised teemad programmis domineerivad?",
            footnotes=(
                "Igal sündmusel on täpselt üks teema, seega „Muu“ on ülejäänud "
                "klassifikatsiooni jääk, mitte välja jäetud kirjed.",
            ),
        ),
        delivery_chart=charts.ranking_chart(
            delivery,
            payload_id="events-delivery",
            title="Toimumisviis",
            question="Kui suur osa programmist on kohapeal, veebis või hübriidis?",
            footnotes=(
                "Tühi väärtus on „Määramata“, mitte „Kohapeal“: lähtefail ei ole "
                "nende sündmuste toimumisviisi öelnud.",
            ),
        ),
        delivery_over_time=charts.mix_over_time_chart(
            delivery_years,
            payload_id="events-delivery-years",
            title="Toimumisviisi muutus",
            question="Kuidas on kohapealse ja veebis toimuva suhe aastatega muutunud?",
            keys=(
                ("onsite", "Kohapeal"),
                ("online", "Veebis"),
                ("hybrid", "Hübriid"),
                (analytics.UNKNOWN_KEY, analytics.UNKNOWN_LABEL),
            ),
            footnotes=(
                "Osakaalud, mitte arvud: küsimus on programmi koosseisust, mitte selle mahust.",
            ),
        ),
        tag_over_time=(
            charts.mix_over_time_chart(
                tag_years,
                payload_id="events-tags-years",
                title="Teemade muutus",
                question="Millised teemad on aastatega kasvanud või kahanenud?",
                keys=(*top_tags, ("_other", "Muu")),
                footnotes=(
                    f"Jälgitakse {len(top_tags)} suurimat teemat kogu ajaloo lõikes; "
                    "ülejäänud on „Muu“.",
                ),
            )
            if top_tags
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Attention
# ---------------------------------------------------------------------------


def build_attention(snapshot, *, year: int | None, today: date) -> AttentionView:
    cohort = list(analytics.population(snapshot, year=year))
    ga4 = get_coverage()
    measured = attention.attach_attention(cohort, coverage=ga4, today=today)
    coverage = attention.coverage_report(cohort, measured, coverage=ga4)

    names = {item.event_id: item.event_name for item in cohort}

    upcoming_rows = sorted(
        (
            (measured[item.event_id].recent_views, names[item.event_id])
            for item in cohort
            if item.start_date
            and item.start_date >= today
            and item.event_id in measured
            and measured[item.event_id].recent_views is not None
        ),
        key=lambda pair: (-pair[0], pair[1]),
    )[: charts.RANKING_LIMIT]

    strongest_rows = sorted(
        (
            (measured[item.event_id].pre_event_views, names[item.event_id])
            for item in cohort
            if item.event_id in measured and measured[item.event_id].has_fair_window
        ),
        key=lambda pair: (-pair[0], pair[1]),
    )[: charts.RANKING_LIMIT]

    join = commerce.join_report(cohort)
    registration_rows: tuple[EventPreview, ...] = ()
    lead_bands: tuple[tuple[str, int], ...] = ()
    if join.has_data:
        pages = commerce.registration_pages()
        registrations = commerce.attach_registrations(cohort, pages=pages)
        ranked = sorted(registrations.values(), key=lambda row: (-row.units, row.event_id))[
            :PREVIEW_LIMIT
        ]
        by_id = {item.event_id: item for item in cohort}
        registration_rows = tuple(
            _preview(
                attach_public_links([by_id[row.event_id]])[0],
                metric=f"{integer(int(row.units))} ühikut",
            )
            for row in ranked
            if row.event_id in by_id
        )
        lead_bands = commerce.registration_lead_bands(cohort, pages=pages)

    return AttentionView(
        coverage=coverage,
        distribution=attention.distribution_of(measured),
        watched=charts.attention_ranking_chart(
            tuple((name, views) for views, name in upcoming_rows),
            payload_id="events-watched",
            title="Praegu enim vaadatud tulevased sündmused",
            question="Millised tulevased sündmused saavad praegu tähelepanu?",
            value_header="Vaatamisi 30 päevaga",
            footnotes=(
                f"Viimased {attention.RECENT_DAYS} mõõdetud päeva. See ei ole "
                "sündmuse kogupopulaarsus.",
                "Lehevaatamised, mitte inimesed: üks lugeja kahel korral on kaks vaatamist.",
            ),
            empty_message="Ühelgi tulevasel sündmusel pole mõõdetud avalikku lehte.",
        ),
        strongest=charts.attention_ranking_chart(
            tuple((name, views) for views, name in strongest_rows),
            payload_id="events-strongest",
            title="Suurim tähelepanu enne sündmust",
            question="Millised toimunud sündmused said enne toimumist kõige rohkem tähelepanu?",
            value_header=f"Vaatamisi {attention.PRE_EVENT_DAYS} päeva enne",
            footnotes=(
                f"Võrdne aken igale sündmusele: {attention.PRE_EVENT_DAYS} päeva, mis "
                "lõpeb sündmuse alguskuupäeval.",
                "Ainult sündmused, mille kogu aken jääb GA4 mõõtmisperioodi sisse.",
            ),
            empty_message=(
                "Ühelgi sündmusel ei jää kogu 30-päevane eelaken mõõtmisperioodi sisse."
            ),
        ),
        registrations=join,
        registration_rows=registration_rows,
        lead_bands=lead_bands,
    )


# ---------------------------------------------------------------------------
# Planning and price
# ---------------------------------------------------------------------------


def build_planning_view(snapshot, *, year: int | None) -> PlanningView:
    planning = analytics.build_planning(snapshot, year=year)
    prices = analytics.build_prices(snapshot, year=year)
    return PlanningView(
        planning=planning,
        prices=prices,
        bands_chart=charts.planning_bands_chart(planning),
        type_chart=charts.planning_by_type_chart(planning),
        price_chart=charts.ranking_chart(
            prices.status,
            payload_id="events-price-status",
            title="Hinnastruktuur",
            question="Kui suur osa programmist on tasuta ja kui suur tasuline?",
            footnotes=(
                "Allika enda hinnaolek, mitte hinna puudumisest tuletatud järeldus. "
                "„Hind teadmata“ ei ole tasuta.",
                "Need on programmi planeeritud hinnad, mitte tehingud.",
            ),
            empty_message="Hinnaolekut ei ole üheski kirjes.",
        ),
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_intelligence_page(
    summary: EventProgrammeSummary, params, *, today: date | None = None
) -> IntelligencePage:
    """Read the programme once and shape the requested focus."""
    today = today or timezone.localdate()
    snapshot = summary.snapshot
    options = get_event_programme_filter_options(snapshot)
    focus = parse_focus(params.get("fookus"))

    raw_year = params.get("year")
    if raw_year == YEAR_ALL:
        year = None
    else:
        try:
            candidate = int(raw_year) if raw_year else None
        except TypeError, ValueError:
            candidate = None
        year = candidate if candidate in options.years else options.default_year(today)

    page = IntelligencePage(
        summary=summary,
        focus=focus,
        focus_links=_focus_links(focus, year=year),
        year=year,
        year_links=_year_links(options, focus=focus, year=year),
        period_label=str(year) if year is not None else ALL_YEARS_LABEL,
        quality=build_coverage(snapshot, include_commerce=focus == FOCUS_ATTENTION),
    )
    if snapshot is None:
        return page

    from dataclasses import replace

    if focus == FOCUS_OVERVIEW:
        return replace(page, overview=build_overview(snapshot, year=year, today=today))
    if focus == FOCUS_VOLUME:
        return replace(page, volume=build_volume_view(snapshot, year=year, today=today))
    if focus == FOCUS_FORMATS:
        return replace(page, formats=build_formats(snapshot, year=year, today=today))
    if focus == FOCUS_ATTENTION:
        return replace(page, attention=build_attention(snapshot, year=year, today=today))
    if focus == FOCUS_PLANNING:
        return replace(page, planning=build_planning_view(snapshot, year=year))
    return page


__all__ = [
    "ALL_YEARS_LABEL",
    "FOCUS_ATTENTION",
    "FOCUS_FORMATS",
    "FOCUS_LABELS",
    "FOCUS_OVERVIEW",
    "FOCUS_PLANNING",
    "FOCUS_REGISTER",
    "FOCUS_VALUES",
    "FOCUS_VOLUME",
    "AttentionView",
    "Change",
    "DataCoverage",
    "EventPreview",
    "FormatsView",
    "Headline",
    "IntelligencePage",
    "Notice",
    "OverviewView",
    "PlanningView",
    "VolumeView",
    "build_coverage",
    "build_intelligence_page",
    "focus_url",
    "parse_focus",
]
