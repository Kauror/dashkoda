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
