"""`Admin` at `/haldus/` — the maintainers' page.

Three things are worth pinning about a page that deliberately holds nothing:

- **it is not Django's admin.** `/admin/` is the Django admin site plus the
  staff data-entry routes, and taking or shadowing that prefix would break
  workflows that have their own `is_staff` requirement. This page grants
  nothing;
- **it is protected exactly like every other dashboard page**, by the viewer PIN
  and nothing extra;
- **it invents no figure.** In particular it must never say "0 probleemi": the
  checks have not been moved here yet, and a page reporting its own emptiness as
  a clean bill of health is the one reading a maintainer must not be given.

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


def test_the_admin_page_fabricates_no_figure(viewer_client):
    """An empty foundation states its emptiness; it does not count it.

    `0 probleemi` beside a check that has not been moved here would report the
    absence of the check as the absence of problems.

    Read off the **visible text**, not the markup. Every utility class in the
    stylesheet carries digits — `gap-1`, `py-3` — so scanning the raw HTML would
    pass for a reason unrelated to what is on screen, and go on passing after
    somebody put a real number on the page.
    """
    content = viewer_client.get(reverse("dashboard-admin")).content.decode()

    # From after the opening tag, not from after `<main` — its own class
    # attribute is full of `px-4` and would be counted as page content.
    main = content.split("<main", 1)[1].split(">", 1)[1].split("</main>", 1)[0]
    text = " ".join(re.sub(r"<[^>]*>", " ", main).split())

    assert text, "the Admin page rendered no visible text at all"
    assert not re.search(r"\d", text), f"the Admin page grew a number it cannot support: {text}"


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
