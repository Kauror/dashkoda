import hashlib
from pathlib import Path

import pytest
from django.core.files import File

from apps.event_programme.bootstrap import ensure_event_programme_source
from apps.event_programme.public_download import XLSX_MIME_TYPE, PublicDownload
from apps.sources.services import register_artifact

from .workbook_factory import build_workbook

WORKBOOK_NAME = "dashkoda_events.xlsx"


@pytest.fixture
def event_programme_source(db):
    return ensure_event_programme_source()


@pytest.fixture
def make_workbook(tmp_path):
    """Write a synthetic workbook and return its path."""
    counter = {"index": 0}

    def build(**kwargs):
        counter["index"] += 1
        path = tmp_path / f"synthetic-{counter['index']}.xlsx"
        return build_workbook(path, **kwargs)

    return build


@pytest.fixture
def register_workbook(event_programme_source):
    """Register a workbook file as an immutable private artifact."""

    def register(path, *, source=None):
        with path.open("rb") as handle:
            return register_artifact(
                source=source or event_programme_source,
                upload=File(handle, name=WORKBOOK_NAME),
                original_name=WORKBOOK_NAME,
                mime_type=XLSX_MIME_TYPE,
            )

    return register


class FakeDownloader:
    """Copies a local synthetic workbook instead of contacting SharePoint.

    No test in this package ever performs a network request. The real collector
    is exercised only through its pure URL guards, which need no transport.
    """

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


@pytest.fixture
def fake_downloader():
    return FakeDownloader
