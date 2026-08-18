"""The Sündmused intelligence dashboard: one page since 2026-08-18.

`/sundmused/` was three focus views chosen by `?fookus=` — `Ülevaade`, `Maht`,
`Formaadid` — until they became one scroll: the headline, both volume charts,
the type/mode breakdown, price structure and delivery mix over time, the two
standing lists, and the searchable programme register underneath all of it.
`Huvi` and `Planeerimine` came off earlier, on 2026-08-15; the theme-only
charts (`Teemad`, `Teemade muutus`) and the duration ranking left with this
round, because none of the three is in the page Kaur asked for. An old
`?fookus=` value — including `maht` or `formaadid` — is simply unread now, the
same as any other unrecognised query parameter.

**The year is an event cohort, never a measurement window.** `2026` means
"events whose own start date falls in 2026". Their public pages may have been
viewed in 2025 and their registrations bought in 2025 too; those windows are
chosen by the analyses that need them and are labelled where they appear. One
control that silently meant three different periods at once is the specific
confusion this page is built to avoid.

Every comparison on the page is a fold of two figures a reader could find
elsewhere — never a generated sentence, and never a claim about *why* a
number moved.
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

ALL_YEARS_LABEL = "Kõik aastad"

#: How many events the overview previews. Five is a glance; twenty is a list the
#: reader has to work through, and the programme register below it is where a
#: list belongs.
PREVIEW_LIMIT = 5


@dataclass(frozen=True)
class YearLink:
    value: str
    label: str
    url: str
    is_active: bool


@dataclass(frozen=True)
class Headline:
    """One of the four KPI cards, its comparison folded into its own note.

    `label` is the card's title now — since 2026-08-18 the four are titled
    cards again, each with a real label, rather than the two bare `value unit`
    sentences the board reduced this strip to on 2026-08-15. `change`/
    `direction` carry a year-on-year comparison the same way every other
    dashboard's headline does: an arrow and a signed figure, never a colour
    alone.
    """

    value: str | None
    label: str = ""
    unit: str = ""
    note: str = ""
    change: str = ""
    change_label: str = ""
    direction: str = ""

    @property
    def has_change(self) -> bool:
        return bool(self.change)


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
    """Everything one render of `/sundmused/` draws, now that it is one page.

    `Maht ja kalender` and `Formaadid ja teemad` folded in whole on
    2026-08-18: `year_chart`/`month_chart`/`quarters` were `VolumeView`'s,
    `delivery_over_time` was `FormatsView`'s. Their theme-only charts —
    `Teemad`, `Teemade muutus` — and the duration ranking did not fold in;
    none of the three is in the page Kaur asked for, so nothing here builds
    them any more.
    """

    headline: tuple[Headline, ...] = ()
    types: analytics.Distribution | None = None
    delivery: analytics.Distribution | None = None
    upcoming: tuple[EventPreview, ...] = ()
    watched: tuple[EventPreview, ...] = ()
    state: analytics.TemporalState | None = None
    #: `Hinnastruktuur`, the one part of `Planeerimine` worth keeping when that
    #: focus came off. `None` when the programme records no price status at all,
    #: which is also what keeps the chart bundle off this page.
    price_chart: charts.ChartPayload | None = None
    year_chart: charts.ChartPayload | None = None
    month_chart: charts.ChartPayload | None = None
    quarters: tuple[analytics.Category, ...] = ()
    delivery_over_time: charts.ChartPayload | None = None


@dataclass(frozen=True)
class IntelligencePage:
    """One rendered state of `/sundmused/`."""

    summary: EventProgrammeSummary
    year: int | None = None
    year_links: tuple[YearLink, ...] = ()
    period_label: str = ALL_YEARS_LABEL
    quality: DataCoverage = field(default_factory=DataCoverage)
    overview: OverviewView | None = None

    @property
    def has_data(self) -> bool:
        return self.summary.has_data

    @property
    def draws_charts(self) -> bool:
        """Whether the chart bundle is worth loading at all.

        ECharts is over a megabyte. The by-year and by-month charts draw
        whenever the programme has any dated event at all, so in practice this
        is `has_data` restated — but it stays a real check rather than an
        assumption, because an empty programme draws nothing and should not
        pay for the bundle either.
        """
        view = self.overview
        return bool(
            view
            and (view.price_chart or view.year_chart or view.month_chart or view.delivery_over_time)
        )


# ---------------------------------------------------------------------------
# Query state
# ---------------------------------------------------------------------------


def year_url(*, year: int | None) -> str:
    query = [("year", str(year) if year is not None else YEAR_ALL)]
    return f"{reverse('events')}?{urlencode(query)}"


def _year_links(options, *, year: int | None) -> tuple[YearLink, ...]:
    links = [
        YearLink(
            value=str(value),
            label=str(value),
            url=year_url(year=value),
            is_active=year == value,
        )
        for value in options.years
    ]
    links.append(
        YearLink(
            value=YEAR_ALL,
            label=ALL_YEARS_LABEL,
            url=year_url(year=None),
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


def _headlines(
    snapshot, *, year: int | None, today: date, total: int, state, quarters, starting: int
) -> tuple[Headline, ...]:
    """The four cards the page leads with, each carrying its own comparison.

    Four again since 2026-08-18 — the board reduced this strip to two bare
    `value unit` sentences on 2026-08-15, and Kaur's mockup restored the other
    two rather than repeating them lower as a separate `Mis muutus?` list. The
    two comparisons this function used to hand back as standalone `Change`
    rows are exactly the notes on cards two and four now; nothing about what
    they compare changed; only where they render did.
    """
    by_year = analytics.counts_by_year(snapshot)
    previous = year - 1 if year is not None else None
    has_previous = year is not None and previous in by_year

    period_words = (
        "kogu programmis"
        if year is None
        else "aasta algusest"
        if year == today.year
        else f"{year}. aastal"
    )

    cards = [
        Headline(
            label=f"Sündmusi programmis {year}" if year is not None else "Sündmusi programmis",
            value=integer(total),
            note=f"toimunud {integer(state.past)} · tulemas {integer(state.upcoming)}",
        )
    ]

    if has_previous and year == today.year:
        ytd_now = analytics.count_year_to_date(snapshot, year=year, today=today)
        ytd_then = analytics.count_year_to_date(snapshot, year=previous, today=today)
        ytd_delta = ytd_now - ytd_then
        cards.append(
            Headline(
                label="Alanud 1. jaanuarist",
                value=integer(ytd_now),
                change=signed_integer(ytd_delta) if ytd_delta else "muutumatu",
                change_label=f"{signed_integer(ytd_delta)} vs {previous}. aasta sama kuupäev",
                direction=_direction(ytd_delta),
                note=f"vs {previous}",
            )
        )
    else:
        cards.append(
            Headline(label="Alanud 1. jaanuarist", value=integer(state.past), note=period_words)
        )

    quarter_note = ""
    if year == today.year:
        current_quarter = _quarter_number_for(today)
        remaining = [
            row for row in quarters if row.count and _quarter_number(row.key) >= current_quarter
        ]
        if remaining:
            quarter_note = " · ".join(f"{row.key} {integer(row.count)}" for row in remaining)
            quarter_note = f"{quarter_note} sündmust"
    cards.append(
        Headline(
            label="Järgmise kuu jooksul",
            value=integer(starting),
            unit="sündmust",
            note=quarter_note,
        )
    )

    if has_previous:
        modes = analytics.delivery_mode_by_year(snapshot, years=(previous, year))
        online_now = next((row for row in modes[year].all_rows if row.key == "online"), None)
        online_then = next((row for row in modes[previous].all_rows if row.key == "online"), None)
        if online_now and online_then and modes[year].total and modes[previous].total:
            shift = online_now.share - online_then.share
            cards.append(
                Headline(
                    label="Veebis toimuvate osakaal",
                    value=percent(online_now.share),
                    # Percentage **points**, not percent. A share moving from
                    # 20% to 25% has risen five points and by a quarter, and
                    # writing the first as "+5%" is how the two get confused.
                    change=percentage_points(shift),
                    change_label=f"{percentage_points(shift)} vs {previous}",
                    direction=_direction(shift),
                    note=f"vs {previous} ({percent(online_then.share)})",
                )
            )

    return tuple(cards)


def _quarter_number(key: str) -> int:
    return int(key[1]) if key.startswith("Q") and len(key) == 2 else 0


def _quarter_number_for(today: date) -> int:
    return (today.month - 1) // 3 + 1


def build_overview(snapshot, *, year: int | None, today: date) -> OverviewView:
    cohort = analytics.population(snapshot, year=year)
    total = cohort.count()
    state = analytics.temporal_state(cohort, today=today)
    starting = count_events_starting_within(snapshot)
    volume = analytics.build_volume(snapshot, year=year, today=today)

    headline = _headlines(
        snapshot,
        year=year,
        today=today,
        total=total,
        state=state,
        quarters=volume.quarters,
        starting=starting,
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

    prices = analytics.build_prices(snapshot, year=year)

    years = tuple(sorted(analytics.counts_by_year(snapshot)))
    delivery_years = analytics.delivery_mode_by_year(snapshot, years=years)

    return OverviewView(
        headline=headline,
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
        # `Maht ja kalender`'s two charts, folded in whole on 2026-08-18.
        year_chart=charts.events_by_year_chart(volume),
        month_chart=(
            charts.events_by_month_with_typical_chart(volume, year=year)
            if year is not None
            else None
        ),
        quarters=volume.quarters,
        # `Formaadid ja teemad`'s delivery-over-time chart, folded in the same
        # round. Its theme charts and the duration ranking did not fold in —
        # neither is in the page Kaur asked for.
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
        ),
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_intelligence_page(
    summary: EventProgrammeSummary, params, *, today: date | None = None
) -> IntelligencePage:
    """Read the programme once and build the one page."""
    today = today or timezone.localdate()
    snapshot = summary.snapshot
    options = get_event_programme_filter_options(snapshot)

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
        year=year,
        year_links=_year_links(options, year=year),
        period_label=str(year) if year is not None else ALL_YEARS_LABEL,
        # `include_commerce` stays off: no chart on this page reads the
        # registration join, only `/haldus/` does, and that block builds its
        # own coverage separately.
        quality=build_coverage(snapshot),
    )
    if snapshot is None:
        return page

    from dataclasses import replace

    return replace(page, overview=build_overview(snapshot, year=year, today=today))


__all__ = [
    "ALL_YEARS_LABEL",
    "DataCoverage",
    "EventPreview",
    "Headline",
    "IntelligencePage",
    "OverviewView",
    "build_coverage",
    "build_intelligence_page",
    "year_url",
]
