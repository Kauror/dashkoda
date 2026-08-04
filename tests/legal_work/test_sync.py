"""Synchronisation: idempotency, locking, failure containment and output."""

import datetime as dt
import json
from dataclasses import replace
from io import StringIO

import pytest
from django.core.management import call_command

from apps.legal_work.graph import GraphError, RemoteFile
from apps.legal_work.models import LegalWorkFeedState, LegalWorkSnapshot, SyncResult
from apps.legal_work.sync import EXIT_LOCKED, SyncLocked, advisory_lock, synchronize

from .workbook_factory import synthetic_row

pytestmark = pytest.mark.django_db

REMOTE = RemoteFile(
    item_id="synthetic-item",
    name="dashkoda_oigusloome.xlsx",
    size_bytes=1234,
    etag="synthetic-ctag-1",
    modified_at=dt.datetime(2099, 3, 1, 6, 0, tzinfo=dt.UTC),
    drive_id="synthetic-drive",
)


class FakeGraphClient:
    """Serves a local synthetic workbook instead of contacting OneDrive."""

    def __init__(self, path, *, remote=REMOTE, metadata_error=None, download_error=None):
        self.path = path
        self.remote = remote
        self.metadata_error = metadata_error
        self.download_error = download_error
        self.downloads = 0

    def get_item_metadata(self):
        if self.metadata_error:
            raise self.metadata_error
        return self.remote

    def download_to(self, destination):
        if self.download_error:
            raise self.download_error
        self.downloads += 1
        destination.write_bytes(self.path.read_bytes())
        return destination.stat().st_size


def feed_state():
    return LegalWorkFeedState.objects.get()


# -- first import -------------------------------------------------------


def test_first_synchronisation_imports_and_publishes(make_workbook):
    client = FakeGraphClient(make_workbook())

    outcome = synchronize(client=client)

    assert outcome.result == SyncResult.IMPORTED
    assert outcome.rows_imported == 3
    snapshot = LegalWorkSnapshot.objects.get()
    assert snapshot.is_current is True

    state = feed_state()
    assert state.last_result == SyncResult.IMPORTED
    assert state.last_successful_sync_at is not None
    assert state.current_snapshot_id == snapshot.pk
    assert state.remote_etag == "synthetic-ctag-1"


def test_an_immediately_repeated_run_reports_unchanged(make_workbook):
    client = FakeGraphClient(make_workbook())
    synchronize(client=client)

    outcome = synchronize(client=client)

    assert outcome.result == SyncResult.UNCHANGED
    assert LegalWorkSnapshot.objects.count() == 1
    assert client.downloads == 1
    assert feed_state().last_result == SyncResult.UNCHANGED


def test_identical_content_under_a_new_etag_does_not_duplicate_the_snapshot(make_workbook):
    path = make_workbook()
    synchronize(client=FakeGraphClient(path))

    # The remote metadata moved, but the bytes did not: the checksum decides.
    moved = FakeGraphClient(path, remote=replace(REMOTE, etag="new"))
    outcome = synchronize(client=moved)

    assert outcome.result == SyncResult.UNCHANGED
    assert LegalWorkSnapshot.objects.count() == 1


def test_changed_content_creates_a_new_artifact_and_snapshot(make_workbook):
    first = make_workbook()
    synchronize(client=FakeGraphClient(first))

    second = make_workbook(rows=[synthetic_row(record_id="SYN-NEW", source_row=2)])
    outcome = synchronize(client=FakeGraphClient(second, remote=replace(REMOTE, etag="ctag-2")))

    assert outcome.result == SyncResult.IMPORTED
    assert LegalWorkSnapshot.objects.count() == 2
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1
    assert LegalWorkSnapshot.objects.get(is_current=True).total_record_count == 1


# -- failure containment ------------------------------------------------


def test_an_invalid_changed_workbook_preserves_the_previous_snapshot(make_workbook):
    synchronize(client=FakeGraphClient(make_workbook()))
    good = LegalWorkSnapshot.objects.get()

    broken = make_workbook(control_overrides={"total_record_count": 999})
    outcome = synchronize(client=FakeGraphClient(broken, remote=replace(REMOTE, etag="ctag-2")))

    assert outcome.result == SyncResult.FAILED
    good.refresh_from_db()
    assert good.is_current is True
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1

    state = feed_state()
    assert state.last_result == SyncResult.FAILED
    assert state.last_error_summary
    assert state.current_snapshot_id == good.pk


def test_a_metadata_failure_is_recorded_without_touching_data(make_workbook):
    synchronize(client=FakeGraphClient(make_workbook()))
    good = LegalWorkSnapshot.objects.get()

    outcome = synchronize(
        client=FakeGraphClient(make_workbook(), metadata_error=GraphError("Graph ei vastanud."))
    )

    assert outcome.result == SyncResult.FAILED
    good.refresh_from_db()
    assert good.is_current is True


def test_a_download_failure_is_recorded_as_failed(make_workbook):
    outcome = synchronize(
        client=FakeGraphClient(make_workbook(), download_error=GraphError("Allalaadimine katkes."))
    )

    assert outcome.result == SyncResult.FAILED
    assert LegalWorkSnapshot.objects.count() == 0
    assert feed_state().last_result == SyncResult.FAILED


def test_the_error_summary_is_bounded_and_free_of_workbook_content(make_workbook):
    secret = "Sünteetiline salajane pealkiri"
    broken = make_workbook(rows=[synthetic_row(record_id="SYN-1", topic=secret, is_open="jah")])

    synchronize(client=FakeGraphClient(broken))

    summary = feed_state().last_error_summary
    assert secret not in summary
    assert len(summary) <= 500


# -- dry run and force --------------------------------------------------


def test_a_dry_run_publishes_nothing(make_workbook):
    outcome = synchronize(client=FakeGraphClient(make_workbook()), dry_run=True)

    assert outcome.dry_run is True
    assert LegalWorkSnapshot.objects.count() == 0
    state = feed_state()
    assert state.last_checked_at is not None
    assert state.current_snapshot_id is None


def test_a_dry_run_does_not_replace_published_data(make_workbook):
    path = make_workbook()
    synchronize(client=FakeGraphClient(path))
    published = LegalWorkSnapshot.objects.get()

    synchronize(
        client=FakeGraphClient(
            make_workbook(rows=[synthetic_row(record_id="SYN-OTHER", source_row=2)]),
            remote=replace(REMOTE, etag="ctag-2"),
        ),
        dry_run=True,
    )

    published.refresh_from_db()
    assert published.is_current is True
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1


def test_a_dry_run_does_not_block_the_later_live_import(make_workbook):
    """Regression: a dry run registers the workbook's artifact, and the live
    run of the same bytes must reuse it. It used to try to register a second
    artifact with the same checksum, fail the uniqueness rule and record the
    run as FAILED — so a validated workbook could never be published."""
    path = make_workbook()
    synchronize(client=FakeGraphClient(path), dry_run=True)

    outcome = synchronize(client=FakeGraphClient(path))

    assert outcome.result == SyncResult.IMPORTED
    assert LegalWorkSnapshot.objects.get().is_current is True
    assert feed_state().last_result == SyncResult.IMPORTED


def test_force_reimports_even_when_the_remote_looks_unchanged(make_workbook):
    path = make_workbook()
    client = FakeGraphClient(path)
    synchronize(client=client)

    outcome = synchronize(client=client, force=True)

    # Same bytes, so the registry refuses a second successful live import and
    # the previous snapshot survives untouched.
    assert outcome.result == SyncResult.FAILED
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1


# -- locking ------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_an_overlapping_run_is_refused_across_connections():
    """The guarantee has to hold between processes, not just inside one."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    def try_to_lock():
        close_old_connections()
        try:
            with advisory_lock():
                return "acquired"
        except SyncLocked:
            return "refused"
        finally:
            close_old_connections()

    with advisory_lock():
        # A separate connection must not be able to take the same lock.
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(try_to_lock).result() == "refused"

    # Once released, the next run may take it.
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(try_to_lock).result() == "acquired"


# -- command output -----------------------------------------------------


def test_json_output_is_one_line_and_free_of_secrets(make_workbook, monkeypatch):
    from apps.legal_work.management.commands import sync_oigusloome as command_module

    client = FakeGraphClient(make_workbook())
    monkeypatch.setattr(
        command_module,
        "synchronize",
        lambda **kwargs: synchronize(client=client, **kwargs),
    )

    output = StringIO()
    call_command("sync_oigusloome", "--json", stdout=output)

    lines = output.getvalue().strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["result"] == SyncResult.IMPORTED
    assert payload["rows_imported"] == 3
    for secret in ("synthetic-not-a-real-secret", "access_token", "Bearer"):
        assert secret not in lines[0]


def test_missing_graph_configuration_fails_the_command_clearly(settings):
    settings.MS_GRAPH_CLIENT_SECRET = ""
    output = StringIO()

    with pytest.raises(SystemExit) as exit_info:
        call_command("sync_oigusloome", stdout=output, stderr=output)

    assert exit_info.value.code == 1
    assert "MS_GRAPH_CLIENT_SECRET" in output.getvalue()


def test_the_locked_exit_code_is_distinct():
    assert EXIT_LOCKED == 3
