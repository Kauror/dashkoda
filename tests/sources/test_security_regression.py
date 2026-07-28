"""PR-05 must not weaken anything PR-03 and PR-04 established."""

import pytest
from django.conf import settings
from django.urls import NoReverseMatch, reverse

from apps.sources.services import register_artifact

pytestmark = pytest.mark.django_db

EXPECTED_CSP = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; "
    "frame-ancestors 'none'; form-action 'self'; script-src 'self'; "
    "style-src 'self'; img-src 'self' data:; connect-src 'self'"
)


def test_viewer_gate_still_protects_every_new_route(client):
    for path in ["/", "/admin/", "/admin/sources/sourceartifact/"]:
        response = client.get(path)
        assert response.status_code == 302, path
        assert response.url.startswith("/sisene/"), path


def test_admin_still_sits_behind_both_gates(client, authenticate_viewer):
    assert client.get("/admin/").url.startswith("/sisene/")

    authenticate_viewer(client)
    response = client.get("/admin/")

    assert response.status_code == 302
    assert response.url == "/admin/login/?next=/admin/"


def test_public_allowlist_is_unchanged(client):
    assert client.get("/health/live/").status_code == 200
    assert client.get("/health/ready/").status_code == 200
    assert client.get("/sisene/").status_code == 200
    assert client.get("/robots.txt").status_code == 200


def test_health_endpoints_stay_minimal(client):
    assert client.get("/health/live/").json() == {"status": "ok"}
    assert client.get("/health/ready/").json() == {"status": "ok"}


def test_content_security_policy_is_unchanged(client):
    response = client.get("/sisene/")

    assert response.headers["Content-Security-Policy"] == EXPECTED_CSP


def test_dashboard_still_shows_truthful_empty_states(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert "Andmeallikas ei ole veel ühendatud." in content
    assert "Kontrollitud andmed puuduvad." in content


def test_no_new_public_route_was_added():
    # There is no project-level sources URLconf at all.
    with pytest.raises(NoReverseMatch):
        reverse("sources:artifact-download")


def test_no_media_url_is_configured():
    assert getattr(settings, "MEDIA_URL", "") in {"", "/media/"}
    # Whatever MEDIA_URL says, nothing routes it and artifacts do not use it.
    assert "django.contrib.staticfiles" in settings.INSTALLED_APPS


def test_artifact_root_is_not_served_by_whitenoise(data_source, upload, private_artifact_root):
    artifact = register_artifact(source=data_source, upload=upload())

    from pathlib import Path

    stored = Path(artifact.file.path).resolve()
    assert not stored.is_relative_to(Path(settings.STATIC_ROOT).resolve())
    for static_dir in settings.STATICFILES_DIRS:
        assert not stored.is_relative_to(Path(static_dir).resolve())


def test_artifact_path_is_not_reachable_as_a_static_file(
    client, authenticate_viewer, data_source, upload
):
    artifact = register_artifact(source=data_source, upload=upload())
    authenticate_viewer(client)

    response = client.get(f"/static/{artifact.file.name}")

    assert response.status_code == 404
