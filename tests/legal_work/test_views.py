"""The Õigusloome page and the overview integration."""

import datetime as dt

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
    """One truthful sentence, page-wide, since `Andmete seis` — the section
    that used to carry both this message and the separate `Ühendamata`
    status badge — moved to `/haldus/` on 2026-08-17. The badge went with
    it; the guarantee that every focus states its own emptiness did not,
    matching the pattern `visibility/koduleht.html` already uses for the
    same "nothing collected yet" case.
    """
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "Andmeallikas ei ole veel ühendatud." in content


def test_with_data_the_page_shows_its_sections(client, authenticate_viewer, imported_snapshot):
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "Hetkel töös" in content
    assert "Viimati välja läinud" in content
    # `Andmete seis` moved to `/haldus/` on 2026-08-17; see
    # `tests/dashboard/test_admin_area.py::test_the_legal_work_data_block_arrived`.
    assert "Sünteetiline avatud teema" in content


def test_arrivals_are_no_longer_a_section_or_a_headline(
    client, authenticate_viewer, imported_snapshot
):
    """A record that has just come in is active work, and Hetkel töös is where
    active work is listed. The arrivals table repeated those same rows under a
    second heading, so it went — table, heading and anchor together, rather
    than left behind as an empty section or a dead fragment link.

    `Sisse tulnud sel aastal` followed it off the page on 2026-08-16. Until then
    this test asserted the phrase was still *somewhere*, which was true only
    because that readout survived the table.

    The measurement is not lost: `topics_year_on_year` still drives the note
    under `Teemasid {aasta}` in the headline strip — `Mis muutus?`, the section
    that read it before, retired on 2026-08-18. That is not asserted here,
    because the note is conditional on a prior year being present in the
    snapshot — a render check would pin the fixture's shape rather than the
    page's rule. `tests/legal_work/test_executive_consistency.py` covers the
    selector itself.
    """
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "Uusimad sisse tulnud" not in content
    assert 'id="section-received"' not in content
    assert "Sisse tulnud sel aastal" not in content


def test_the_workbook_row_total_is_not_a_headline_figure(
    client, authenticate_viewer, imported_snapshot
):
    """ "Kirjeid kokku" answers how big the file is, not how much work there is.

    It is not one of the figures the page leads with — and since `Andmete
    seis` (the section that described the file) moved to `/haldus/` on
    2026-08-17, it is not anywhere on this page at all any more. See
    `tests/dashboard/test_admin_area.py::test_the_legal_work_data_block_arrived`
    for where it lives now.
    """
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()
    figures = content.split('id="section-figures"', 1)[1].split("</section>", 1)[0]

    assert "Kirjeid kokku" not in figures
    assert "Hetkel töös" in figures
    assert "Kirjeid kokku" not in content


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


def test_the_page_no_longer_states_the_data_reporting_date(
    client, authenticate_viewer, imported_snapshot
):
    """The as-of/schema line left the header on 2026-08-17 — moved, not
    copied, to `/haldus/` along with the rest of `Andmete seis`. See
    `tests/dashboard/test_admin_area.py::test_the_legal_work_data_block_arrived`.
    """
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "Andmed seisuga" not in content


def test_a_failed_check_is_no_longer_disclosed_on_this_page(
    client, authenticate_viewer, imported_snapshot, legal_work_source
):
    """The stale-after-failure callout moved to `/haldus/` with the rest of
    `Andmete seis` on 2026-08-17. See
    `tests/dashboard/test_admin_area.py::test_the_legal_work_data_block_states_a_failed_check`.
    """
    state = get_feed_state(legal_work_source)
    state.last_result = SyncResult.FAILED
    state.last_error_summary = "Sünteetiline sisemine viga."
    state.save()
    authenticate_viewer(client)

    content = client.get(PAGE_URL).content.decode()

    assert "Viimane kontroll ebaõnnestus." not in content
    # The viewer never sees the internal diagnostic, on this page or Admin.
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


def _legal_card():
    """The Õigusloome card as `Koja töölaud` builds it.

    Built directly rather than scraped out of the rendered page, because what
    these tests are about is the wording and the choice of headline — which
    belong to the builder — while the page is asserted separately to render it.
    """
    from apps.dashboard.executive import _legal_card
    from apps.legal_work.executive import get_legal_work_executive
    from apps.legal_work.selectors import get_legal_work_summary

    return _legal_card(get_legal_work_executive(get_legal_work_summary()))


# -- overview integration ----------------------------------------------


def test_overview_keeps_its_empty_state_without_a_snapshot(client, authenticate_viewer):
    """The card is a permanent part of the page and never prints a nought.

    Õigusloome is one of the six domain cards again since 2026-08-17. With no
    workbook imported it says its source is not connected — never `0 teemat`,
    which would claim somebody counted no open matters.
    """
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert "Õigusloome" in content
    assert "Andmeallikas ei ole ühendatud." in content
    assert reverse("legal-work") in content, "the overview still routes there"
    assert "teemat töös" not in content


def test_the_overview_card_leads_with_open_matters_not_with_output(
    client, authenticate_viewer, imported_snapshot
):
    """The headline is the stock, and the year's output is a supporting fact.

    `Arvamusi välja saadetud tänavu` led this card until 2026-08-17 and was the
    wrong figure for a management page: it is cumulative, it can only rise, and
    it says nothing about what the Chamber is holding right now. The count of
    open matters changes when somebody acts, which is what a cockpit is for.

    Both figures are still on the card. What this pins is which one is the
    headline.
    """
    from apps.core.formatting import integer
    from apps.legal_work.analytics import sent_year_on_year
    from apps.legal_work.selectors import get_legal_work_summary

    authenticate_viewer(client)

    card = _legal_card()
    summary = get_legal_work_summary()

    assert card.headline.value == integer(summary.open_count)
    assert card.headline.unit == "teemat töös"
    # The year's output did not leave — it moved one row down, with the
    # like-for-like baseline beside it. Both sides stop on the same calendar
    # day, which is `sent_year_on_year`'s own rule and not restated here.
    sent = sent_year_on_year(summary.snapshot)
    labels = {fact.label: fact.value for fact in card.available_facts}
    assert labels["Arvamusi saadetud tänavu"] == integer(sent.current)
    assert labels["Sama ajaks eelmisel aastal"] == integer(sent.previous)

    content = client.get("/").content.decode()
    assert "teemat töös" in content


def test_the_overview_card_never_calls_opinion_volume_impact(
    client, authenticate_viewer, imported_snapshot
):
    """Output is not impact.

    The workbook counts opinions sent. It does not count opinions accepted or
    provisions changed, so no word on this card may suggest it does.
    """
    card = _legal_card()

    words = " ".join(
        [card.label, card.headline.unit, card.period_line] + [fact.label for fact in card.facts]
    ).casefold()

    for forbidden in ("mõju", "tulemus", "edukus", "saavutus"):
        assert forbidden not in words


def test_a_passed_deadline_reaches_the_page_as_a_signal_and_not_as_a_quiet_fact(
    client, authenticate_viewer, imported_snapshot
):
    """The most urgent number belongs in the least quiet place.

    `overdue_pending` is the domain's own critical signal, and `Tähelepanu`
    renders it with the evidence and a link to the list the rows sit in. Putting
    it on the card as a fourth grey fact would bury it under the three figures
    it outranks.
    """
    from apps.legal_work.executive import get_legal_work_executive
    from apps.legal_work.selectors import get_legal_work_summary

    executive = get_legal_work_executive(get_legal_work_summary())
    card = _legal_card()

    assert not any("möödas" in fact.label.casefold() for fact in card.facts)
    if executive.overdue_pending:
        keys = {signal.key for signal in executive.signals}
        assert "legal-overdue-pending" in keys


def test_the_overview_no_longer_reproduces_the_topic_lists(
    client, authenticate_viewer, imported_snapshot
):
    """Two seven-row lists of `/oigusloome/` left the front page on 2026-08-17.

    They were half of the Õigusloome dashboard, a scroll above the link to it.
    What replaced them is the card's four figures, the deadline lane of
    `Järgmised 30 päeva`, the domain's signals and that same link.

    The lists are not merely unrendered: `LegalWorkExecutive` stopped building
    them, so the selector reads and the link-resolution pass they cost are gone
    too. The Õigusloome page still calls the same selectors, which is asserted
    where that page is tested.
    """
    from apps.legal_work.executive import LegalWorkExecutive

    authenticate_viewer(client)

    content = client.get("/").content.decode()
    page = client.get("/").context["page"]

    assert "Viimased välja saadetud" not in content
    fields = set(LegalWorkExecutive.__dataclass_fields__)
    assert not fields & {"in_progress", "recently_sent"}
    assert not hasattr(page, "legal_in_progress")
    assert not hasattr(page, "legal_recently_sent")


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

    freshness = client.get("/dashboard/varskus/").content.decode()

    assert "Vananenud: 1" in freshness
    # The figures stay put. That is the half this test is about — a failed
    # refresh must not withdraw the last good data.
    cards = {card.key: card for card in client.get("/").context["page"].cards}
    assert cards["legal_work"].is_available
    # The disclosure itself is in `Andmete seis`, on `/haldus/` since
    # 2026-08-15. Both halves are named: the figures stay, and the staleness is
    # still stated somewhere a maintainer will find it.
    admin = client.get(reverse("dashboard-admin")).content.decode()
    assert "Vananenud pärast ebaõnnestunud uuendust" in admin


def test_overview_dates_the_data_by_the_workbook_not_by_page_load(
    client, authenticate_viewer, imported_snapshot
):
    """The claim must be "data as of <workbook date>", not "loaded today".

    `Andmete seis` at `/haldus/` states it with the source named, and that is
    the one place the claim is made. The card printed it too until 2026-08-18,
    when the three cards whose figures are a current state rather than a window
    lost their period lines — a date under a figure that is simply "as of the
    latest report" told a reader nothing `Andmete seis` does not.

    What must never happen is the page dating the figures by when it was
    loaded, and that is what this checks: a reporting date that is not today,
    stated where the source is named.
    """
    authenticate_viewer(client)

    reporting_date = imported_snapshot.reporting_date
    assert reporting_date != dt.date.today()
    assert _legal_card().period_line == ""

    admin = client.get(reverse("dashboard-admin")).content.decode()
    assert "Seis" in admin
    assert f"{reporting_date:%d.%m.%Y}" in admin
