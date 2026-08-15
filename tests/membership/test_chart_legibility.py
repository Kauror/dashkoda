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
import re
from decimal import Decimal
from pathlib import Path

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


def test_every_labelled_series_can_drop_a_label_that_would_collide(every_chart):
    """`hideOverlap` is a series option, and every series that draws a label
    needs it — not just the bar charts where the collision was first noticed.

    Three of the fee chart's four years finish within a few points of the
    budget, so their year labels land on each other at the right edge; the
    trend's two converge whenever the paid count approaches the total.
    """
    for name in ("trend", "fees", "movement", "reasons"):
        option = every_chart[name].option
        assert "labelLayout" not in option, f"{name} sets it where ECharts ignores it"
        for series in option["series"]:
            assert series["labelLayout"]["hideOverlap"] is True, name


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


# ---------------------------------------------------------------------------
# The series palette
# ---------------------------------------------------------------------------
#
# Same class of defect as the two above, and found the same way: the values were
# right and could not be read. Two of the five series colours were both blue —
# `--color-brand` against `--color-info`, which measure ΔE 7.8 apart for a reader
# with full colour vision against a floor of 15, and 2.5 under tritanopia — and
# three of the five were the reserved status colours.
#
# Neither is visible to a test that inspects values, and neither is visible to a
# test that renders a chart and checks it drew something. So the palette is
# asserted as a contract, in the two files that have to agree about it.

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

#: The categorical order, slot by slot. Slot 1 is the Chamber blue; 2–6 are the
#: series tokens. Validated as a set against the dark card surface: every
#: adjacent pair clears the colour-blind and normal-vision floors.
SERIES_TOKENS = (
    "--color-brand",
    "--color-series-2",
    "--color-series-3",
    "--color-series-4",
    "--color-series-5",
    "--color-series-6",
)

#: Reserved for saying a thing is good or wrong. A chart that spends one on
#: "the third category" leaves nothing to say it with, and teaches a reader that
#: amber means attention everywhere except here.
STATUS_TOKENS = ("--color-success", "--color-warning", "--color-danger", "--color-info")


def test_every_series_token_is_defined() -> None:
    styles = (FRONTEND / "styles.css").read_text(encoding="utf-8")

    for token in SERIES_TOKENS:
        assert f"{token}:" in styles, f"{token} is used by the chart theme but not defined"


def test_the_chart_theme_draws_its_series_in_the_declared_order() -> None:
    """The order is the accessibility mechanism, so it is pinned, not just the set."""
    charts_js = (FRONTEND / "charts.js").read_text(encoding="utf-8")
    palette = charts_js.split("color: [", 1)[1].split("]", 1)[0]

    found = re.findall(r'token\("(--color-[a-z0-9-]+)"', palette)

    assert found == list(SERIES_TOKENS), f"series order changed: {found}"


def test_no_status_colour_is_used_as_a_series_colour() -> None:
    charts_js = (FRONTEND / "charts.js").read_text(encoding="utf-8")
    palette = charts_js.split("color: [", 1)[1].split("]", 1)[0]

    for token in STATUS_TOKENS:
        assert token not in palette, f"{token} is a status colour and may not be a series"


def test_the_theme_names_its_own_axis_colours() -> None:
    """ECharts' defaults are written for a light background.

    Its `axisLabel` default overrides the theme's `textStyle`, which put the
    labels at 3.47:1 on this surface while the gridline default sat at 13.67:1 —
    the text you must read fainter than the grid behind it. Both are named by
    the theme now, so neither can fall back.
    """
    charts_js = (FRONTEND / "charts.js").read_text(encoding="utf-8")

    axis_block = charts_js.split("const AXIS_BASE", 1)[1].split("});", 1)[0]

    assert "--color-text-secondary" in axis_block, (
        "axis labels must not inherit the ECharts default"
    )
    assert "--color-border-strong" in axis_block, "gridlines must be recessive"


def test_the_theme_is_registered_rather_than_spread_into_setoption() -> None:
    """`categoryAxis` and its siblings are theme keys, not option keys.

    Spread into `setOption` they are inert — the axis styling would apply to
    nothing and raise nothing. That silence is the whole hazard, so the wiring
    is asserted rather than assumed.
    """
    charts_js = (FRONTEND / "charts.js").read_text(encoding="utf-8")

    assert "registerTheme(THEME_NAME, chartTheme())" in charts_js
    assert "echarts.init(canvas, THEME_NAME" in charts_js
    setoption = charts_js.split("instance.setOption({", 1)[1].split("});", 1)[0]
    assert "chartTheme()" not in setoption, "the theme belongs to init, not to setOption"


def test_the_theme_names_its_own_legend_colours() -> None:
    """`legend.textStyle` outranks the theme's `textStyle`, exactly as `axisLabel` does.

    Unnamed it drew the legend labels at roughly 2.2:1 while the axis labels
    beside them sat at 6.98:1 — and on a stacked chart the legend is the only
    thing that says which colour is which series.
    """
    charts_js = (FRONTEND / "charts.js").read_text(encoding="utf-8")

    legend_block = charts_js.split("const LEGEND_BASE", 1)[1].split("});", 1)[0]

    assert "--color-text-secondary" in legend_block, (
        "legend labels must not inherit the ECharts default"
    )
    assert "--color-text-muted" in legend_block, (
        "a toggled-off swatch must read as off, not as emphasis"
    )
    assert "legend: LEGEND_BASE()" in charts_js, "the theme must carry the legend"


def test_the_tooltip_surface_belongs_to_the_theme_not_to_a_branch() -> None:
    """The regression this replaces: a dark tooltip only where a readout existed.

    The surface used to be set inside `if (dashkoda.tooltip)`, so the ten
    builders that say no more than `{"trigger": "axis"}` kept ECharts' near-white
    panel and rendered as a white card on a dark page. A surface cannot be
    conditional on whether a chart also wanted a custom formatter.
    """
    charts_js = (FRONTEND / "charts.js").read_text(encoding="utf-8")

    assert "tooltip: TOOLTIP_BASE()" in charts_js, "the theme must carry the tooltip surface"

    surface_block = charts_js.split("const TOOLTIP_BASE", 1)[1].split("});", 1)[0]
    assert "--color-elevated" in surface_block
    assert "--color-text" in surface_block

    branch = charts_js.split("if (dashkoda.tooltip) {", 1)[1].split("\n  }", 1)[0]
    for key in ("backgroundColor", "borderColor", "textStyle"):
        assert key not in branch, (
            f"{key} is a surface and belongs to the theme, so charts without readouts get it too"
        )


def test_charts_exist_that_would_regress_if_the_surface_were_conditional() -> None:
    """Guards the guard.

    The test above is only worth its runtime while builders that set a tooltip
    without server readouts still exist. If they ever stop existing the branch
    check is vacuous and should be reconsidered rather than quietly kept.
    """
    bare = [
        path
        for path in sorted(Path("apps").rglob("*charts*.py"))
        if '"tooltip"' in (source := path.read_text(encoding="utf-8"))
        and '"dashkoda"' not in source.split('"tooltip"', 1)[1]
    ]

    assert bare, "no builder sets a tooltip without readouts — recheck the branch assertion"


def test_the_default_readout_is_only_applied_where_a_tooltip_was_asked_for() -> None:
    """A fallback must not hand a tooltip to a chart that wanted none.

    Charts that omit `tooltip` do so deliberately — the bar states its own count
    and the hover has nothing to add. Applying the fallback unconditionally
    would give every one of them a hover panel nobody asked for, which no
    browser test would fail on because appearing is not an error.
    """
    charts_js = (FRONTEND / "charts.js").read_text(encoding="utf-8")

    assert "} else if (option.tooltip) {" in charts_js, (
        "the fallback must be conditional on the chart having requested a tooltip"
    )


def test_the_default_readout_does_not_round_the_value_it_states() -> None:
    """An axis tick is a scale marker; a readout is the value itself.

    `groupThousands` rounds, which is right for a tick and wrong for a readout —
    a median response window of 14.5 days stated as `15` is a different number.
    """
    charts_js = (FRONTEND / "charts.js").read_text(encoding="utf-8")

    readout = charts_js.split("function readoutNumber", 1)[1].split("\n}", 1)[0]

    assert "Math.round" not in readout, "a readout states the value, it does not round it"
    assert "groupThousands" not in readout, "groupThousands rounds; readouts must not"
