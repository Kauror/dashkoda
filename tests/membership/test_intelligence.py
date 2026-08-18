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

from apps.core.formatting import GROUP_SEPARATOR
from apps.membership.intelligence import (
    build_headlines,
    build_movement_summary,
    build_quality_badge,
    build_source_stamps,
    composition_subtitles,
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


def test_the_paid_card_leads_with_the_paid_count_and_its_share():
    """The card that replaced `Liitumised ja väljaarvamised` on 2026-08-16.

    It led with both totals until 2026-08-18, and the first of them was the
    card to its left printed a second time. What is unique to this card is the
    paid side, so that is what it leads with — with the share beside it,
    because a paid count without its denominator is a number nobody can size.

    The gap is members who have **not** paid: nobody joined and nobody left, so
    the movement vocabulary the old card was forbidden is forbidden here too,
    for a different reason and just as firmly.
    """
    latest = point(LATEST, total=3429, paid=3426)

    card = by_key(build_headlines(latest, ()))["members_and_paid"]
    words = f"{card.label} {card.value} {card.detail}".lower()

    # `integer` groups thousands with a non-breaking space; compare digits only.
    digits = lambda s: "".join(ch for ch in s if ch.isdigit())  # noqa: E731
    assert card.label == "Tasunud liikmeid"
    assert digits(card.value.split("·")[0]) == "3426"
    assert "99,9%" in card.value
    # The member total belongs to the card before this one and appears once.
    assert "3429" not in digits(card.value)
    # The gap is the whole of the detail line; the share moved into the value.
    assert card.detail.startswith("vahe")
    assert digits(card.detail) == "3"
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

    assert "96,1" in card.value
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


def test_the_fee_card_carries_a_meter_and_no_other_card_does():
    """One figure on the strip is a completion against a stated target.

    A proportion bar under a member count would be a bar against no
    denominator, so `meter_pct` is set on this card alone.
    """
    headlines = by_key(
        build_headlines(
            point(LATEST, total=3412, paid=3279, received="410000", budget="500000"), ()
        )
    )

    assert headlines["fee_collection"].meter_pct == 82.0
    assert headlines["total_members"].meter_pct is None
    assert headlines["members_and_paid"].meter_pct is None


def test_a_year_that_collected_more_than_it_budgeted_fills_the_meter_once():
    """The bar stops at full; the amounts under it still state the overshoot.

    Clamping the drawing is not the same as clipping the figure, and the card
    prints both — `110,0%` beside a bar that cannot say more than "all of it".
    """
    headlines = by_key(build_headlines(point(LATEST, received="550000", budget="500000"), ()))

    assert headlines["fee_collection"].meter_pct == 100.0
    assert "110,0%" in headlines["fee_collection"].value


def test_no_meter_at_all_when_the_amounts_cannot_produce_a_completion():
    headlines = by_key(build_headlines(point(LATEST, reported_pct="88.0"), ()))

    assert headlines["fee_collection"].meter_pct is None


def test_a_zero_budget_produces_no_percentage_rather_than_a_division():
    latest = point(LATEST, received="410000", budget="0")

    fees = by_key(build_headlines(latest, ()))["fee_collection"]

    assert fees.value == ""


def test_fee_collection_is_compared_with_a_genuinely_comparable_year():
    """Joined the card on 2026-08-17, the same baseline rule as the other two.

    Computed on both sides: a reported percentage `quality.py` withheld cannot
    enter the comparison from either end.
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
# Sel aastal
# ---------------------------------------------------------------------------


def test_this_years_counts_each_carry_their_own_comparison():
    """Arrivals rising is good news; departures rising is not.

    Direction is the arithmetic sign and tone is what the movement means, and
    they disagree for departures on purpose. A cell that painted more
    exclusions green because the number rose would be worse than one with no
    colour at all.
    """
    latest = point(LATEST, new_ytd=128, removed_ytd=130)
    history = (point(YEAR_AGO, new_ytd=127, removed_ytd=186), latest)

    figures = {f.key: f for f in build_movement_summary(latest, history).figures}

    assert figures["joined"].direction == "up"
    assert figures["joined"].tone == "positive"
    assert figures["joined"].comparison_label == "vs 2025"
    assert figures["removed"].direction == "down"
    assert figures["removed"].tone == "positive"
    assert "%" in figures["joined"].change


def test_a_year_to_date_count_refuses_a_baseline_six_weeks_off_its_anniversary():
    """The trap this comparison exists to avoid.

    `new_members_ytd` counts from 1 January and grows all year. A baseline 45
    days short of the anniversary — which the membership *total* tolerates,
    correctly, because a stock is a stock in July as in August — is six weeks
    short of the year it stands in for, and every previous year would read low
    by construction. The count is printed with no percentage instead.
    """
    latest = point(LATEST, new_ytd=128)
    six_weeks_early = (point(dt.date(2025, 6, 18), new_ytd=95), latest)
    a_fortnight_early = (point(dt.date(2025, 7, 18), new_ytd=127), latest)

    refused = {f.key: f for f in build_movement_summary(latest, six_weeks_early).figures}
    accepted = {f.key: f for f in build_movement_summary(latest, a_fortnight_early).figures}

    assert refused["joined"].change == ""
    assert refused["joined"].value == "128"
    assert accepted["joined"].change != ""


def test_the_suspended_total_is_a_state_and_carries_no_comparison():
    latest = point(LATEST, suspended=45)
    history = (point(YEAR_AGO, suspended=30), latest)

    figures = {f.key: f for f in build_movement_summary(latest, history).figures}

    assert figures["suspended"].value == "45"
    assert figures["suspended"].change == ""


def test_the_current_year_block_carries_the_suspended_count():
    """A secondary status, and the third figure of the strip's fourth cell.

    It was moved out of the strip on 2026-08-16 as one of nine equal cards and
    came back on 2026-08-18 inside `Sel aastal` — beside the movement it
    describes rather than beside the membership total.
    """
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
# Kes on meie liikmed?
# ---------------------------------------------------------------------------


def test_a_composition_subtitle_never_names_the_unclassified_group():
    """ "Most members are unclassified" is a fact about the import.

    It is not a statement about the Chamber, and a subtitle that reads as one
    would be the drawing's own caption saying something the roster does not.
    `CompositionDimensionResult.largest` is where the rule lives; this checks
    the subtitle is built from it rather than from the raw categories.
    """
    from apps.membership.composition import Dimension
    from apps.membership.composition_selectors import (
        CompositionCategory,
        CompositionDimensionResult,
        CompositionSnapshot,
    )

    result = CompositionDimensionResult(
        dimension=Dimension.REGION,
        label="Piirkond",
        population="all_current",
        total=100,
        categories=(
            CompositionCategory(key="unknown", label="Teadmata", count=70, share_pct=Decimal("70")),
            CompositionCategory(key="37", label="Harjumaa", count=30, share_pct=Decimal("30")),
        ),
    )
    snapshot = CompositionSnapshot(
        id=1,
        snapshot_date=LATEST,
        row_count=100,
        median_tenure_days=None,
        coverage_pct={},
        mapping_version="1",
        sector_mapping_version="1",
        dimensions={("all_current", Dimension.REGION): result},
    )

    subtitles = composition_subtitles(snapshot)

    assert subtitles[Dimension.REGION] == "suurim: Harjumaa"
    assert "Teadmata" not in subtitles[Dimension.REGION]


def test_there_are_no_subtitles_at_all_without_a_roster():
    assert composition_subtitles(None) == {}


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
