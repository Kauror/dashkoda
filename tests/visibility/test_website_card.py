"""The website card, which collected traffic for a while without showing it.

`_website_slot` returned the planned state unconditionally while its own
docstring said "planned until a real observation exists". So `sync_ga4`
published readings, the audit trail recorded them, and the card went on saying
the source was not connected — with the connection note underneath it saying the
opposite.

The slot is a pure function of a status and a reading, so every state it can be
in is checked here without PostgreSQL.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.visibility.ga4 import Ga4Configuration, Ga4ConnectionStatus
from apps.visibility.page import _website_slot
from apps.visibility.selectors import WebsiteTraffic

YESTERDAY = dt.date(2026, 8, 8)
BEFORE = dt.date(2026, 8, 7)


def connected(has_observation: bool = True) -> Ga4ConnectionStatus:
    return Ga4ConnectionStatus(
        configuration=Ga4Configuration(
            property_id="384525786", credentials_file="/run/secrets/dashkoda/ga4.json"
        ),
        has_observation=has_observation,
    )


def unconfigured() -> Ga4ConnectionStatus:
    return Ga4ConnectionStatus(
        configuration=Ga4Configuration(property_id="", credentials_file=""),
        has_observation=False,
    )


@pytest.fixture
def reading() -> WebsiteTraffic:
    return WebsiteTraffic(
        period_end=YESTERDAY,
        sessions=77,
        active_users=58,
        page_views=134,
        previous_period_end=BEFORE,
        previous_sessions=65,
    )


def test_a_collected_reading_is_shown_rather_than_announced_as_planned(reading):
    """The defect: traffic was being collected and the card said otherwise."""
    slot = _website_slot(connected(), reading)

    assert slot.is_planned is False
    assert slot.value == 77
    assert slot.as_of == YESTERDAY


def test_the_card_states_the_movement_against_the_previous_reading(reading):
    slot = _website_slot(connected(), reading)

    assert slot.secondary == "+12 võrreldes 7.08.26"


def test_the_figure_is_visits_because_that_is_what_the_card_is_labelled(reading):
    """Users and page views answer different questions and are not crowded into
    the same cell."""
    slot = _website_slot(connected(), reading)

    assert slot.unit == "seanssi"
    assert slot.value == reading.sessions


def test_the_one_automated_figure_on_the_band_says_it_is_automated(reading):
    """Every other card here was typed by a person and says so. Saying the same
    of this one would be false in the opposite direction."""
    slot = _website_slot(connected(), reading)

    assert slot.state_label == "Automaatselt kogutud"
    assert "Käsitsi" not in slot.state_label


def test_the_first_ever_reading_has_nothing_to_compare_against():
    """A comparison against no earlier reading would be a change from nothing."""
    slot = _website_slot(connected(), WebsiteTraffic(period_end=YESTERDAY, sessions=77))

    assert slot.value == 77
    assert slot.secondary == ""


def test_a_day_with_no_visits_is_a_reading_and_not_an_absence():
    """Zero is what the source reported, and it is not the same as unmeasured."""
    slot = _website_slot(
        connected(),
        WebsiteTraffic(
            period_end=YESTERDAY, sessions=0, previous_period_end=BEFORE, previous_sessions=12
        ),
    )

    assert slot.is_planned is False
    assert slot.value == 0
    assert slot.secondary.startswith("\N{MINUS SIGN}12")


def test_nothing_collected_yet_still_says_so():
    slot = _website_slot(unconfigured(), WebsiteTraffic())

    assert slot.is_planned is True
    assert slot.state_label == "Lisamisel"
    assert slot.value is None


def test_configured_but_never_collected_is_still_planned():
    """Configuration alone never claims a connection — a property ID and a key
    file describe an intention, not a measurement."""
    slot = _website_slot(connected(has_observation=False), WebsiteTraffic())

    assert slot.is_planned is True


def test_the_card_links_nowhere_in_either_state(reading):
    """A link to Google Analytics sends a board member to a login screen."""
    assert _website_slot(connected(), reading).profile_url == ""
    assert _website_slot(unconfigured(), WebsiteTraffic()).profile_url == ""


def test_a_reading_with_no_sessions_figure_is_not_shown_as_a_value():
    """GA4 returning no rows publishes all three metrics as `None`, and that is
    an absence rather than a quiet zero."""
    slot = _website_slot(connected(), WebsiteTraffic(period_end=YESTERDAY, sessions=None))

    assert slot.is_planned is True
