"""The trend-window vocabulary both membership views share.

Pure functions, no database. What is pinned down here is not the field names
but the rules the control must not break: the default window is measured from
the newest observation, whatever arrives is folded back inside the history,
unreadable input never raises, and a retired `?vahemik=` bookmark keeps meaning
what it meant.
"""

from __future__ import annotations

import datetime as dt

from apps.membership.ranges import (
    CARD_DEFAULT_MONTHS,
    PAGE_DEFAULT_MONTHS,
    DateWindow,
    months_before,
    offers_choice,
    parse_iso_date,
    resolve_window,
)

# The observation span in these tests: a long history ending on the reporting
# date of the newest board report.
EARLIEST = dt.date(2011, 3, 1)
LATEST = dt.date(2026, 6, 4)


def resolve(raw_from=None, raw_to=None, **overrides):
    keywords = {
        "earliest": EARLIEST,
        "latest": LATEST,
        "default_months": CARD_DEFAULT_MONTHS,
    }
    keywords.update(overrides)
    return resolve_window(raw_from, raw_to, **keywords)


# -- the default window --------------------------------------------------


def test_the_default_window_is_counted_back_from_the_newest_observation():
    """Anchored to the report, not to today: a report four days late must not
    shorten the window by four days and silently drop its oldest point."""
    assert resolve() == DateWindow(dt.date(2025, 12, 4), LATEST)
    assert resolve(default_months=PAGE_DEFAULT_MONTHS) == DateWindow(dt.date(2021, 6, 4), LATEST)


def test_a_default_the_history_cannot_fill_starts_where_the_history_does():
    window = resolve(earliest=dt.date(2026, 3, 1))

    assert window == DateWindow(dt.date(2026, 3, 1), LATEST)


def test_stepping_back_a_month_lands_on_a_date_that_exists():
    """One month before 31 March is the end of February, not the 31st of it."""
    assert months_before(dt.date(2026, 3, 31), 1) == dt.date(2026, 2, 28)
    assert months_before(dt.date(2024, 3, 31), 1) == dt.date(2024, 2, 29)
    assert months_before(dt.date(2026, 1, 15), 1) == dt.date(2025, 12, 15)


# -- what a reader typed into the fields ---------------------------------


def test_two_readable_dates_are_taken_as_given():
    window = resolve("2024-02-10", "2025-11-05")

    assert window == DateWindow(dt.date(2024, 2, 10), dt.date(2025, 11, 5))


def test_reversed_dates_are_swapped_rather_than_refused():
    """A reader who filled the fields backwards asked for that span."""
    assert resolve("2025-11-05", "2024-02-10") == DateWindow(
        dt.date(2024, 2, 10), dt.date(2025, 11, 5)
    )


def test_one_date_keeps_the_other_side_of_the_default_window():
    """Touching a single field never silently moves both ends."""
    # Only a start: the window runs to the newest observation.
    assert resolve("2024-02-10", None) == DateWindow(dt.date(2024, 2, 10), LATEST)
    # Only an end: the default months are counted back from that end.
    assert resolve(None, "2025-11-05") == DateWindow(dt.date(2025, 5, 5), dt.date(2025, 11, 5))


def test_a_window_is_folded_back_inside_the_history():
    """`min`/`max` on the fields are advice; the URL is not obliged to obey
    them, so the server clamps what actually arrived."""
    assert resolve("1999-01-01", "2030-01-01") == DateWindow(EARLIEST, LATEST)


def test_a_window_entirely_outside_the_history_collapses_to_its_edge():
    """Honest, not helpful: nothing was observed there, and the page says
    "too few observations" rather than quietly drawing something else."""
    assert resolve("1998-01-01", "1999-01-01") == DateWindow(EARLIEST, EARLIEST)


def test_unreadable_dates_fall_back_to_the_default_rather_than_raising():
    """A stale bookmark or a hand-typed URL still renders the page."""
    default = resolve()

    assert resolve("'; DROP TABLE", "not-a-date") == default
    assert resolve("", "") == default
    assert resolve("2026-13-45", None) == default


def test_parse_reads_what_a_date_field_submits_and_nothing_else():
    assert parse_iso_date("2026-06-04") == dt.date(2026, 6, 4)
    assert parse_iso_date("04.06.2026") is None
    assert parse_iso_date("") is None
    assert parse_iso_date(None) is None


# -- the retired button control's bookmarks ------------------------------


def test_a_legacy_key_still_draws_the_window_it_always_drew():
    assert resolve(legacy_key="24") == DateWindow(dt.date(2024, 6, 4), LATEST)
    assert resolve(legacy_key="koik") == DateWindow(EARLIEST, LATEST)


def test_an_unknown_legacy_key_falls_back_to_the_default():
    assert resolve(legacy_key="'; DROP TABLE") == resolve()


def test_explicit_dates_beat_a_legacy_key():
    """A bookmark that somehow carries both spellings means the dates: they are
    the vocabulary, the key is a translation."""
    window = resolve("2024-02-10", "2025-11-05", legacy_key="6")

    assert window == DateWindow(dt.date(2024, 2, 10), dt.date(2025, 11, 5))


# -- when there is nothing to bound --------------------------------------


def test_no_history_resolves_to_no_window():
    assert resolve(earliest=None, latest=None) is None
    assert resolve(earliest=EARLIEST, latest=None) is None


def test_a_choice_is_offered_only_when_it_can_change_something():
    assert offers_choice(earliest=EARLIEST, latest=LATEST) is True
    # One observation date is one drawable window; a control over it is broken.
    assert offers_choice(earliest=LATEST, latest=LATEST) is False
    assert offers_choice(earliest=None, latest=LATEST) is False
    assert offers_choice(earliest=None, latest=None) is False
