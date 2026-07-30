"""Public-link synchronisation: idempotency, dry runs, failure and secrecy.

The transport is always mocked. No real sharing URL exists anywhere in this
file, and the assertions prove that a synthetic one placed in the configuration
never reaches the feed state, the audit trail, the artifact or the JSON output.
"""

from __future__ import annotations

import hashlib
import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.audit.models import AuditAction, AuditEvent
from apps.legal_work.models import LegalWorkFeedState, LegalWorkSnapshot, SyncResult
from apps.legal_work.public_download import (
    XLSX_MIME_TYPE,
    PublicDownload,
    PublicDownloadError,
)
from apps.legal_work.public_sync import (
    PUBLIC_EXTERNAL_REFERENCE,
    synchronize_public_workbook,
)
from apps.legal_work.sync import EXIT_LOCKED, SyncLocked, advisory_lock
from apps.sources.models import ImportRun, ImportStatus, SourceArtifact

from .workbook_factory import synthetic_row

pytestmark = pytest.mark.django_db

SECRET_MARKER = "synthetic-not-a-real-share-token"
PUBLIC_URL = (
    f"https://synthetic-tenant-my.sharepoint.com/:x:/g/personal/synthetic/"
    f"{SECRET_MARKER}?e=synthetic"
)


class FakeDownloader:
    """Copies a local synthetic workbook instead of contacting SharePoint."""

    def __init__(self, path: Path | None = None, *, error: Exception | None = None):
        self.path = path
        self.error = error
        self.calls = 0
        self.destinations: list[Path] = []

    def __call__(self, destination: Path) -> PublicDownload:
        self.calls += 1
        self.destinations.append(destination)
        if self.error is not None:
            raise self.error
        payload = self.path.read_bytes()
        destination.write_bytes(payload)
        return PublicDownload(
            path=destination,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            content_type=XLSX_MIME_TYPE,
            final_host="synthetic-tenant-my.sharepoint.com",
        )


def feed_state() -> LegalWorkFeedState:
    return LegalWorkFeedState.objects.get()


@pytest.fixture(autouse=True)
def configured_url(settings):
    settings.OIGUSLOOME_PUBLIC_URL = PUBLIC_URL


# -- first import -------------------------------------------------------


def test_a_first_live_run_imports_and_publishes(make_workbook):
    downloader = FakeDownloader(make_workbook())

    outcome = synchronize_public_workbook(downloader=downloader)

    assert outcome.result == SyncResult.IMPORTED
    assert outcome.rows_imported == 3
    assert outcome.reporting_date
    snapshot = LegalWorkSnapshot.objects.get()
    assert snapshot.is_current is True
    assert outcome.snapshot_id == snapshot.pk


def test_the_published_artifact_is_metadata_only(make_workbook):
    path = make_workbook()
    synchronize_public_workbook(downloader=FakeDownloader(path))

    artifact = SourceArtifact.objects.get()
    assert artifact.is_external is True
    assert not artifact.file
    assert artifact.external_reference == PUBLIC_EXTERNAL_REFERENCE
    assert artifact.original_name == "dashkoda_oigusloome.xlsx"
    assert artifact.mime_type == XLSX_MIME_TYPE
    assert artifact.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert artifact.size_bytes == path.stat().st_size


def test_no_workbook_file_remains_anywhere_after_a_successful_run(
    make_workbook, private_artifact_root
):
    downloader = FakeDownloader(make_workbook())

    synchronize_public_workbook(downloader=downloader)

    temporary = downloader.destinations[0]
    assert not temporary.exists()
    assert not temporary.parent.exists()
    assert list(private_artifact_root.rglob("*.xlsx")) == []


# -- idempotency --------------------------------------------------------


def test_an_immediately_repeated_run_reports_unchanged(make_workbook):
    downloader = FakeDownloader(make_workbook())
    synchronize_public_workbook(downloader=downloader)

    outcome = synchronize_public_workbook(downloader=downloader)

    assert outcome.result == SyncResult.UNCHANGED
    assert LegalWorkSnapshot.objects.count() == 1
    assert SourceArtifact.objects.count() == 1
    # The public route has no metadata call to skip: it downloads every time and
    # the checksum is what decides.
    assert downloader.calls == 2


def test_changed_bytes_create_a_new_artifact_and_snapshot(make_workbook):
    synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))

    changed = make_workbook(rows=[synthetic_row(record_id="SYN-NEW", source_row=2)])
    outcome = synchronize_public_workbook(downloader=FakeDownloader(changed))

    assert outcome.result == SyncResult.IMPORTED
    assert LegalWorkSnapshot.objects.count() == 2
    assert SourceArtifact.objects.count() == 2
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1
    assert LegalWorkSnapshot.objects.get(is_current=True).total_record_count == 1


def test_previously_imported_content_is_unchanged_even_after_other_imports(make_workbook):
    first = make_workbook()
    synchronize_public_workbook(downloader=FakeDownloader(first))
    synchronize_public_workbook(
        downloader=FakeDownloader(make_workbook(rows=[synthetic_row(record_id="SYN-2")]))
    )

    # The generator reverted to the earlier content; it already imported once, so
    # the registry refuses a second live import and this must be reported as
    # unchanged rather than as a failure.
    outcome = synchronize_public_workbook(downloader=FakeDownloader(first))

    assert outcome.result == SyncResult.UNCHANGED
    assert LegalWorkSnapshot.objects.count() == 2


# -- the four checksum cases -------------------------------------------


def test_same_checksum_with_a_successful_live_import_is_unchanged(make_workbook):
    path = make_workbook()
    synchronize_public_workbook(downloader=FakeDownloader(path))

    outcome = synchronize_public_workbook(downloader=FakeDownloader(path))

    assert outcome.result == SyncResult.UNCHANGED
    assert SourceArtifact.objects.count() == 1


def test_same_checksum_with_only_dry_runs_reuses_the_artifact_and_imports(make_workbook):
    path = make_workbook()
    synchronize_public_workbook(downloader=FakeDownloader(path), dry_run=True)
    assert SourceArtifact.objects.count() == 1
    registered = SourceArtifact.objects.get()

    outcome = synchronize_public_workbook(downloader=FakeDownloader(path))

    assert outcome.result == SyncResult.IMPORTED
    assert outcome.rows_imported == 3
    assert SourceArtifact.objects.count() == 1
    assert LegalWorkSnapshot.objects.get().artifact_id == registered.pk


def test_same_checksum_with_only_failed_runs_reuses_the_artifact_and_retries(make_workbook):
    """The workbook was invalid, then the generator fixed the *other* problem.

    The bytes are unchanged and the artifact already exists, so the retry must
    reuse it rather than attempt a duplicate registration.
    """
    broken = make_workbook(control_overrides={"total_record_count": 999})
    first = synchronize_public_workbook(downloader=FakeDownloader(broken))
    assert first.result == SyncResult.FAILED
    registered = SourceArtifact.objects.get()

    second = synchronize_public_workbook(downloader=FakeDownloader(broken))

    assert second.result == SyncResult.FAILED
    assert SourceArtifact.objects.count() == 1
    assert registered.import_runs.filter(status=ImportStatus.FAILED).count() == 2


def test_a_new_checksum_registers_a_new_metadata_artifact(make_workbook):
    synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))

    synchronize_public_workbook(
        downloader=FakeDownloader(make_workbook(rows=[synthetic_row(record_id="SYN-3")]))
    )

    references = set(SourceArtifact.objects.values_list("external_reference", flat=True))
    assert references == {PUBLIC_EXTERNAL_REFERENCE}
    assert SourceArtifact.objects.count() == 2


def test_a_failed_run_followed_by_a_corrected_workbook_succeeds(make_workbook):
    broken = make_workbook(control_overrides={"total_record_count": 999})
    assert synchronize_public_workbook(downloader=FakeDownloader(broken)).result == (
        SyncResult.FAILED
    )

    outcome = synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))

    assert outcome.result == SyncResult.IMPORTED
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1


# -- dry runs -----------------------------------------------------------


def test_a_dry_run_publishes_nothing(make_workbook):
    outcome = synchronize_public_workbook(downloader=FakeDownloader(make_workbook()), dry_run=True)

    assert outcome.dry_run is True
    assert outcome.result == SyncResult.IMPORTED
    assert LegalWorkSnapshot.objects.count() == 0
    state = feed_state()
    assert state.last_checked_at is not None
    assert state.current_snapshot_id is None
    assert state.last_result == SyncResult.NEVER_RUN


def test_a_dry_run_then_a_live_run_of_unchanged_bytes_succeeds(make_workbook):
    """The sequence an operator actually performs during acceptance."""
    path = make_workbook()

    dry = synchronize_public_workbook(downloader=FakeDownloader(path), dry_run=True)
    live = synchronize_public_workbook(downloader=FakeDownloader(path))

    assert dry.dry_run is True and dry.result == SyncResult.IMPORTED
    assert live.result == SyncResult.IMPORTED
    assert live.rows_imported == 3
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1


def test_a_dry_run_does_not_replace_published_data(make_workbook):
    synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))
    published = LegalWorkSnapshot.objects.get()

    synchronize_public_workbook(
        downloader=FakeDownloader(make_workbook(rows=[synthetic_row(record_id="SYN-OTHER")])),
        dry_run=True,
    )

    published.refresh_from_db()
    assert published.is_current is True
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1


def test_a_dry_run_over_already_imported_content_records_nothing(make_workbook):
    path = make_workbook()
    synchronize_public_workbook(downloader=FakeDownloader(path))
    before = feed_state()

    outcome = synchronize_public_workbook(downloader=FakeDownloader(path), dry_run=True)

    after = feed_state()
    assert outcome.result == SyncResult.UNCHANGED
    assert outcome.dry_run is True
    assert after.last_result == before.last_result == SyncResult.IMPORTED
    assert after.current_snapshot_id == before.current_snapshot_id


# -- failure containment ------------------------------------------------


def test_an_invalid_changed_workbook_preserves_the_previous_snapshot(make_workbook):
    synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))
    good = LegalWorkSnapshot.objects.get()

    broken = make_workbook(control_overrides={"total_record_count": 999})
    outcome = synchronize_public_workbook(downloader=FakeDownloader(broken))

    assert outcome.result == SyncResult.FAILED
    good.refresh_from_db()
    assert good.is_current is True
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1
    assert feed_state().current_snapshot_id == good.pk


def test_a_download_failure_is_recorded_as_failed(make_workbook):
    downloader = FakeDownloader(error=PublicDownloadError("Jagamislink ei ole kättesaadav (404)."))

    outcome = synchronize_public_workbook(downloader=downloader)

    assert outcome.result == SyncResult.FAILED
    assert "404" in outcome.detail
    assert LegalWorkSnapshot.objects.count() == 0
    assert SourceArtifact.objects.count() == 0
    assert feed_state().last_result == SyncResult.FAILED


def test_an_unexpected_downloader_exception_is_contained(make_workbook):
    downloader = FakeDownloader(error=RuntimeError("synthetic unexpected failure"))

    outcome = synchronize_public_workbook(downloader=downloader)

    assert outcome.result == SyncResult.FAILED
    assert "RuntimeError" in outcome.detail


def test_the_temporary_file_is_removed_after_a_failure(make_workbook):
    broken = make_workbook(control_overrides={"total_record_count": 999})
    downloader = FakeDownloader(broken)

    synchronize_public_workbook(downloader=downloader)

    temporary = downloader.destinations[0]
    assert not temporary.exists()
    assert not temporary.parent.exists()


def test_the_error_summary_is_bounded_and_free_of_workbook_content(make_workbook):
    secret = "Sünteetiline salajane pealkiri"
    broken = make_workbook(rows=[synthetic_row(record_id="SYN-1", topic=secret, is_open="jah")])

    synchronize_public_workbook(downloader=FakeDownloader(broken))

    summary = feed_state().last_error_summary
    assert summary
    assert secret not in summary
    assert len(summary) <= 500


# -- feed state ---------------------------------------------------------


def test_feed_state_after_an_import(make_workbook):
    path = make_workbook()

    synchronize_public_workbook(downloader=FakeDownloader(path))

    state = feed_state()
    assert state.last_result == SyncResult.IMPORTED
    assert state.last_checked_at is not None
    assert state.last_successful_sync_at is not None
    assert state.last_changed_at is not None
    assert state.last_error_summary == ""
    assert state.remote_size_bytes == path.stat().st_size
    assert state.current_snapshot_id == LegalWorkSnapshot.objects.get().pk
    # This route has no trustworthy non-secret value for either, and the
    # checksum belongs on the artifact rather than in an etag field.
    assert state.remote_etag == ""
    assert state.remote_modified_at is None


def test_feed_state_after_an_unchanged_run(make_workbook):
    path = make_workbook()
    synchronize_public_workbook(downloader=FakeDownloader(path))

    synchronize_public_workbook(downloader=FakeDownloader(path))

    state = feed_state()
    assert state.last_result == SyncResult.UNCHANGED
    assert state.last_error_summary == ""
    assert state.remote_size_bytes == path.stat().st_size
    assert state.current_snapshot_id is not None
    assert state.remote_etag == ""


def test_feed_state_after_a_failure_keeps_the_previous_snapshot(make_workbook):
    synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))
    published = LegalWorkSnapshot.objects.get()

    synchronize_public_workbook(
        downloader=FakeDownloader(error=PublicDownloadError("Allalaadimine katkes."))
    )

    state = feed_state()
    assert state.last_result == SyncResult.FAILED
    assert state.last_error_summary
    assert state.current_snapshot_id == published.pk


# -- audit --------------------------------------------------------------


def test_audit_events_contain_no_url_and_only_safe_facts(make_workbook):
    path = make_workbook()
    synchronize_public_workbook(downloader=FakeDownloader(path))

    summaries = " ".join(str(event.change_summary) for event in AuditEvent.objects.all())
    assert SECRET_MARKER not in summaries
    assert "sharepoint.com" not in summaries
    assert PUBLIC_URL not in summaries

    registration = AuditEvent.objects.get(
        action=AuditAction.ARTIFACT_REGISTERED,
    )
    assert registration.change_summary["external_reference"] == PUBLIC_EXTERNAL_REFERENCE
    assert registration.change_summary["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_unchanged_audit_event_carries_the_checksum_not_the_url(make_workbook):
    path = make_workbook()
    synchronize_public_workbook(downloader=FakeDownloader(path))

    synchronize_public_workbook(downloader=FakeDownloader(path))

    event = AuditEvent.objects.get(action=AuditAction.LEGAL_WORK_SYNC_UNCHANGED)
    assert event.change_summary["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert SECRET_MARKER not in str(event.change_summary)


def test_a_failure_audit_event_carries_no_url(make_workbook):
    synchronize_public_workbook(
        downloader=FakeDownloader(error=PublicDownloadError("Jagamislink keeldus (403)."))
    )

    event = AuditEvent.objects.get(action=AuditAction.LEGAL_WORK_SYNC_FAILED)
    assert SECRET_MARKER not in str(event.change_summary)
    assert "403" in str(event.change_summary)


def test_one_correlation_id_threads_the_whole_run(make_workbook):
    result = synchronize_public_workbook(downloader=FakeDownloader(make_workbook()))

    run = ImportRun.objects.get()
    actions = set(
        AuditEvent.objects.filter(correlation_id=run.correlation_id).values_list(
            "action", flat=True
        )
    )
    assert result.result == SyncResult.IMPORTED
    assert AuditAction.ARTIFACT_REGISTERED in actions
    assert AuditAction.LEGAL_WORK_SNAPSHOT_IMPORTED in actions
    assert AuditAction.LEGAL_WORK_SNAPSHOT_PUBLISHED in actions


# -- command ------------------------------------------------------------


def run_command(*arguments, downloader, monkeypatch):
    from apps.legal_work.management.commands import sync_oigusloome_public as command_module

    monkeypatch.setattr(
        command_module,
        "synchronize_public_workbook",
        lambda **kwargs: synchronize_public_workbook(downloader=downloader, **kwargs),
    )
    output = StringIO()
    call_command("sync_oigusloome_public", *arguments, stdout=output, stderr=output)
    return output.getvalue()


def test_json_output_is_one_line_and_free_of_secrets(make_workbook, monkeypatch):
    path = make_workbook()
    output = run_command("--json", downloader=FakeDownloader(path), monkeypatch=monkeypatch)

    lines = output.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["result"] == SyncResult.IMPORTED
    assert payload["rows_imported"] == 3
    assert payload["dry_run"] is False
    assert set(payload) == {
        "result",
        "detail",
        "snapshot_id",
        "reporting_date",
        "rows_imported",
        "dry_run",
        "warnings",
    }
    for forbidden in (SECRET_MARKER, "sharepoint", "download=1", "Bearer", str(path.parent)):
        assert forbidden not in lines[0]


def test_json_output_of_a_dry_run_declares_itself(make_workbook, monkeypatch):
    output = run_command(
        "--dry-run", "--json", downloader=FakeDownloader(make_workbook()), monkeypatch=monkeypatch
    )

    payload = json.loads(output.strip())
    assert payload["dry_run"] is True
    assert payload["snapshot_id"] is None
    assert LegalWorkSnapshot.objects.count() == 0


def test_an_unchanged_run_exits_zero(make_workbook, monkeypatch):
    path = make_workbook()
    run_command("--json", downloader=FakeDownloader(path), monkeypatch=monkeypatch)

    output = run_command("--json", downloader=FakeDownloader(path), monkeypatch=monkeypatch)

    assert json.loads(output.strip())["result"] == SyncResult.UNCHANGED


def test_a_failed_run_exits_one(make_workbook, monkeypatch):
    downloader = FakeDownloader(error=PublicDownloadError("Jagamislink ei ole kättesaadav (404)."))

    with pytest.raises(SystemExit) as exit_info:
        run_command("--json", downloader=downloader, monkeypatch=monkeypatch)

    assert exit_info.value.code == 1


def test_a_missing_url_fails_the_command_naming_only_the_variable(settings):
    settings.OIGUSLOOME_PUBLIC_URL = ""
    output = StringIO()

    with pytest.raises(SystemExit) as exit_info:
        call_command("sync_oigusloome_public", stdout=output, stderr=output)

    assert exit_info.value.code == 1
    assert "OIGUSLOOME_PUBLIC_URL" in output.getvalue()


def test_the_command_offers_no_url_or_force_option():
    """The URL must never be able to reach shell history or a process listing."""
    from apps.legal_work.management.commands.sync_oigusloome_public import Command

    parser = Command().create_parser("manage.py", "sync_oigusloome_public")
    options = {action.dest for action in parser._actions}
    assert "url" not in options
    assert "force" not in options
    assert {"dry_run", "as_json"} <= options


@pytest.mark.django_db(transaction=True)
def test_an_overlapping_run_exits_three():
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    def run_locked_command():
        close_old_connections()
        try:
            with pytest.raises(SystemExit) as exit_info:
                call_command("sync_oigusloome_public", "--json", stdout=StringIO())
            return exit_info.value.code
        except SyncLocked:  # pragma: no cover - defensive
            return EXIT_LOCKED
        finally:
            close_old_connections()

    with advisory_lock():
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(run_locked_command).result() == EXIT_LOCKED
