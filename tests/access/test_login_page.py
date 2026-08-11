"""Presentation of the viewer gate.

PR-04 restyles `/sisene/` with the shared design system. The access behaviour
itself is covered by `test_access_flow` and `test_rate_limit`; these assertions
only guard what the page shows.
"""

import pytest

pytestmark = pytest.mark.django_db


def test_login_page_uses_the_design_system(client):
    content = client.get("/sisene/").content.decode()

    assert "PIN-kood" in content
    assert '<link rel="stylesheet" href="/static/build/styles.css">' in content
    assert "dk-button-primary" in content


def test_login_page_keeps_its_heading_for_screen_readers_only(client):
    """The gate shows the logo, a field and a button, and no prose.

    `Sisene DashKodasse` and the `Koja sisene juhatuse ja juhtkonna töölaud.`
    tagline were removed from the visible page: a gate with one field does not
    need to be told apart from anything, and the logo says whose door it is.

    The `h1` is hidden rather than deleted, which is the distinction this test
    exists to hold. A page with no level-1 heading gives a screen-reader user
    nothing to orient by, and here the whole page is the form.
    """
    content = client.get("/sisene/").content.decode()

    assert '<h1 class="sr-only">Sisene DashKodasse</h1>' in content
    assert "Koja sisene juhatuse ja juhtkonna töölaud." not in content


def test_login_page_labels_the_pin_field_accessibly(client):
    content = client.get("/sisene/").content.decode()

    assert 'for="id_pin"' in content
    assert 'id="id_pin"' in content
    assert 'aria-describedby="pin-errors"' in content
    assert 'id="pin-errors"' in content
    assert 'aria-live="polite"' in content


def test_login_page_shows_an_accessible_error_without_security_detail(client):
    response = client.post("/sisene/", {"pin": "1111"})
    content = response.content.decode()

    assert "PIN-kood ei ole õige." in content
    assert 'aria-live="polite"' in content
    assert "hash" not in content.lower()
    assert "rate" not in content.lower()


def test_login_page_never_renders_a_pin_value(client):
    content = client.post("/sisene/", {"pin": "1111"}).content.decode()

    assert 'value="1111"' not in content
    assert "1111" not in content


def test_login_page_loads_no_remote_asset(client):
    content = client.get("/sisene/").content.decode()

    assert "https://" not in content
    assert "<script>" not in content
    assert 'style="' not in content
