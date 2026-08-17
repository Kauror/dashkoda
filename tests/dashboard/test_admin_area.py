"""`Admin` at `/haldus/` — the maintainers' page.

Three things are worth pinning about a page that deliberately holds nothing:

- **it is not Django's admin.** `/admin/` is the Django admin site plus the
  staff data-entry routes, and taking or shadowing that prefix would break
  workflows that have their own `is_staff` requirement. This page grants
  nothing;
- **it is protected exactly like every other dashboard page**, by the viewer PIN
  and nothing extra;
- **it invents no figure.** In particular it must never say "0 probleemi": an
  empty section reporting its own emptiness as a clean bill of health is the one
  reading a maintainer must not be given. Real figures are a different matter —
  Sündmused' provenance block moved here on 2026-08-15 and is full of them.

The link's placement is asserted too. It has to be findable without competing
with the Chamber's own subjects, which means present in the shell, absent from
the primary navigation tuple, and beside the build stamp.
"""

from __future__ import annotations

import re

import pytest
from django.urls import resolve, reverse

from apps.dashboard.navigation import NAVIGATION, iter_items

pytestmark = pytest.mark.django_db


@pytest.fixture
def viewer_client(client, authenticate_viewer):
    """A client holding the viewer PIN.

    `tests/dashboard/` has no `viewer_client` of its own — the fixture is
    declared per package, in the conftest of each domain suite — so it is
    declared here rather than reaching into another package's conftest.
    """
    authenticate_viewer(client)
    return client


def test_haldus_resolves_and_is_not_the_django_admin():
    assert reverse("dashboard-admin") == "/haldus/"
    assert resolve("/haldus/").view_name == "dashboard-admin"


def test_the_django_admin_is_untouched():
    """The constraint that decided the address.

    `/admin/` stays Django's, and the staff data-entry routes registered in
    front of it keep their own addresses.
    """
    assert reverse("admin:index") == "/admin/"
    assert resolve("/admin/").view_name == "admin:index"
    assert not reverse("dashboard-admin").startswith("/admin/")


def test_the_django_admin_still_demands_a_django_login(client, authenticate_viewer):
    """The viewer PIN alone is not enough for `/admin/`, and never was.

    A viewer holds the shared PIN and no Django account. Passing the middleware
    gets them to the admin's own login, not into it.
    """
    authenticate_viewer(client)

    response = client.get("/admin/")

    assert response.status_code in (301, 302)
    assert "/admin/login/" in response.headers["Location"]


def test_the_admin_page_is_behind_the_viewer_gate(client):
    response = client.get(reverse("dashboard-admin"))

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")


def test_the_admin_page_renders_for_an_ordinary_viewer(viewer_client):
    """No second permission system. A viewer who can read a dashboard can read this."""
    response = viewer_client.get(reverse("dashboard-admin"))
    content = response.content.decode()

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert content.count("<h1") == 1
    assert "Admin" in content


def test_the_admin_page_states_what_it_is_for(viewer_client):
    content = viewer_client.get(reverse("dashboard-admin")).content.decode()

    assert "andmekvaliteedi" in content
    for heading in ("Andmekvaliteet", "Andmeallikad ja import", "Tehniline info"):
        assert heading in content


def test_the_events_provenance_block_arrived(viewer_client):
    """The other half of the move off `/sundmused/`.

    A block deleted from one page and never rendered on the other would pass
    the events-side assertion just as well, so this names what has to be here.
    """
    content = viewer_client.get(reverse("dashboard-admin")).content.decode()

    assert "Andmeallikad ja import" in content
    assert "Sündmused ·" in content
    # The parts that carry the actual denominators, not just the heading.
    assert "Mida need andmed ei tõesta" in content
    assert "Koda.ee avalik kalender" in content


def test_the_koduleht_data_block_arrived(viewer_client):
    """Koduleht's `Andmete kohta`, moved off all five focus views on 2026-08-16.

    Asserted by its contents rather than its heading. The rule about users not
    being summable is the single most load-bearing sentence in it, and a move
    that kept the title and dropped the arithmetic would pass a heading check.

    No GA4 history is seeded here on purpose: the prose has to render for a
    property that has collected nothing, because that is exactly when a
    maintainer opens this page.
    """
    content = viewer_client.get(reverse("dashboard-admin")).content.decode()

    assert "Näitajate definitsioonid" in content
    assert "ei ole 780" in content
    assert "Mis liidetakse ja mis mitte" in content


def test_the_koduleht_page_no_longer_carries_its_data_block(viewer_client):
    """Moved, not copied — the other half of the pair above.

    Named here as well as in the Koduleht suite because a block deleted from one
    page and never rendered on the other passes either assertion alone.
    """
    content = viewer_client.get("/koduleht/").content.decode()

    assert "Andmete kohta" not in content
    assert "Kaetus, mõisted ja allikas" not in content


def test_the_mailings_data_block_arrived(viewer_client):
    """Otsepostitused' `Andmete kohta`, moved here on 2026-08-16.

    It absorbed two further copies of the rate definitions on the way — the
    weighted note under the comparison table and the paragraph under the sends
    table — so the assertions name a sentence from each, not just the heading.
    """
    content = viewer_client.get(reverse("dashboard-admin")).content.decode()

    assert "Otsepostitused (Smaily)" in content
    # The rule the whole block exists for.
    assert "Nimekirju ei liideta" in content or "ei liideta" in content
    # The pair with different denominators, which the page no longer explains.
    # Named as the page names it today, or a reader looking `Klikid` up finds
    # only its old name.
    assert "Klikid:" in content
    assert "teine küsimus" in content
    # The weighted-rate note, from under the comparison table.
    assert "üksiksaadetiste protsentide keskmine" in content


def test_the_mailings_page_no_longer_carries_its_data_block(viewer_client):
    """Moved, not copied."""
    content = viewer_client.get("/otsepostitused/").content.decode()

    assert "Andmete kohta" not in content


def test_the_legal_work_data_block_arrived(viewer_client):
    """Õigusloome's `Andmete seis`, moved here on 2026-08-17 — the header's
    as-of/schema line and the whole on-page disclosure, together.

    No workbook is imported here on purpose: the block's own empty state
    ("Andmeallikas ei ole veel ühendatud.") and the feedback-count qualifier
    are unconditional prose, so both have to render for a source that has
    collected nothing — exactly when a maintainer is most likely to open
    this page.
    """
    content = viewer_client.get(reverse("dashboard-admin")).content.decode()

    # `Õigusloome` and `Andmeallikas ei ole veel ühendatud.` both also appear
    # in `Andmete seis` above this section, so the block's own anchor is what
    # actually names it — a page missing this block would still pass an
    # assertion on either string alone.
    assert 'id="oigusloome-andmeallikad"' in content
    assert "Andmeallikas ei ole veel ühendatud." in content
    # The rule the block exists to state, regardless of whether data exists.
    assert "ei ole unikaalsete liikmete arv" in content


@pytest.fixture
def legal_work_snapshot(tmp_path):
    """A published Õigusloome snapshot, the same chain
    `tests/legal_work/conftest.py::imported_snapshot` builds, reproduced here
    because that fixture is declared for `tests/legal_work/` alone."""
    from django.core.files import File

    from apps.legal_work.bootstrap import ensure_legal_work_source
    from apps.legal_work.importer import import_artifact
    from apps.sources.services import register_artifact
    from tests.legal_work.workbook_factory import write_workbook

    source = ensure_legal_work_source()
    path = write_workbook(tmp_path / "synthetic.xlsx")
    with path.open("rb") as handle:
        artifact = register_artifact(
            source=source,
            upload=File(handle, name="dashkoda_oigusloome.xlsx"),
            original_name="dashkoda_oigusloome.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    return import_artifact(artifact, dry_run=False).snapshot


def test_the_legal_work_data_block_states_the_workbook_date(
    client, authenticate_viewer, legal_work_snapshot
):
    """The other half: with a workbook imported, the as-of/schema line and
    the file-level facts it used to lead the Õigusloome page with are here."""
    authenticate_viewer(client)

    content = client.get(reverse("dashboard-admin")).content.decode()

    stated = legal_work_snapshot.reporting_date
    # `%d.%m.%Y`, not `{stated.day}.{stated:%m.%Y}`: the template's `date:"d.m.Y"`
    # zero-pads the day, and an unpadded day only happens to match it on dates
    # from the 10th onward.
    assert f"Andmed seisuga {stated:%d.%m.%Y}" in content
    assert "Kirjeid kokku" in content


def test_the_legal_work_data_block_states_a_failed_check(
    client, authenticate_viewer, legal_work_snapshot
):
    """The stale-after-failure callout, moved off `/oigusloome/` on
    2026-08-17 along with the rest of `Andmete seis`. See
    `tests/legal_work/test_views.py::test_a_failed_check_is_no_longer_disclosed_on_this_page`.
    """
    from apps.legal_work.bootstrap import ensure_legal_work_source
    from apps.legal_work.models import SyncResult
    from apps.legal_work.sync import get_feed_state

    state = get_feed_state(ensure_legal_work_source())
    state.last_result = SyncResult.FAILED
    state.last_error_summary = "Sünteetiline sisemine viga."
    state.save()
    authenticate_viewer(client)

    content = client.get(reverse("dashboard-admin")).content.decode()

    assert "Viimane kontroll ebaõnnestus." in content
    assert "Kuvatakse viimase eduka impordi andmeid." in content
    # The viewer never sees the internal diagnostic, here either.
    assert "Sünteetiline sisemine viga." not in content


def test_the_legal_work_page_no_longer_states_the_workbook_date(
    client, authenticate_viewer, legal_work_snapshot
):
    """Moved, not copied — the other half of the pair above. A workbook is
    imported here too, so the absence means the block left rather than that
    nothing was ever there to show."""
    authenticate_viewer(client)

    content = client.get("/oigusloome/").content.decode()

    assert "Andmed seisuga" not in content
    assert "Kirjeid kokku" not in content


def test_the_membership_data_block_arrived(viewer_client):
    """Liikmeskond's `Andmete seis`, moved here on 2026-08-17 — the header's
    per-source stamps and the whole on-page disclosure, together.

    No internal report is imported here on purpose, matching the Õigusloome
    test above: the heading itself has to render for a source that has
    collected nothing.
    """
    content = viewer_client.get(reverse("dashboard-admin")).content.decode()

    # `Liikmeskond` alone appears in the sidebar and in `Andmete seis` above
    # this section too, so the block's own anchor is the specific claim.
    assert 'id="liikmeskond-andmeallikad"' in content


def test_the_membership_data_block_states_the_report_facts(client, authenticate_viewer, tmp_path):
    """The other half: with an internal report imported, the source stamps,
    the quality badge, the conflict notice and the two-sources-are-different
    rule are all here.

    The default synthetic package carries at least one conflicted metric —
    the same fact `tests/membership/test_membership_page.py`'s
    `test_conflict_notice_no_longer_reaches_this_page` used to check on the
    page itself, before this notice moved here on 2026-08-17.
    """
    from apps.membership.history_import import import_history_package
    from tests.membership.package_factory import build_package

    authenticate_viewer(client)
    import_history_package(build_package(tmp_path / "package.zip"), dry_run=False)

    content = client.get(reverse("dashboard-admin")).content.decode()

    assert "Sisemine aruanne" in content
    assert "vastuolude tõttu graafikult välja jäetud" in content
    assert "Avalik liikmekataloog ja sisemine aruanne loendavad eri asju" in content


def test_the_membership_page_no_longer_carries_its_data_block(
    client, authenticate_viewer, tmp_path
):
    """Moved, not copied — the other half of the pair above."""
    from apps.membership.history_import import import_history_package
    from tests.membership.package_factory import build_package

    authenticate_viewer(client)
    import_history_package(build_package(tmp_path / "package.zip"), dry_run=False)

    content = client.get("/liikmeskond/").content.decode()

    assert "Sisemine aruanne" not in content
    assert "Avalik liikmekataloog ja sisemine aruanne loendavad eri asju" not in content


def test_the_news_data_block_arrived(viewer_client):
    """Uudised' `Andmete kohta`, moved here on 2026-08-17 — the header's
    two-source link and the whole on-page disclosure, together.

    No catalogue entries or GA4 coverage are seeded here on purpose, matching
    the Koduleht test above: the prose has to render for a source that has
    collected nothing.
    """
    content = viewer_client.get(reverse("dashboard-admin")).content.decode()

    assert 'id="uudised-andmeallikad"' in content
    # The rule the block exists to state, regardless of whether data exists.
    assert "Kumbki juhtnupp ei mõjuta teise küsimuse arve." in content
    assert "see on leid" in content


def test_the_news_page_no_longer_carries_its_data_block(viewer_client):
    """Moved, not copied — the other half of the pair above."""
    content = viewer_client.get("/uudised/").content.decode()

    assert "Andmete kohta" not in content


def test_the_shop_data_block_arrived(client, authenticate_viewer, tmp_path):
    """E-pood's `Andmete kohta`, moved here on 2026-08-17 — the header's
    as-of/coverage line and the whole on-page disclosure, together.

    Seeded, unlike the other pairs above: the block describes a default,
    unfiltered `ShopOverview`, and an unseeded source renders `has_source=False`
    on the block itself rather than the prose these assertions name.
    """
    from apps.shop.importing import import_shop_package
    from tests.shop.package_factory import build_package

    authenticate_viewer(client)
    import_shop_package(build_package(tmp_path), dry_run=False)

    content = client.get(reverse("dashboard-admin")).content.decode()

    assert 'id="epood-andmeallikad"' in content
    assert "Käsitsi koostatud väljavõte seisuga" in content
    assert "Lepingupõhjal on tutvustusleht ja tooteleht" in content


def test_the_shop_page_no_longer_carries_its_data_block(client, authenticate_viewer, tmp_path):
    """Moved, not copied — the other half of the pair above."""
    from apps.shop.importing import import_shop_package
    from tests.shop.package_factory import build_package

    authenticate_viewer(client)
    import_shop_package(build_package(tmp_path), dry_run=False)

    content = client.get("/epood/").content.decode()

    assert "Andmete kohta" not in content


def test_andmete_seis_arrived_and_keeps_its_anchor(viewer_client):
    """The other half of the move off Koja töölaud.

    The `id` matters as much as the content: the overview's header chip still
    counts the sources worth disclosing and deep-links straight here, so an
    anchor that changed name would leave that chip pointing at nothing.
    """
    content = viewer_client.get(reverse("dashboard-admin")).content.decode()

    assert 'id="andmete-seis"' in content
    assert "Andmete seis" in content


def test_the_overview_no_longer_carries_andmete_seis(viewer_client):
    """Moved, not copied — and the chip that stayed points here.

    A section deleted from one page and never rendered on the other would pass
    the overview-side assertion just as well, which is why both are named.
    """
    overview = viewer_client.get(reverse("home")).content.decode()
    main = overview.split("<main", 1)[1].split("</main>", 1)[0]

    assert "Andmete seis" not in main
    assert f'href="{reverse("dashboard-admin")}#andmete-seis"' in main
    # The old in-page anchor would now be a link to nothing.
    assert 'href="#andmete-seis"' not in main


def test_an_empty_admin_section_counts_nothing(viewer_client):
    """An empty foundation states its emptiness; it does not count it.

    `0 probleemi` beside a check nobody has moved here would report the absence
    of the check as the absence of problems.

    Scoped to the two sections that are still empty. The whole page carried no
    digit until 2026-08-15; Sündmused' provenance block arrived that day and is
    full of real ones, so a page-wide rule would now be asserting the opposite
    of what it means.
    """
    content = viewer_client.get(reverse("dashboard-admin")).content.decode()

    for note in (
        "Andmekvaliteedi kontrolle ei ole veel siia toodud.",
        "Tehnilist infot ei ole veel siia toodud.",
    ):
        assert note in content
        assert not re.search(r"\d", note), f"an empty section grew a figure: {note}"

    # The shape the rule exists to forbid, in the words it would be written in.
    for fabricated in ("0 probleemi", "0 viga", "0 hoiatust"):
        assert fabricated not in content


def test_admin_is_not_a_primary_navigation_item():
    """It is a maintainer's destination, not one of the Chamber's subjects."""
    keys = {item.key for item in iter_items()}
    labels = {item.label for item in iter_items()}

    assert "admin" not in keys
    assert "Admin" not in labels
    assert len(NAVIGATION) == 5


def test_the_admin_link_sits_beside_the_build_stamp(viewer_client):
    """Findable, and quiet. Both halves matter.

    The class is asserted because it is what makes the link secondary; if it
    ever became `dk-nav-item` the page would have gained a primary navigation
    entry without anybody adding one to the tuple.
    """
    content = viewer_client.get(reverse("home")).content.decode()

    assert 'class="dk-admin-link' in content
    assert f'href="{reverse("dashboard-admin")}"' in content
    # Not styled as, or nested inside, the primary navigation list.
    assert '<a href="/haldus/" class="dk-nav-item' not in content


def test_the_admin_link_reaches_the_admin_page(viewer_client):
    """The link is on the page and the page it names answers."""
    home = viewer_client.get(reverse("home")).content.decode()
    assert reverse("dashboard-admin") in home

    assert viewer_client.get(reverse("dashboard-admin")).status_code == 200


def test_the_admin_page_marks_the_link_current_and_no_module(viewer_client):
    """Opening Admin must not leave a primary item looking selected."""
    response = viewer_client.get(reverse("dashboard-admin"))
    content = response.content.decode()

    assert response.context["active_nav"] == "admin"
    assert "dk-nav-item-active" not in content
    assert "dk-admin-link-active" in content


def test_the_admin_link_is_reachable_without_javascript(viewer_client):
    """The mobile drawer needs Alpine; the `<noscript>` navigation does not.

    Admin lives at the foot of the sidebar, which that fallback does not render,
    so it is repeated there — and a phone with no bundle would otherwise have no
    way to reach the page at all.
    """
    content = viewer_client.get(reverse("home")).content.decode()

    noscript = content.split("<noscript", 1)[1].split("</noscript>", 1)[0]
    assert reverse("dashboard-admin") in noscript
