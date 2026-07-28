"""Refuse UPDATE and DELETE on the audit table at the database level.

The model and its manager already refuse both, but application-level guards can
be bypassed by raw SQL. This trigger cannot. Its limits are documented in
`docs/data-model.md`: TRUNCATE does not fire row triggers, and a role with DDL
rights can still drop the trigger, so this is strong protection against
accident and casual misuse rather than an absolute guarantee against a database
superuser.
"""

from django.db import migrations

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION dashkoda_audit_event_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditevent is append-only: % is not permitted', TG_OP;
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
