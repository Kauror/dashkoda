"""The orchestration two workbook feeds now share, and the line it must not cross.

`legal_work/public_sync.py` and `event_programme/sync.py` had written the same
sequence out twice: temporary directory, download, classify the download's
failure, look up the checksum, register the artifact, import, classify the
import's failure. That is transport and bookkeeping — the same for any workbook,
because it is about files and import runs rather than about legal records or
events.

It is shared now. What these tests hold is that it stayed *mechanical*: the
feeds keep their own outcome types, their own feed-state rows, their own audit
actions and their own view of which exception is an operator's mistake. The two
feeds' externally observable behaviour is covered by their own suites, which
were written before this refactor and are unchanged by it — 62 tests across
`tests/legal_work/test_public_sync.py` and `tests/event_programme/test_sync.py`.
"""

from __future__ import annotations

import pathlib

import pytest

from apps.sources.public_download import (
    PublicDownload,
    PublicDownloadError,
    PublicUrlNotConfigured,
)
from apps.sources.workbook_sync import (
    ATTEMPT_FAILED,
    ATTEMPT_IMPORTED,
    ATTEMPT_UNCHANGED,
    STAGE_DOWNLOAD,
    STAGE_IMPORT,
    WorkbookFeed,
    attempt_workbook_sync,
)

FEED = WorkbookFeed(
    importer_name="synthetic_importer",
    filename="synthetic.xlsx",
    external_reference="synthetic:workbook",
    temp_prefix="dashkoda-test-workbook-",
)


def download_at(destination) -> PublicDownload:
    destination.write_bytes(b"synthetic workbook bytes")
    return PublicDownload(
        path=destination,
        size_bytes=24,
        sha256="a" * 64,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        final_host="synthetic.invalid",
    )


class Result:
    """Stands in for an importer's own result. Its shape is the feed's."""

    rows_added = 7


def run(monkeypatch, *, fetch, import_artifact=None, already_published=False, artifact=object()):
    """Drive the orchestration with both seams faked and no database."""
    from apps.sources import workbook_sync

    monkeypatch.setattr(
        workbook_sync, "find_published_artifact", lambda *a, **k: (None, already_published)
    )
    monkeypatch.setattr(workbook_sync, "register_external_reference", lambda **k: artifact)

    return attempt_workbook_sync(
        feed=FEED,
        source=object(),
        fetch=fetch,
        import_artifact=import_artifact or (lambda *a, **k: Result()),
        dry_run=False,
        allow_collapse=False,
        actor=None,
        correlation_id="synthetic",
    )


class TestTheThreeOutcomes:
    def test_a_new_workbook_imports(self, monkeypatch):
        attempt = run(monkeypatch, fetch=download_at)

        assert attempt.status == ATTEMPT_IMPORTED
        assert attempt.result.rows_added == 7
        assert attempt.download.sha256 == "a" * 64
        assert attempt.failed is False

    def test_already_published_content_is_unchanged(self, monkeypatch):
        attempt = run(monkeypatch, fetch=download_at, already_published=True)

        assert attempt.status == ATTEMPT_UNCHANGED
        assert attempt.result is None, "nothing was imported"
        assert attempt.download is not None, "the caller still needs the checksum"

    def test_a_download_failure_names_the_download_stage(self, monkeypatch):
        def fetch(destination):
            raise PublicDownloadError("Allikas vastas koodiga 503.")

        attempt = run(monkeypatch, fetch=fetch)

        assert (attempt.status, attempt.stage) == (ATTEMPT_FAILED, STAGE_DOWNLOAD)
        assert attempt.message == "Allikas vastas koodiga 503."

    def test_an_import_failure_names_the_import_stage(self, monkeypatch):
        def importer(*args, **kwargs):
            raise ValueError("Toetamata skeemi versioon '9.9'.")

        attempt = run(monkeypatch, fetch=download_at, import_artifact=importer)

        assert (attempt.status, attempt.stage) == (ATTEMPT_FAILED, STAGE_IMPORT)
        assert "Toetamata skeemi versioon" in attempt.message

    def test_the_two_stages_are_distinguishable(self, monkeypatch):
        """ "Unreachable" and "arrived but would not import" are different faults."""
        assert STAGE_DOWNLOAD != STAGE_IMPORT


class TestNothingEscapesAsATraceback:
    """This runs unattended every morning. A traceback in cron helps nobody."""

    def test_an_unexpected_download_error_is_recorded(self, monkeypatch):
        def fetch(destination):
            raise RuntimeError("something nobody anticipated")

        attempt = run(monkeypatch, fetch=fetch)

        assert attempt.status == ATTEMPT_FAILED
        assert attempt.message.startswith("RuntimeError:")

    def test_an_unexpected_import_error_is_recorded(self, monkeypatch):
        def importer(*args, **kwargs):
            raise KeyError("missing_column")

        attempt = run(monkeypatch, fetch=download_at, import_artifact=importer)

        assert attempt.status == ATTEMPT_FAILED
        assert attempt.message.startswith("KeyError:")

    def test_a_multiline_message_becomes_one_line(self, monkeypatch):
        def fetch(destination):
            raise RuntimeError("first line\nsecond line")

        assert "\n" not in run(monkeypatch, fetch=fetch).message


class TestTheFailureDomainPolicyStaysWithTheFeed:
    def test_an_unset_url_is_re_raised_rather_than_classified(self, monkeypatch):
        """An operator's mistake is not a synchronisation failure.

        The command reports it as plain text naming the missing variable. If
        this layer swallowed it, the feed state would record a failure as though
        the remote had misbehaved.
        """

        def fetch(destination):
            raise PublicUrlNotConfigured("Puudub keskkonnamuutuja: OIGUSLOOME_PUBLIC_URL.")

        with pytest.raises(PublicUrlNotConfigured, match="OIGUSLOOME_PUBLIC_URL"):
            run(monkeypatch, fetch=fetch)


class TestTheWorkbookNeverOutlivesTheRun:
    @pytest.mark.parametrize(
        "fetch_raises", [None, PublicDownloadError("failed"), RuntimeError("unexpected")]
    )
    def test_the_temporary_directory_is_removed_on_every_path(self, monkeypatch, fetch_raises):
        seen: dict[str, pathlib.Path] = {}

        def fetch(destination):
            seen["directory"] = destination.parent
            if fetch_raises is not None:
                raise fetch_raises
            return download_at(destination)

        run(monkeypatch, fetch=fetch)

        assert not seen["directory"].exists(), "the workbook outlived the run"

    def test_it_is_removed_after_an_import_failure_too(self, monkeypatch):
        seen: dict[str, pathlib.Path] = {}

        def fetch(destination):
            seen["directory"] = destination.parent
            return download_at(destination)

        def importer(*args, **kwargs):
            raise ValueError("no")

        run(monkeypatch, fetch=fetch, import_artifact=importer)

        assert not seen["directory"].exists()

    def test_the_importer_reads_the_file_while_it_still_exists(self, monkeypatch):
        """The import has to happen inside the directory's lifetime."""
        seen = {}

        def importer(artifact, *, workbook_path, **kwargs):
            seen["existed"] = workbook_path.exists()
            return Result()

        run(monkeypatch, fetch=download_at, import_artifact=importer)

        assert seen["existed"] is True


class TestTheBoundary:
    """What the shared layer is allowed to know about.

    The brief's line: mechanical consolidation is wanted, failure-domain
    coupling is not.
    """

    def test_it_imports_no_domain_module(self):
        import ast
        import inspect

        from apps.sources import workbook_sync

        imported = set()
        for node in ast.walk(ast.parse(inspect.getsource(workbook_sync))):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for domain in ("legal_work", "event_programme", "visibility", "membership"):
            assert not any(domain in name for name in imported), (
                f"the shared layer imports {domain}; it must know about no feed"
            )

    def test_it_records_no_state_and_no_audit(self):
        import inspect

        from apps.sources import workbook_sync

        source = inspect.getsource(workbook_sync)
        for forbidden in ("record_event", "AuditAction", "FeedState", "last_checked_at", "save("):
            assert forbidden not in source, (
                f"{forbidden} is the feed's business, not the orchestration's"
            )

    def test_each_feed_keeps_its_own_outcome_type(self):
        """A shared outcome would have to carry both feeds' fields."""
        from apps.event_programme.sync import SyncOutcome as EventOutcome
        from apps.legal_work.sync import SyncOutcome as LegalOutcome

        legal = {f.name for f in LegalOutcome.__dataclass_fields__.values()}
        event = {f.name for f in EventOutcome.__dataclass_fields__.values()}

        assert LegalOutcome is not EventOutcome
        assert "reporting_date" in legal
        assert "export_refreshed_at" in event
        assert legal != event

    def test_each_feed_describes_itself(self):
        from apps.event_programme.sync import WORKBOOK_FEED as EVENT
        from apps.legal_work.public_sync import WORKBOOK_FEED as LEGAL

        assert LEGAL.importer_name != EVENT.importer_name
        assert LEGAL.filename != EVENT.filename
        assert LEGAL.external_reference != EVENT.external_reference
        assert LEGAL.temp_prefix != EVENT.temp_prefix

    def test_no_feed_still_carries_its_own_copy_of_the_sequence(self):
        import inspect

        from apps.event_programme import sync as event_sync
        from apps.legal_work import public_sync as legal_sync

        for module in (legal_sync, event_sync):
            source = inspect.getsource(module)
            assert "tempfile.TemporaryDirectory" not in source, "a second copy is back"
            assert "find_published_artifact" not in source, "a second copy is back"

    def test_neither_feed_names_a_sharing_url(self):
        """The external reference is a fixed label, never the URL."""
        from apps.event_programme.sync import WORKBOOK_FEED as EVENT
        from apps.legal_work.public_sync import WORKBOOK_FEED as LEGAL

        for feed in (LEGAL, EVENT):
            assert "http" not in feed.external_reference
            assert "?" not in feed.external_reference
