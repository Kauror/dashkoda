"""The collapse comparison itself, independent of any feed.

Kept separate from the feed tests so the rule can be read in one place: what it
refuses, what it deliberately lets through, and the two cases where there is
nothing to compare.
"""

import pytest
from django.test import override_settings

from apps.core.collapse_guard import collapse_reason, minimum_ratio


@pytest.fixture(autouse=True)
def guard_at_the_production_floor(settings):
    """The suite disables the guard; these tests are the ones that need it on."""
    settings.FEED_COLLAPSE_MIN_RATIO = 0.5


def test_a_collapse_below_the_floor_is_refused():
    reason = collapse_reason(current_count=600, incoming_count=200, noun="kirjet")

    assert reason is not None
    assert "200" in reason
    assert "600" in reason


def test_growth_is_never_refused():
    assert collapse_reason(current_count=600, incoming_count=6000, noun="kirjet") is None


def test_an_equal_count_is_never_refused():
    assert collapse_reason(current_count=600, incoming_count=600, noun="kirjet") is None


def test_a_shrink_at_the_floor_is_allowed():
    # Exactly half of the published count is the boundary, and the boundary is
    # inclusive: the guard refuses what falls *below* it.
    assert collapse_reason(current_count=600, incoming_count=300, noun="kirjet") is None


def test_a_shrink_just_below_the_floor_is_refused():
    assert collapse_reason(current_count=600, incoming_count=299, noun="kirjet") is not None


def test_a_first_import_has_nothing_to_compare_with():
    assert collapse_reason(current_count=None, incoming_count=1, noun="kirjet") is None


def test_an_empty_published_snapshot_is_not_a_baseline():
    assert collapse_reason(current_count=0, incoming_count=0, noun="kirjet") is None


def test_allow_collapse_answers_the_question():
    reason = collapse_reason(
        current_count=600, incoming_count=1, noun="kirjet", allow_collapse=True
    )

    assert reason is None


def test_an_empty_workbook_against_a_populated_one_is_refused():
    assert collapse_reason(current_count=600, incoming_count=0, noun="kirjet") is not None


@override_settings(FEED_COLLAPSE_MIN_RATIO=0.9)
def test_the_floor_is_configurable():
    assert minimum_ratio() == pytest.approx(0.9)
    assert collapse_reason(current_count=100, incoming_count=80, noun="kirjet") is not None


def test_the_noun_reaches_the_message():
    reason = collapse_reason(current_count=10, incoming_count=1, noun="sündmust")

    assert "sündmust" in reason
