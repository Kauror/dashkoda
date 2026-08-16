"""The Õigusloome page and the overview integration."""

import datetime as dt
import re

import pytest
from django.urls import reverse

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


def test_with_data_the_page_shows_its_sections(client, authenticate_viewer, imported_snapshot):
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "Hetkel töös" in content
    assert "Viimati välja läinud" in content
    assert "Andmete seis" in content
    assert "Sünteetiline avatud teema" in content


def test_arrivals_are_no_longer_a_section_of_their_own(
    client, authenticate_viewer, imported_snapshot
):
    """A record that has just come in is active work, and Hetkel töös is where
    active work is listed. The arrivals table repeated those same rows under a
    second heading, so it is gone — table, heading and anchor together, rather
    than left behind as an empty section or a dead fragment link."""
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "Uusimad sisse tulnud" not in content
    assert 'id="section-received"' not in content
    # The arrival count itself is still measured; it is the table that went.
    assert "Sisse tulnud" in content


def test_the_workbook_row_total_is_not_a_headline_figure(
    client, authenticate_viewer, imported_snapshot
):
    """ "Kirjeid kokku" answers how big the file is, not how much work there is.

    It stays in Andmete seis, which is the section about the published snapshot;
    it is no longer one of the figures the page leads with.
    """
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()
    figures = content.split('id="section-figures"', 1)[1].split("</section>", 1)[0]

    assert "Kirjeid kokku" not in figures
    assert "Hetkel töös" in figures
    assert "Kirjeid kokku" in content, "the data-state section still describes the file"


def test_the_open_table_no_longer_carries_the_next_step_column(
    client, authenticate_viewer, imported_snapshot
):
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()
    table = content.split('id="section-open"', 1)[1].split("</table>", 1)[0]

    assert "Järgmiseks" not in table
    assert "Hetkeseis" in table, "the columns that stayed are still there"


def test_every_section_the_overview_links_to_exists_on_this_page(
    client, authenticate_viewer, imported_snapshot
):
    """The Õigusloome counts that link into this page do so by anchor.

    Two of the three link now: the arrivals count went to plain text with the
    section that listed its rows, because no remaining section holds exactly the
    records it counts.

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
    """The route survives the pillar, and still never shows a nought.

    The `Huvikaitse` card left the overview on 2026-08-16, so the claim this
    test used to make — that a pillar is a permanent part of the page's
    structure — is no longer true of this domain. What has to remain true is
    the part that mattered: the overview still offers a way through to an
    honest empty Õigusloome page, and never prints a nought standing in for a
    count nobody made.
    """
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert "Huvikaitse" not in content, "the pillar left on 2026-08-16"
    assert "Andmeallikas ei ole ühendatud." in content
    assert reverse("legal-work") in content, "the overview still routes there"
    assert "arvamust sellel aastal" not in content


def test_overview_shows_real_legal_work_data_once_imported(
    client, authenticate_viewer, imported_snapshot
):
    """The figure left the overview with its card; the page still carries it.

    Until 2026-08-16 the `Huvikaitse` pillar stated how much work was being
    carried. The card is gone, so the assertion follows the figure to the
    Õigusloome page, which is where it is now read.
    """
    authenticate_viewer(client)

    overview = client.get("/").content.decode()
    assert "Huvikaitse" not in overview

    content = client.get(reverse("legal-work")).content.decode()
    # `Arvamusi välja saadetud tänavu` was the caption under the figure. It came
    # off the card on 2026-08-15 and the window moved into the unit, so the
    # headline states its own scope: `165 arvamust sellel aastal`.
    assert "arvamust sellel aastal" in content
    assert "Arvamusi välja saadetud tänavu" not in content
    assert "Teemasid töös" in content
    assert "Vaata õigusloomet" in content
    # The workbook's own date is claimed in `Andmete seis`, which moved to
    # `/haldus/` on 2026-08-15. The overview no longer dates its own figures,
    # so this reads it where it is now rather than pretending it is still here.
    stated = imported_snapshot.reporting_date
    admin = client.get(reverse("dashboard-admin")).content.decode()
    assert f"{stated:%d.%m.%Y}" in admin


def test_overview_discloses_a_failed_sync_alongside_old_data(
    client, authenticate_viewer, imported_snapshot, legal_work_source
):
    """The failure is disclosed, and the last good data is not withdrawn.

    The disclosure is on `/dashboard/varskus/` and in `Andmete seis`; the
    figures staying put is the part that matters to a reader.
    """
    state = get_feed_state(legal_work_source)
    state.last_result = SyncResult.FAILED
    state.save()
    authenticate_viewer(client)

    content = client.get("/").content.decode()
    freshness = client.get("/dashboard/varskus/").content.decode()

    assert "Vananenud: 1" in freshness
    # The figure stays put; only its caption went. That is the half this test is
    # about — a failed refresh must not withdraw the last good data.
    assert "arvamust sellel aastal" in content
    # The disclosure itself is in `Andmete seis`, on `/haldus/` since
    # 2026-08-15. Both halves are named: the figures stay, and the staleness is
    # still stated somewhere a maintainer will find it.
    admin = client.get(reverse("dashboard-admin")).content.decode()
    assert "Vananenud pärast ebaõnnestunud uuendust" in admin


def test_overview_dates_the_data_by_the_workbook_not_by_page_load(
    client, authenticate_viewer, imported_snapshot
):
    """The claim must be "data as of <workbook date>", not "loaded today".

    The shell's own freshness region legitimately shows the current time — that
    is a fact about the application, not about the data — so this checks the
    legal-work claim specifically.

    The pillar used to state it twice, as the headline's as-of date and as the
    period the figure stops on. Both captions came off the card on 2026-08-15
    and `Andmete seis` moved to `/haldus/` the same day, so that page is the one
    place the date is claimed — which is where this now reads it.
    """
    authenticate_viewer(client)

    admin = client.get(reverse("dashboard-admin")).content.decode()
    reporting_date = imported_snapshot.reporting_date

    assert reporting_date != dt.date.today()
    assert "Seis" in admin
    assert f"{reporting_date:%d.%m.%Y}" in admin


# ---------------------------------------------------------------------------
# The overview's Õigusloome section
# ---------------------------------------------------------------------------


def test_the_overview_lists_work_in_progress_and_recent_opinions(
    client, authenticate_viewer, imported_snapshot
):
    """The board's two lists, added 2026-08-15.

    `Töös` is ordered by the opinion deadline rather than by arrival, because
    what a reader wants off that list is what has to leave next.
    """
    authenticate_viewer(client)

    page = client.get("/").context["page"]
    content = client.get("/").content.decode()

    assert "Õigusloome" in content
    assert "Töös" in content
    assert "Viimased välja saadetud" in content
    assert page.has_legal_lists

    deadlines = [row.item.deadline_date for row in page.legal_in_progress if row.item.deadline_date]
    assert deadlines == sorted(deadlines), "Töös must lead with what is due next"

    sent = [row.item.sent_date for row in page.legal_recently_sent]
    assert sent == sorted(sent, reverse=True), "sent opinions must lead with the newest"


def test_neither_overview_list_exceeds_seven_rows(client, authenticate_viewer, imported_snapshot):
    """Seven is the board's own number, and the section stays a summary.

    A list that grew past it would be the Õigusloome page reproduced a scroll
    above the link to it.
    """
    from apps.legal_work.executive import OVERVIEW_LIST_LIMIT

    authenticate_viewer(client)

    page = client.get("/").context["page"]

    assert OVERVIEW_LIST_LIMIT == 7
    assert len(page.legal_in_progress) <= OVERVIEW_LIST_LIMIT
    assert len(page.legal_recently_sent) <= OVERVIEW_LIST_LIMIT


def test_an_overview_row_without_a_resolved_address_is_plain_text(
    client, authenticate_viewer, imported_snapshot
):
    """The rule that makes the section trustworthy.

    `topic_links` refuses an address computed against a different snapshot, and
    nothing has matched this synthetic workbook — so every row here is
    unmatched, and not one of them may be rendered as a link. A lawyer sent to
    last week's consultation is worse off than one sent nowhere.
    """
    authenticate_viewer(client)

    page = client.get("/").context["page"]
    rows = tuple(page.legal_in_progress) + tuple(page.legal_recently_sent)

    assert rows, "the fixture must produce rows for this to prove anything"
    assert all(not row.public_url for row in rows)
    assert all(not row.is_linked for row in rows)

    # The two `<ul>` lists only. Slicing to the section's own footer keeps the
    # `Vaata õigusloomet` anchor, whose opening tag sits before its text — a
    # property of the markup, not of any row, and what made the first version
    # of this assertion fail.
    section = client.get("/").content.decode().split('id="oigusloome"', 1)[1]
    # Bounded by the section that follows it. `Praegu huvi pakkuv` was the
    # delimiter until it left the page on 2026-08-16.
    section = section.split('aria-labelledby="section-channels"', 1)[0]
    lists = re.findall(r"<ul[ >].*?</ul>", section, flags=re.S)

    assert lists, "the section rendered no list to inspect"
    for markup in lists:
        assert "<a" not in markup, "an unmatched topic became a link"


def test_a_sent_row_never_offers_a_consultation_link(
    client, authenticate_viewer, imported_snapshot
):
    """The mutual exclusivity, asserted where the two lists sit side by side.

    `consultation.py` and `opinion_eligibility.py` are exclusive by
    construction and each is tested in its own suite; this checks the guarantee
    survives being composed into one section, which is the layer that could
    quietly resolve both mappings and merge them the wrong way round.
    """
    from apps.legal_work.models import SentStatus

    authenticate_viewer(client)

    page = client.get("/").context["page"]

    for row in page.legal_recently_sent:
        assert row.item.sent_status == SentStatus.SENT
        if row.public_url:
            assert row.public_url.startswith("/oigusloome/arvamused/"), (
                "a sent opinion must point at its own resource, never a consultation"
            )
    for row in page.legal_in_progress:
        assert row.item.is_open
        if row.public_url:
            assert not row.public_url.startswith("/oigusloome/arvamused/"), (
                "an open matter must not point at an opinion resource"
            )
