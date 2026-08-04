import datetime as dt
import hashlib
from pathlib import Path

import pytest
from django.core.files import File
from django.utils import timezone

from apps.event_programme.bootstrap import ensure_event_programme_source
from apps.event_programme.public_download import XLSX_MIME_TYPE, PublicDownload
from apps.sources.services import register_artifact

from .workbook_factory import build_workbook, synthetic_row

WORKBOOK_NAME = "dashkoda_events.xlsx"

# A synthetic public page on the allowed host, on an obviously non-production
# path. Nothing in this package links to a real Chamber event.
SYNTHETIC_URL = "https://www.koda.ee/et/sundmused/sunteetiline-programmi-sundmus"

# Two historical years plus rows placed relative to today. Derived from today
# rather than fixed, because these rows have to be genuinely in the past: an
# event dated 2099 and labelled `past` would make every history assertion a lie
# and would land in the forward window instead.
OLD_YEAR_OFFSET = 5
MID_YEAR_OFFSET = 3


def programme_years(today: dt.date | None = None) -> tuple[int, int]:
    """The two historical years the synthetic programme uses, older first."""
    today = today or timezone.localdate()
    return today.year - OLD_YEAR_OFFSET, today.year - MID_YEAR_OFFSET


def _at(date: dt.date) -> dt.datetime:
    """The workbook writes dates as Excel datetimes at midnight."""
    return dt.datetime.combine(date, dt.time())


def synthetic_programme(today: dt.date | None = None) -> list[dict]:
    """One synthetic programme covering every shape the page has to handle.

    Several years, several months, a quarter boundary, past, ongoing and upcoming
    events, a multi-day range, an undated record, three tags, two types, all three
    delivery modes, a linked and an unlinked title, and a review-required row.

    `source_year` is deliberately not the event's own year on the first row, so a
    test can prove period filtering reads `event_year` and not the annual sheet
    the operational workbook happened to hold the row on.
    """
    today = today or timezone.localdate()
    old_year, mid_year = programme_years(today)
    return [
        synthetic_row(
            event_id="EVENT-8001",
            service_code="8001",
            event_name="Sünteetiline veebruari seminar",
            start_date=_at(dt.date(old_year, 2, 10)),
            event_status="past",
            tag_key="seminar",
            tag_label="Sünteetiline seminar",
            event_type_key="training",
            event_type_label="Sünteetiline koolitusvorm",
            delivery_mode="onsite",
            public_url=SYNTHETIC_URL,
            public_link_status="linked_embedded_latest",
            # A different year from the event's own, so a test can prove the
            # period filters read `event_year` rather than the annual sheet.
            source_year=old_year - 1,
            source_sheet=f"KOOD {old_year - 1}",
            source_row=2,
        ),
        synthetic_row(
            event_id="EVENT-8002",
            service_code="8002",
            event_name="Sünteetiline mai konverents",
            start_date=_at(dt.date(old_year, 5, 20)),
            event_status="past",
            tag_key="konverents",
            tag_label="Sünteetiline konverents",
            event_type_key="conference",
            event_type_label="Sünteetiline konverentsivorm",
            delivery_mode="online",
            source_row=3,
        ),
        synthetic_row(
            event_id="EVENT-8003",
            service_code="8003",
            event_name="Sünteetiline mitmepäevane märtsi sündmus",
            start_date=_at(dt.date(mid_year, 3, 4)),
            end_date=_at(dt.date(mid_year, 3, 6)),
            event_status="past",
            tag_key="seminar",
            tag_label="Sünteetiline seminar",
            event_type_key="training",
            event_type_label="Sünteetiline koolitusvorm",
            delivery_mode="hybrid",
            date_parse_status="parsed_range",
            review_required=True,
            source_row=4,
        ),
        synthetic_row(
            event_id="EVENT-8004",
            service_code="8004",
            event_name="Sünteetiline kuupäevata sündmus",
            start_date=None,
            end_date=None,
            event_status="date_unknown",
            tag_key="koolitus",
            tag_label="Sünteetiline koolitus",
            event_type_key="training",
            event_type_label="Sünteetiline koolitusvorm",
            delivery_mode="onsite",
            include_status="REVIEW",
            date_parse_status="unparsed",
            review_required=True,
            warning_codes="date_unparsed",
            source_row=5,
        ),
        synthetic_row(
            event_id="EVENT-8005",
            service_code="8005",
            event_name="Sünteetiline tulev koolitus",
            start_date=_at(today + dt.timedelta(days=5)),
            event_status="upcoming",
            tag_key="koolitus",
            tag_label="Sünteetiline koolitus",
            event_type_key="training",
            event_type_label="Sünteetiline koolitusvorm",
            delivery_mode="online",
            public_url=f"{SYNTHETIC_URL}-tulev",
            public_link_status="linked_embedded_latest",
            source_year=today.year,
            source_sheet=f"KOOD {today.year}",
            source_row=6,
        ),
        synthetic_row(
            event_id="EVENT-8006",
            service_code="8006",
            event_name="Sünteetiline hiljuti toimunud sündmus",
            start_date=_at(today - dt.timedelta(days=10)),
            event_status="past",
            tag_key="seminar",
            tag_label="Sünteetiline seminar",
            event_type_key="training",
            event_type_label="Sünteetiline koolitusvorm",
            delivery_mode="onsite",
            source_year=today.year,
            source_sheet=f"KOOD {today.year}",
            source_row=7,
        ),
        synthetic_row(
            event_id="EVENT-8007",
            service_code="8007",
            event_name="Sünteetiline käimasolev sündmus",
            start_date=_at(today - dt.timedelta(days=1)),
            end_date=_at(today + dt.timedelta(days=1)),
            event_status="ongoing",
            tag_key="konverents",
            tag_label="Sünteetiline konverents",
            event_type_key="conference",
            event_type_label="Sünteetiline konverentsivorm",
            delivery_mode="hybrid",
            date_parse_status="parsed_range",
            source_year=today.year,
            source_sheet=f"KOOD {today.year}",
            source_row=8,
        ),
        # The quarter boundary: consecutive days that must land in Q1 and Q2.
        synthetic_row(
            event_id="EVENT-8008",
            service_code="8008",
            event_name="Sünteetiline kvartali lõpu sündmus",
            start_date=_at(dt.date(mid_year, 3, 31)),
            event_status="past",
            tag_key="seminar",
            tag_label="Sünteetiline seminar",
            event_type_key="training",
            event_type_label="Sünteetiline koolitusvorm",
            delivery_mode="onsite",
            source_year=mid_year,
            source_sheet=f"KOOD {mid_year}",
            source_row=9,
        ),
        synthetic_row(
            event_id="EVENT-8009",
            service_code="8009",
            event_name="Sünteetiline kvartali alguse sündmus",
            start_date=_at(dt.date(mid_year, 4, 1)),
            event_status="past",
            tag_key="seminar",
            tag_label="Sünteetiline seminar",
            event_type_key="training",
            event_type_label="Sünteetiline koolitusvorm",
            delivery_mode="online",
            source_year=mid_year,
            source_sheet=f"KOOD {mid_year}",
            source_row=10,
        ),
    ]


@pytest.fixture
def event_programme_source(db):
    return ensure_event_programme_source()


@pytest.fixture
def superuser(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_superuser(
        username="synthetic-event-programme-root",
        password="synthetic-test-password",
    )


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


@pytest.fixture
def publish_programme(make_workbook, event_programme_source):
    """Publish a synthetic programme through the real parser and importer.

    Deliberately not a factory that writes `EventProgrammeItem` rows: the whole
    point of the selector and page tests is that they read what the canonical
    contract actually produces, including the derived calendar fields, the
    controlled vocabularies and the immutable publication.
    """
    from apps.event_programme.sync import synchronize_public_workbook

    def publish(rows=None, **kwargs):
        path = make_workbook(rows=rows, **kwargs)
        return synchronize_public_workbook(downloader=FakeDownloader(path))

    return publish
