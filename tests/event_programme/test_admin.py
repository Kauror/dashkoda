"""The event programme is inspected in the admin, never edited there."""

from __future__ import annotations

import pytest
from django.contrib import admin

from apps.event_programme.admin import (
    EventProgrammeFeedStateAdmin,
    EventProgrammeItemAdmin,
    EventProgrammeSnapshotAdmin,
)
from apps.event_programme.models import (
    EventProgrammeFeedState,
    EventProgrammeItem,
    EventProgrammeSnapshot,
)

pytestmark = pytest.mark.django_db

MODELS = (
    (EventProgrammeSnapshot, EventProgrammeSnapshotAdmin),
    (EventProgrammeItem, EventProgrammeItemAdmin),
    (EventProgrammeFeedState, EventProgrammeFeedStateAdmin),
)


def admin_for(model):
    return admin.site._registry[model]


@pytest.mark.parametrize(("model", "expected"), MODELS)
def test_each_model_is_registered_read_only(model, expected):
    assert isinstance(admin_for(model), expected)


@pytest.mark.parametrize(("model", "_expected"), MODELS)
def test_no_model_may_be_added_changed_or_deleted(rf, superuser, model, _expected):
    request = rf.get("/admin/")
    request.user = superuser
    model_admin = admin_for(model)

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False
    assert model_admin.has_view_permission(request) is True


@pytest.mark.parametrize(("model", "_expected"), MODELS)
def test_every_field_is_read_only(rf, superuser, model, _expected):
    request = rf.get("/admin/")
    request.user = superuser
    model_admin = admin_for(model)

    editable = {field.name for field in model._meta.fields} - set(
        model_admin.get_readonly_fields(request)
    )

    assert editable == set()


def test_the_admin_offers_the_inspection_fields_an_operator_needs():
    """The snapshot's counts and the feed state's timestamps, on the list page.

    These are what an operator checks after a morning run, so they belong where
    they can be read without opening a row.
    """
    snapshot_admin = admin_for(EventProgrammeSnapshot)
    assert set(snapshot_admin.list_display) >= {
        "export_refreshed_at",
        "is_current",
        "canonical_event_count",
        "dated_event_count",
        "linked_public_url_count",
        "review_required_count",
    }

    state_admin = admin_for(EventProgrammeFeedState)
    assert set(state_admin.list_display) >= {
        "last_result",
        "last_checked_at",
        "last_successful_sync_at",
        "last_changed_at",
        "current_snapshot",
    }

    item_admin = admin_for(EventProgrammeItem)
    assert set(item_admin.list_display) >= {
        "start_date",
        "service_code",
        "tag_label",
        "event_status",
        "has_public_url",
        "review_required",
    }
    assert "event_year" in item_admin.list_filter
    assert "public_link_status" in item_admin.list_filter


def test_the_admin_offers_no_url_editor():
    """Linking is decided in `DASH_URL_OVERRIDES`, not in DashKoda.

    A second place to decide it would give the dashboard an opinion the workbook
    could not see.
    """
    item_admin = admin_for(EventProgrammeItem)

    assert item_admin.get_readonly_fields(None) is not None
    assert "public_url" in item_admin.get_readonly_fields(None)
    assert getattr(item_admin, "actions", None) in (None, [], ())
