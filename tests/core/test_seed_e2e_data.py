"""The browser-suite seed: refusal, determinism, idempotency and shape.

The seed exists so the browser suite meets realistic content instead of empty
states. These tests run it against a real database and assert the properties the
browser suite depends on — and the one property nothing else can check, that it
refuses to touch a production database.
"""

from __future__ import annotations

import os
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.management.commands import seed_e2e_data
from apps.events.models import EventItem, EventSnapshot
from apps.legal_work.models import LegalWorkItem, LegalWorkSnapshot
from apps.membership.models import InternalMembershipObservation, MembershipCountObservation
from apps.news.models import NewsItem, NewsSnapshot
from apps.sources.models import ImportRun, ImportStatus
from apps.visibility.models import VisibilityObservation

pytestmark = pytest.mark.django_db


def run_seed() -> str:
    output = StringIO()
    call_command("seed_e2e_data", stdout=output)
    return output.getvalue()


# -- it refuses production ----------------------------------------------


def test_the_seed_refuses_to_run_under_production_settings(monkeypatch):
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.production")

    with pytest.raises(CommandError) as error:
        call_command("seed_e2e_data", stdout=StringIO())

    assert "production" in str(error.value)


def test_the_seed_refuses_an_unset_settings_module(monkeypatch):
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)

    with pytest.raises(CommandError):
        call_command("seed_e2e_data", stdout=StringIO())


def test_production_is_not_in_the_permitted_set():
    assert "config.settings.production" not in seed_e2e_data.ALLOWED_SETTINGS_MODULES
    assert seed_e2e_data.ALLOWED_SETTINGS_MODULES == {
        "config.settings.local",
        "config.settings.test",
    }


def test_the_test_run_itself_is_under_a_permitted_module():
    assert os.environ.get("DJANGO_SETTINGS_MODULE") in seed_e2e_data.ALLOWED_SETTINGS_MODULES


# -- it publishes through the real domain paths -------------------------


def test_the_seed_publishes_every_wired_module():
    run_seed()

    # One current snapshot per feed, published atomically.
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1
    assert EventSnapshot.objects.filter(is_current=True).count() == 1
    assert NewsSnapshot.objects.filter(is_current=True).count() == 1
    assert MembershipCountObservation.objects.filter(is_current=True).count() == 1

    assert LegalWorkItem.objects.count() >= 20
    assert EventItem.objects.count() >= 15
    assert NewsItem.objects.count() >= 10
    # Six board reports, so both overview trend lines have enough points.
    assert InternalMembershipObservation.objects.count() == 6
    assert VisibilityObservation.objects.exists()


def test_every_import_run_the_seed_created_reached_a_successful_terminal_state():
    run_seed()

    runs = ImportRun.objects.all()
    assert runs.exists()
    assert not runs.exclude(status=ImportStatus.SUCCEEDED).exists()


def test_the_legal_work_workbook_passed_the_real_parser():
    """The seed writes a genuine XLSX and imports it through the real importer,
    so a workbook the parser would reject cannot be seeded."""
    run_seed()

    snapshot = LegalWorkSnapshot.objects.get(is_current=True)
    # CONTROL must agree with DATA or the parser refuses the file outright.
    assert snapshot.total_record_count == LegalWorkItem.objects.filter(snapshot=snapshot).count()
    assert snapshot.open_record_count > 0
    assert snapshot.sent_record_count > 0
    assert snapshot.warning_record_count > 0


# -- the content the browser suite depends on ---------------------------


def test_the_seed_creates_content_long_enough_to_truncate():
    """The 152-pixel overflow only appeared with content longer than the card.

    A short fixture cannot reproduce it, so the seed's long values are part of
    the contract rather than decoration.
    """
    run_seed()

    longest_topic = max(LegalWorkItem.objects.values_list("topic", flat=True), key=len)
    longest_event = max(EventItem.objects.values_list("title", flat=True), key=len)
    longest_news = max(NewsItem.objects.values_list("title", flat=True), key=len)

    assert len(longest_topic) > 150
    assert len(longest_event) > 150
    assert len(longest_news) > 150


def test_an_explicit_zero_and_a_missing_value_both_exist():
    """The interface must distinguish "counted, and it was none" from "nobody
    counted". Seeding only one of the two would let a regression hide."""
    run_seed()

    suspended = list(
        InternalMembershipObservation.objects.values_list("suspended_members", flat=True)
    )

    assert 0 in suspended, "an explicitly reported zero must be seeded"
    assert None in suspended, "a genuinely missing value must be seeded"


def test_events_span_month_and_year_boundaries():
    run_seed()

    months = {item.starts_on.month for item in EventItem.objects.all()}
    years = {item.starts_on.year for item in EventItem.objects.all()}

    assert len(months) > 1, "date formatting breaks most often at a month boundary"
    assert len(years) > 1, "and at a year boundary"


def test_both_dated_and_ranged_events_exist():
    run_seed()

    assert EventItem.objects.filter(ends_on__isnull=True).exists()
    assert EventItem.objects.filter(ends_on__isnull=False).exists()


def test_legal_work_covers_dated_and_undated_deadlines():
    run_seed()

    open_items = LegalWorkItem.objects.filter(is_open=True)
    assert open_items.filter(deadline_date__isnull=False).exists()
    assert open_items.filter(deadline_date__isnull=True).exists()


# -- determinism and idempotency ----------------------------------------


def test_running_the_seed_twice_publishes_nothing_new():
    """A cron-safe seed must be re-runnable: CI may seed a database that a
    previous step already seeded."""
    run_seed()
    counts = (
        LegalWorkSnapshot.objects.count(),
        EventSnapshot.objects.count(),
        NewsSnapshot.objects.count(),
        InternalMembershipObservation.objects.count(),
        VisibilityObservation.objects.count(),
    )

    run_seed()

    assert (
        LegalWorkSnapshot.objects.count(),
        EventSnapshot.objects.count(),
        NewsSnapshot.objects.count(),
        InternalMembershipObservation.objects.count(),
        VisibilityObservation.objects.count(),
    ) == counts


def test_the_seeded_workbook_is_byte_identical_between_builds(tmp_path):
    """Idempotency depends on this, and it is not free.

    An XLSX is a ZIP, and openpyxl stamps every member with the current time,
    so two identical workbooks saved a second apart differ in bytes. The
    synchronisation deduplicates on the checksum of those bytes, so without
    frozen timestamps the seed published a fresh snapshot on every run.
    """
    import datetime as dt
    import hashlib

    today = dt.date(2099, 6, 1)
    first = seed_e2e_data._write_legal_work_workbook(tmp_path / "first.xlsx", today)
    second = seed_e2e_data._write_legal_work_workbook(tmp_path / "second.xlsx", today)

    assert (
        hashlib.sha256(first.read_bytes()).hexdigest()
        == hashlib.sha256(second.read_bytes()).hexdigest()
    )


def test_the_seed_is_deterministic_in_its_values():
    """No randomness: the same day must produce the same numbers, or a failing
    browser test could not be reproduced."""
    run_seed()
    totals = sorted(InternalMembershipObservation.objects.values_list("total_members", flat=True))

    assert totals == [4050, 4090, 4120, 4150, 4176, 4203]


# -- nothing real, nothing fetched --------------------------------------


def test_the_seed_stores_no_real_looking_identifier():
    run_seed()

    for url in EventItem.objects.values_list("canonical_url", flat=True):
        assert "sunteetiline" in url
    for url in NewsItem.objects.values_list("canonical_url", flat=True):
        assert "sunteetiline" in url


def test_the_seed_opens_no_socket(monkeypatch):
    """Seeding is offline by construction: every collector is a local callable."""
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("seed_e2e_data must not open a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    run_seed()


def test_the_seed_keeps_no_workbook_behind(tmp_path, settings):
    """The legal-work artifact is metadata-only, exactly as in production."""
    settings.SOURCE_ARTIFACT_ROOT = str(tmp_path)

    run_seed()

    snapshot = LegalWorkSnapshot.objects.get(is_current=True)
    assert snapshot.artifact.is_external, "the seeded artifact must carry no stored file"
    assert not any(tmp_path.rglob("*.xlsx"))
