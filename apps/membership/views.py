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

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.navigation import NAVIGATION

from .charts import (
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

    charts = []
    if trend.has_data:
        charts.append(total_and_paid_chart(trend))

        fee_rows = get_fee_collection_trend(date_from=trend.date_from, date_to=trend.date_to)
        if fee_rows:
            charts.append(fee_collection_chart(fee_rows))

    monthly_years = _monthly_years(quality.latest_observation_date)
    monthly = get_monthly_new_members(monthly_years) if monthly_years else {}
    if any(monthly.values()):
        charts.append(monthly_new_members_chart(monthly))

    if latest is not None:
        movements = get_membership_size_movement(latest.observation.pk)
        if movements:
            charts.append(size_movement_chart(movements, observation_date=latest.observation_date))
        reasons = get_removal_reasons(latest.observation.pk)
        if reasons:
            charts.append(removal_reasons_chart(reasons, observation_date=latest.observation_date))

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
            # Charts are only built when they have something to draw, so the
            # template renders no empty figures.
            "charts": charts,
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
