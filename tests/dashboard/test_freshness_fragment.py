import pytest

pytestmark = pytest.mark.django_db

FRAGMENT_URL = "/dashboard/varskus/"


def test_fragment_is_protected_like_every_other_route(client):
    response = client.get(FRAGMENT_URL)

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")


def test_expired_session_gets_an_hx_redirect_instead_of_a_swapped_login_page(client):
    response = client.get(FRAGMENT_URL, headers={"HX-Request": "true"})

    assert response.status_code == 204
    assert response.headers["HX-Redirect"].startswith("/sisene/?")
    assert b"PIN" not in response.content


def test_authenticated_fragment_returns_only_the_partial(client, authenticate_viewer):
    authenticate_viewer(client)

    response = client.get(FRAGMENT_URL, headers={"HX-Request": "true"})
    content = response.content.decode()

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "<html" not in content
    assert "Andmeallikas ei ole veel ühendatud." in content
    assert "Ühendamata" in content


def test_fragment_is_read_only(client, authenticate_viewer):
    authenticate_viewer(client)

    assert client.post(FRAGMENT_URL).status_code == 405
