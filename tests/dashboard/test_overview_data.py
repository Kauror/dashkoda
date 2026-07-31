"""The overview once sources are actually connected.

`test_overview.py` covers the page with nothing connected, where its job is to
show no numbers at all. This module covers the opposite: that every figure the
board reads is the one the source published, that a comparison names its own
baseline, and that a part with no source still says so on a page full of data.

Every value is synthetic and built here. Nothing reads a real workbook, calls
Koda.ee or touches the approved membership package.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files import File
from django.urls import reverse
from django.utils.html import strip_tags

from apps.events.collector import EventCollectionError
from apps.events.sync import synchronize_events
from apps.legal_work.bootstrap import ensure_legal_work_source
from apps.legal_work.importer import import_artifact
from apps.membership.history_import import import_history_package
from apps.membership.sync import synchronize_membership
from apps.news.sync import synchronize_news
from apps.sources.services import register_artifact
from tests.koda.conftest import (
    collector_raising,
    collector_returning,
    event_collection,
    membership_collection,
    news_collection,
)
from tests.legal_work.workbook_factory import synthetic_row, write_workbook
from tests.membership.package_factory import build_package

pytestmark = pytest.mark.django_db

TODAY = dt.date.today()


@pytest.fixture
def viewer(client, authenticate_viewer):
    authenticate_viewer(client)
    return client


@pytest.fixture
def imported_internal_history(db, tmp_path):
    """The Chamber's own board-report history, from a synthetic package."""
    return import_history_package(build_package(tmp_path / "package.zip"), dry_run=False)


def legal_work_rows() -> list[list]:
    """Three rows chosen to exercise every count the overview shows.

    One arrived inside the activity window and has a deadline three days out;
    one was sent inside the window; one is old enough to be outside it, so a
    window that quietly ignored its bounds would be visible as a wrong count.
    """
    return [
        synthetic_row(
            record_id="SYN-0001",
            topic="Sünteetiline kiireloomuline teema",
            received_date=TODAY - dt.timedelta(days=5),
            deadline_date=TODAY + dt.timedelta(days=3),
            is_open=True,
            source_row=2,
        ),
        synthetic_row(
            record_id="SYN-0002",
            topic="Sünteetiline saadetud teema",
            received_date=TODAY - dt.timedelta(days=10),
            deadline_date=TODAY - dt.timedelta(days=1),
            sent_date=TODAY - dt.timedelta(days=2),
            sent_status="sent",
            is_open=False,
            source_row=3,
        ),
        synthetic_row(
            record_id="SYN-0003",
            topic="Sünteetiline vana teema",
            received_date=TODAY - dt.timedelta(days=200),
            deadline_date=TODAY + dt.timedelta(days=400),
            is_open=True,
            source_row=4,
        ),
    ]


@pytest.fixture
def legal_work_snapshot(db, tmp_path):
    path = write_workbook(tmp_path / "synthetic.xlsx", rows=legal_work_rows())
    source = ensure_legal_work_source()
    with path.open("rb") as handle:
        artifact = register_artifact(
            source=source,
            upload=File(handle, name="dashkoda_oigusloome.xlsx"),
            original_name="dashkoda_oigusloome.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    return import_artifact(artifact, dry_run=False).snapshot


def body(response) -> str:
    return response.content.decode()


def text_of(response) -> str:
    return " ".join(strip_tags(body(response)).split())


def section(response, heading_id: str) -> str:
    """One `<section>` of the page, by the id its heading carries.

    Several assertions are about where something appears rather than whether it
    appears at all — "pärast" is ordinary Estonian and turns up in half the
    empty states, so a whole-page search would prove nothing.
    """
    return body(response).split(f'aria-labelledby="{heading_id}"')[1].split("</section>")[0]


def kpi_strip(response) -> str:
    return section(response, "section-kpi")


# -- legal work ---------------------------------------------------------


def test_the_open_count_and_activity_come_from_the_snapshot(viewer, legal_work_snapshot):
    page = body(viewer.get(reverse("home")))

    assert legal_work_snapshot.open_record_count == 2
    # Two open topics; one arrival and one send inside the window, with the
    # 200-day-old row deliberately outside it.
    assert "uut õigusloome teemat" in page
    assert "esitatud arvamust" in page
    assert "Sünteetiline kiireloomuline teema" in page


def test_an_approaching_deadline_reaches_the_attention_section(viewer, legal_work_snapshot):
    page = text_of(viewer.get(reverse("home")))

    assert "Juhatuse tähelepanu" in page
    assert "Sünteetiline kiireloomuline teema" in page
    assert "3 päeva" in page


def test_only_the_approaching_deadline_is_flagged(viewer, legal_work_snapshot):
    """Which rows qualify is asserted precisely in `tests/legal_work`.

    Here the point is narrower: exactly one attention row reaches the page, so
    an expired deadline and one four hundred days out do not both arrive with
    it.
    """
    attention = section(viewer.get(reverse("home")), "section-attention")

    assert attention.count("<li") == 1
    assert "Sünteetiline kiireloomuline teema" in attention


# -- public membership --------------------------------------------------


def test_the_member_total_carries_its_own_baseline_date(viewer):
    synchronize_membership(collector=collector_returning(membership_collection(3400)))
    synchronize_membership(collector=collector_returning(membership_collection(3396)))

    strip = kpi_strip(viewer.get(reverse("home")))

    assert "3396" in strip
    # The delta's baseline is the previous reading, not the activity window, so
    # it is stated beside the figure with its own date rather than being mixed
    # into a row of counts that all mean the same period.
    assert "-4" in strip
    assert "↓" in strip
    assert "pärast" in strip


def test_a_first_ever_reading_shows_no_change_it_cannot_know(viewer):
    synchronize_membership(collector=collector_returning(membership_collection(3400)))

    strip = kpi_strip(viewer.get(reverse("home")))

    assert "3400" in strip
    # A first observation has nothing behind it, so the difference is unknown
    # rather than zero. No direction marker may appear at all.
    assert "↑" not in strip
    assert "↓" not in strip
    assert "→" not in strip
    assert "pärast" not in strip


def test_the_two_membership_sources_are_never_merged(viewer, imported_internal_history):
    synchronize_membership(collector=collector_returning(membership_collection(3400)))

    page = body(viewer.get(reverse("home")))

    assert "Koda.ee liikmekataloog" in page
    assert "Sisemine liikmeskonna aruanne" in page
    assert "iga päev" in page
    assert "kord kuus" in page
    assert "Neid ei liideta ega esitata ühe näitajana." in page


# -- feeds --------------------------------------------------------------


def test_news_and_events_reach_their_cards(viewer):
    synchronize_news(collector=collector_returning(news_collection(3)))
    synchronize_events(collector=collector_returning(event_collection(3)))

    page = body(viewer.get(reverse("home")))

    assert "Sünteetiline uudis" in page
    assert "Sünteetiline sündmus" in page
    assert "avaldatud uudist" in page
    assert "eelseisvat sündmust" in page


def test_a_stale_source_is_named_in_the_attention_section(viewer):
    synchronize_events(collector=collector_returning(event_collection(3)))
    synchronize_events(collector=collector_raising(EventCollectionError("Sünteetiline viga.")))

    page = text_of(viewer.get(reverse("home")))

    assert "viimane kontroll ebaõnnestus" in page.lower()
    assert "Sünteetiline viga" not in page, "no exception detail may reach a viewer"
    assert "Sünteetiline sündmus 0" in page, "the last good data must still be shown"


# -- what is not connected ----------------------------------------------


def test_unconnected_parts_still_say_so_on_a_page_full_of_data(viewer, legal_work_snapshot):
    synchronize_news(collector=collector_returning(news_collection(3)))
    synchronize_membership(collector=collector_returning(membership_collection(3400)))

    page = body(viewer.get(reverse("home")))

    assert "Kanalite statistika" in page
    assert "Meediakajastused" in page
    # Website visits have no source at all and say exactly that.
    assert "Kodulehe külastused" in page
    assert "Google Analytics ei ole ühendatud." in page
    # The five channels that *can* hold a value have none entered yet, which is a
    # different statement and gets different wording.
    assert page.count("Andmed puuduvad.") >= 5
    assert "Andmeallikas ei ole veel ühendatud." in page


def test_an_unconnected_source_contributes_no_zero(viewer):
    """Nothing is connected, so no count may appear — least of all a zero."""
    page = text_of(viewer.get(reverse("home")))

    assert "uut õigusloome teemat" not in page
    assert "avaldatud uudist" not in page
