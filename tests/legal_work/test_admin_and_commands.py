"""Read-only admin, the manual import command and source bootstrap."""

from io import StringIO

import pytest
from django.contrib import admin
from django.core.management import CommandError, call_command
from django.urls import reverse

from apps.legal_work.bootstrap import ensure_legal_work_source
from apps.legal_work.models import LegalWorkFeedState, LegalWorkItem, LegalWorkSnapshot
from apps.sources.models import DataSource, SourceArtifact, UpdateFrequency

pytestmark = pytest.mark.django_db


def admin_for(model):
    return admin.site._registry[model]


def sign_in(client, user, authenticate_viewer):
    authenticate_viewer(client)
    client.force_login(user)


# -- bootstrap ----------------------------------------------------------


def test_the_source_is_created_once_and_reused(db):
    first = ensure_legal_work_source()
    second = ensure_legal_work_source()

    assert first.pk == second.pk
    assert DataSource.objects.filter(slug="oigusloome-onedrive").count() == 1
    assert first.expected_update_frequency == UpdateFrequency.DAILY
    assert first.stale_after_days == 2


def test_the_source_names_no_individual_lawyer(db):
    source = ensure_legal_work_source()

    assert source.responsible_person == ""


# -- admin --------------------------------------------------------------


@pytest.mark.parametrize("model", [LegalWorkSnapshot, LegalWorkItem, LegalWorkFeedState])
def test_admin_is_read_only(rf, superuser, model):
    request = rf.get("/admin/")
    request.user = superuser
    model_admin = admin_for(model)

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False
    assert model_admin.has_view_permission(request) is True


@pytest.mark.parametrize(
    "url_name",
    [
        "admin:legal_work_legalworksnapshot_changelist",
        "admin:legal_work_legalworkitem_changelist",
        "admin:legal_work_legalworkfeedstate_changelist",
    ],
)
def test_admin_changelists_render(
    client, authenticate_viewer, superuser, url_name, imported_snapshot
):
    sign_in(client, superuser, authenticate_viewer)

    assert client.get(reverse(url_name)).status_code == 200


def test_the_item_admin_exposes_no_lawyer_column():
    columns = set(admin_for(LegalWorkItem).list_display)

    assert not columns & {"responsible_person", "lawyer", "assignee", "owner"}


# -- manual import command ----------------------------------------------


def test_manual_dry_run_creates_no_snapshot(make_workbook):
    output = StringIO()

    call_command("import_oigusloome", "--file", str(make_workbook()), "--dry-run", stdout=output)

    assert LegalWorkSnapshot.objects.count() == 0
    assert "Kuivkäivitus" in output.getvalue()


def test_manual_import_publishes_a_snapshot(make_workbook):
    output = StringIO()

    call_command("import_oigusloome", "--file", str(make_workbook()), stdout=output)

    snapshot = LegalWorkSnapshot.objects.get()
    assert snapshot.is_current is True
    assert "Imporditud 3 kirjet" in output.getvalue()


def test_manual_import_reuses_an_existing_artifact_for_identical_content(make_workbook):
    path = make_workbook()
    call_command("import_oigusloome", "--file", str(path), "--dry-run", stdout=StringIO())
    call_command("import_oigusloome", "--file", str(path), "--dry-run", stdout=StringIO())

    assert SourceArtifact.objects.count() == 1


def test_a_missing_file_is_a_clear_command_error():
    with pytest.raises(CommandError, match="ei leitud"):
        call_command("import_oigusloome", "--file", "/nonexistent/synthetic.xlsx")


def test_an_invalid_workbook_fails_the_command(make_workbook):
    broken = make_workbook(dataset_key="not-oigusloome")

    with pytest.raises(CommandError, match="Vale andmestik"):
        call_command("import_oigusloome", "--file", str(broken))
