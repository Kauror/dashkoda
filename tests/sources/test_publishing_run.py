"""Closing an import run after a failed publication.

The rule these cover is not obvious from the call site, which is why it was got
wrong three times by hand: publication happens inside a transaction, so by the
time a handler runs the database has already rolled back and the in-memory run
may describe a state that was never committed.
"""

import pytest

from apps.sources.models import ImportRun, ImportStatus
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    fail_publication,
    publishing_run,
    register_artifact,
    start_import_run,
)

pytestmark = pytest.mark.django_db

IMPORTER = "synthetic-importer"
SCHEMA = "v1"


@pytest.fixture
def artifact(data_source, upload):
    return register_artifact(source=data_source, upload=upload())


@pytest.fixture
def started_run(artifact):
    run = build_import_run(
        artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA, dry_run=False
    )
    start_import_run(run)
    return run


def test_a_failed_publication_closes_the_run_and_lets_the_error_out(started_run):
    with pytest.raises(ValueError, match="publication failed"):
        with publishing_run(started_run, actor=None):
            raise ValueError("publication failed")

    started_run.refresh_from_db()
    assert started_run.status == ImportStatus.FAILED
    assert started_run.errors == [{"type": "ValueError"}]


def test_a_stale_success_in_memory_does_not_bury_the_real_error(started_run):
    """The failure this exists for.

    A publisher whose last statement inside the atomic block is
    `complete_import_run` holds a run marked succeeded, while the rolled-back
    row is still running. Failing it without re-reading asks for a
    `succeeded -> failed` transition, the state machine refuses, and
    `InvalidImportTransition` replaces the error that actually happened.
    """
    started_run.status = ImportStatus.SUCCEEDED  # never committed

    with pytest.raises(RuntimeError, match="the real failure"):
        with publishing_run(started_run, actor=None):
            raise RuntimeError("the real failure")

    started_run.refresh_from_db()
    assert started_run.status == ImportStatus.FAILED


def test_a_run_already_closed_in_the_database_is_left_alone(started_run):
    """Failing twice is the same illegal transition, so it must not be tried."""
    complete_import_run(started_run, rows_added=1)

    with pytest.raises(ValueError):
        with publishing_run(started_run, actor=None):
            raise ValueError("after the run was completed")

    started_run.refresh_from_db()
    assert started_run.status == ImportStatus.SUCCEEDED
    assert started_run.errors == []


def test_a_fixed_error_payload_is_recorded_as_given(started_run):
    with pytest.raises(ValueError):
        with publishing_run(started_run, errors=[{"type": "publication_failed"}], actor=None):
            raise ValueError("boom")

    started_run.refresh_from_db()
    assert started_run.errors == [{"type": "publication_failed"}]


def test_a_callable_payload_is_given_the_exception(started_run):
    """How a caller records a sanitized error without this module knowing how."""

    def sanitize(error):
        return [{"type": type(error).__name__, "detail": "redacted"}]

    with pytest.raises(FileNotFoundError):
        with publishing_run(started_run, errors=sanitize, actor=None):
            raise FileNotFoundError("/a/path/that/must/not/be/recorded")

    started_run.refresh_from_db()
    assert started_run.errors == [{"type": "FileNotFoundError", "detail": "redacted"}]
    assert "path" not in str(started_run.errors)


def test_a_successful_publication_is_untouched(started_run):
    with publishing_run(started_run, actor=None):
        complete_import_run(started_run, rows_added=3)

    started_run.refresh_from_db()
    assert started_run.status == ImportStatus.SUCCEEDED
    assert started_run.rows_added == 3


def test_fail_publication_applies_the_same_rule_without_a_context_manager(started_run):
    """The seam for callers that also translate the error or write their own audit."""
    started_run.status = ImportStatus.SUCCEEDED  # never committed

    fail_publication(started_run, errors=[{"type": "publication_failed"}], actor=None)

    started_run.refresh_from_db()
    assert started_run.status == ImportStatus.FAILED


def test_fail_publication_leaves_a_genuinely_closed_run_alone(started_run):
    complete_import_run(started_run, rows_added=1)

    fail_publication(started_run, errors=[{"type": "publication_failed"}], actor=None)

    started_run.refresh_from_db()
    assert started_run.status == ImportStatus.SUCCEEDED
    assert ImportRun.objects.filter(status=ImportStatus.FAILED).count() == 0
