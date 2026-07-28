"""The approved way to record an audit event.

Everything that writes to the audit trail goes through :func:`record_event`.
There are no signal handlers: a reader should be able to find every writer by
searching for this function.
"""

import uuid

from .models import AuditEvent


def record_event(
    *,
    action: str,
    obj=None,
    object_type: str | None = None,
    object_id: str | None = None,
    actor=None,
    change_summary: dict | None = None,
    correlation_id: uuid.UUID | None = None,
) -> AuditEvent:
    """Append one event.

    Pass either ``obj`` or an explicit ``object_type``/``object_id`` pair. The
    type and id are stored as text so the entry stays readable after the
    referenced object is gone.
    """
    if obj is not None:
        object_type = object_type or f"{obj._meta.app_label}.{obj._meta.model_name}"
        object_id = object_id if object_id is not None else str(obj.pk)

    if not object_type or object_id is None:
        raise ValueError("record_event needs either obj or object_type and object_id")

    # An unauthenticated or anonymous caller is a system action, not an actor.
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None

    return AuditEvent.objects.create(
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        actor=actor,
        change_summary=change_summary or {},
        correlation_id=correlation_id,
    )
