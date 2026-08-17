"""The `fookus` navigation contract.

The page has three views behind one URL, so the rules about what a query value
may do are load-bearing: an unknown focus must render something, a focus with no
data must not be advertised, and a focus link must not carry a control that means
nothing where it lands.

Runs without PostgreSQL — `focus.py` reads no model.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from apps.membership.focus import (
    DEFAULT_FOCUS,
    FOCUS_COMPOSITION,
    FOCUS_FEES,
    FOCUS_GROWTH,
    FOCUS_KEYS,
    FOCUS_MOVEMENT,
    FOCUS_OVERVIEW,
    FOCUS_REGISTER,
    PARAM_FOCUS,
    focus_links,
    resolve_focus,
)


def query_of(link) -> dict[str, list[str]]:
    return parse_qs(urlparse(link.query).query)


def test_the_default_focus_is_the_overview():
    assert DEFAULT_FOCUS == FOCUS_OVERVIEW
    assert resolve_focus(None) == FOCUS_OVERVIEW
    assert resolve_focus("") == FOCUS_OVERVIEW


def test_every_named_focus_resolves_to_itself():
    for key in FOCUS_KEYS:
        assert resolve_focus(key) == key


def test_the_retired_fee_focus_resolves_to_the_overview():
    """`Liikmemaks` retired on 2026-08-16; its chart joined the overview's
    trend section, so a saved link lands where the content went."""
    assert FOCUS_FEES not in FOCUS_KEYS
    assert resolve_focus(FOCUS_FEES) == FOCUS_OVERVIEW


def test_the_retired_movement_focus_resolves_to_growth():
    """`Liikumine ja põhjused` merged into `kasv` on 2026-08-17 and took the
    new name `Sisse-välja`; a saved link to the old focus lands on the
    content it merged into, not on the overview."""
    assert FOCUS_MOVEMENT not in FOCUS_KEYS
    assert resolve_focus(FOCUS_MOVEMENT) == FOCUS_GROWTH


def test_the_retired_composition_focus_resolves_to_the_overview():
    """`Koosseis` retired on 2026-08-17. Most of its distributions joined the
    overview; two followed `liikumine` into `kasv` instead — see
    `RETIRED_FOCUSES` — but the overview is where a stale bookmark lands,
    since most of the content is there."""
    assert FOCUS_COMPOSITION not in FOCUS_KEYS
    assert resolve_focus(FOCUS_COMPOSITION) == FOCUS_OVERVIEW


def test_an_unknown_focus_is_the_overview_rather_than_an_error():
    """A stale bookmark or a typed URL renders the page, it does not raise.

    This is the same rule `ranges.py` applies to a malformed date: a reader
    should not be punished for a link somebody else wrote.
    """
    for raw in ("koosseiss", "growth", "../etc", "1", "ÜLEVAADE"):
        assert resolve_focus(raw) == FOCUS_OVERVIEW


def test_a_focus_with_nothing_to_draw_is_not_offered():
    links = focus_links(FOCUS_OVERVIEW, available=frozenset({FOCUS_OVERVIEW, FOCUS_GROWTH}))
    offered = {link.key for link in links}

    assert offered == {FOCUS_OVERVIEW, FOCUS_GROWTH}
    assert FOCUS_REGISTER not in offered


def test_the_active_focus_is_listed_even_when_it_has_no_data():
    """A navigation that hides the item the reader is standing on reads as a fault."""
    links = focus_links(FOCUS_REGISTER, available=frozenset({FOCUS_OVERVIEW}))

    assert FOCUS_REGISTER in {link.key for link in links}
    assert next(link for link in links if link.key == FOCUS_REGISTER).is_active


def test_exactly_one_link_is_active():
    links = focus_links(FOCUS_GROWTH)
    assert [link.is_active for link in links].count(True) == 1
    assert next(link for link in links if link.is_active).key == FOCUS_GROWTH


def test_a_focus_link_carries_the_window_forward():
    """The window means the same thing on every focus that draws a time series."""
    links = focus_links(FOCUS_OVERVIEW, carried={"alates": "2025-01-01", "kuni": "2026-01-01"})
    params = query_of(next(link for link in links if link.key == FOCUS_GROWTH))

    assert params["alates"] == ["2025-01-01"]
    assert params["kuni"] == ["2026-01-01"]
    assert params[PARAM_FOCUS] == [FOCUS_GROWTH]


def test_a_focus_link_does_not_carry_a_chart_toggle():
    """`vaade` governs the recruitment chart and means nothing on the register view.

    Carrying it across would land a reader on a control state that does not
    apply where they arrived, which is how a control comes to look broken.
    """
    links = focus_links(FOCUS_OVERVIEW, carried={"alates": "2025-01-01"})
    params = query_of(next(link for link in links if link.key == FOCUS_REGISTER))

    assert "vaade" not in params
    assert "vordlus" not in params
    assert "otsus" not in params


def test_the_register_focus_is_not_offered_before_a_roster_is_imported():
    links = focus_links(FOCUS_OVERVIEW, available=frozenset({FOCUS_OVERVIEW}))
    assert FOCUS_REGISTER not in {link.key for link in links}


def test_focus_is_not_a_parameter_the_charts_already_use():
    """`vaade` already means monthly-versus-cumulative inside one chart.

    Reusing it would make one word govern two unrelated things, and a bookmark
    of a cumulative chart would start changing which page section existed.
    """
    from apps.membership.charts import PARAM_BENCHMARK, PARAM_VIEW
    from apps.membership.ranges import LEGACY_PARAM, PARAM_FROM, PARAM_TO

    assert PARAM_FOCUS not in {PARAM_VIEW, PARAM_BENCHMARK, PARAM_FROM, PARAM_TO, LEGACY_PARAM}
