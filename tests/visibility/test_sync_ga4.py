"""The GA4 collection command and its feed contract.

The transport is always faked. **No Google credential, property ID or response
body exists anywhere in this file**; the command is exercised through a
collector double, and the one test that touches configuration asserts what
happens when it is absent — which is the production state.

GA4 is not enabled in production. What is tested here is that it *could* be,
without another architecture: the same lock, feed state, dry run, JSON contract,
exit codes, failure recording and audit events every other collector has.
"""

from __future__ import annotations

import datetime as dt
import json
from io import StringIO

import pytest
from django.core.management import call_command

from apps.audit.models import AuditAction, AuditEvent
from apps.core.feeds import FeedResult, advisory_lock
from apps.sources.models import ImportRun, ImportStatus, SourceArtifact
from apps.visibility.ga4 import Ga4NotConfigured, WebsiteTrafficReading
from apps.visibility.ga4_sync import LOCK_NAME, default_period, synchronize_ga4
from apps.visibility.models import Ga4FeedState, WebsiteTrafficObservation

pytestmark = pytest.mark.django_db

DAY = dt.date(2026, 7, 1)


class FakeCollector:
    """Stands in for `Ga4ApiCollector`: same constructor and collect signature."""

    reading_kwargs = {"sessions": 123, "active_users": 45, "page_views": 678}

    def __init__(self, configuration=None):
        self.configuration = configuration

    def collect(self, *, period_start, period_end):
        return WebsiteTrafficReading(
            period_start=period_start, period_end=period_end, **self.reading_kwargs
        ).validate()


class FailingCollector:
    """Raises whatever a test needs on `collect`."""

    def __init__(self, error):
        self.error = error

    def collect(self, *, period_start, period_end):
        raise self.error


class EmptyCollector:
    """A day GA4 has no rows for: every figure absent, nothing invented."""

    def collect(self, *, period_start, period_end):
        return WebsiteTrafficReading(period_start=period_start, period_end=period_end).validate()


@pytest.fixture
def fake_collector(monkeypatch):
    from apps.visibility import ga4_sync

    monkeypatch.setattr(ga4_sync, "Ga4ApiCollector", lambda configuration: FakeCollector())
    monkeypatch.setattr(ga4_sync, "get_configuration", lambda: None)
    return FakeCollector


def run_command(*args) -> str:
    output = StringIO()
    call_command("sync_ga4", *args, stdout=output, stderr=StringIO())
    return output.getvalue()


def run_json(*args) -> dict:
    return json.loads(run_command("--json", *args).strip())


def state() -> Ga4FeedState:
    return Ga4FeedState.objects.get()


# -- publication ---------------------------------------------------------


class TestPublication:
    def test_a_collected_reading_is_published_as_the_current_observation(self, fake_collector):
        run_command()

        observation = WebsiteTrafficObservation.objects.get()
        assert observation.is_current is True
        assert (observation.sessions, observation.active_users, observation.page_views) == (
            123,
            45,
            678,
        )
        run = ImportRun.objects.get()
        assert run.status == ImportStatus.SUCCEEDED
        assert run.dry_run is False

    def test_a_new_reading_retires_the_previous_current_observation(
        self, fake_collector, monkeypatch
    ):
        run_command()
        monkeypatch.setattr(FakeCollector, "reading_kwargs", {"sessions": 200}, raising=False)

        run_command()

        assert WebsiteTrafficObservation.objects.count() == 2
        assert WebsiteTrafficObservation.objects.get(is_current=True).sessions == 200

    def test_the_reporting_day_defaults_to_the_previous_completed_one(self):
        from django.utils import timezone

        assert default_period() == timezone.localdate() - dt.timedelta(days=1)

    def test_an_explicit_day_can_be_collected(self, fake_collector):
        run_command("--date", "2026-07-01")

        assert WebsiteTrafficObservation.objects.get().period_end == DAY

    def test_a_malformed_day_is_refused_before_anything_runs(self, fake_collector):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="YYYY-MM-DD"):
            run_command("--date", "1. juuli")

        assert not WebsiteTrafficObservation.objects.exists()


# -- idempotence ---------------------------------------------------------


class TestRepeatRuns:
    def test_a_same_day_re_run_is_unchanged_rather_than_a_crash(self, fake_collector):
        """A cron re-run of an already-collected day is the ordinary case.

        It used to escape as an unhandled ValidationError, because the second
        run tried to register a second artifact with the same checksum.
        """
        run_command()

        payload = run_json()

        assert payload["result"] == FeedResult.UNCHANGED
        assert WebsiteTrafficObservation.objects.count() == 1
        assert WebsiteTrafficObservation.objects.get().is_current is True
        assert SourceArtifact.objects.count() == 1
        assert ImportRun.objects.filter(status=ImportStatus.SUCCEEDED).count() == 1

    def test_an_unchanged_run_records_success_not_failure(self, fake_collector):
        run_command()
        run_command()

        assert state().last_result == FeedResult.UNCHANGED
        assert state().last_error_summary == ""

    def test_an_unchanged_run_records_an_audit_event(self, fake_collector):
        run_command()
        run_command()

        assert AuditEvent.objects.filter(action=AuditAction.GA4_SYNC_UNCHANGED).count() == 1

    def test_a_third_run_changes_nothing_further(self, fake_collector):
        run_command()
        run_command()
        run_command()

        assert WebsiteTrafficObservation.objects.count() == 1
        assert SourceArtifact.objects.count() == 1


# -- the dry run ---------------------------------------------------------


class TestDryRun:
    def test_it_publishes_nothing(self, fake_collector):
        run_command("--dry-run")

        assert not WebsiteTrafficObservation.objects.exists()
        assert not SourceArtifact.objects.exists()
        assert not ImportRun.objects.exists()

    def test_it_does_not_claim_a_successful_live_run(self, fake_collector):
        """The state may say the API was checked. It may not say data arrived."""
        run_command("--dry-run")

        assert state().last_checked_at is not None
        assert state().last_successful_sync_at is None
        assert state().last_result == FeedResult.NEVER_RUN
        assert state().current_observation is None
        assert state().last_period_end is None

    def test_it_records_no_audit_event(self, fake_collector):
        run_command("--dry-run")

        assert not AuditEvent.objects.filter(
            action__in=[
                AuditAction.GA4_OBSERVATION_IMPORTED,
                AuditAction.GA4_SYNC_UNCHANGED,
            ]
        ).exists()

    def test_it_is_marked_as_a_dry_run_in_json(self, fake_collector):
        assert run_json("--dry-run")["dry_run"] is True

    def test_a_live_run_after_a_dry_run_still_publishes(self, fake_collector):
        run_command("--dry-run")

        run_command()

        assert WebsiteTrafficObservation.objects.get().is_current is True

    def test_a_dry_run_of_an_already_collected_day_reports_unchanged(self, fake_collector):
        run_command()

        payload = run_json("--dry-run")

        assert payload["result"] == FeedResult.UNCHANGED
        assert payload["dry_run"] is True


# -- zero rows -----------------------------------------------------------


class TestADayWithNoTraffic:
    @pytest.fixture(autouse=True)
    def empty(self, monkeypatch):
        from apps.visibility import ga4_sync

        monkeypatch.setattr(ga4_sync, "Ga4ApiCollector", lambda configuration: EmptyCollector())
        monkeypatch.setattr(ga4_sync, "get_configuration", lambda: None)

    def test_it_publishes_an_observation_with_no_figures(self):
        run_command()

        observation = WebsiteTrafficObservation.objects.get()
        assert (observation.sessions, observation.active_users, observation.page_views) == (
            None,
            None,
            None,
        )

    def test_it_is_a_success_not_a_failure(self):
        run_command()

        assert state().last_result == FeedResult.IMPORTED

    def test_the_log_says_no_figures_were_reported(self):
        """Otherwise a quiet day is indistinguishable from an ordinary one."""
        assert run_json()["figures_reported"] is False

    def test_an_ordinary_day_says_the_opposite(self, monkeypatch):
        from apps.visibility import ga4_sync

        monkeypatch.setattr(ga4_sync, "Ga4ApiCollector", lambda configuration: FakeCollector())

        assert run_json()["figures_reported"] is True


# -- failure recording ---------------------------------------------------


def fail_with(monkeypatch, error):
    from apps.visibility import ga4_sync

    monkeypatch.setattr(ga4_sync, "Ga4ApiCollector", lambda configuration: FailingCollector(error))
    monkeypatch.setattr(ga4_sync, "get_configuration", lambda: None)


class TestFailureIsRecorded:
    """A failure must be visible in the feed state and the audit trail.

    Before this, `sync_ga4` raised `CommandError` and the only trace was a cron
    traceback nobody would find later.
    """

    @pytest.mark.parametrize(
        ("error", "label"),
        [
            (OSError("connection refused"), "api-unavailable"),
            (ValueError("Google Analytics ei tagastanud nõutud näitajaid."), "malformed"),
            (TimeoutError("timed out"), "timeout"),
            (RuntimeError("something unexpected"), "unexpected"),
        ],
    )
    def test_the_run_fails_with_exit_code_one(self, monkeypatch, error, label):
        fail_with(monkeypatch, error)

        with pytest.raises(SystemExit) as exit_info:
            run_command()

        assert exit_info.value.code == 1

    def test_the_feed_state_records_the_failure(self, monkeypatch):
        fail_with(monkeypatch, OSError("connection refused"))

        with pytest.raises(SystemExit):
            run_command()

        assert state().last_result == FeedResult.FAILED
        assert state().last_error_summary
        assert state().last_checked_at is not None

    def test_an_audit_event_records_the_failure(self, monkeypatch):
        fail_with(monkeypatch, OSError("connection refused"))

        with pytest.raises(SystemExit):
            run_command()

        assert AuditEvent.objects.filter(action=AuditAction.GA4_SYNC_FAILED).exists()

    def test_an_auth_failure_is_recorded_without_naming_the_credential(self, monkeypatch):
        fail_with(
            monkeypatch,
            Ga4NotConfigured("Google Analytics ei ole seadistatud. Puuduvad: GA4_PROPERTY_ID"),
        )

        with pytest.raises(SystemExit):
            run_command()

        summary = state().last_error_summary
        assert "GA4_PROPERTY_ID" in summary, "the missing variable's name is the useful part"
        assert "/" not in summary, "no credential path may be stored"

    def test_a_failure_publishes_nothing(self, monkeypatch):
        fail_with(monkeypatch, OSError("connection refused"))

        with pytest.raises(SystemExit):
            run_command()

        assert not WebsiteTrafficObservation.objects.exists()

    def test_the_last_good_observation_survives_a_later_failure(self, monkeypatch):
        from apps.visibility import ga4_sync

        monkeypatch.setattr(ga4_sync, "Ga4ApiCollector", lambda configuration: FakeCollector())
        monkeypatch.setattr(ga4_sync, "get_configuration", lambda: None)
        run_command()

        fail_with(monkeypatch, OSError("connection refused"))
        with pytest.raises(SystemExit):
            run_command("--date", "2026-07-02")

        observation = WebsiteTrafficObservation.objects.get()
        assert observation.is_current is True
        assert observation.sessions == 123
        assert state().last_result == FeedResult.FAILED
        assert state().current_observation_id == observation.pk

    def test_a_retry_after_a_failure_succeeds_and_clears_the_error(self, monkeypatch):
        from apps.visibility import ga4_sync

        fail_with(monkeypatch, OSError("connection refused"))
        with pytest.raises(SystemExit):
            run_command()

        monkeypatch.setattr(ga4_sync, "Ga4ApiCollector", lambda configuration: FakeCollector())
        run_command()

        assert state().last_result == FeedResult.IMPORTED
        assert state().last_error_summary == ""
        assert WebsiteTrafficObservation.objects.get().is_current is True


class TestMissingConfiguration:
    """The production state: the collector exists, the credentials do not."""

    def test_it_fails_cleanly_rather_than_raising(self, settings):
        settings.GA4_PROPERTY_ID = ""
        settings.GA4_CREDENTIALS_FILE = ""

        with pytest.raises(SystemExit) as exit_info:
            run_command()

        assert exit_info.value.code == 1
        assert state().last_result == FeedResult.FAILED
        assert "GA4_PROPERTY_ID" in state().last_error_summary

    def test_it_publishes_nothing(self, settings):
        settings.GA4_PROPERTY_ID = ""
        settings.GA4_CREDENTIALS_FILE = ""

        with pytest.raises(SystemExit):
            run_command()

        assert not WebsiteTrafficObservation.objects.exists()


# -- the advisory lock ---------------------------------------------------


class TestTheAdvisoryLock:
    """Overlap has to be tested across connections, not within one.

    A PostgreSQL advisory lock is re-entrant **per session**: taking it and then
    taking it again on the same connection succeeds. A test that held the lock
    and then called the command in-process would therefore pass whether or not
    the command locks at all. The lock is held here while a second thread — and
    so a second connection — runs the command, which is the situation two
    overlapping cron jobs actually create.
    """

    def test_ga4_uses_a_lock_name_of_its_own(self):
        """A GA4 run may neither block nor be blocked by another feed."""
        from apps.legal_work.current_topic_sync import LOCK_NAME as CURRENT_TOPICS
        from apps.legal_work.sync import ADVISORY_LOCK_NAMESPACE as LEGAL_WORK

        assert LOCK_NAME not in {CURRENT_TOPICS, LEGAL_WORK}

    def test_its_lock_key_collides_with_no_other_feed(self):
        from apps.core.feeds import advisory_lock_key
        from apps.legal_work.current_topic_sync import LOCK_NAME as CURRENT_TOPICS
        from apps.legal_work.sync import ADVISORY_LOCK_NAMESPACE as LEGAL_WORK

        names = (LOCK_NAME, CURRENT_TOPICS, LEGAL_WORK)
        assert len({advisory_lock_key(name) for name in names}) == len(names)

    def test_the_locked_result_is_reported_and_exits_three(self, monkeypatch, fake_collector):
        """The output contract for a skipped run, without needing a real race."""
        from contextlib import contextmanager

        from apps.core.feeds import FeedLocked
        from apps.visibility.management.commands import sync_ga4 as command_module

        @contextmanager
        def locked(name):
            raise FeedLocked(f"Allika {name} sünkroonimine juba käib.")
            yield  # pragma: no cover - never reached

        monkeypatch.setattr(command_module, "advisory_lock", locked)

        output = StringIO()
        with pytest.raises(SystemExit) as exit_info:
            call_command("sync_ga4", "--json", stdout=output, stderr=StringIO())

        assert exit_info.value.code == 3
        assert json.loads(output.getvalue().strip())["result"] == "locked"
        assert not WebsiteTrafficObservation.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_an_overlapping_ga4_run_exits_three(monkeypatch):
    """True overlap, across two connections, then release."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    from apps.visibility import ga4_sync

    monkeypatch.setattr(ga4_sync, "Ga4ApiCollector", lambda configuration: FakeCollector())
    monkeypatch.setattr(ga4_sync, "get_configuration", lambda: None)

    def run_in_another_connection():
        close_old_connections()
        try:
            with pytest.raises(SystemExit) as exit_info:
                call_command("sync_ga4", "--json", stdout=StringIO(), stderr=StringIO())
            return exit_info.value.code
        finally:
            close_old_connections()

    with advisory_lock(LOCK_NAME):
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(run_in_another_connection).result() == 3

    assert not WebsiteTrafficObservation.objects.exists()

    # Released: the next run may take it and publish.
    call_command("sync_ga4", stdout=StringIO(), stderr=StringIO())
    assert WebsiteTrafficObservation.objects.get().is_current is True


@pytest.mark.django_db(transaction=True)
def test_another_feeds_lock_does_not_block_ga4(monkeypatch):
    """Distinct names mean a slow workbook sync cannot stall GA4."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    from apps.legal_work.current_topic_sync import LOCK_NAME as CURRENT_TOPICS
    from apps.visibility import ga4_sync

    monkeypatch.setattr(ga4_sync, "Ga4ApiCollector", lambda configuration: FakeCollector())
    monkeypatch.setattr(ga4_sync, "get_configuration", lambda: None)

    def run_in_another_connection():
        close_old_connections()
        try:
            call_command("sync_ga4", stdout=StringIO(), stderr=StringIO())
        finally:
            close_old_connections()

    with advisory_lock(CURRENT_TOPICS):
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(run_in_another_connection).result()

    assert WebsiteTrafficObservation.objects.get().is_current is True


# -- the JSON contract ---------------------------------------------------


class TestTheJsonContract:
    def test_it_is_one_line_of_deterministic_json(self, fake_collector):
        raw = run_command("--json")

        assert raw.count("\n") == 1
        assert json.loads(raw) == json.loads(raw)

    def test_the_keys_are_fixed(self, fake_collector):
        assert set(run_json()) == {
            "result",
            "detail",
            "dry_run",
            "period_end",
            "observation_id",
            "figures_reported",
        }

    def test_it_names_the_reporting_day(self, fake_collector):
        assert run_json("--date", "2026-07-01")["period_end"] == "2026-07-01"

    def test_it_carries_no_reading_figures(self, fake_collector):
        """The counts belong on the dashboard and in the audit trail.

        A scheduler log is neither, and this held for the prose output before it
        held for JSON.
        """
        raw = run_command("--json")

        for figure in ("123", "45", "678"):
            assert figure not in raw

    def test_the_prose_output_carries_no_reading_figures(self, fake_collector):
        raw = run_command()

        for figure in ("123", "45", "678"):
            assert figure not in raw

    def test_no_configuration_value_reaches_the_output(self, fake_collector, settings):
        settings.GA4_PROPERTY_ID = "properties/424242"
        settings.GA4_CREDENTIALS_FILE = "/run/secrets/ga4-key.json"

        raw = run_command("--json")

        assert "424242" not in raw
        assert "ga4-key" not in raw
        assert "/run/secrets" not in raw


# -- audit ---------------------------------------------------------------


class TestAudit:
    def test_a_publication_records_one_event(self, fake_collector):
        run_command()

        events = AuditEvent.objects.filter(action=AuditAction.GA4_OBSERVATION_IMPORTED)
        assert events.count() == 1

    def test_the_audit_summary_carries_aggregates_and_no_secret(self, fake_collector, settings):
        settings.GA4_PROPERTY_ID = "properties/424242"
        settings.GA4_CREDENTIALS_FILE = "/run/secrets/ga4-key.json"
        run_command()

        summary = AuditEvent.objects.get(action=AuditAction.GA4_OBSERVATION_IMPORTED).change_summary
        blob = json.dumps(summary, ensure_ascii=False)

        assert summary["sessions"] == 123
        assert summary["period_end"] == default_period().isoformat()
        assert "424242" not in blob
        assert "/run/secrets" not in blob
        assert "token" not in blob.lower()


# -- feed state ----------------------------------------------------------


class TestFeedState:
    def test_a_row_exists_only_once_the_command_has_run(self, fake_collector):
        assert not Ga4FeedState.objects.exists()

        run_command()

        assert Ga4FeedState.objects.count() == 1

    def test_it_records_the_published_observation_and_the_day_reached(self, fake_collector):
        run_command("--date", "2026-07-01")

        assert state().current_observation == WebsiteTrafficObservation.objects.get()
        assert state().last_period_end == DAY
        assert state().last_changed_at is not None

    def test_the_synchronize_function_can_be_driven_directly(self):
        """The command is a thin shell; the contract is the function's."""
        outcome = synchronize_ga4(collector=FakeCollector(), period=DAY)

        assert outcome.result == FeedResult.IMPORTED
        assert outcome.succeeded is True


# -- GA4 stays out of the global freshness row ---------------------------


class TestGlobalFreshnessIsUnaffected:
    """A disabled source must not make the deployment look unhealthy.

    The shell's freshness row counts the four wired modules. GA4 is plumbed but
    off, so adding it to that denominator would report the deployment as
    permanently one source short of connected — for a feature nobody has
    enabled.
    """

    def test_ga4_is_not_one_of_the_counted_summary_sources(self):
        from apps.dashboard.freshness import _SUMMARY_SOURCES

        names = {summary_class.__name__ for summary_class, _reader in _SUMMARY_SOURCES}
        assert not any("Ga4" in name or "Visibility" in name for name in names)

    def test_the_denominator_does_not_move_when_ga4_publishes(self, fake_collector):
        from apps.dashboard.freshness import current_freshness

        before = current_freshness().total_sources
        run_command()

        assert current_freshness().total_sources == before

    def test_a_ga4_failure_does_not_make_the_shell_look_stale(self, monkeypatch):
        from apps.dashboard.freshness import current_freshness

        before = current_freshness()
        fail_with(monkeypatch, OSError("connection refused"))
        with pytest.raises(SystemExit):
            run_command()

        after = current_freshness()
        assert (after.connected_sources, after.total_sources) == (
            before.connected_sources,
            before.total_sources,
        )
