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
from datetime import date
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
#: Owned by `page.py`, which builds every link the register emits. One spelling,
#: so a pagination link and a focus tab can never disagree about the register's
#: own name.
FOCUS_REGISTER = REGISTER_FOCUS

#: The navigation, in reading order: the answer, then the two structural
#: analyses, then the list.
#:
#: `Huvi` and `Planeerimine` were the fourth and fifth and came off on
#: 2026-08-15 at the board's request. Almost everything they held went with
#: them; `Hinnastruktuur` was the one section worth keeping and is on `Ülevaade`
#: now. Neither key is parsed any more, so an old `?fookus=huvi` bookmark falls
#: through `parse_focus` to `Ülevaade` exactly as any other unreadable value
#: does — the documented behaviour of this page, and why no redirect is needed.
FOCUS_LABELS: tuple[tuple[str, str], ...] = (
    (FOCUS_OVERVIEW, "Ülevaade"),
    (FOCUS_VOLUME, "Maht ja kalender"),
    (FOCUS_FORMATS, "Formaadid ja teemad"),
    (FOCUS_REGISTER, "Ürituste nimekiri"),
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
    """One primary answer. `value` is `None` when the source cannot supply it.

    `label` is optional. The overview's two figures carry no caption since
    2026-08-15 — the board struck them — and say what they count in `unit`
    instead, so the card reads `12 sündmust järgmise 30 päeva jooksul` rather
    than a bare number under a heading.
    """

    value: str | None
    label: str = ""
    unit: str = ""
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
    types: analytics.Distribution | None = None
    delivery: analytics.Distribution | None = None
    upcoming: tuple[EventPreview, ...] = ()
    watched: tuple[EventPreview, ...] = ()
    state: analytics.TemporalState | None = None
    #: `Hinnastruktuur`, the one part of `Planeerimine` worth keeping when that
    #: focus came off. `None` when the programme records no price status at all,
    #: which is also what keeps the chart bundle off this page.
    price_chart: charts.ChartPayload | None = None


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
    tag_chart: charts.ChartPayload | None = None
    delivery_over_time: charts.ChartPayload | None = None
    tag_over_time: charts.ChartPayload | None = None


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

    @property
    def has_data(self) -> bool:
        return self.summary.has_data

    @property
    def draws_charts(self) -> bool:
        """Whether this focus loads the chart bundle at all.

        ECharts is over a megabyte, so it ships only to a view that draws
        something. `Ürituste nimekiri` never does.

        `Ülevaade` is the conditional one: it drew nothing until
        `Hinnastruktuur` moved onto it, and that chart is absent whenever the
        programme records no price. Asking the built view rather than the focus
        key keeps the bundle off a page with no canvas — the rule this property
        has always enforced, now that one focus can go either way.
        """
        if self.focus in (FOCUS_VOLUME, FOCUS_FORMATS):
            return True
        return bool(self.overview and self.overview.price_chart)


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

    # The programme count, the median planning lead and the fastest-growing
    # theme were struck on 2026-08-15. What is left is the two comparisons a
    # reader acts on: how much has started this year against the same point
    # last year, and how the online share moved.
    rows: list[Change] = []

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

    return tuple(rows)


def build_overview(snapshot, *, year: int | None, today: date) -> OverviewView:
    cohort = analytics.population(snapshot, year=year)
    state = analytics.temporal_state(cohort, today=today)
    starting = count_events_starting_within(snapshot)

    # Two figures, each stating its own scope in the line the reader already
    # reads. The board struck the programme count and the median planning lead
    # on 2026-08-15, and struck the captions and `Seisuga` rows off the two that
    # stayed — so a figure that does not name what it counts would now be a bare
    # number with nothing beside it.
    if year is None:
        period_words = "kogu programmis"
    elif year == today.year:
        period_words = "aasta algusest"
    else:
        period_words = f"{year}. aastal"
    headline = [
        Headline(
            value=integer(starting),
            # `kuu` rather than `{NEAR_TERM_DAYS} päeva` since 2026-08-16. The
            # wording no longer derives from the constant, so if the window ever
            # stops being thirty days this string has to change with it.
            unit="sündmust järgmise kuu jooksul",
        ),
        Headline(value=integer(state.past), unit=f"sündmust toimunud {period_words}"),
    ]

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

    prices = analytics.build_prices(snapshot, year=year)

    return OverviewView(
        headline=tuple(headline),
        changes=_changes(snapshot, year=year, today=today),
        types=analytics.labelled_distribution(
            cohort,
            key_field="event_type_key",
            label_field="event_type_label",
            dimension="type",
            top=5,
        ),
        delivery=analytics.delivery_mode_distribution(cohort),
        upcoming=tuple(_preview(item) for item in upcoming),
        watched=tuple(watched),
        state=state,
        # Moved from `Planeerimine` unchanged: same selector, same statuses,
        # same two footnotes. A planned price is not a transaction and a price
        # nobody recorded is not free, so both notes travelled with the chart
        # rather than being trimmed as chrome.
        price_chart=(
            charts.ranking_chart(
                prices.status,
                payload_id="events-price-status",
                title="Hinnastruktuur",
                # Both notes moved to `Andmete kohta` on `/haldus/` on
                # 2026-08-16, where the rest of Sündmused' provenance already
                # lives. They were kept here in an earlier round precisely
                # because they are not chrome, and that reasoning is unchanged —
                # only the place a reader finds them.
                empty_message="Hinnaolekut ei ole üheski kirjes.",
            )
            if prices.status.total
            else None
        ),
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
        tag_chart=charts.ranking_chart(
            tags,
            payload_id="events-tags",
            title="Teemad",
            question="Millised teemad programmis domineerivad?",
        ),
        delivery_over_time=charts.mix_over_time_chart(
            delivery_years,
            payload_id="events-delivery-years",
            title="Toimumisviis aastate lõikes",
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
            )
            if top_tags
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Attention
# ---------------------------------------------------------------------------


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
        # No focus asks for the Commerce columns now that `Huvi` is gone. The
        # parameter stays on `build_coverage` because the registration join is
        # real and reported elsewhere; nothing this page renders reads it.
        quality=build_coverage(snapshot),
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
    return page


__all__ = [
    "ALL_YEARS_LABEL",
    "FOCUS_FORMATS",
    "FOCUS_LABELS",
    "FOCUS_OVERVIEW",
    "FOCUS_REGISTER",
    "FOCUS_VALUES",
    "FOCUS_VOLUME",
    "Change",
    "DataCoverage",
    "EventPreview",
    "FormatsView",
    "Headline",
    "IntelligencePage",
    "OverviewView",
    "VolumeView",
    "build_coverage",
    "build_intelligence_page",
    "focus_url",
    "parse_focus",
]
