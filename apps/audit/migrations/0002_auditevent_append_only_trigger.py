"""Refuse UPDATE and DELETE on the audit table at the database level.

The model and its manager already refuse both, but application-level guards can
be bypassed by raw SQL. This trigger cannot.

One mutation is deliberately allowed: clearing `actor_id` to NULL. `actor` uses
SET_NULL so an audit entry outlives the user it names, and Django performs that
with a direct UPDATE that never passes through the model. The trigger therefore
permits exactly that single-column change and nothing else — every other column
must be byte-identical, and the actor may only move from set to NULL, never the
other way round.

Its remaining limits are documented in `docs/data-model.md`: TRUNCATE does not
fire row triggers, and a role with DDL rights can drop the trigger, so this is
strong protection against application bugs and casual misuse rather than an
absolute guarantee against a database superuser.
"""

from django.db import migrations

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION dashkoda_audit_event_append_only()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'audit_auditevent is append-only: DELETE is not permitted';
    END IF;

    -- The only tolerated change: releasing the actor when that user is removed.
    IF NEW.id = OLD.id
       AND OLD.actor_id IS NOT NULL
       AND NEW.actor_id IS NULL
       AND NEW.timestamp = OLD.timestamp
       AND NEW.action = OLD.action
       AND NEW.object_type = OLD.object_type
       AND NEW.object_id = OLD.object_id
       AND NEW.change_summary = OLD.change_summary
       AND NEW.correlation_id IS NOT DISTINCT FROM OLD.correlation_id
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'audit_auditevent is append-only: UPDATE is not permitted';
END;
$$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS dashkoda_audit_event_append_only();"

CREATE_TRIGGER = """
CREATE TRIGGER audit_auditevent_append_only
BEFORE UPDATE OR DELETE ON audit_auditevent
FOR EACH ROW EXECUTE FUNCTION dashkoda_audit_event_append_only();
"""

DROP_TRIGGER = "DROP TRIGGER IF EXISTS audit_auditevent_append_only ON audit_auditevent;"


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_FUNCTION, reverse_sql=DROP_FUNCTION),
        migrations.RunSQL(sql=CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
