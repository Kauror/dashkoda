"""The period control on the Nähtavus traffic section.

Pure functions over a `Coverage`, so the whole control is checkable without
PostgreSQL — which matters, because what it must never do is imply history the
property does not have.
"""

from __future__ import annotations

import datetime as dt

from apps.visibility.ga4_selectors import Coverage
from apps.visibility.traffic_page import (
    DEFAULT_PERIOD,
    PERIODS,
    parse_period,
    period_options,
    window_for,
)

LATEST = dt.date(2026, 8, 8)


def coverage(days: int) -> Coverage:
    """A history `days` long, ending on the newest collected day."""
    return Coverage(
        earliest=LATEST - dt.timedelta(days=days - 1),
        latest=LATEST,
        days_covered=days,
    )


def offered(options) -> set[str]:
    return {option.label for option in options if option.is_offered}


# -- reading the parameter -----------------------------------------------


def test_every_period_is_reachable_by_its_key():
    for period in PERIODS:
        assert parse_period(period.key) is period


def test_an_unreadable_period_falls_back_rather_than_raising():
    """A stale bookmark or a hand-typed URL still renders the page."""
    for raw in (None, "", "   ", "kõik-aastad", "'; DROP TABLE", "12"):
        assert parse_period(raw) is DEFAULT_PERIOD


def test_the_default_is_the_shortest_window():
    """The page opens on what a board member checks most often, and the one
    window every property can fill."""
    assert DEFAULT_PERIOD.key == "30"


# -- what the history can fill -------------------------------------------


def test_a_short_history_does_not_offer_the_long_windows():
    """Five years of mostly-empty axis is not a five-year chart."""
    options = period_options(DEFAULT_PERIOD, coverage(days=40))

    assert "30 päeva" in offered(options)
    assert "90 päeva" in offered(options)
    assert "1 aasta" not in offered(options)
    assert "5 aastat" not in offered(options)


def test_everything_is_always_offered():
    """`Kõik` is the one option whose length is a property of the data, so it is
    never the option that cannot be filled."""
    for days in (1, 40, 400, 4000):
        assert "Kõik" in offered(period_options(DEFAULT_PERIOD, coverage(days=days)))


def test_the_real_property_offers_five_years_but_not_falsely():
    """1 151 days as measured on 2026-08-09: three years of data. The five-year
    button is offered because a third of it is filled, and the section states
    the coverage in words so the gap is never implied to be quiet traffic."""
    options = period_options(DEFAULT_PERIOD, coverage(days=1151))

    assert offered(options) == {
        "30 päeva",
        "90 päeva",
        "1 aasta",
        "3 aastat",
        "5 aastat",
        "Kõik",
    }


def test_an_empty_history_offers_only_everything():
    options = period_options(DEFAULT_PERIOD, Coverage())

    assert offered(options) == {"Kõik"}


def test_exactly_one_option_is_active():
    options = period_options(parse_period("1a"), coverage(days=1151))

    assert [option.label for option in options if option.is_active] == ["1 aasta"]


def test_the_query_a_button_carries_names_the_period():
    options = period_options(DEFAULT_PERIOD, coverage(days=1151))
    year = next(option for option in options if option.label == "1 aasta")

    assert year.query == "periood=1a"


# -- the window a period resolves to -------------------------------------


def test_a_window_ends_on_the_newest_collected_day_not_today():
    """A chart running to today would end in a flat gap the width of however
    late the collector is."""
    start, end = window_for(parse_period("30"), coverage(days=1151), today=dt.date(2026, 8, 20))

    assert end == LATEST


def test_a_window_never_starts_before_the_history():
    """Padding the start with the period before collection began would draw a
    quiet stretch that was never measured."""
    start, end = window_for(parse_period("5a"), coverage(days=40))

    assert start == LATEST - dt.timedelta(days=39)


def test_everything_spans_the_whole_history():
    history = coverage(days=1151)

    start, end = window_for(parse_period("koik"), history)

    assert (start, end) == (history.earliest, history.latest)


def test_a_thirty_day_window_is_thirty_days():
    start, end = window_for(parse_period("30"), coverage(days=1151))

    assert (end - start).days + 1 == 30


def test_an_empty_history_still_resolves_to_a_drawable_pair():
    """Nothing here may raise on a deployment that has collected nothing."""
    today = dt.date(2026, 8, 20)

    start, end = window_for(parse_period("1a"), Coverage(), today=today)

    assert start <= end
