"""The Google Analytics configuration, connection state and reading contract.

A real collector exists (`Ga4ApiCollector`, driven by the `sync_ga4` command,
covered in `test_sync_ga4.py`). What these tests pin down is everything around
it: that nothing requires the settings, that reading the configuration or the
connection state never opens a socket, that configuration alone is not a
connection, and that the normalisation contract refuses impossible readings.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from apps.visibility.ga4 import (
    Ga4ApiCollector,
    Ga4NotConfigured,
    WebsiteTrafficReading,
    get_configuration,
    get_connection_status,
)
from apps.visibility.models import WebsiteTrafficObservation

pytestmark = pytest.mark.django_db


# -- configuration ------------------------------------------------------


def test_the_application_starts_with_no_ga_settings(settings):
    """Neither setting is required for anything to work."""
    settings.GA4_PROPERTY_ID = ""
    settings.GA4_CREDENTIALS_FILE = ""

    configuration = get_configuration()

    assert configuration.is_configured is False
    assert set(configuration.missing) == {"GA4_PROPERTY_ID", "GA4_CREDENTIALS_FILE"}


def test_the_test_suite_supplies_no_credentials(settings):
    assert getattr(settings, "GA4_PROPERTY_ID", "") == ""
    assert getattr(settings, "GA4_CREDENTIALS_FILE", "") == ""


def test_requiring_configuration_names_what_is_missing_without_echoing_a_value(settings):
    settings.GA4_PROPERTY_ID = "synthetic-property"
    settings.GA4_CREDENTIALS_FILE = ""

    with pytest.raises(Ga4NotConfigured) as error:
        get_configuration().require()

    message = str(error.value)
    assert "GA4_CREDENTIALS_FILE" in message
    assert "synthetic-property" not in message, "a configured value must not be echoed"


def test_a_complete_configuration_satisfies_require(settings):
    settings.GA4_PROPERTY_ID = "synthetic-property"
    settings.GA4_CREDENTIALS_FILE = "/run/secrets/synthetic.json"

    assert get_configuration().require().is_configured is True


# -- connection state ---------------------------------------------------


def test_configuration_alone_does_not_make_it_connected(settings):
    """Intending to connect is not the same as having collected anything."""
    settings.GA4_PROPERTY_ID = "synthetic-property"
    settings.GA4_CREDENTIALS_FILE = "/run/secrets/synthetic.json"

    status = get_connection_status()

    assert status.is_connected is False
    assert status.message == "Google Analytics ei ole ühendatud."
    assert "ühtegi vaatlust ei ole veel kogutud" in status.detail


def test_the_unconnected_message_contains_no_digit():
    """It reaches the overview band, which asserts it renders no digits."""
    status = get_connection_status()

    assert not any(character.isdigit() for character in f"{status.message} {status.detail}")


def test_no_website_traffic_observation_exists():
    assert WebsiteTrafficObservation.objects.count() == 0
    assert get_connection_status().is_connected is False


# -- the collector contract --------------------------------------------


def test_the_module_makes_no_network_call(monkeypatch):
    """Every public entry point is exercised with sockets disabled."""
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("apps.visibility.ga4 must not open a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    get_configuration()
    get_connection_status()


def test_api_collector_requires_configuration(settings):
    settings.GA4_PROPERTY_ID = ""
    settings.GA4_CREDENTIALS_FILE = ""

    with pytest.raises(Ga4NotConfigured):
        Ga4ApiCollector(get_configuration())


def test_the_normalisation_contract_validates_a_reading():
    reading = WebsiteTrafficReading(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        sessions=1234,
    )

    assert reading.validate() is reading
    payload = reading.canonical_payload()
    assert payload["source"] == "ga4-website-traffic"
    # Absent metrics stay absent rather than becoming zero.
    assert payload["page_views"] is None


def test_the_contract_refuses_an_inverted_period():
    reading = WebsiteTrafficReading(period_start=date(2026, 7, 31), period_end=date(2026, 7, 1))

    with pytest.raises(ValueError):
        reading.validate()


def test_the_contract_refuses_a_negative_figure():
    reading = WebsiteTrafficReading(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 1) + timedelta(days=30),
        sessions=-1,
    )

    with pytest.raises(ValueError):
        reading.validate()
