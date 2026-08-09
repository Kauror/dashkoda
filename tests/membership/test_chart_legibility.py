"""What every chart must declare so the browser can draw it legibly.

These exist because the charts shipped correct and unreadable. The numbers were
right, the tooltips said the right things, and none of it could be read: the
tooltip surface was ECharts' near-white default under our light-on-dark text,
and the axis ticks were grouped the English way, so `3 820` was drawn as
`3,820` — which in Estonian reads as three point eight two.

Neither defect is visible to a test that inspects values. Both are contracts
between the payload and `frontend/src/charts.js`, so they are asserted here as
contracts.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.membership.charts import (
    fee_collection_chart,
    monthly_new_members_chart,
    removal_reasons_chart,
    size_movement_chart,
    total_and_paid_chart,
)
from apps.membership.internal_selectors import InternalTrend, MonthlyValue, ObservationPoint
from apps.membership.models import InternalMembershipObservation
from apps.membership.models.internal import MonthlyValueStatus
from apps.membership.quality import computed_collection_pct

WHEN = dt.date(2026, 7, 31)

# The finite set `charts.js` implements. A payload naming anything else would
# silently fall back to English grouping, which is the defect this prevents.
KNOWN_FORMATS = {"integer", "percent", "absolute"}


def observation(day: dt.date, total: int, paid: int) -> ObservationPoint:
    return ObservationPoint(
        observation=InternalMembershipObservation(
            observation_date=day, total_members=total, paid_members=paid
        ),
        withheld=frozenset(),
    )


@pytest.fixture
def every_chart() -> dict:
    trend = InternalTrend(
        points=(
            observation(dt.date(2025, 7, 31), 3547, 3282),
            observation(WHEN, 3412, 3208),
        ),
        date_from=dt.date(2025, 7, 31),
        date_to=WHEN,
        withheld_metric_points=0,
        review_required_points=0,
    )
    fee_rows = (
        {
            "observation_date": WHEN,
            "received": Decimal("742400"),
            "budget": Decimal("1020000"),
            "reported_pct": None,
            "computed_pct": computed_collection_pct(Decimal("742400"), Decimal("1020000")),
            "reported_withheld": False,
            "is_year_precision": False,
        },
    )
    monthly = {
        2026: (
            MonthlyValue(
                calendar_year=2026,
                calendar_month=1,
                new_members=24,
                value_status=MonthlyValueStatus.VERIFIED,
            ),
        )
    }
    movement = ({"band": "micro", "label": "1–9", "joined": 21, "removed": 38},)
    reasons = ({"key": "other", "label": "Muu", "count": 12, "share_pct": Decimal("100.0")},)

    return {
        "trend": total_and_paid_chart(trend),
        "fees": fee_collection_chart(fee_rows),
        "monthly": monthly_new_members_chart(monthly),
        "movement": size_movement_chart(movement, observation_date=WHEN),
        "reasons": removal_reasons_chart(reasons, observation_date=WHEN),
    }


def test_every_chart_names_an_axis_format(every_chart):
    """An axis with no declared format is drawn with English grouping."""
    for name, chart in every_chart.items():
        declared = chart.option["dashkoda"].get("axisFormat")
        assert declared, f"{name} declares no axis format"
        assert set(declared) <= {"x", "y"}, name


def test_no_chart_names_a_format_the_browser_does_not_implement(every_chart):
    for name, chart in every_chart.items():
        for axis, format_name in chart.option["dashkoda"]["axisFormat"].items():
            assert format_name in KNOWN_FORMATS, f"{name}.{axis} names {format_name!r}"


def test_the_value_axes_are_counts_and_the_budget_axis_is_a_percentage(every_chart):
    assert every_chart["trend"].option["dashkoda"]["axisFormat"]["y"] == "integer"
    assert every_chart["monthly"].option["dashkoda"]["axisFormat"]["y"] == "integer"
    assert every_chart["fees"].option["dashkoda"]["axisFormat"]["y"] == "percent"


def test_the_diverging_axis_states_magnitudes_rather_than_the_drawing(every_chart):
    """The bars extend leftwards because the removed count is drawn negative.
    An axis tick reading `−40` states that geometry as a business quantity —
    the same defect the tooltip already refuses to repeat."""
    assert every_chart["movement"].option["dashkoda"]["axisFormat"]["x"] == "absolute"


def test_a_chart_that_labels_its_last_point_leaves_room_for_the_label(every_chart):
    """Clipped mid-word, a direct label names nothing: "Liit" is not a series."""
    for name in ("trend", "fees"):
        option = every_chart[name].option
        labelled = any(series.get("endLabel", {}).get("show") for series in option["series"])
        assert labelled, f"{name} was expected to label its last point"
        assert option["grid"]["right"] > 24, f"{name} has no room for its end label"


def test_a_chart_without_end_labels_keeps_the_ordinary_margin(every_chart):
    """Room taken from the plot is only worth it where something is drawn in it."""
    assert every_chart["monthly"].option["grid"]["right"] == 24


def test_the_end_label_names_the_series_and_states_its_latest_value(every_chart):
    """The two things a reader wants at the end of a line: which line, and where
    it ended up. The full series name would cost a sixth of the plot width."""
    formatters = [
        series["endLabel"]["formatter"] for series in every_chart["trend"].option["series"]
    ]

    assert formatters[0].startswith("Kokku ")
    assert formatters[1].startswith("Tasunud ")
    assert "3" in formatters[0], "the latest figure is part of the label"
    # Not the ECharts series-name placeholder, which drew the long name.
    assert "{a}" not in formatters[0]


def test_an_empty_series_still_produces_a_label_that_names_something(every_chart):
    """A chart with no points must not label a line `Kokku None`."""
    empty = total_and_paid_chart(
        InternalTrend(
            points=(),
            date_from=None,
            date_to=None,
            withheld_metric_points=0,
            review_required_points=0,
        )
    )

    for series in empty.option["series"]:
        assert series["endLabel"]["formatter"] in ("Kokku", "Tasunud")
