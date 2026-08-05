"""The trend-window vocabulary both membership views share.

Pure functions, no database. What is pinned down here is not the labels but the
four rules the control must not break: a window is measured from the newest
observation, a window the history cannot fill is never offered, an unknown key
never raises, and the whole history stays reachable.
"""

from __future__ import annotations

import datetime as dt

from apps.membership.ranges import (
    CARD_CHOICES,
    CARD_DEFAULT,
    PAGE_CHOICES,
    PAGE_DEFAULT,
    RANGE_6,
    RANGE_12,
    RANGE_24,
    RANGE_ALL,
    available,
    months_before,
    resolve,
)

# The reporting date on the newest board report in these tests.
LATEST = dt.date(2026, 6, 4)


def labels(choices):
    return [choice.label for choice in choices]


# -- the window itself ---------------------------------------------------


def test_a_window_is_counted_back_from_the_observation_it_is_anchored_to():
    assert RANGE_6.start_from(LATEST) == dt.date(2025, 12, 4)
    assert RANGE_24.start_from(LATEST) == dt.date(2024, 6, 4)


def test_the_whole_history_has_no_start_rather_than_a_very_early_one():
    """`None` reaches the selector as "no lower bound". A date far in the past
    would be a claim about when the Chamber began counting."""
    assert RANGE_ALL.start_from(LATEST) is None
    assert RANGE_ALL.is_everything is True


def test_a_window_with_no_anchor_has_no_start():
    assert RANGE_12.start_from(None) is None


def test_stepping_back_a_month_lands_on_a_date_that_exists():
    """One month before 31 March is the end of February, not the 31st of it."""
    assert months_before(dt.date(2026, 3, 31), 1) == dt.date(2026, 2, 28)
    assert months_before(dt.date(2024, 3, 31), 1) == dt.date(2024, 2, 29)
    assert months_before(dt.date(2026, 1, 15), 1) == dt.date(2025, 12, 15)


# -- what may be offered -------------------------------------------------


def test_a_long_history_is_offered_every_window_the_page_has():
    offered = available(PAGE_CHOICES, earliest=dt.date(2011, 3, 1), latest=LATEST)

    assert labels(offered) == [
        "6 kuud",
        "12 kuud",
        "2 aastat",
        "3 aastat",
        "5 aastat",
        "Kogu ajalugu",
    ]


def test_offering_stops_at_the_first_window_that_covers_everything():
    """Ten months of history stops at twelve. `2 aastat` would draw the
    identical line under a different name, which reads as a broken button."""
    offered = available(PAGE_CHOICES, earliest=dt.date(2025, 8, 4), latest=LATEST)

    assert labels(offered) == ["6 kuud", "12 kuud"]


def test_a_history_shorter_than_the_shortest_window_offers_no_choice():
    offered = available(PAGE_CHOICES, earliest=dt.date(2026, 3, 1), latest=LATEST)

    assert labels(offered) == ["6 kuud"]
    # One button is not a choice; the caller renders no control at all.
    assert len(offered) == 1


def test_no_history_offers_nothing():
    assert available(PAGE_CHOICES, earliest=None, latest=None) == ()
    assert available(PAGE_CHOICES, earliest=dt.date(2011, 3, 1), latest=None) == ()


def test_the_card_stops_at_three_years_even_with_a_longer_history():
    """The card draws a polyline at card width. The long windows belong to the
    Liikmeskond page, which draws the same data across the full page."""
    offered = available(CARD_CHOICES, earliest=dt.date(2011, 3, 1), latest=LATEST)

    assert labels(offered) == ["6 kuud", "12 kuud", "2 aastat", "3 aastat"]


# -- resolving what arrived in the query string --------------------------


def test_a_known_key_selects_its_window():
    offered = available(PAGE_CHOICES, earliest=dt.date(2011, 3, 1), latest=LATEST)

    assert resolve("24", available=offered, default=PAGE_DEFAULT) is RANGE_24
    assert resolve("koik", available=offered, default=PAGE_DEFAULT) is RANGE_ALL


def test_an_unknown_key_falls_back_rather_than_raising():
    """A stale bookmark or a hand-typed URL still renders the page."""
    offered = available(PAGE_CHOICES, earliest=dt.date(2011, 3, 1), latest=LATEST)

    assert resolve("'; DROP TABLE", available=offered, default=PAGE_DEFAULT) is PAGE_DEFAULT
    assert resolve(None, available=offered, default=PAGE_DEFAULT) is PAGE_DEFAULT
    assert resolve("", available=offered, default=PAGE_DEFAULT) is PAGE_DEFAULT


def test_a_default_the_history_cannot_fill_gives_way_to_the_longest_it_can():
    """Ten months of history cannot draw five years, so the page opens on the
    longest window that exists rather than on an empty one."""
    offered = available(PAGE_CHOICES, earliest=dt.date(2025, 8, 4), latest=LATEST)

    assert resolve(None, available=offered, default=PAGE_DEFAULT) is RANGE_12
    # A window past the end of what is offered is treated the same way.
    assert resolve("36", available=offered, default=PAGE_DEFAULT) is RANGE_12


def test_the_card_default_survives_a_history_that_cannot_fill_it():
    offered = available(CARD_CHOICES, earliest=dt.date(2026, 3, 1), latest=LATEST)

    assert resolve("36", available=offered, default=CARD_DEFAULT) is RANGE_6


def test_nothing_offered_still_returns_a_window():
    """The caller renders no control, but it still has to ask the selector for
    something rather than branch on `None`."""
    assert resolve("12", available=(), default=CARD_DEFAULT) is CARD_DEFAULT
