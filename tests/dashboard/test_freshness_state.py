"""The freshness dataclass alone: label, variant and message stay consistent.

No database: these pin the vocabulary the shell row may use, so a refactor
cannot quietly change what "connected" or "stale" claims.
"""

import datetime as dt

from apps.dashboard.freshness import NO_SOURCE_MESSAGE, FreshnessState

CHECKED_AT = dt.datetime(2026, 7, 30, 8, 0, tzinfo=dt.UTC)


def test_no_sources_keeps_the_original_empty_state():
    state = FreshnessState(checked_at=CHECKED_AT, total_sources=4)

    assert state.has_sources is False
    assert state.state_label == "Ühendamata"
    assert state.state_variant == "neutral"
    assert state.message == NO_SOURCE_MESSAGE


def test_partial_connection_is_reported_as_a_fraction():
    state = FreshnessState(checked_at=CHECKED_AT, connected_sources=2, total_sources=4)

    assert state.state_label == "Ühendatud"
    assert state.state_variant == "success"
    assert state.message == "Ühendatud andmeallikaid: 2/4."


def test_a_stale_source_turns_the_row_into_a_warning():
    state = FreshnessState(
        checked_at=CHECKED_AT, connected_sources=4, total_sources=4, stale_sources=1
    )

    assert state.state_label == "Vananenud"
    assert state.state_variant == "warning"
    assert state.message == "Ühendatud andmeallikaid: 4/4. Vananenud: 1."
