from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.test import Client

pytestmark = pytest.mark.django_db


def test_protected_home_redirects_to_login_with_internal_next(client):
    response = client.get("/")

    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == "/sisene/"
    assert parse_qs(parsed.query) == {"next": ["/"]}


def test_unknown_route_is_protected_before_it_can_return_404(client):
    response = client.get("/tundmatu/")

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")


def test_login_page_is_public_and_uses_password_input(client):
    response = client.get("/sisene/")

    assert response.status_code == 200
    assert b'type="password"' in response.content
    assert b'autocomplete="current-password"' in response.content
    assert b'aria-live="polite"' in response.content


def test_correct_pin_rotates_session_and_grants_seven_day_access(client, viewer_pin):
    session = client.session
    session["pre_login"] = True
    session.save()
    previous_session_key = session.session_key

    response = client.post("/sisene/", {"pin": viewer_pin, "next": "/"})

    assert response.status_code == 302
    assert response.url == "/"
    session = client.session
    assert session.session_key != previous_session_key
    assert session[settings.VIEWER_SESSION_AUTHENTICATED_KEY] is True
    assert session[settings.VIEWER_SESSION_VERSION_KEY] == settings.VIEWER_PIN_VERSION
    assert 604_700 <= session.get_expiry_age() <= 604_800
    assert client.get("/").status_code == 200


def test_wrong_pin_does_not_authenticate_and_clears_field(client):
    response = client.post("/sisene/", {"pin": "1111"})

    assert response.status_code == 200
    assert b"PIN-kood ei ole" in response.content
    assert b'value="1111"' not in response.content
    assert settings.VIEWER_SESSION_AUTHENTICATED_KEY not in client.session


def test_pin_version_change_invalidates_existing_session(
    client,
    authenticate_viewer,
    settings,
):
    authenticate_viewer(client, version=3)
    settings.VIEWER_PIN_VERSION = 4

    response = client.get("/")

    assert response.status_code == 302
    assert response.url.startswith("/sisene/")
    assert settings.VIEWER_SESSION_AUTHENTICATED_KEY not in client.session


def test_external_next_url_is_not_followed(client, viewer_pin):
    response = client.post(
        "/sisene/",
        {"pin": viewer_pin, "next": "https://example.invalid/steal"},
    )

    assert response.status_code == 302
    assert response.url == "/"


def test_logout_requires_post_and_csrf(authenticate_viewer):
    client = Client(enforce_csrf_checks=True)
    login_page = client.get("/sisene/")
    csrf_token = login_page.cookies["csrftoken"].value
    login_response = client.post(
        "/sisene/",
        {"pin": "8642", "csrfmiddlewaretoken": csrf_token},
    )
    assert login_response.status_code == 302

    assert client.get("/logi-valja/").status_code == 405
    assert client.post("/logi-valja/").status_code == 403

    csrf_token = client.cookies["csrftoken"].value
    response = client.post(
        "/logi-valja/",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert response.status_code == 302
    assert response.url == "/sisene/"
    assert settings.VIEWER_SESSION_AUTHENTICATED_KEY not in client.session


def test_admin_requires_viewer_access_then_uses_django_admin_login(
    client,
    authenticate_viewer,
):
    unauthenticated = client.get("/admin/")
    assert unauthenticated.status_code == 302
    assert unauthenticated.url.startswith("/sisene/")

    authenticate_viewer(client)
    viewer_authenticated = client.get("/admin/")
    assert viewer_authenticated.status_code == 302
    assert viewer_authenticated.url == "/admin/login/?next=/admin/"


def test_authenticated_home_renders_the_dashboard_shell(client, authenticate_viewer):
    authenticate_viewer(client)

    response = client.get("/")

    assert response.status_code == 200
    assert b"DashKoda" in response.content
    assert "Koja juhatuse töölaud".encode() in response.content
