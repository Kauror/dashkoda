import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, transaction

from apps.audit.models import AuditEvent, AuditEventImmutable
from apps.audit.redaction import MASK, redact
from apps.audit.services import record_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def actor(db):
    return get_user_model().objects.create_user(
        username="synthetic-actor",
        password="synthetic-test-password",
    )


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------


def test_service_records_an_event_from_an_object(actor, data_source):
    event = record_event(action="test.action", obj=data_source, actor=actor)

    assert event.object_type == "sources.datasource"
    assert event.object_id == str(data_source.pk)
    assert event.actor_id == actor.pk


def test_service_accepts_an_explicit_type_and_id():
    event = record_event(action="test.action", object_type="thing.gone", object_id="404")

    assert event.object_type == "thing.gone"
    assert event.object_id == "404"
    assert event.actor is None


def test_service_needs_something_to_point_at():
    with pytest.raises(ValueError):
        record_event(action="test.action")


def test_an_anonymous_caller_is_recorded_as_a_system_action():
    class Anonymous:
        is_authenticated = False

    event = record_event(action="test.action", object_type="t", object_id="1", actor=Anonymous())

    assert event.actor is None


def test_object_identity_survives_the_object_itself(data_source):
    event = record_event(action="test.action", obj=data_source)
    data_source.delete()

    stored = AuditEvent.objects.get(pk=event.pk)
    assert stored.object_type == "sources.datasource"
    assert stored.object_id != ""


def test_actor_is_set_to_null_when_the_user_is_removed(actor):
    event = record_event(action="test.action", object_type="t", object_id="1", actor=actor)

    actor.delete()

    stored = AuditEvent.objects.get(pk=event.pk)
    assert stored.actor is None
    assert stored.action == "test.action"


def test_newest_events_come_first():
    record_event(action="a", object_type="t", object_id="1")
    record_event(action="b", object_type="t", object_id="2")

    assert list(AuditEvent.objects.values_list("action", flat=True)) == ["b", "a"]


def test_correlation_id_groups_one_operation():
    correlation = uuid.uuid4()
    record_event(action="a", object_type="t", object_id="1", correlation_id=correlation)
    record_event(action="b", object_type="t", object_id="2", correlation_id=correlation)
    record_event(action="c", object_type="t", object_id="3")

    assert AuditEvent.objects.filter(correlation_id=correlation).count() == 2


# --------------------------------------------------------------------------
# Append-only
# --------------------------------------------------------------------------


def test_an_event_cannot_be_updated():
    event = record_event(action="a", object_type="t", object_id="1")
    event.action = "tampered"

    with pytest.raises(AuditEventImmutable):
        event.save()

    assert AuditEvent.objects.get(pk=event.pk).action == "a"


def test_an_event_cannot_be_deleted():
    event = record_event(action="a", object_type="t", object_id="1")

    with pytest.raises(AuditEventImmutable):
        event.delete()

    assert AuditEvent.objects.filter(pk=event.pk).exists()


def test_queryset_update_and_delete_are_blocked():
    record_event(action="a", object_type="t", object_id="1")

    with pytest.raises(AuditEventImmutable):
        AuditEvent.objects.all().update(action="tampered")
    with pytest.raises(AuditEventImmutable):
        AuditEvent.objects.all().delete()
    with pytest.raises(AuditEventImmutable):
        AuditEvent.objects.filter(action="a").delete()


def test_bulk_update_is_blocked():
    event = record_event(action="a", object_type="t", object_id="1")
    event.action = "tampered"

    with pytest.raises(AuditEventImmutable):
        AuditEvent.objects.bulk_update([event], ["action"])


def test_the_database_itself_refuses_raw_updates_and_deletes():
    event = record_event(action="a", object_type="t", object_id="1")

    with pytest.raises(Exception), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE audit_auditevent SET action = %s WHERE id = %s",
                ["tampered", event.pk],
            )

    with pytest.raises(Exception), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM audit_auditevent WHERE id = %s", [event.pk])

    assert AuditEvent.objects.get(pk=event.pk).action == "a"


def test_the_only_permitted_mutation_is_releasing_the_actor(actor):
    """SET_NULL needs one narrow exception; nothing may ride along with it."""
    event = record_event(action="a", object_type="t", object_id="1", actor=actor)

    with pytest.raises(Exception), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE audit_auditevent SET actor_id = NULL, action = %s WHERE id = %s",
                ["tampered", event.pk],
            )

    # Clearing the actor alone is allowed, which is what user deletion does.
    with connection.cursor() as cursor:
        cursor.execute("UPDATE audit_auditevent SET actor_id = NULL WHERE id = %s", [event.pk])

    stored = AuditEvent.objects.get(pk=event.pk)
    assert stored.actor_id is None
    assert stored.action == "a"


def test_an_actor_cannot_be_attached_after_the_fact(actor):
    event = record_event(action="a", object_type="t", object_id="1")

    with pytest.raises(Exception), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE audit_auditevent SET actor_id = %s WHERE id = %s",
                [actor.pk, event.pk],
            )

    assert AuditEvent.objects.get(pk=event.pk).actor_id is None


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "pin",
        "viewer_pin_hash",
        "password",
        "api_key",
        "secret",
        "token",
        "authorization",
        "cookie",
        "connection_string",
        "csrf_token",
    ],
)
def test_sensitive_keys_are_masked(key):
    event = record_event(action="a", object_type="t", object_id="1", change_summary={key: "1925"})

    assert event.change_summary[key] == MASK
    assert "1925" not in str(AuditEvent.objects.get(pk=event.pk).change_summary)


def test_checksums_are_kept_because_they_are_not_secrets():
    summary = {"sha256": "a" * 64, "import_key": "b" * 64}

    assert redact(summary) == summary


def test_redaction_reaches_nested_structures():
    event = record_event(
        action="a",
        object_type="t",
        object_id="1",
        change_summary={"changed": {"password": {"from": "x", "to": "y"}}, "rows": [{"pin": "1"}]},
    )

    assert event.change_summary["changed"]["password"] == MASK
    assert event.change_summary["rows"][0]["pin"] == MASK


def test_long_values_are_truncated_so_a_file_body_cannot_be_stored():
    event = record_event(
        action="a", object_type="t", object_id="1", change_summary={"note": "x" * 5000}
    )

    assert event.change_summary["note"].endswith("[truncated]")
    assert len(event.change_summary["note"]) < 600


def test_redaction_cannot_be_bypassed_by_using_the_orm_directly():
    event = AuditEvent.objects.create(
        action="a", object_type="t", object_id="1", change_summary={"pin": "1925"}
    )

    assert event.change_summary["pin"] == MASK


def test_a_non_dict_summary_is_refused():
    with pytest.raises(TypeError):
        record_event(action="a", object_type="t", object_id="1", change_summary=["not-a-dict"])
