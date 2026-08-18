"""The Liikmeskond page.

An ordinary protected page that reads PostgreSQL only. It never calls Koda.ee
and never reads a file: collection is a separate scheduled command and the
internal history arrives through a one-time import or the staff form.

The page is the internal board report: its latest figures, its history and its
data-quality notes. The public directory count is on the overview and is not
repeated here — the board asked for the source list, the connection strip and
the public-catalogue section to come off the top of this page.

`summary` is still read, because the closing note that the two counts are not
the same measurement only appears when both actually exist. Nothing on this page
adds them, compares them as though they should agree, or continues one series
with the other.

## One page, three focuses

The page answers three different management questions and `fookus` names which
one is drawn. It is an ordinary GET parameter, every control is a link, and an
unknown value renders the overview rather than raising — so the page survives a
stale bookmark, a typed URL and the back button. There is no client-side state
and no SPA.

Three keys have retired into this shape: `liikmemaks` on 2026-08-16, its one
chart onto the overview; `koosseis` and `liikumine` on 2026-08-17 — `koosseis`
mostly onto the overview, `liikumine` into `kasv`, which took its content and
the name `Sisse-välja`. See `RETIRED_FOCUSES` in `focus.py`.

The overview is the default and is built to be read without interaction: the
headline figures, what changed and who the members are, the current year's
movement, the membership trend with the fee history under one window, and the
composition distributions those "who" facts preview. `Sisse-välja` is where
the turnover analysis lives — arrivals, departures and what predicts them.
Anything a control governs sits inside the section that carries the control,
which is the rule the range control already followed.
"""

from dataclasses import replace
from datetime import timedelta
from urllib.parse import urlencode

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.core.formatting import integer, short_date
from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .charts import (
    BENCHMARK_AVERAGE,
    BENCHMARK_PREVIOUS,
    PARAM_BENCHMARK,
    PARAM_VIEW,
    VIEW_CUMULATIVE,
    VIEW_MONTHLY,
    AnalyticsSection,
    Toggle,
    ToggleOption,
    available_benchmarks,
    composition_chart,
    fee_collection_chart,
    growth_index_chart,
    join_cohort_chart,
    monthly_new_members_chart,
    new_member_periods_chart,
    removal_reasons_chart,
    seasonality_chart,
    size_movement_chart,
    total_and_paid_chart,
)
from .composition import Dimension
from .composition_selectors import (
    get_composition_growth,
    get_current_composition_snapshot,
)
from .focus import (
    FOCUS_GROWTH,
    FOCUS_OVERVIEW,
    FOCUS_REGISTER,
    PARAM_FOCUS,
    focus_links,
    resolve_focus,
)
from .intelligence import (
    KPI_BASELINE_LOOKBACK_DAYS,
    build_headlines,
    build_movement_summary,
    composition_subtitles,
)
from .internal_selectors import (
    DEFAULT_MONTHLY_HISTORY_YEARS,
    get_decision_batches,
    get_fee_collection_trend,
    get_internal_membership_latest,
    get_internal_membership_observations,
    get_internal_membership_quality_summary,
    get_internal_membership_trend,
    get_membership_size_movement,
    get_monthly_new_members,
    get_new_member_periods,
    get_removal_reasons,
)
from .ranges import (
    LEGACY_PARAM,
    PAGE_DEFAULT_MONTHS,
    PARAM_FROM,
    PARAM_TO,
    offers_choice,
    range_presets,
    resolve_window,
)
from .register_selectors import (
    PAGE_SIZE,
    compare_sources,
    get_current_register_snapshot,
    get_member_list,
    get_register_snapshot_info,
    status_options,
)
from .selectors import get_membership_summary


@require_GET
def membership_overview(request):
    summary = get_membership_summary()
    latest = get_internal_membership_latest()
    quality = get_internal_membership_quality_summary()
    focus = resolve_focus(request.GET.get(PARAM_FOCUS))

    # The roster snapshot. A third source, read once: its date, its counts
    # and nothing that identifies a member. It is never added to either
    # membership total — it describes what kinds of organisations the
    # membership is made of, not how many there are.
    composition = get_current_composition_snapshot()

    # The register — the same roster export with its rows kept — read only for
    # its date here, so the focus can be offered and the source line can state
    # what the list describes. The rows themselves are read below, and only on
    # the focus that draws them: a page of members is 25 rows and there is no
    # reason for the overview to pay for them.
    register_snapshot = get_current_register_snapshot()

    # The window comes from `ranges.py`, which the overview card also reads, so
    # the two pages describe the same window with the same words. Whatever the
    # query string says is folded back inside the observation span, and
    # anything unreadable — a malformed date, a stale `?vahemik=` bookmark —
    # falls back to the default rather than raising, so the control cannot be
    # used to ask for an unbounded or arbitrary query.
    window = resolve_window(
        request.GET.get(PARAM_FROM),
        request.GET.get(PARAM_TO),
        earliest=quality.earliest_observation_date,
        latest=quality.latest_observation_date,
        legacy_key=request.GET.get(LEGACY_PARAM),
        default_months=PAGE_DEFAULT_MONTHS,
    )

    trend = get_internal_membership_trend(
        date_from=window.start if window else None,
        date_to=window.end if window else None,
    )

    # The headline comparisons need observations from around this time last
    # year, which the drawn window does not necessarily contain: a reader who
    # narrows the chart to six months has not asked for the year-ago readout to
    # disappear. So the baselines come from their own bounded lookback, and the
    # figures above the chart stay put while the chart's range moves.
    if latest is not None:
        baseline_history = get_internal_membership_observations(
            date_from=latest.observation_date - timedelta(days=KPI_BASELINE_LOOKBACK_DAYS),
            date_to=latest.observation_date,
        )
    else:
        baseline_history = ()

    monthly_years = _monthly_years(quality.latest_observation_date)
    monthly = get_monthly_new_members(monthly_years) if monthly_years else {}

    presets = range_presets(
        earliest=quality.earliest_observation_date,
        latest=quality.latest_observation_date,
        active=window,
    )
    has_range_choice = offers_choice(
        earliest=quality.earliest_observation_date,
        latest=quality.latest_observation_date,
    )

    view = _one_of(request.GET.get(PARAM_VIEW), (VIEW_MONTHLY, VIEW_CUMULATIVE), VIEW_MONTHLY)
    supported = available_benchmarks(monthly)
    benchmark = _one_of(
        request.GET.get(PARAM_BENCHMARK),
        supported,
        supported[0] if supported else BENCHMARK_PREVIOUS,
    )

    fee_rows = (
        get_fee_collection_trend(date_from=trend.date_from, date_to=trend.date_to)
        if trend.has_data
        else ()
    )

    batches = get_decision_batches(
        date_from=window.start if window else None,
        date_to=window.end if window else None,
    )
    decisions = _decisions_offered(batches)
    chosen = _one_of(
        request.GET.get(PARAM_DECISION),
        [key for key, _label in decisions],
        decisions[0][0] if decisions else "",
    )

    # Only resolved values reach a link: the window is clamped to the history
    # and every choice has already been validated.
    control_state: dict[str, str] = {
        PARAM_FOCUS: focus,
        PARAM_VIEW: view,
        PARAM_BENCHMARK: benchmark,
    }
    if chosen:
        control_state[PARAM_DECISION] = chosen
    carried: dict[str, str] = {}
    if window is not None:
        control_state[PARAM_FROM] = window.start.isoformat()
        control_state[PARAM_TO] = window.end.isoformat()
        # The window is the one choice that survives a focus change: it means
        # the same thing on every focus that draws a time series. The chart
        # toggles do not, so they are left to resolve from their defaults.
        carried[PARAM_FROM] = window.start.isoformat()
        carried[PARAM_TO] = window.end.isoformat()

    # Which focuses have something to draw. A navigation item leading to an
    # empty page reads as a fault, so an unbuilt focus is simply not offered.
    # `Sisse-välja` draws from four sources since the 2026-08-17 merge — the
    # trend, the monthly recruitment, the movement and the composition flow —
    # any one of which is enough to offer it.
    available = {FOCUS_OVERVIEW}
    if (
        trend.has_data
        or any(monthly.values())
        or latest is not None
        or batches
        or composition is not None
    ):
        available.add(FOCUS_GROWTH)
    if register_snapshot is not None:
        available.add(FOCUS_REGISTER)

    # The list, its filters and the two-source comparison, built only for the
    # focus that shows them. `otsing` and `staatus` are ordinary GET values and
    # are resolved the same way every other control on this page is: an unknown
    # status falls back to "all", and a page number past the end is clamped
    # rather than raising, so a stale bookmark renders a page instead of a 404.
    member_list = None
    comparison = None
    register_statuses = ()
    if focus == FOCUS_REGISTER and register_snapshot is not None:
        member_list = get_member_list(
            snapshot=register_snapshot,
            query=request.GET.get(PARAM_SEARCH, ""),
            status=request.GET.get(PARAM_STATUS, ""),
            page=_page_number(request.GET.get(PARAM_PAGE)),
            page_size=PAGE_SIZE,
        )
        if member_list.page > member_list.page_count:
            member_list = get_member_list(
                snapshot=register_snapshot,
                query=member_list.query,
                status=member_list.status,
                page=member_list.page_count,
                page_size=PAGE_SIZE,
            )
        register_statuses = status_options(register_snapshot)
        comparison = compare_sources(snapshot=register_snapshot)

    sections = _sections_for(
        focus,
        trend=trend,
        fee_rows=fee_rows,
        monthly=monthly,
        latest=latest,
        batches=batches,
        decisions=decisions,
        chosen=chosen,
        view=view,
        benchmark=benchmark,
        supported=supported,
        presets=presets,
        has_range_choice=has_range_choice,
        control_state=control_state,
        window=window,
        composition=composition,
    )

    return render(
        request,
        "membership/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "membership",
            "freshness": current_freshness(summary),
            # Read only by the closing note, which says the two counts are
            # not the same measurement and appears only when both exist.
            "summary": summary,
            # The internal board-report history, clearly separate. Only
            # `internal_latest` still governs anything on this page — the
            # "nothing imported yet" empty state — now that `Andmete seis`
            # itself has moved to `/haldus/`.
            "internal_latest": latest,
            "internal_trend": trend,
            "focus": focus,
            "focus_links": focus_links(focus, carried=carried, available=frozenset(available)),
            "is_overview": focus == FOCUS_OVERVIEW,
            # The four cells of the headline strip: three figures and the
            # current year's movement, which was a section of its own until
            # 2026-08-18 and is the strip's fourth column now.
            #
            # `Mis muutus?` was between them and went the same day. Every
            # comparison it drew is either in the strip — the member total, the
            # paid share, the fee completion, this year's arrivals and
            # departures — or on a chart below it.
            "headlines": build_headlines(latest, baseline_history)
            if focus == FOCUS_OVERVIEW
            else (),
            "movement_summary": build_movement_summary(latest, baseline_history)
            if focus == FOCUS_OVERVIEW
            else None,
            # The members list and what it is a reading of. Present only on the
            # focus that draws them, so no other view can start rendering rows.
            "member_list": member_list,
            "register_snapshot": get_register_snapshot_info() if focus == FOCUS_REGISTER else None,
            "register_statuses": register_statuses,
            "register_search": PARAM_SEARCH,
            "register_status_param": PARAM_STATUS,
            "register_page_param": PARAM_PAGE,
            # Two sources compared by identity, never merged into one number.
            "source_comparison": comparison,
            # Each section carries only the controls that govern it. A section
            # with nothing to draw renders nothing.
            "sections": [section for section in sections if section.has_charts],
            "trend_window": window,
            "trend_earliest": quality.earliest_observation_date,
            "trend_latest": quality.latest_observation_date,
        },
    )


def _sections_for(focus, **ctx) -> list[AnalyticsSection]:
    """The analytical sections belonging to one focus.

    Every focus is assembled by its own function, so adding a chart to one view
    cannot quietly change another, and a reader of this module can see what a
    given URL draws without tracing conditionals through a single long list.
    """
    builders = {
        FOCUS_OVERVIEW: _overview_sections,
        FOCUS_GROWTH: _growth_sections,
        # The members list draws no chart. It is a table, a search box and a
        # comparison, all of which the template renders from context — so this
        # focus contributes no analytical section and, because `sections` is
        # empty, ships no chart JavaScript either.
        FOCUS_REGISTER: _register_sections,
    }
    return builders[focus](**ctx)


def _trend_section(*, trend, presets, has_range_choice, section_id="section-trend"):
    """The membership stock, which is the page's dominant visual.

    Total and paid on one axis, a real time axis, no interpolation across the
    months nobody reported. It carries the range control because it is the chart
    the range governs.
    """
    charts = [total_and_paid_chart(trend)] if trend.has_data else []
    return AnalyticsSection(
        section_id=section_id,
        title="Liikmeskonna areng",
        show_title=False,
        description="",
        charts=tuple(charts),
        presets=presets,
        show_custom_range=has_range_choice,
    )


def _overview_sections(*, trend, fee_rows, presets, has_range_choice, composition, **_ignored):
    """Three sections, each answering one question and carrying its own control.

    `Liikmete arv ja tasunud liikmed` is the page's dominant visual and the one
    thing the range control governs, so the control sits on its heading row.
    `Liikmemaksu laekumine` was drawn inside that same section until 2026-08-18
    — one range control above two charts of which it governed both, which was
    true but made the fee chart look like a detail of the trend. It is its own
    section now, with its own four figures on its own heading row.

    `Kes on meie liikmed?` is the third. It was two things until the same day: a
    strip of four facts, and a separate `Koosseisu jaotused` holding the four
    charts those facts previewed. A reader had to hold a fact in mind while
    scrolling to the drawing that proved it. One section now, each chart
    carrying its own fact as a subtitle.
    """
    sections = []

    if trend.has_data:
        sections.append(
            AnalyticsSection(
                section_id="section-trend",
                title="Liikmete arv ja tasunud liikmed",
                # Drawn without its readouts here, and only here. They are
                # `Liikmeid kokku` and `Tasunud liikmeid` with their year-ago
                # changes — which is the headline strip one row above, word for
                # word. `Sisse-välja` draws the same chart with them, because
                # that view carries no strip to repeat.
                charts=(replace(total_and_paid_chart(trend), readouts=()),),
                presets=presets,
                show_custom_range=has_range_choice,
            )
        )

    if fee_rows:
        fees = fee_collection_chart(fee_rows)
        sections.append(
            AnalyticsSection(
                section_id="section-fees",
                title="Liikmemaksu laekumine",
                charts=(replace(fees, title_hidden=True),),
                # Lifted onto the heading row rather than drawn twice: the
                # section is named after the chart, so its four figures belong
                # beside that name.
                readouts=fees.readouts,
            )
        )

    if composition is not None:
        sections.append(_composition_section(composition))

    return sections


def _composition_section(composition) -> AnalyticsSection:
    """`Kes on meie liikmed?` — four distributions of one roster reading.

    Ordinal dimensions keep their scale order; nominal ones are ranked, because
    for a county or a sector the ranking is most of the answer.

    Every chart states its own fact in its subtitle — the largest group, or for
    tenure the median — so the drawing and the sentence about it cannot drift
    apart. `composition_subtitles` composes them, and it is the one place the
    "ignore Teadmata" rule is applied.
    """
    on = composition.snapshot_date
    subtitles = composition_subtitles(composition)
    structure = (
        (Dimension.EMPLOYEE_SIZE, "Ettevõtte suurus", False),
        (Dimension.REGION, "Piirkonnad", True),
        (Dimension.SECTOR, "Tegevusalad", True),
        (Dimension.TENURE_BAND, "Liikmestaaž", False),
    )

    charts = []
    for dimension, title, ranked in structure:
        chart = composition_chart(
            composition.dimension(dimension),
            payload_id=f"membership-composition-{dimension.replace('_', '-')}",
            title=title,
            snapshot_date=on,
            ranked=ranked,
            question=subtitles.get(dimension, ""),
        )
        if chart is not None:
            charts.append(chart)

    return AnalyticsSection(
        section_id="section-structure",
        title="Kes on meie liikmed?",
        # The roster's own date and its own row count. Not a membership total:
        # this is what one hand-imported export counted, and the strip above
        # states the two membership totals that are.
        description=(f"seisuga {short_date(on)} · {integer(composition.row_count)} liiget"),
        charts=tuple(charts),
        grid=True,
    )


def _register_sections(**_ignored):
    """No analytical section: the list focus is a table, not a chart.

    Kept as an explicit builder rather than a missing key, so `_sections_for`
    stays total over the focus vocabulary and a new focus cannot render the
    previous one's charts by falling through.
    """
    return []


def _growth_sections(
    *,
    trend,
    monthly,
    presets,
    has_range_choice,
    view,
    benchmark,
    supported,
    control_state,
    latest,
    batches,
    composition,
    **_ignored,
):
    """Recruitment, departures, the stock both feed, and what predicts them.

    `Sisse-välja` merged from `Kasv ja püsimine` and `Liikumine ja põhjused` on
    2026-08-17: who is arriving, who is leaving and which categories forecast
    the arrivals read as one question — the membership's turnover — not two.
    `Juhatuse otsused`, one board decision's own list toggled by `batches`,
    `decisions` and `chosen`, left the movement section the same day it left
    `Liikumine ja põhjused`; those two are still computed by the caller and
    still accepted here so its signature does not have to change. True
    retention is still not here at all — no source in this application
    records departures per member, so a retention figure would have to be
    invented.
    """
    sections = [
        _trend_section(
            trend=trend,
            presets=presets,
            has_range_choice=has_range_choice,
            section_id="section-stock",
        )
    ]

    recruitment = []
    if any(monthly.values()):
        recruitment.append(monthly_new_members_chart(monthly, view=view, benchmark=benchmark))
    sections.append(
        AnalyticsSection(
            section_id="section-recruitment",
            title="Uute liikmete dünaamika",
            charts=tuple(recruitment),
            toggles=(
                Toggle(
                    label="Vaade",
                    options=(
                        _toggle(
                            control_state,
                            PARAM_VIEW,
                            VIEW_MONTHLY,
                            "Kuu kaupa",
                            view,
                            anchor="section-recruitment",
                        ),
                        _toggle(
                            control_state,
                            PARAM_VIEW,
                            VIEW_CUMULATIVE,
                            "Kumulatiivselt",
                            view,
                            anchor="section-recruitment",
                        ),
                    ),
                ),
                Toggle(
                    label="Võrdlus",
                    options=tuple(
                        _toggle(
                            control_state,
                            PARAM_BENCHMARK,
                            key,
                            label,
                            benchmark,
                            anchor="section-recruitment",
                        )
                        for key, label in (
                            (BENCHMARK_PREVIOUS, "Eelmine aasta"),
                            (BENCHMARK_AVERAGE, "3 aasta keskmine"),
                        )
                        if key in supported
                    ),
                ),
            ),
        )
    )

    season = seasonality_chart(monthly)
    if season is not None:
        sections.append(
            AnalyticsSection(
                # Renamed from `Kuude hooajalisus` and lost its description
                # on 2026-08-17; the chart's own question and footnote left
                # with it — see `seasonality_chart`.
                section_id="section-seasonality",
                title="Kuuline liitumine",
                charts=(season,),
            )
        )

    periods = get_new_member_periods()
    period_chart = new_member_periods_chart(periods)
    if period_chart is not None:
        sections.append(
            AnalyticsSection(
                section_id="section-periods",
                title="Ajaloolised perioodid",
                description=(
                    "Mitut kuud korraga hõlmavad liitumisnäitajad, nagu juhatus need "
                    "esitas. Neid ei jagata kuudeks ega liideta kuude reale."
                ),
                charts=(period_chart,),
            )
        )

    # Who arrived and who left, as a year-to-date breakdown — merged in from
    # `Liikumine ja põhjused` on 2026-08-17.
    movement_charts = []
    if latest is not None:
        movements = get_membership_size_movement(latest.observation.pk)
        if movements:
            movement_charts.append(
                size_movement_chart(movements, observation_date=latest.observation_date)
            )
        reasons = get_removal_reasons(latest.observation.pk)
        if reasons:
            movement_charts.append(
                removal_reasons_chart(reasons, observation_date=latest.observation_date)
            )
    sections.append(
        AnalyticsSection(
            section_id="section-movement",
            title="Liikmete liikumine",
            description="",
            charts=tuple(movement_charts),
        )
    )

    if composition is not None:
        on = composition.snapshot_date

        # Which joining years make up today's membership — merged in from
        # `Koosseis` on 2026-08-17, beside the movement it complements.
        cohorts = join_cohort_chart(composition.dimension(Dimension.JOIN_COHORT), snapshot_date=on)
        if cohorts is not None:
            sections.append(
                AnalyticsSection(
                    section_id="section-cohorts",
                    title="Tänased liikmed liitumisaasta järgi",
                    show_title=False,
                    charts=(cohorts,),
                )
            )

        # Which kinds of organisation are over-represented among the members
        # who joined most recently. Sector carries the most signal and is the
        # only dimension drawn here — three growth-index charts would ask the
        # reader to hold three baselines at once.
        rows, suppressed = get_composition_growth(composition, Dimension.SECTOR)
        growth = growth_index_chart(
            rows,
            suppressed,
            dimension_label="Tegevusalad",
            snapshot_date=on,
            recent_total=composition.recent_joiner_count,
        )
        if growth is not None:
            sections.append(
                AnalyticsSection(
                    section_id="section-growth-index",
                    title="Aastaga liitunute valdkonnad",
                    charts=(growth,),
                )
            )

    return sections


def _monthly_years(latest_date) -> list[int]:
    if latest_date is None:
        return []
    newest = latest_date.year
    return list(range(newest - DEFAULT_MONTHLY_HISTORY_YEARS, newest + 1))


def _one_of(raw: str | None, allowed, fallback: str) -> str:
    """A query value, or the fallback. An unknown value is never an error.

    A stale bookmark or a typed URL renders the page in its default state rather
    than raising, which is the same rule `ranges.py` applies to a malformed date.
    """
    return raw if raw in allowed else fallback


#: Which board decision the decision section describes.
PARAM_DECISION = "otsus"

#: The members list's own controls. Estonian keys like every other control on
#: this page, and distinct from `otsing` on Nähtavus only in that they govern a
#: different page — the word means the same thing in both.
PARAM_SEARCH = "otsing"
PARAM_STATUS = "staatus"
PARAM_PAGE = "leht"


def _page_number(raw: str | None) -> int:
    """A page number, or the first page. An unreadable value is never an error.

    Same rule as `_one_of` and `resolve_window`: a stale bookmark, a typo or a
    truncated URL renders the list from the top rather than raising.
    """
    try:
        return max(1, int(str(raw)))
    except TypeError, ValueError:
        return 1


#: How many decisions the control offers. The list is a row of links, not a
#: dropdown, so it has to stay readable; older decisions remain reachable by
#: narrowing the date window, which already filters the batches.
MAX_DECISIONS_OFFERED = 8


def _decision_key(batch) -> str:
    """A decision's identity for the control: its as-of date.

    Termination and suspension are two batches of the same decision, so keying
    on the date brings both under one choice rather than offering the reader two
    halves of the same board meeting.
    """
    return batch.as_of_date.isoformat() if batch.as_of_date else ""


def _decisions_offered(batches) -> list[tuple[str, str]]:
    """The decisions the control lists, newest first, de-duplicated by date."""
    seen: dict[str, str] = {}
    for batch in batches:
        key = _decision_key(batch)
        if not key or key in seen:
            continue
        label = batch.as_of_date.strftime("%d.%m.%Y")
        if batch.reference:
            label = f"{label} · {batch.reference}"
        seen[key] = label
        if len(seen) >= MAX_DECISIONS_OFFERED:
            break
    return list(seen.items())


def _toggle(
    state: dict,
    param: str,
    value: str,
    label: str,
    active: str,
    anchor: str = "section-recruitment",
) -> ToggleOption:
    """One control option, as a link that keeps every other choice intact.

    The link is assembled from `state`, which holds only values this view has
    already resolved: a window clamped to the observation span, a focus that
    survived `resolve_focus` and two choices that survived `_one_of`. Copying the
    incoming query string instead would reflect whatever arrived — a stale key, a
    typo, a hostile string — back into an href on the page, and a control that
    carries someone else's junk forward is a control that eventually carries it
    somewhere worse.

    Built server-side so switching a view is an ordinary GET the browser can
    bookmark, share and go back through — no client state, no SPA.
    """
    params = dict(state)
    params[param] = value
    # A control changes what is drawn below it, so the link returns the reader
    # to the section rather than to the top of the page.
    return ToggleOption(
        label=label,
        query=f"?{urlencode(params)}#{anchor}",
        is_active=active == value,
    )
