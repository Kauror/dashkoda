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
"""

from datetime import date
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
    fee_collection_chart,
    monthly_new_members_chart,
    removal_reasons_chart,
    size_movement_chart,
    total_and_paid_chart,
)
from .internal_selectors import (
    DEFAULT_MONTHLY_HISTORY_YEARS,
    get_fee_collection_trend,
    get_internal_membership_latest,
    get_internal_membership_quality_summary,
    get_internal_membership_trend,
    get_membership_size_movement,
    get_monthly_new_members,
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
from .selectors import get_membership_summary


@require_GET
def membership_overview(request):
    summary = get_membership_summary()
    latest = get_internal_membership_latest()
    quality = get_internal_membership_quality_summary()

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

    presets = range_presets(
        earliest=quality.earliest_observation_date,
        latest=quality.latest_observation_date,
        active=window,
    )
    has_range_choice = offers_choice(
        earliest=quality.earliest_observation_date,
        latest=quality.latest_observation_date,
    )

    growth_charts = []
    fee_charts = []
    if trend.has_data:
        growth_charts.append(total_and_paid_chart(trend))
        fee_rows = get_fee_collection_trend(date_from=trend.date_from, date_to=trend.date_to)
        if fee_rows:
            fee_charts.append(fee_collection_chart(fee_rows))

    monthly_years = _monthly_years(quality.latest_observation_date)
    monthly = get_monthly_new_members(monthly_years) if monthly_years else {}
    recruitment_charts = []
    view = _one_of(request.GET.get(PARAM_VIEW), (VIEW_MONTHLY, VIEW_CUMULATIVE), VIEW_MONTHLY)
    supported = available_benchmarks(monthly)
    benchmark = _one_of(
        request.GET.get(PARAM_BENCHMARK),
        supported,
        supported[0] if supported else BENCHMARK_PREVIOUS,
    )
    if any(monthly.values()):
        recruitment_charts.append(
            monthly_new_members_chart(monthly, view=view, benchmark=benchmark)
        )

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

    # Only resolved values reach a link: the window is clamped to the history
    # and both choices have already been validated.
    control_state: dict[str, str] = {PARAM_VIEW: view, PARAM_BENCHMARK: benchmark}
    if window is not None:
        control_state[PARAM_FROM] = window.start.isoformat()
        control_state[PARAM_TO] = window.end.isoformat()

    sections = [
        AnalyticsSection(
            section_id="section-growth",
            # Heading and description both struck out on the print-out. The
            # title is kept as the landmark's accessible name; each chart in
            # the section carries its own visible title.
            title="Liikmeskonna areng",
            show_title=False,
            description="",
            charts=tuple(growth_charts),
            presets=presets,
            show_custom_range=has_range_choice,
        ),
        AnalyticsSection(
            section_id="section-fees",
            title="Liikmemaksu laekumine",
            description="",
            charts=tuple(fee_charts),
        ),
        AnalyticsSection(
            section_id="section-recruitment",
            title="Uute liikmete dünaamika",
            description="Jooksva aasta uued liikmed ja ajalooline võrdlus.",
            charts=tuple(recruitment_charts),
            toggles=(
                Toggle(
                    label="Vaade",
                    options=(
                        _toggle(control_state, PARAM_VIEW, VIEW_MONTHLY, "Kuu kaupa", view),
                        _toggle(control_state, PARAM_VIEW, VIEW_CUMULATIVE, "Kumulatiivselt", view),
                    ),
                ),
                Toggle(
                    label="Võrdlus",
                    options=tuple(
                        _toggle(control_state, PARAM_BENCHMARK, key, label, benchmark)
                        for key, label in (
                            (BENCHMARK_PREVIOUS, "Eelmine aasta"),
                            (BENCHMARK_AVERAGE, "3 aasta keskmine"),
                        )
                        if key in supported
                    ),
                ),
            ),
        ),
        AnalyticsSection(
            section_id="section-movement",
            title="Liikmete liikumine",
            description="",
            charts=tuple(movement_charts),
        ),
    ]

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
            # Four analytical sections, each carrying only the controls that
            # govern it. A section with nothing to draw renders nothing.
            "sections": [section for section in sections if section.has_charts],
            "trend_window": window,
            "trend_earliest": quality.earliest_observation_date,
            "trend_latest": quality.latest_observation_date,
            # A history of one date has one drawable window: two fields that
            # cannot change anything render no control rather than a broken one.
            "has_range_choice": offers_choice(
                earliest=quality.earliest_observation_date,
                latest=quality.latest_observation_date,
            ),
        },
    )


def _monthly_years(latest_date: date | None) -> list[int]:
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


def _toggle(state: dict, param: str, value: str, label: str, active: str) -> ToggleOption:
    """One control option, as a link that keeps every other choice intact.

    The link is assembled from `state`, which holds only values this view has
    already resolved: a window clamped to the observation span and two choices
    that survived `_one_of`. Copying the incoming query string instead would
    reflect whatever arrived — a stale key, a typo, a hostile string — back into
    an href on the page, and a control that carries someone else's junk forward
    is a control that eventually carries it somewhere worse.

    Built server-side so switching a view is an ordinary GET the browser can
    bookmark, share and go back through — no client state, no SPA.
    """
    params = dict(state)
    params[param] = value
    # A control changes what is drawn below it, so the link returns the reader
    # to the section rather than to the top of the page.
    return ToggleOption(
        label=label,
        query=f"?{urlencode(params)}#section-recruitment",
        is_active=active == value,
    )
