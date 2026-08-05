"""The Õigusloome page and the overview integration."""

import datetime as dt

import pytest

from apps.legal_work.models import SyncResult
from apps.legal_work.sections import LINKED_SECTIONS
from apps.legal_work.sync import get_feed_state

pytestmark = pytest.mark.django_db

PAGE_URL = "/oigusloome/"


def test_the_page_requires_viewer_access(client):
    response = client.get(PAGE_URL)

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")


def test_the_page_is_read_only(client, authenticate_viewer):
    authenticate_viewer(client)

    assert client.post(PAGE_URL).status_code == 405


def test_the_navigation_item_is_now_a_real_route(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert f'href="{PAGE_URL}"' in content


def test_without_data_the_page_shows_truthful_empty_states(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "Andmeallikas ei ole veel ühendatud." in content
    assert "Ühendamata" in content


def test_with_data_the_page_shows_all_three_sections(
    client, authenticate_viewer, imported_snapshot
):
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "Hetkel töös" in content
    assert "Viimati välja läinud" in content
    assert "Uusimad sisse tulnud" in content
    assert "Andmete seis" in content
    assert "Sünteetiline avatud teema" in content


def test_every_section_the_overview_links_to_exists_on_this_page(
    client, authenticate_viewer, imported_snapshot
):
    """The overview's three Õigusloome counts link into this page by anchor.

    The ids are named in `apps/legal_work/sections.py`, but the template still
    writes its own `heading_id`, so nothing but this test stops the two drifting
    apart — and a broken fragment fails silently in a browser, landing the reader
    at the top of the page with no error anywhere.

    The empty-state branches render the same headings, so this holds whether or
    not a section has rows.
    """
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    for section_id in LINKED_SECTIONS:
        assert f'id="{section_id}"' in content, f"the overview links to #{section_id}"


def test_the_page_states_the_data_reporting_date(client, authenticate_viewer, imported_snapshot):
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "Andmed seisuga" in content
    stated = imported_snapshot.reporting_date
    assert f"{stated.day}.{stated:%m.%y}" in content


def test_a_failed_check_is_disclosed_while_old_data_is_shown(
    client, authenticate_viewer, imported_snapshot, legal_work_source
):
    state = get_feed_state(legal_work_source)
    state.last_result = SyncResult.FAILED
    state.last_error_summary = "Sünteetiline sisemine viga."
    state.save()
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "Viimane kontroll ebaõnnestus." in content
    assert "Kuvatakse viimase eduka impordi andmeid." in content
    # The viewer never sees the internal diagnostic.
    assert "Sünteetiline sisemine viga." not in content


def test_the_page_never_renders_a_lawyer_or_microsoft_identifier(
    client, authenticate_viewer, imported_snapshot, legal_work_source
):
    state = get_feed_state(legal_work_source)
    state.remote_etag = "synthetic-ctag"
    state.save()
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    for forbidden in (
        "synthetic-ctag",
        "synthetic-drive",
        "synthetic-item",
        "Vastutaja",
        "sourceartifact",
        "source_row",
        "/media/",
    ):
        assert forbidden not in content


def test_the_page_keeps_the_shell_accessibility_contract(
    client, authenticate_viewer, imported_snapshot
):
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert content.count("<h1") == 1
    assert 'href="#main"' in content
    assert 'id="main"' in content
    assert 'aria-current="page"' in content


def test_the_page_keeps_the_content_security_policy(client, authenticate_viewer):
    authenticate_viewer(client)

    response = client.get(PAGE_URL)

    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'self'; script-src 'self'; "
        "style-src 'self'; img-src 'self' data:; connect-src 'self'"
    )
    assert response.headers["Cache-Control"] == "private, no-store"


def test_the_page_loads_only_local_bundled_assets(client, authenticate_viewer, imported_snapshot):
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "https://" not in content
    assert "<script>" not in content
    assert 'style="' not in content


# -- overview integration ----------------------------------------------


def test_overview_keeps_its_empty_state_without_a_snapshot(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert "Andmeallikas ei ole veel ühendatud." in content
    assert "Eelnõud ja arvamused ilmuvad siia pärast esimest edukat sünkroonimist." in content
    # The card does not offer a way through to a page that has nothing on it.
    assert "Vaata õigusloomet" not in content


def test_overview_shows_real_legal_work_data_once_imported(
    client, authenticate_viewer, imported_snapshot
):
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert "Sünteetiline avatud teema" in content
    # The headline cell is named for the module and lists its counts; the count
    # itself is what carries the wording "teemasid töös".
    assert "Õigusloome" in content
    assert "teemasid töös" in content
    assert "Vaata õigusloomet" in content
    stated = imported_snapshot.reporting_date
    assert f"{stated.day}.{stated:%m.%y}" in content


def test_overview_discloses_a_failed_sync_alongside_old_data(
    client, authenticate_viewer, imported_snapshot, legal_work_source
):
    """The failure is disclosed where the board will see it, and the last good
    data stays on the page rather than being withdrawn.

    Since the attention section was removed the disclosure is the connection
    strip at the foot of the overview, which counts the stale sources."""
    state = get_feed_state(legal_work_source)
    state.last_result = SyncResult.FAILED
    state.save()
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert "Vananenud: 1" in content
    assert "Sünteetiline avatud teema" in content


def test_overview_dates_the_data_by_the_workbook_not_by_page_load(
    client, authenticate_viewer, imported_snapshot
):
    """The claim must be "data as of <workbook date>", not "loaded today".

    The shell's own freshness region legitimately shows the current time — that
    is a fact about the application, not about the data — so this checks the
    legal-work claim specifically.
    """
    authenticate_viewer(client)

    content = client.get("/").content.decode()
    reporting_date = imported_snapshot.reporting_date

    assert reporting_date != dt.date.today()
    assert "Seisuga" in content
    assert f"{reporting_date.day}.{reporting_date:%m.%y}" in content
