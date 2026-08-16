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

## One page, four focuses

The page answers four different management questions and `fookus` names which
one is drawn. It is an ordinary GET parameter, every control is a link, and an
unknown value renders the overview rather than raising — so the page survives a
stale bookmark, a typed URL and the back button. There is no client-side state
and no SPA. `Liikmemaks` was a fifth focus holding one chart; the chart draws
on the overview since 2026-08-16 and the retired key resolves there.

The overview is the default and is built to be read without interaction: three
headline figures, the membership trend with the fee history under one window,
what changed, and the current year's movement. The other three focuses are
where the analysis lives. Anything a control governs sits inside the section
that carries the control, which is the rule the range control already followed.
"""

from datetime import timedelta
from urllib.parse import urlencode

from django.shortcuts import render
from django.views.decorators.http import require_GET

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
    decision_batch_reasons_chart,
    decision_batch_sizes_chart,
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
    FOCUS_COMPOSITION,
    FOCUS_GROWTH,
    FOCUS_MOVEMENT,
    FOCUS_OVERVIEW,
    PARAM_FOCUS,
    focus_links,
    resolve_focus,
)
from .intelligence import (
    KPI_BASELINE_LOOKBACK_DAYS,
    build_composition_preview,
    build_headlines,
    build_insights,
    build_movement_summary,
    build_quality_badge,
    build_source_stamps,
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
from .reconciliation import reconcile_history
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

    # A longer, still bounded run for the stock-and-flow check. Seven years
    # is enough to fill the six periods the diagnostic lists and is a few
    # dozen rows, not a scan that grows every month.
    if latest is not None:
        reconciliation_history = get_internal_membership_observations(
            date_from=latest.observation_date.replace(
                year=latest.observation_date.year - RECONCILIATION_LOOKBACK_YEARS
            ),
            date_to=latest.observation_date,
        )
    else:
        reconciliation_history = ()

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
    available = {FOCUS_OVERVIEW}
    if trend.has_data or any(monthly.values()):
        available.add(FOCUS_GROWTH)
    if latest is not None or batches:
        available.add(FOCUS_MOVEMENT)
    if composition is not None:
        available.add(FOCUS_COMPOSITION)

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
            # The internal board-report history, clearly separate.
            "internal_latest": latest,
            "internal_quality": quality,
            "internal_trend": trend,
            "focus": focus,
            "focus_links": focus_links(focus, carried=carried, available=frozenset(available)),
            "is_overview": focus == FOCUS_OVERVIEW,
            # The four questions the page answers before it is scrolled, plus
            # the strip and the current-year block that sit under them.
            "headlines": build_headlines(latest, baseline_history)
            if focus == FOCUS_OVERVIEW
            else (),
            "insights": build_insights(latest, baseline_history, monthly)
            if focus == FOCUS_OVERVIEW
            else (),
            "movement_summary": build_movement_summary(latest) if focus == FOCUS_OVERVIEW else None,
            "composition_preview": build_composition_preview(
                composition,
                link_query=f"?{urlencode({**carried, PARAM_FOCUS: FOCUS_COMPOSITION})}",
            )
            if focus == FOCUS_OVERVIEW
            else None,
            # Read only by the composition focus, to state the date the
            # whole view describes before any chart is reached.
            "composition_snapshot": composition if focus == FOCUS_COMPOSITION else None,
            "quality_badge": build_quality_badge(quality),
            # Evidence for the methodology disclosure, never a headline: a
            # residual is a question about four reported figures, not a
            # correction to any of them.
            "reconciliations": reconcile_history(reconciliation_history),
            "source_stamps": build_source_stamps(
                latest=latest,
                quality=quality,
                composition_date=composition.snapshot_date if composition else None,
            ),
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
        FOCUS_MOVEMENT: _movement_sections,
        FOCUS_COMPOSITION: _composition_sections,
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


def _overview_sections(*, trend, fee_rows, presets, has_range_choice, **_ignored):
    """The membership trend and, under the same window, the fee history.

    `Liikmemaks` was a whole focus holding the one fee chart; since 2026-08-16
    it draws here, in the section the range control already governs — the two
    series answer over the same window, so one control serves both and the
    reader stops switching views to hold the pair in mind.
    """
    charts = [total_and_paid_chart(trend)] if trend.has_data else []
    if fee_rows:
        charts.append(fee_collection_chart(fee_rows))
    return [
        AnalyticsSection(
            section_id="section-trend",
            title="Liikmeskonna areng",
            show_title=False,
            description="",
            charts=tuple(charts),
            presets=presets,
            show_custom_range=has_range_choice,
        )
    ]


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
    **_ignored,
):
    """Recruitment, the stock it feeds, and the seasonal shape of both.

    Four different quantities live under "growth" and the section keeps them
    apart: the membership stock is a level, recruitment is a flow, a calendar
    month has a seasonal position, and a period the board reported without
    splitting into months is none of the three. True retention is a fifth thing
    and is not here at all — no source in this application records departures
    per member, so a retention figure would have to be invented.
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
            description="Jooksva aasta uued liikmed ja ajalooline võrdlus.",
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
                section_id="section-seasonality",
                title="Kuude hooajalisus",
                description=(
                    "Kas see kuu on tavapärasest tugevam või nõrgem? Võrdlusaluseks "
                    "on sama kalendrikuu varasematel täielikult raporteeritud aastatel."
                ),
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
    return sections


def _movement_sections(*, latest, batches, decisions, chosen, control_state, **_ignored):
    """Who arrived, who left, and what a single board decision contained.

    The two are deliberately separate sections. A year-to-date breakdown and one
    decision's own list answer different questions, and drawing them together
    would invite exactly the addition this dataset exists to prevent.
    """
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

    decision_charts = []
    for batch in [b for b in batches if _decision_key(b) == chosen]:
        if batch.reasons:
            decision_charts.append(decision_batch_reasons_chart(batch))
        if batch.sizes:
            decision_charts.append(decision_batch_sizes_chart(batch))

    return [
        AnalyticsSection(
            section_id="section-movement",
            title="Liikmete liikumine",
            description="",
            charts=tuple(movement_charts),
        ),
        AnalyticsSection(
            section_id="section-decisions",
            title="Juhatuse otsused",
            description=(
                "Ühe juhatuse otsuse enda nimekiri. Ei ole aasta algusest "
                "kogunenud arv ega ole sellega liidetav."
            ),
            charts=tuple(decision_charts),
            toggles=(
                (
                    Toggle(
                        label="Otsus",
                        options=tuple(
                            _toggle(
                                control_state,
                                PARAM_DECISION,
                                key,
                                label,
                                chosen,
                                anchor="section-decisions",
                            )
                            for key, label in decisions
                        ),
                    ),
                )
                # One decision is not a choice, and a control that cannot change
                # anything reads as a control that is broken.
                if len(decisions) > 1
                else ()
            ),
        ),
    ]


def _composition_sections(*, composition=None, **_ignored):
    """What kinds of organisations the membership is made of.

    Everything here describes **one dated roster export** and says so in every
    heading, because a composition total is not a membership count: the board
    report and the public directory both measure that differently, and putting
    a third number beside them without its date would invite a comparison none
    of the three supports.

    The focus is not offered until a roster has been imported, so there is no
    empty state to draw. `composition_chart` still returns `None` for a
    dimension with nothing in it, and a section with no chart renders nothing.
    """
    if composition is None:
        return []

    on = composition.snapshot_date
    sections: list[AnalyticsSection] = []

    # Ordinal dimensions keep their scale order; nominal ones are ranked, because
    # for a county or a sector the ranking is most of the answer.
    structure = [
        (
            Dimension.EMPLOYEE_SIZE,
            "section-size",
            "Ettevõtte suurus",
            "Kui suured on koja liikmed?",
            False,
        ),
        (
            Dimension.REGION,
            "section-region",
            "Piirkonnad",
            "Kus koja liikmed asuvad?",
            True,
        ),
        (
            Dimension.SECTOR,
            "section-sector",
            "Tegevusalad",
            "Millistel tegevusaladel koja liikmed tegutsevad?",
            True,
        ),
        (
            Dimension.TENURE_BAND,
            "section-tenure",
            "Liikmestaaž",
            "Kui kaua on tänased liikmed kojas olnud?",
            False,
        ),
    ]

    # One section, two columns from `xl`. Four stacked full-width sections of
    # the same categorical shape were twice the scroll to say four things, and
    # each chart already carries its own title and question.
    structure_charts = []
    for dimension, _section_id, title, question, ranked in structure:
        chart = composition_chart(
            composition.dimension(dimension),
            payload_id=f"membership-composition-{dimension.replace('_', '-')}",
            title=title,
            question=question,
            snapshot_date=on,
            ranked=ranked,
            footnotes=(
                (
                    "Tegevusala on tuletatud EMTAK/NACE koodist jaotise "
                    f"tasemel (klassifikaatori versioon {composition.sector_mapping_version}).",
                )
                if dimension == Dimension.SECTOR
                else ()
            ),
        )
        if chart is not None:
            structure_charts.append(chart)
    if structure_charts:
        sections.append(
            AnalyticsSection(
                section_id="section-structure",
                title="Koosseisu jaotused",
                show_title=False,
                charts=tuple(structure_charts),
                grid=True,
            )
        )

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

    # Which kinds of organisation are over-represented among the members who
    # joined most recently. Sector carries the most signal and is the only
    # dimension drawn here — three growth-index charts would ask the reader to
    # hold three baselines at once.
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
                title="Kust kasv tuleb",
                description=(
                    "Viimase 12 kuu jooksul liitunud tänaste liikmete koosseis "
                    "võrreldes kogu liikmeskonnaga."
                ),
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


#: How far back the stock-and-flow diagnostic looks for periods to check.
RECONCILIATION_LOOKBACK_YEARS = 7

#: Which board decision the decision section describes.
PARAM_DECISION = "otsus"

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
