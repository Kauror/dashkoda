import pytest

pytestmark = pytest.mark.django_db


def test_public_routes_remain_available(client):
    assert client.get("/health/live/").status_code == 200
    assert client.get("/health/ready/").status_code == 200
    assert client.get("/sisene/").status_code == 200

    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert robots.content == b"User-agent: *\nDisallow: /\n"


@pytest.mark.parametrize(
    "path",
    ["/", "/sisene/", "/robots.txt", "/health/live/"],
)
def test_security_headers_are_present(client, path):
    response = client.get(path)

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert response.headers["Permissions-Policy"] == ("camera=(), microphone=(), geolocation=()")
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'self'; script-src 'self'; "
        "style-src 'self'; img-src 'self' data:; connect-src 'self'"
    )


def test_protected_html_is_not_cacheable(client, authenticate_viewer):
    authenticate_viewer(client)

    response = client.get("/")

    assert response.headers["Cache-Control"] == "private, no-store"


def test_session_cookie_security_defaults(settings):
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.SESSION_COOKIE_SAMESITE == "Lax"
    assert settings.SESSION_COOKIE_AGE == 604_800
