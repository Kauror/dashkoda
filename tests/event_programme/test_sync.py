"""Public-link synchronisation: publication, idempotency, failure and secrecy.

The transport is always mocked. No real sharing URL exists anywhere in this
file, and the assertions prove that a synthetic one placed in the configuration
never reaches the feed state, the audit trail, the artifact or the JSON output.
"""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command

from apps.audit.models import AuditAction, AuditEvent
from apps.event_programme.models import (
    EventProgrammeFeedState,
    EventProgrammeItem,
    EventProgrammeSnapshot,
    SyncResult,
)
from apps.event_programme.public_download import PublicDownloadError, PublicUrlNotConfigured
from apps.event_programme.sync import (
    EXIT_LOCKED,
    PUBLIC_EXTERNAL_REFERENCE,
    SyncLocked,
    advisory_lock,
    synchronize_public_workbook,
)
from apps.sources.models import ImportStatus, SourceArtifact

from .conftest import FakeDownloader
from .workbook_factory import default_control, default_rows, synthetic_row

pytestmark = pytest.mark.django_db

# A synthetic value shaped like the real secret, so the "never leaks" assertions
# have something specific to hunt for.
SECRET_MARKER = "synthetic-not-a-real-share-token"
PUBLIC_URL = (
    f"https://synthetic-tenant-my.sharepoint.com/:x:/g/personal/synthetic/"
    f"{SECRET_MARKER}?e=synthetic"
)


def feed_state() -> EventProgrammeFeedState:
    return EventProgrammeFeedState.objects.get()


def test_first_run_publishes_a_snapshot(make_workbook, event_programme_source):
    outcome = synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))

    assert outcome.result == SyncResult.IMPORTED
    assert outcome.rows_imported == 3

    snapshot = EventProgrammeSnapshot.objects.get()
    assert snapshot.is_current is True
    assert snapshot.canonical_event_count == 3
    assert snapshot.dated_event_count == 2
    assert snapshot.linked_public_url_count == 1
    assert snapshot.review_required_count == 1
    assert snapshot.schema_version == "1.0"
    assert EventProgrammeItem.objects.count() == 3

    state = feed_state()
    assert state.last_result == SyncResult.IMPORTED
    assert state.current_snapshot == snapshot
    assert state.last_error_summary == ""


def test_no_price_field_exists_on_the_model(make_workbook):
    """The workbook carries pricing; the model must have nowhere to put it."""
    synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))

    field_names = {field.name for field in EventProgrammeItem._meta.get_fields()}
    assert not any("price" in name for name in field_names)
    assert not any("discount" in name for name in field_names)


def test_identical_bytes_are_reported_unchanged(make_workbook):
    workbook = make_workbook()
    synchronize_public_workbook(downloader=FakeDownloader(workbook))

    outcome = synchronize_public_workbook(downloader=FakeDownloader(workbook))

    assert outcome.result == SyncResult.UNCHANGED
    # Still exactly one snapshot: identical content publishes nothing new.
    assert EventProgrammeSnapshot.objects.count() == 1
    assert feed_state().last_result == SyncResult.UNCHANGED
    assert AuditEvent.objects.filter(action=AuditAction.EVENT_PROGRAMME_SYNC_UNCHANGED).exists()


def test_changed_bytes_publish_a_new_snapshot_and_retire_the_old(make_workbook):
    synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))

    rows = [
        *default_rows(),
        synthetic_row(event_id="EVENT-9004", service_code="9004", source_row=5),
    ]
    second = make_workbook(rows=rows, control=default_control(rows))
    outcome = synchronize_public_workbook(downloader=FakeDownloader(second))

    assert outcome.result == SyncResult.IMPORTED
    assert outcome.rows_imported == 4
    assert EventProgrammeSnapshot.objects.count() == 2
    # Exactly one current snapshot, enforced by a partial unique constraint.
    current = EventProgrammeSnapshot.objects.get(is_current=True)
    assert current.canonical_event_count == 4


def test_dry_run_validates_without_publishing(make_workbook):
    outcome = synchronize_public_workbook(downloader=FakeDownloader(make_workbook()), dry_run=True)

    assert outcome.result == SyncResult.IMPORTED
    assert outcome.dry_run is True
    assert outcome.rows_imported == 0
    assert EventProgrammeSnapshot.objects.count() == 0
    # A dry run records that a check happened and nothing else.
    state = feed_state()
    assert state.last_checked_at is not None
    assert state.last_result == SyncResult.NEVER_RUN


def test_dry_run_does_not_block_the_later_live_import(make_workbook):
    """The same bytes must still import for real afterwards."""
    workbook = make_workbook()
    synchronize_public_workbook(downloader=FakeDownloader(workbook), dry_run=True)

    outcome = synchronize_public_workbook(downloader=FakeDownloader(workbook))

    assert outcome.result == SyncResult.IMPORTED
    assert EventProgrammeSnapshot.objects.count() == 1


def test_download_failure_preserves_the_published_snapshot(make_workbook):
    synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))
    published = EventProgrammeSnapshot.objects.get()

    outcome = synchronize_public_workbook(
        downloader=FakeDownloader(
            error=PublicDownloadError("Jagamislink ei ole kättesaadav (404).")
        )
    )

    assert outcome.result == SyncResult.FAILED
    published.refresh_from_db()
    assert published.is_current is True
    assert EventProgrammeSnapshot.objects.count() == 1
    state = feed_state()
    assert state.last_result == SyncResult.FAILED
    assert state.current_snapshot == published
    assert AuditEvent.objects.filter(action=AuditAction.EVENT_PROGRAMME_SYNC_FAILED).exists()


def test_defective_workbook_preserves_the_published_snapshot(make_workbook):
    synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))
    published = EventProgrammeSnapshot.objects.get()

    control = default_control(default_rows())
    control["blocking_error_count"] = "2"
    broken = make_workbook(control=control)
    outcome = synchronize_public_workbook(downloader=FakeDownloader(broken))

    assert outcome.result == SyncResult.FAILED
    assert "blokeerivast veast" in outcome.detail
    published.refresh_from_db()
    assert published.is_current is True
    assert EventProgrammeSnapshot.objects.count() == 1


def test_artifact_is_metadata_only(make_workbook):
    synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))

    artifact = SourceArtifact.objects.get()
    assert artifact.external_reference == PUBLIC_EXTERNAL_REFERENCE
    assert artifact.is_external is True
    # The bytes were never kept: the checksum is the content identity.
    assert not artifact.file
    assert artifact.sha256


def test_the_downloaded_file_does_not_outlive_the_run(make_workbook):
    downloader = FakeDownloader(make_workbook())
    synchronize_public_workbook(downloader=downloader)

    assert downloader.destinations
    for destination in downloader.destinations:
        assert not destination.exists()
        assert not destination.parent.exists()


def test_the_sharing_url_never_leaves_the_configuration(settings, make_workbook):
    settings.EVENT_PROGRAMME_PUBLIC_URL = PUBLIC_URL

    outcome = synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))

    haystack = [
        json.dumps(outcome.as_dict(), ensure_ascii=False),
        json.dumps(feed_state().last_error_summary),
        SourceArtifact.objects.get().external_reference,
        json.dumps(
            [event.change_summary for event in AuditEvent.objects.all()],
            ensure_ascii=False,
            default=str,
        ),
    ]
    for text in haystack:
        assert SECRET_MARKER not in text
        assert PUBLIC_URL not in text


def test_the_sharing_url_never_leaks_through_a_failure(settings, make_workbook):
    """The failure path is the one most likely to echo configuration."""
    settings.EVENT_PROGRAMME_PUBLIC_URL = PUBLIC_URL

    outcome = synchronize_public_workbook(
        downloader=FakeDownloader(error=PublicDownloadError("Ühendus ebaõnnestus: Timeout."))
    )

    assert outcome.result == SyncResult.FAILED
    assert SECRET_MARKER not in outcome.detail
    assert SECRET_MARKER not in feed_state().last_error_summary


def test_reuses_the_artifact_left_by_a_failed_import(make_workbook):
    """A failed run registers content; the retry must not register it twice."""
    control = default_control(default_rows())
    control["canonical_event_count"] = "99"
    broken = make_workbook(control=control)

    synchronize_public_workbook(downloader=FakeDownloader(broken))
    assert SourceArtifact.objects.count() == 1

    synchronize_public_workbook(downloader=FakeDownloader(broken))

    assert SourceArtifact.objects.count() == 1
    assert SourceArtifact.objects.get().import_runs.filter(status=ImportStatus.FAILED).count() == 2


# -- the management command ---------------------------------------------


def test_command_emits_one_json_line(make_workbook, monkeypatch):
    from apps.event_programme.management.commands import sync_event_programme as command_module

    workbook = make_workbook()
    monkeypatch.setattr(
        command_module,
        "synchronize_public_workbook",
        lambda **kwargs: synchronize_public_workbook(downloader=FakeDownloader(workbook), **kwargs),
    )

    out = StringIO()
    call_command("sync_event_programme", "--json", stdout=out)

    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["result"] == SyncResult.IMPORTED
    assert payload["rows_imported"] == 3
    assert set(payload) == {
        "result",
        "detail",
        "snapshot_id",
        "export_refreshed_at",
        "rows_imported",
        "dry_run",
        "warnings",
    }


def test_command_reports_missing_configuration_by_name(settings):
    settings.EVENT_PROGRAMME_PUBLIC_URL = ""
    out = StringIO()

    with pytest.raises(SystemExit) as exit_info:
        call_command("sync_event_programme", stderr=out, stdout=out)

    assert exit_info.value.code == 1
    assert "EVENT_PROGRAMME_PUBLIC_URL" in out.getvalue()


@pytest.mark.django_db(transaction=True)
def test_command_refuses_to_overlap():
    """A second run must exit 3 rather than import alongside the first.

    The contending run needs its own database connection: a PostgreSQL advisory
    lock is re-entrant within one session, so taking it twice on the same
    connection succeeds and would prove nothing.
    """
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    def run_locked_command():
        close_old_connections()
        try:
            with pytest.raises(SystemExit) as exit_info:
                call_command("sync_event_programme", "--json", stdout=StringIO())
            return exit_info.value.code
        except SyncLocked:  # pragma: no cover - defensive
            return EXIT_LOCKED
        finally:
            close_old_connections()

    with advisory_lock():
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(run_locked_command).result() == EXIT_LOCKED


def test_the_command_offers_no_url_option():
    """The URL must come from the environment and nowhere else.

    A `--url` option would put a bearer-style secret into shell history and
    every process listing on the host.
    """
    from apps.event_programme.management.commands.sync_event_programme import Command

    parser = Command().create_parser("manage.py", "sync_event_programme")
    options = {action.dest for action in parser._actions}
    assert "url" not in options


def test_unconfigured_url_is_not_recorded_as_a_sync_failure(settings):
    """An operator's mistake must not look like the remote misbehaving."""
    settings.EVENT_PROGRAMME_PUBLIC_URL = ""

    with pytest.raises(PublicUrlNotConfigured):
        synchronize_public_workbook()

    assert not AuditEvent.objects.filter(action=AuditAction.EVENT_PROGRAMME_SYNC_FAILED).exists()
    assert feed_state().last_result == SyncResult.NEVER_RUN
