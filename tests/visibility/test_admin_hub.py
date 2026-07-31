"""The central staff-only data-entry hub.

The hub must be a signpost inside the existing admin boundary and nothing more.
Every test here is really one question: did adding it create a second way in?
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.data_entry import DATA_ENTRY_MODULES, available_modules

from .conftest import HUB_URL

pytestmark = pytest.mark.django_db


# -- access -------------------------------------------------------------


def test_the_hub_requires_staff(viewer_client):
    """The shared viewer PIN alone must never be sufficient."""
    response = viewer_client.get(HUB_URL)

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_an_anonymous_visitor_is_sent_to_the_viewer_login(client):
    response = client.get(HUB_URL)

    assert response.status_code == 302
    assert "/sisene/" in response["Location"]


def test_a_non_staff_account_cannot_reach_the_hub(nonstaff_client):
    response = nonstaff_client.get(HUB_URL)

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_staff_can_open_the_hub(staff_client):
    response = staff_client.get(HUB_URL)

    assert response.status_code == 200
    assert "Andmete sisestamine" in response.content.decode()


def test_the_hub_rejects_a_post(staff_client):
    assert staff_client.post(HUB_URL, {}).status_code == 405


# -- what it links to ---------------------------------------------------


def test_the_hub_links_to_the_existing_membership_entry_form(staff_client):
    body = staff_client.get(HUB_URL).content.decode()

    assert "Liikmeskonna aruanne" in body
    assert "/admin/membership/internal-report/new/" in body


def test_the_hub_links_to_the_new_visibility_entry_form(staff_client):
    body = staff_client.get(HUB_URL).content.decode()

    assert "Kanalite statistika" in body
    assert "/admin/data-entry/visibility/new/" in body
    assert "/admin/data-entry/visibility/" in body


def test_the_membership_link_actually_works(staff_client):
    body = staff_client.get(HUB_URL).content.decode()
    assert reverse("membership-admin-report-new") in body

    assert staff_client.get(reverse("membership-admin-report-new")).status_code == 200


def test_the_visibility_link_actually_works(staff_client):
    assert staff_client.get(reverse("visibility-admin-entry-new")).status_code == 200


def test_every_registered_module_resolves(staff_client):
    """A hub entry naming a route that does not exist would be a dead link."""
    assert len(available_modules()) == len(DATA_ENTRY_MODULES)
    for module in DATA_ENTRY_MODULES:
        assert module.is_available, module.key
        assert len(module.available_links) == len(module.links), module.key


def test_the_admin_index_links_to_the_hub(staff_client):
    body = staff_client.get("/admin/").content.decode()

    assert "Andmete sisestamine" in body
    assert HUB_URL in body


def test_the_admin_index_still_shows_djangos_own_app_list(staff_client):
    """The hub is added above the app list, not instead of it."""
    body = staff_client.get("/admin/").content.decode()

    assert "Nähtavuse sisestused" in body
    assert "Auditisündmused" in body


def test_the_hub_does_not_duplicate_the_model_app_list(staff_client):
    body = staff_client.get(HUB_URL).content.decode()

    assert "Auditisündmused" not in body
    assert "Algfailid" not in body


# -- no second authentication system ------------------------------------


def test_no_second_admin_site_or_login_was_created(staff_client):
    """One admin site, one login, one permission model."""
    import re

    from django.contrib import admin

    body = staff_client.get(HUB_URL).content.decode()

    assert admin.site.name == "admin"
    assert 'type="password"' not in body
    # Whatever forms the admin chrome renders, none of them posts anywhere the
    # hub owns. The hub is a signpost; it writes nothing.
    posted_to = re.findall(r'<form[^>]*action="([^"]*)"', body)
    assert not any(action.startswith("/admin/data-entry") for action in posted_to), posted_to


def test_the_hub_holds_no_data_entry_form_of_its_own(staff_client):
    body = staff_client.get(HUB_URL).content.decode()

    assert 'name="action"' not in body
    assert 'name="metric_facebook_followers"' not in body
