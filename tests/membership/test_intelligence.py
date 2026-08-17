"""What the membership overview says, and what it refuses to say.

These are the rules the redesign rests on: four headline answers rather than
nine equal figures, a comparison that names its baseline or is absent, a
difference that is never called a net change, and a missing value that never
becomes a zero.

Every observation is an unsaved model instance, so the module runs without
PostgreSQL.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from apps.core.formatting import GROUP_SEPARATOR, MINUS_SIGN
from apps.membership.intelligence import (
    MAX_INSIGHTS,
    build_headlines,
    build_insights,
    build_movement_summary,
    build_quality_badge,
    build_source_stamps,
)
from apps.membership.internal_selectors import (
    InternalQualitySummary,
    MonthlyValue,
    ObservationPoint,
)
from apps.membership.models import InternalMembershipObservation, MonthlyValueStatus

LATEST = dt.date(2026, 7, 31)
YEAR_AGO = dt.date(2025, 7, 31)


def point(
    day: dt.date,
    *,
    total: int | None = None,
    paid: int | None = None,
    new_ytd: int | None = None,
    removed_ytd: int | None = None,
    suspended: int | None = None,
    received: str | None = None,
    budget: str | None = None,
    reported_pct: str | None = None,
    withheld: frozenset[str] = frozenset(),
) -> ObservationPoint:
    observation = InternalMembershipObservation(
        observation_date=day,
        total_members=total,
        paid_members=paid,
        new_members_ytd=new_ytd,
        removed_members_ytd=removed_ytd,
        suspended_members=suspended,
        membership_fees_received_eur=Decimal(received) if received else None,
        membership_fee_budget_eur=Decimal(budget) if budget else None,
        membership_fee_collection_pct_reported=(Decimal(reported_pct) if reported_pct else None),
    )
    return ObservationPoint(observation=observation, withheld=withheld)


def months(*values: int | None, year: int = 2026) -> tuple[MonthlyValue, ...]:
    return tuple(
        MonthlyValue(
            calendar_year=year,
            calendar_month=index + 1,
            new_members=value,
            value_status=(
                MonthlyValueStatus.CONFLICT if value is None else MonthlyValueStatus.VERIFIED
            ),
        )
        for index, value in enumerate(values)
        if value is not None
    )


def quality(**overrides) -> InternalQualitySummary:
    base = {
        "observation_count": 10,
        "preferred_count": 10,
        "conflicted_metric_count": 0,
        "review_required_count": 0,
        "unresolved_error_count": 0,
        "provisional_month_count": 0,
        "conflict_month_count": 0,
        "earliest_observation_date": dt.date(2014, 1, 31),
        "latest_observation_date": LATEST,
    }
    return InternalQualitySummary(**{**base, **overrides})


def by_key(headlines) -> dict:
    return {headline.key: headline for headline in headlines}


# ---------------------------------------------------------------------------
# Headlines
# ---------------------------------------------------------------------------


def test_the_page_leads_with_three_questions_not_nine_figures():
    """Three since 2026-08-16: `paid_share` folded into `members_and_paid`.

    The two were the same pair twice — the share's detail line was this card's
    value — so the strip was answering one question in two cells.
    """
    headlines = build_headlines(point(LATEST, total=3412, paid=3279), ())

    assert len(headlines) == 3
    assert [headline.key for headline in headlines] == [
        "total_members",
        "members_and_paid",
        "fee_collection",
    ]
    assert "paid_share" not in {headline.key for headline in headlines}


def test_no_headlines_at_all_without_an_observation():
    assert build_headlines(None, ()) == ()


def test_the_membership_total_is_compared_with_a_genuinely_comparable_year():
    latest = point(LATEST, total=3412)
    history = (point(YEAR_AGO, total=3547), latest)

    members = by_key(build_headlines(latest, history))["total_members"]

    assert members.value == f"3{GROUP_SEPARATOR}412"
    assert members.direction == "down"
    assert members.tone == "negative"
    assert "135" in members.change
    assert "2025" in members.comparison_label


def test_a_baseline_outside_the_tolerance_produces_no_comparison():
    """Not the nearest earlier report wearing a year-ago label."""
    latest = point(LATEST, total=3412)
    history = (point(dt.date(2025, 1, 31), total=3600), latest)

    members = by_key(build_headlines(latest, history))["total_members"]

    assert members.value == f"3{GROUP_SEPARATOR}412"
    assert members.change == ""
    assert members.note  # says why, rather than showing nothing


def test_a_missing_total_shows_no_value_and_never_a_zero():
    members = by_key(build_headlines(point(LATEST, total=None), ()))["total_members"]

    assert members.value == ""
    assert members.is_available is False
    assert "0" not in members.value


def test_a_withheld_total_is_treated_as_missing_rather_than_drawn():
    latest = point(
        LATEST, total=3412, paid=9999, withheld=frozenset({"total_members", "paid_members"})
    )

    headlines = by_key(build_headlines(latest, ()))

    assert headlines["total_members"].is_available is False
    assert headlines["members_and_paid"].is_available is False


def test_the_members_and_paid_card_states_the_gap_without_calling_it_movement():
    """The card that replaced `Liitumised ja väljaarvamised` on 2026-08-16.

    It puts the two counts side by side and names the gap between them. The gap
    is members who have not paid — nobody joined and nobody left — so the same
    vocabulary the old card was forbidden is forbidden here, for a different
    reason and just as firmly.
    """
    latest = point(LATEST, total=3429, paid=3426)

    card = by_key(build_headlines(latest, ()))["members_and_paid"]
    words = f"{card.label} {card.value} {card.detail}".lower()

    # `integer` groups thousands with a non-breaking space; compare digits only.
    digits = lambda s: "".join(ch for ch in s if ch.isdigit())  # noqa: E731
    assert digits(card.value) == "34293426"
    # The gap leads the detail; the share follows it, from 2026-08-16.
    assert card.detail.startswith("vahe")
    assert digits(card.detail.split("·")[0]) == "3"
    assert "tasunud" in card.detail
    for forbidden in ("neto", "netokasv", "liikmeskonna muutus", "liitus", "välja arvati"):
        assert forbidden not in words


def test_the_gap_is_withheld_when_only_one_side_was_reported():
    latest = point(LATEST, total=3429, paid=None)

    card = by_key(build_headlines(latest, ()))["members_and_paid"]

    assert "ei saa" in card.detail.lower()


def test_paid_share_moves_in_percentage_points_not_percent():
    """The share is now the folded card's detail, and the pp movement its change.

    Deliberately not the member total's year-on-year change, which is what this
    card carried until 2026-08-16: that figure is `Liikmeid kokku`'s comparison
    and printing it again a card later said the same thing twice. The pp
    movement is the only figure in this card that is nowhere else in the strip.
    """
    latest = point(LATEST, total=1000, paid=961)
    history = (point(YEAR_AGO, total=1000, paid=947), latest)

    card = by_key(build_headlines(latest, history))["members_and_paid"]

    assert "96,1" in card.detail
    assert "pp" in card.change
    assert "%" not in card.change
    assert "tasunute osakaal" in card.change_label


def test_fee_collection_draws_the_completion_the_amounts_imply():
    latest = point(LATEST, received="410000", budget="500000")

    fees = by_key(build_headlines(latest, ()))["fee_collection"]

    assert fees.value == "82,0%"
    assert "410" in fees.detail and "500" in fees.detail


def test_a_reported_percentage_that_disagrees_is_disclosed_not_swapped_in():
    """`quality.py` withholds it; the headline says so rather than resolving it."""
    latest = point(
        LATEST,
        received="410000",
        budget="500000",
        reported_pct="91.0",
        withheld=frozenset({"membership_fee_collection_pct_reported"}),
    )

    fees = by_key(build_headlines(latest, ()))["fee_collection"]

    assert fees.value == "82,0%"
    assert fees.note


def test_a_reported_percentage_is_used_and_labelled_when_amounts_cannot_produce_one():
    latest = point(LATEST, received=None, budget=None, reported_pct="82.0")

    fees = by_key(build_headlines(latest, ()))["fee_collection"]

    assert fees.value == "82,0%"
    assert "esitatud" in fees.note.lower()


def test_a_zero_budget_produces_no_percentage_rather_than_a_division():
    latest = point(LATEST, received="410000", budget="0")

    fees = by_key(build_headlines(latest, ()))["fee_collection"]

    assert fees.value == ""


def test_fee_collection_is_compared_with_a_genuinely_comparable_year():
    """Joined the card on 2026-08-17, the same baseline rule as the other two.

    Computed on both sides, same as `build_insights`' own `fee_collection_yoy`
    candidate: a reported percentage `quality.py` withheld cannot enter the
    comparison from either end.
    """
    latest = point(LATEST, received="410000", budget="500000")
    history = (point(YEAR_AGO, received="450000", budget="500000"), latest)

    fees = by_key(build_headlines(latest, history))["fee_collection"]

    assert fees.value == "82,0%"
    assert fees.direction == "down"
    assert fees.tone == "negative"
    assert "pp" in fees.change
    assert "2025" in fees.comparison_label


def test_fee_collection_has_no_comparison_without_a_comparable_year():
    latest = point(LATEST, received="410000", budget="500000")

    fees = by_key(build_headlines(latest, ()))["fee_collection"]

    assert fees.change == ""
    assert fees.comparison_label == ""


# ---------------------------------------------------------------------------
# Mis muutus?
# ---------------------------------------------------------------------------


def test_insights_are_absent_rather_than_zero_when_nothing_can_be_compared():
    assert build_insights(point(LATEST, total=3412), (), {}) == ()
    assert build_insights(None, (), {}) == ()


def test_the_strip_is_capped_and_each_signal_names_its_baseline():
    latest = point(
        LATEST, total=3412, paid=3279, removed_ytd=146, received="410000", budget="500000"
    )
    history = (
        point(YEAR_AGO, total=3547, paid=3358, removed_ytd=120, received="380000", budget="500000"),
        latest,
    )
    monthly = {2025: months(*[10] * 12, year=2025), 2026: months(*[9] * 7)}

    insights = build_insights(latest, history, monthly)

    assert 0 < len(insights) <= MAX_INSIGHTS
    for insight in insights:
        assert insight.detail, f"{insight.key} does not name its baseline"
        assert insight.change


def test_more_departures_is_an_increase_and_still_reads_as_bad_news():
    """Direction is the arithmetic; tone is the meaning. They differ here."""
    latest = point(LATEST, removed_ytd=146)
    history = (point(YEAR_AGO, removed_ytd=120), latest)

    removed = next(i for i in build_insights(latest, history, {}) if i.key == "removed_yoy")

    assert removed.direction == "up"
    assert removed.tone == "negative"


def test_recruitment_is_compared_only_against_the_same_elapsed_months():
    """July against a full twelve months is a collapse that never happened."""
    latest = point(LATEST, total=3412)
    monthly = {2025: months(*[10] * 12, year=2025), 2026: months(*[9] * 7)}

    joined = next(
        i for i in build_insights(latest, (), monthly) if i.key == "joined_vs_previous_year"
    )

    # 63 this year against 70 for the same January–July stretch, not against 120.
    assert joined.value == "63"
    assert "jaanuar" in joined.detail
    assert f"{MINUS_SIGN}7" in joined.change


def test_recruitment_comparison_withdraws_when_a_month_is_unreported():
    latest = point(LATEST, total=3412)
    monthly = {2025: months(10, 10, None, 10, year=2025), 2026: months(9, 9, 9)}

    keys = {insight.key for insight in build_insights(latest, (), monthly)}

    assert "joined_vs_previous_year" not in keys


def test_the_insight_order_is_fixed_rather_than_ranked_by_magnitude():
    """A strip that reorders itself loses the reader's ability to look twice."""
    latest = point(LATEST, total=3412, paid=3279, removed_ytd=146)
    history = (point(YEAR_AGO, total=3411, paid=3100, removed_ytd=20), latest)

    keys = [insight.key for insight in build_insights(latest, history, {})]

    assert keys == sorted(keys, key=["members_yoy", "paid_share_yoy", "removed_yoy"].index)


# ---------------------------------------------------------------------------
# Sel aastal
# ---------------------------------------------------------------------------


def test_the_current_year_block_carries_the_suspended_count():
    """Moved out of the headline strip; it is a secondary status."""
    summary = build_movement_summary(point(LATEST, new_ytd=111, removed_ytd=146, suspended=71))

    assert summary.joined == "111"
    assert summary.removed == "146"
    assert summary.suspended == "71"
    assert summary.has_difference


def test_the_current_year_block_is_absent_when_nothing_was_reported():
    assert build_movement_summary(point(LATEST)) is None
    assert build_movement_summary(None) is None


def test_a_reported_zero_is_a_real_value_and_still_renders():
    summary = build_movement_summary(point(LATEST, new_ytd=0, removed_ytd=5))

    assert summary.joined == "0"
    assert summary.has_data


# ---------------------------------------------------------------------------
# Quality and provenance
# ---------------------------------------------------------------------------


def test_the_quality_badge_says_so_when_nothing_was_withheld():
    badge = build_quality_badge(quality())

    assert badge.needs_attention is False
    assert badge.label == "Andmed korras"


def test_the_quality_badge_counts_what_a_person_could_act_on():
    badge = build_quality_badge(quality(conflicted_metric_count=1, review_required_count=1))

    assert badge.needs_attention is True
    assert "2" in badge.label


def test_each_source_states_its_own_as_of_date():
    """One freshness date for the whole page would be a claim it cannot make."""
    stamps = build_source_stamps(latest=point(LATEST, total=3412), quality=quality())
    labels = {stamp.label for stamp in stamps}

    assert "Sisemine aruanne" in labels
    assert "Ajalugu" in labels
    assert "Koosseis" not in labels  # not imported, so not claimed


def test_an_absent_source_is_not_listed():
    stamps = build_source_stamps(
        latest=None,
        quality=quality(earliest_observation_date=None, latest_observation_date=None),
    )

    assert stamps == ()
