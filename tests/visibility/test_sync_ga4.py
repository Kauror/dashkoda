"""The GA4 collection command: publication and idempotent re-runs.

The transport is always faked. No Google credential, property ID or response
body exists anywhere in this file; the command is exercised through a collector
double that returns a synthetic reading.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.sources.models import ImportRun, ImportStatus, SourceArtifact
from apps.visibility.ga4 import WebsiteTrafficReading
from apps.visibility.management.commands import sync_ga4
from apps.visibility.models import WebsiteTrafficObservation

pytestmark = pytest.mark.django_db


class FakeCollector:
    """Stands in for Ga4ApiCollector: same constructor and collect signature."""

    reading_kwargs = {"sessions": 123, "active_users": 45, "page_views": 678}

    def __init__(self, configuration):
        self.configuration = configuration

    def collect(self, *, period_start, period_end):
        return WebsiteTrafficReading(
            period_start=period_start,
            period_end=period_end,
            **self.reading_kwargs,
        ).validate()


@pytest.fixture
def fake_collector(monkeypatch):
    monkeypatch.setattr(sync_ga4, "Ga4ApiCollector", FakeCollector)
    return FakeCollector


def run_command() -> str:
    output = StringIO()
    call_command("sync_ga4", stdout=output)
    return output.getvalue()


def test_a_collected_reading_is_published_as_the_current_observation(fake_collector):
    run_command()

    observation = WebsiteTrafficObservation.objects.get()
    assert observation.is_current is True
    assert observation.sessions == 123
    assert observation.active_users == 45
    assert observation.page_views == 678
    run = ImportRun.objects.get()
    assert run.status == ImportStatus.SUCCEEDED
    assert run.dry_run is False


def test_a_new_reading_retires_the_previous_current_observation(fake_collector, monkeypatch):
    run_command()
    monkeypatch.setattr(FakeCollector, "reading_kwargs", {"sessions": 200}, raising=False)

    run_command()

    assert WebsiteTrafficObservation.objects.count() == 2
    current = WebsiteTrafficObservation.objects.get(is_current=True)
    assert current.sessions == 200


def test_a_same_day_re_run_reports_cleanly_instead_of_crashing(fake_collector):
    """Regression: a cron re-run of an already-collected day used to escape as
    an unhandled ValidationError, because the second run tried to register a
    second artifact with the same content checksum. It must finish cleanly,
    publish nothing new and leave the existing observation current."""
    run_command()

    output = run_command()

    assert WebsiteTrafficObservation.objects.count() == 1
    assert WebsiteTrafficObservation.objects.get().is_current is True
    assert SourceArtifact.objects.count() == 1
    assert ImportRun.objects.filter(status=ImportStatus.SUCCEEDED).count() == 1
    assert "juba avaldatud" in output


def test_the_command_output_carries_no_reading_figures(fake_collector):
    """Only the period is reported. The figures belong on the dashboard, not in
    a cron log."""
    output = run_command()

    for figure in ("123", "45", "678"):
        assert figure not in output
