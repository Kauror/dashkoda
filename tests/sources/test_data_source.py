import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.audit.models import AuditAction, AuditEvent
from apps.sources.models import DataSource
from apps.sources.services import (
    create_data_source,
    deactivate_data_source,
    register_artifact,
    update_data_source,
)

pytestmark = pytest.mark.django_db


def test_slug_is_unique(data_source):
    with pytest.raises(IntegrityError), transaction.atomic():
        DataSource.objects.create(slug=data_source.slug, name="Duplikaat")


def test_authority_rank_must_be_positive():
    with pytest.raises(IntegrityError), transaction.atomic():
        DataSource.objects.create(slug="synthetic-zero-rank", name="Null", authority_rank=0)


def test_lower_authority_rank_sorts_first(data_source, other_data_source):
    assert list(DataSource.objects.values_list("slug", flat=True)) == [
        data_source.slug,
        other_data_source.slug,
    ]


def test_stale_after_days_accepts_null_and_zero_but_not_negative():
    create_data_source(slug="synthetic-null-stale", name="Null", stale_after_days=None)
    create_data_source(slug="synthetic-zero-stale", name="Zero", stale_after_days=0)

    with pytest.raises(IntegrityError), transaction.atomic():
        DataSource.objects.create(
            slug="synthetic-negative-stale",
            name="Negative",
            stale_after_days=-1,
        )


def test_referenced_source_cannot_be_deleted(data_source, upload):
    register_artifact(source=data_source, upload=upload(), original_name="synthetic.csv")

    with pytest.raises(ProtectedError), transaction.atomic():
        data_source.delete()

    assert DataSource.objects.filter(pk=data_source.pk).exists()


def test_unreferenced_source_can_still_be_deleted(data_source):
    data_source.delete()

    assert not DataSource.objects.filter(pk=data_source.pk).exists()


def test_inactive_source_remains_queryable(inactive_source):
    assert DataSource.objects.filter(pk=inactive_source.pk).exists()
    assert DataSource.objects.get(pk=inactive_source.pk).is_active is False


def test_creation_records_an_audit_event(data_source):
    event = AuditEvent.objects.get(
        action=AuditAction.DATA_SOURCE_CREATED,
        object_id=str(data_source.pk),
    )

    assert event.object_type == "sources.datasource"
    assert event.change_summary["slug"] == data_source.slug


def test_material_update_records_what_changed(data_source):
    update_data_source(data_source, name="Uus nimi", authority_rank=5)

    event = AuditEvent.objects.get(action=AuditAction.DATA_SOURCE_UPDATED)
    changed = event.change_summary["changed"]
    assert changed["name"]["to"] == "Uus nimi"
    assert changed["authority_rank"] == {"from": 10, "to": 5}


def test_update_without_changes_records_nothing(data_source):
    before = AuditEvent.objects.count()

    update_data_source(data_source, name=data_source.name)

    assert AuditEvent.objects.count() == before


def test_deactivation_is_audited_as_deactivation(data_source):
    deactivate_data_source(data_source)

    assert DataSource.objects.get(pk=data_source.pk).is_active is False
    assert AuditEvent.objects.filter(action=AuditAction.DATA_SOURCE_DEACTIVATED).exists()
