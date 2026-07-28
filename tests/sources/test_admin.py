import pytest
from django.contrib import admin
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.sources.admin import DataSourceAdmin, ImportRunAdmin, SourceArtifactAdmin
from apps.sources.models import DataSource, ImportRun, SourceArtifact
from apps.sources.services import build_import_run, register_artifact, start_import_run

from .conftest import sign_in

pytestmark = pytest.mark.django_db


def admin_for(model):
    return admin.site._registry[model]


# --------------------------------------------------------------------------
# Registration and permissions
# --------------------------------------------------------------------------


def test_all_four_models_are_registered():
    assert isinstance(admin_for(DataSource), DataSourceAdmin)
    assert isinstance(admin_for(SourceArtifact), SourceArtifactAdmin)
    assert isinstance(admin_for(ImportRun), ImportRunAdmin)
    assert admin_for(AuditEvent) is not None


def test_artifact_admin_denies_change_and_delete(rf, superuser, data_source, upload):
    artifact = register_artifact(source=data_source, upload=upload())
    request = rf.get("/admin/")
    request.user = superuser
    model_admin = admin_for(SourceArtifact)

    assert model_admin.has_add_permission(request) is True
    assert model_admin.has_change_permission(request, artifact) is False
    assert model_admin.has_delete_permission(request, artifact) is False


def test_import_run_admin_is_inspection_only(rf, superuser):
    request = rf.get("/admin/")
    request.user = superuser
    model_admin = admin_for(ImportRun)

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False


def test_audit_admin_denies_everything(rf, superuser):
    request = rf.get("/admin/")
    request.user = superuser
    model_admin = admin_for(AuditEvent)

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False
    assert model_admin.has_view_permission(request) is True


def test_referenced_source_cannot_be_deleted_through_admin(rf, superuser, data_source, upload):
    request = rf.get("/admin/")
    request.user = superuser
    model_admin = admin_for(DataSource)

    assert model_admin.has_delete_permission(request, data_source) is True

    register_artifact(source=data_source, upload=upload())

    assert model_admin.has_delete_permission(request, data_source) is False


# --------------------------------------------------------------------------
# Pages render and writes go through the services
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url_name",
    [
        "admin:sources_datasource_changelist",
        "admin:sources_sourceartifact_changelist",
        "admin:sources_importrun_changelist",
        "admin:audit_auditevent_changelist",
    ],
)
def test_changelists_render_for_a_superuser(
    client, authenticate_viewer, superuser, url_name, data_source
):
    sign_in(client, superuser, authenticate_viewer)

    assert client.get(reverse(url_name)).status_code == 200


def test_creating_a_source_through_admin_records_an_audit_event(
    client, authenticate_viewer, superuser
):
    sign_in(client, superuser, authenticate_viewer)

    response = client.post(
        reverse("admin:sources_datasource_add"),
        {
            "slug": "synthetic-admin-source",
            "name": "Admini kaudu loodud testallikas",
            "source_type": "other",
            "authority_tier": "unclassified",
            "authority_rank": "50",
            "responsible_person": "",
            "expected_update_frequency": "unknown",
            "stale_after_days": "",
            "description": "",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    source = DataSource.objects.get(slug="synthetic-admin-source")
    assert AuditEvent.objects.filter(
        action="data_source.created", object_id=str(source.pk)
    ).exists()


def test_registering_an_artifact_through_admin_calculates_the_checksum(
    client, authenticate_viewer, superuser, data_source, upload
):
    sign_in(client, superuser, authenticate_viewer)

    response = client.post(
        reverse("admin:sources_sourceartifact_add"),
        {
            "source": str(data_source.pk),
            "external_reference": "",
            "access_level": "staff_only",
            "upload": upload(),
        },
    )

    assert response.status_code == 302
    artifact = SourceArtifact.objects.get()
    assert len(artifact.sha256) == 64
    assert artifact.uploaded_by_id == superuser.pk
    assert AuditEvent.objects.filter(action="source_artifact.registered").exists()


def test_artifact_admin_refuses_both_file_and_reference(
    client, authenticate_viewer, superuser, data_source, upload
):
    sign_in(client, superuser, authenticate_viewer)

    response = client.post(
        reverse("admin:sources_sourceartifact_add"),
        {
            "source": str(data_source.pk),
            "external_reference": "https://example.invalid/synthetic",
            "access_level": "staff_only",
            "upload": upload(),
        },
    )

    assert response.status_code == 200
    assert SourceArtifact.objects.count() == 0


def test_artifact_admin_reports_a_rejected_extension_as_a_form_error(
    client, authenticate_viewer, superuser, data_source, upload
):
    sign_in(client, superuser, authenticate_viewer)

    response = client.post(
        reverse("admin:sources_sourceartifact_add"),
        {
            "source": str(data_source.pk),
            "external_reference": "",
            "access_level": "staff_only",
            "upload": upload(name="payload.exe"),
        },
    )

    # A refused upload is a readable form error, never an unhandled exception.
    assert response.status_code == 200
    assert "laiend" in response.content.decode()
    assert SourceArtifact.objects.count() == 0


def test_artifact_admin_reports_a_duplicate_upload_as_a_form_error(
    client, authenticate_viewer, superuser, data_source, upload
):
    register_artifact(source=data_source, upload=upload())
    sign_in(client, superuser, authenticate_viewer)

    response = client.post(
        reverse("admin:sources_sourceartifact_add"),
        {
            "source": str(data_source.pk),
            "external_reference": "",
            "access_level": "staff_only",
            "upload": upload(),
        },
    )

    assert response.status_code == 200
    assert "sama sisuga" in response.content.decode()
    assert SourceArtifact.objects.count() == 1


def test_artifact_detail_page_never_shows_a_file_url(
    client, authenticate_viewer, superuser, data_source, upload
):
    artifact = register_artifact(source=data_source, upload=upload())
    sign_in(client, superuser, authenticate_viewer)

    response = client.get(reverse("admin:sources_sourceartifact_change", args=[artifact.pk]))
    content = response.content.decode()

    assert response.status_code == 200
    assert artifact.sha256[:12] in content or artifact.sha256 in content
    assert artifact.file.name not in content
    assert "/media/" not in content


def test_import_run_admin_offers_no_way_to_start_one(
    client, authenticate_viewer, superuser, data_source, upload
):
    artifact = register_artifact(source=data_source, upload=upload())
    run = build_import_run(artifact=artifact, importer_name="synthetic", schema_version="v1")
    start_import_run(run)
    sign_in(client, superuser, authenticate_viewer)

    response = client.get(reverse("admin:sources_importrun_changelist"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "synthetic" in content
    # No add route for import runs, and no importer to trigger.
    assert "/admin/sources/importrun/add/" not in content
    assert "Käivita" not in content
