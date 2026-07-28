"""The private artifact download is the only way out for an original file."""

import pytest
from django.urls import reverse

from apps.audit.models import AuditAction, AuditEvent
from apps.sources.models import AccessLevel
from apps.sources.services import register_artifact, register_external_reference

from .conftest import SYNTHETIC_CSV

pytestmark = pytest.mark.django_db


@pytest.fixture
def artifact(data_source, upload):
    return register_artifact(source=data_source, upload=upload())


def download_url(artifact):
    return reverse("admin:sources_sourceartifact_download", args=[artifact.pk])


def sign_in(client, user, authenticate_viewer):
    authenticate_viewer(client)
    client.force_login(user)


def test_download_url_lives_under_admin_and_is_not_public(artifact, client):
    url = download_url(artifact)

    assert url.startswith("/admin/")

    # No viewer session at all: the viewer gate answers before anything else.
    response = client.get(url)
    assert response.status_code == 302
    assert response.url.startswith("/sisene/")


def test_anonymous_staff_area_requires_admin_login(artifact, client, authenticate_viewer):
    authenticate_viewer(client)

    response = client.get(download_url(artifact))

    assert response.status_code == 302
    assert "/admin/login/" in response.url


def test_non_staff_user_cannot_download(artifact, client, authenticate_viewer, viewer_only_user):
    sign_in(client, viewer_only_user, authenticate_viewer)

    response = client.get(download_url(artifact))

    assert response.status_code == 302
    assert "/admin/login/" in response.url


def test_staff_without_the_permission_is_refused(artifact, client, authenticate_viewer, staff_user):
    sign_in(client, staff_user, authenticate_viewer)

    response = client.get(download_url(artifact))

    assert response.status_code == 403


def test_authorised_staff_receives_the_original_as_an_attachment(
    artifact, client, authenticate_viewer, downloader_user
):
    sign_in(client, downloader_user, authenticate_viewer)

    response = client.get(download_url(artifact))

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == SYNTHETIC_CSV
    assert response.headers["Content-Type"] == "application/octet-stream"
    assert "attachment;" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_a_successful_download_is_audited(artifact, client, authenticate_viewer, downloader_user):
    sign_in(client, downloader_user, authenticate_viewer)

    client.get(download_url(artifact))

    event = AuditEvent.objects.get(action=AuditAction.ARTIFACT_DOWNLOADED)
    assert event.actor_id == downloader_user.pk
    assert event.object_id == str(artifact.pk)


def test_a_refused_download_is_not_audited(artifact, client, authenticate_viewer, staff_user):
    sign_in(client, staff_user, authenticate_viewer)

    client.get(download_url(artifact))

    assert not AuditEvent.objects.filter(action=AuditAction.ARTIFACT_DOWNLOADED).exists()


def test_restricted_artifacts_need_a_superuser(
    data_source, upload, client, authenticate_viewer, downloader_user, superuser
):
    restricted = register_artifact(
        source=data_source,
        upload=upload(),
        access_level=AccessLevel.RESTRICTED,
    )

    sign_in(client, downloader_user, authenticate_viewer)
    assert client.get(download_url(restricted)).status_code == 403

    client.logout()
    sign_in(client, superuser, authenticate_viewer)
    assert client.get(download_url(restricted)).status_code == 200


def test_external_reference_has_nothing_to_download(
    data_source, client, authenticate_viewer, downloader_user
):
    external = register_external_reference(
        source=data_source,
        external_reference="https://example.invalid/synthetic",
    )
    sign_in(client, downloader_user, authenticate_viewer)

    assert client.get(download_url(external)).status_code == 404


def test_unknown_artifact_is_not_found(client, authenticate_viewer, downloader_user):
    sign_in(client, downloader_user, authenticate_viewer)

    response = client.get(reverse("admin:sources_sourceartifact_download", args=[999999]))

    assert response.status_code == 404
