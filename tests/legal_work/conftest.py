import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.core.files import File

from apps.legal_work.bootstrap import ensure_legal_work_source
from apps.legal_work.importer import import_artifact
from apps.sources.services import register_artifact

from .workbook_factory import write_workbook

WORKBOOK_NAME = "dashkoda_oigusloome.xlsx"


@pytest.fixture
def legal_work_source(db):
    return ensure_legal_work_source()


@pytest.fixture
def superuser(db):
    return get_user_model().objects.create_superuser(
        username="synthetic-legal-root",
        password="synthetic-test-password",
    )


@pytest.fixture
def make_workbook(tmp_path):
    """Write a synthetic workbook and return its path."""
    counter = {"index": 0}

    def build(**kwargs):
        counter["index"] += 1
        path = tmp_path / f"synthetic-{counter['index']}.xlsx"
        return write_workbook(path, **kwargs)

    return build


@pytest.fixture
def register_workbook(legal_work_source):
    """Register a workbook file as an immutable private artifact."""

    def register(path, *, source=None):
        with path.open("rb") as handle:
            return register_artifact(
                source=source or legal_work_source,
                upload=File(handle, name=WORKBOOK_NAME),
                original_name=WORKBOOK_NAME,
                mime_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            )

    return register


@pytest.fixture
def imported_snapshot(make_workbook, register_workbook):
    """A published current snapshot built from the default synthetic rows."""
    artifact = register_workbook(make_workbook())
    return import_artifact(artifact, dry_run=False).snapshot


@pytest.fixture
def current_topics_source(db):
    from apps.legal_work.bootstrap import ensure_current_topics_source

    return ensure_current_topics_source()


@pytest.fixture
def publish_current_topics(current_topics_source, monkeypatch):
    """Publish a synthetic current-topic catalogue through the real sync path.

    Goes through `synchronize_current_topics` rather than writing rows directly,
    so every test that needs a catalogue also proves the publication path still
    produces one: the artifact, the import run and the `is_current` flag are all
    real.
    """
    from apps.legal_work.current_topic_sync import synchronize_current_topics
    from apps.legal_work.current_topics import collect_current_topics

    from .current_topic_factory import FakeSite

    def publish(site: FakeSite, *, dry_run: bool = False):
        monkeypatch.setattr("apps.legal_work.current_topics.fetch", site)
        return synchronize_current_topics(dry_run=dry_run, collector=collect_current_topics)

    return publish


@pytest.fixture
def archived_topics_source(db):
    from apps.legal_work.archive_bootstrap import ensure_archive_source

    return ensure_archive_source()


@pytest.fixture
def publish_archived_topics(archived_topics_source, monkeypatch, settings):
    """Publish a synthetic archive catalogue through the real sync path.

    Goes through `synchronize_archived_topics` rather than writing rows, so every
    test that needs an archive also proves the publication path still produces
    one — index, hydration, artifact, import run and `is_current` all real.
    """
    from apps.legal_work.archived_topic_sync import synchronize_archived_topics

    settings.KODA_ARCHIVE_REQUEST_PAUSE_SECONDS = 0

    def publish(site, *, dry_run=False, full=True, max_detail_pages=None):
        monkeypatch.setattr("apps.legal_work.archived_topics.fetch", site)
        return synchronize_archived_topics(
            dry_run=dry_run, full=full, max_detail_pages=max_detail_pages
        )

    return publish


@pytest.fixture
def frozen_today(monkeypatch):
    """Pin `timezone.localdate` so future-date rules are deterministic."""

    def pin(value: dt.date):
        from apps.legal_work import selectors

        monkeypatch.setattr(selectors.timezone, "localdate", lambda: value)

    return pin


@pytest.fixture
def opinion_roots(settings, tmp_path):
    """Point the source and store roots at temporary directories.

    Every opinion test runs against real filesystem behaviour — atomic renames,
    resolution under a root, a genuine ZIP — rather than a mocked store, because
    the properties being tested are filesystem properties.
    """
    source = tmp_path / "source"
    store = tmp_path / "store"
    source.mkdir()
    store.mkdir()
    settings.LEGAL_OPINION_SOURCE_ROOT = str(source)
    settings.LEGAL_OPINION_STORE_ROOT = str(store)
    settings.LEGAL_OPINION_BOOTSTRAP_ZIP_NAME = "Opinions.zip"
    # A file is normally ignored until it has stopped changing; tests write and
    # read in the same instant, so the wait is switched off except where the
    # stability rule is what is under test.
    settings.LEGAL_OPINION_MIN_STABLE_AGE_SECONDS = 0
    return source, store


@pytest.fixture
def opinion_source(db):
    from apps.legal_work.opinion_bootstrap import ensure_opinion_source

    return ensure_opinion_source()
