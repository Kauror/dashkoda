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
from apps.membership.models import MembershipCountObservation
from apps.membership.selectors import get_current_membership_observation
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


def backdate_current_observation(*, days: int) -> None:
    """Age the published member count so a window has something behind it.

    A recorded observation is immutable through `save()`, deliberately. A test
    that needs a reading from last month has no other way to make one: the sync
    stamps `timezone.now()`, and freezing the clock would move the window too.
    `QuerySet.update()` writes the column directly and is used here only.
    """
    current = get_current_membership_observation()
    MembershipCountObservation.objects.filter(pk=current.pk).update(
        observed_at=current.observed_at - dt.timedelta(days=days)
    )


# -- legal work ---------------------------------------------------------


def test_the_open_count_and_activity_come_from_the_snapshot(viewer, legal_work_snapshot):
    response = viewer.get(reverse("home"))
    strip = " ".join(strip_tags(kpi_strip(response)).split())

    assert legal_work_snapshot.open_record_count == 2
    # Two open topics; two arrivals and one send inside the window, with the
    # 200-day-old row deliberately outside it. Every count is in the module's
    # own headline cell, and each states the period it was measured over.
    assert "teemasid töös 2" in strip
    assert "uusi teemasid 30 päevaga 2" in strip
    assert "välja läinud teemasid 30 päevaga 1" in strip
    assert "Sünteetiline kiireloomuline teema" in body(response)


def test_the_overview_no_longer_carries_a_deadline_section(viewer, legal_work_snapshot):
    """The board asked for the attention block to go.

    The deadlines themselves are unchanged — `get_upcoming_deadlines` and the
    Õigusloome page still work through them — but the overview no longer
    repeats them above the fold.
    """
    page = text_of(viewer.get(reverse("home")))

    assert "Juhatuse tähelepanu" not in page
    assert "arvamuse tähtaeg" not in page


# -- public membership --------------------------------------------------


def test_the_member_total_states_its_movement_over_the_stated_window(viewer):
    synchronize_membership(collector=collector_returning(membership_collection(3400)))
    backdate_current_observation(days=40)
    synchronize_membership(collector=collector_returning(membership_collection(3396)))

    strip = kpi_strip(viewer.get(reverse("home")))

    assert "3396" in strip
    # The baseline is the last reading before the window opened, and the cell
    # names the window it measured rather than leaving the reader to guess.
    assert "-4" in strip
    assert "↓" in strip
    assert "viimase 30 päeva jooksul" in strip


def test_a_reading_with_no_baseline_that_old_shows_no_change(viewer):
    """Two readings a moment apart do not make a month's movement."""
    synchronize_membership(collector=collector_returning(membership_collection(3400)))
    synchronize_membership(collector=collector_returning(membership_collection(3396)))

    strip = kpi_strip(viewer.get(reverse("home")))

    assert "3396" in strip
    # Nothing predates the window, so the month's difference is unknown rather
    # than the -4 that happened inside a single test run.
    assert "↑" not in strip
    assert "↓" not in strip
    assert "→" not in strip
    assert "viimase 30 päeva jooksul" not in strip


def test_a_first_ever_reading_shows_no_change_it_cannot_know(viewer):
    synchronize_membership(collector=collector_returning(membership_collection(3400)))

    strip = kpi_strip(viewer.get(reverse("home")))

    assert "3400" in strip
    # A first observation has nothing behind it, so the difference is unknown
    # rather than zero. No direction marker may appear at all.
    assert "↑" not in strip
    assert "↓" not in strip
    assert "→" not in strip


def test_the_two_membership_sources_are_never_merged(viewer, imported_internal_history):
    """Each total is stated once, and the two are never drawn together.

    The board asked for the per-figure source lines and the explanatory note to
    go, so the overview no longer argues the point in prose. What still has to
    hold structurally is that the directory count appears only in the headline
    strip and the board report's own figures only in their card. Naming the
    sources is the Liikmeskond page's job now, and `test_membership_page.py`
    holds it to that.
    """
    synchronize_membership(collector=collector_returning(membership_collection(3400)))

    response = viewer.get(reverse("home"))
    page = body(response)
    card = section(response, "section-membership")

    # The directory total is stated once, in the headline strip. Repeating it
    # inside the board report's card is what let a reader read two definitions
    # as one number.
    assert "3400" in kpi_strip(response)
    assert "Liikmeid kataloogis" not in page
    assert "Koda.ee liikmekataloog" not in card, "the directory is not a source of this card"
    assert "Tasunud liikmeid" in card, "the card holds the report's own figures"


def test_fee_collection_sits_with_the_counts_it_was_read_beside(viewer, imported_internal_history):
    """The percentage belongs to the board report, so it lives in its card.

    In the headline strip it sat between a directory count and a calendar, four
    cells with nothing in common; the amounts behind it are the same report's
    and the reader needs them side by side.
    """
    response = viewer.get(reverse("home"))
    card = " ".join(strip_tags(section(response, "section-membership")).split())

    assert "Liikmemaksude laekumine" in card
    assert "Liikmemaksude laekumine" not in strip_tags(kpi_strip(response))
    # The euros behind the percentage, grouped and in the report's own currency.
    assert "€" in card


# -- feeds --------------------------------------------------------------


def test_news_and_events_reach_their_cards(viewer):
    synchronize_news(collector=collector_returning(news_collection(3)))
    synchronize_events(collector=collector_returning(event_collection(3)))

    page = body(viewer.get(reverse("home")))

    assert "Sünteetiline uudis" in page
    assert "Sünteetiline sündmus" in page
    assert "sündmusi järgmise 30 päeva jooksul" in page


def test_a_failed_check_is_still_disclosed_and_keeps_the_last_good_data(viewer):
    """The attention section is gone; the disclosure is not.

    The connection strip at the foot of the page counts the stale sources, so a
    failed check is still stated where a reader can see it, and the last data
    that did arrive stays on the page rather than being withdrawn.
    """
    synchronize_events(collector=collector_returning(event_collection(3)))
    synchronize_events(collector=collector_raising(EventCollectionError("Sünteetiline viga.")))

    page = text_of(viewer.get(reverse("home")))

    assert "Vananenud: 1" in page
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

    assert "teemasid töös" not in page
    assert "uusi teemasid" not in page
    assert "sündmusi järgmise" not in page
    assert "sündmusi eelmise" not in page
