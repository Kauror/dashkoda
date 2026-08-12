"""The one place a visibility observation becomes the value in use.

Publishing never rewrites a number. It moves exactly one flag,
`is_current_for_date`, which answers "is this the row to read for this metric on
this date?". Everything else about a published observation is fixed and the
model refuses any other change.

The precedence rule is deliberately simpler than the membership history's,
because the situation is simpler: there is one writer, one source per metric and
no imported evidence competing with itself. Within one metric on one date, the
**newest published observation wins**, and the row it replaces keeps its number
and stays readable as history.

Supersession is scoped to a single date on purpose. A reading taken in August
does not correct a reading taken in July — it is a later fact about a changing
audience, and both belong to the trend.
"""

from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.visibility.audit_actions import VisibilityAudit

from .models import VisibilityObservation


def find_current(metric: str, observation_date: date) -> VisibilityObservation | None:
    """The row currently read for this metric on this date, if there is one."""
    return (
        VisibilityObservation.objects.filter(
            metric=metric,
            observation_date=observation_date,
            is_current_for_date=True,
        )
        .select_related("source", "batch")
        .first()
    )


def lock_current(metric: str, observation_date: date) -> VisibilityObservation | None:
    """`find_current`, holding a row lock for the rest of the transaction.

    The caller needs the answer *before* it creates the replacement, because
    `supersedes` is immutable and must be set on the first save. Taking the lock
    here is what stops two staff users confirming the same date from both
    believing they are the correction.

    Deliberately without `select_related`: `FOR UPDATE` locks every table in the
    join, so pulling the source in would lock `DataSource` rows that this has no
    reason to hold — and that every concurrent submission also wants.
    """
    return (
        VisibilityObservation.objects.select_for_update()
        .filter(
            metric=metric,
            observation_date=observation_date,
            is_current_for_date=True,
        )
        .first()
    )


def supersede_observation(
    previous: VisibilityObservation,
    *,
    replacement: VisibilityObservation,
    actor=None,
    correlation_id=None,
) -> VisibilityObservation:
    """Retire a published observation in favour of a correction.

    The retired row keeps its value, its batch, its artifact and its place in the
    audit trail. Only the current flag moves.
    """
    if previous.pk == replacement.pk:
        raise ValueError("An observation cannot supersede itself.")
    if previous.metric != replacement.metric:
        raise ValueError("A correction must replace an observation of the same metric.")
    if previous.observation_date != replacement.observation_date:
        raise ValueError("A correction must replace an observation of the same date.")

    previous.is_current_for_date = False
    previous.save(update_fields=["is_current_for_date"])

    record_event(
        action=VisibilityAudit.OBSERVATION_SUPERSEDED,
        obj=previous,
        actor=actor,
        correlation_id=correlation_id,
        change_summary={
            "source": previous.source.slug,
            "metric": previous.metric,
            "observation_date": previous.observation_date.isoformat(),
            "superseded_observation_id": previous.pk,
            "superseded_value": previous.value,
            "replacement_observation_id": replacement.pk,
            "replacement_value": replacement.value,
        },
    )
    return previous


@transaction.atomic
def publish_observation(
    observation: VisibilityObservation,
    *,
    supersedes: VisibilityObservation | None = None,
    actor=None,
    correlation_id=None,
) -> VisibilityObservation:
    """Make a saved observation the current one for its metric and date.

    `supersedes` is the correction case, and the caller is expected to have
    obtained it through `lock_current` in this same transaction. The named row is
    retired **before** the new one is marked current, so
    `visibilityobservation_one_current_per_metric_date` is never even momentarily
    violated.
    """
    if observation.published_at is None:
        # `published_at` is not in MUTABLE_FIELDS, so it is stamped through the
        # queryset rather than by saving the instance. This runs inside the same
        # transaction that created the row.
        VisibilityObservation.objects.filter(pk=observation.pk).update(published_at=timezone.now())
        observation.refresh_from_db(fields=["published_at"])

    if supersedes is not None:
        supersede_observation(
            supersedes,
            replacement=observation,
            actor=actor,
            correlation_id=correlation_id,
        )

    if not observation.is_current_for_date:
        observation.is_current_for_date = True
        observation.save(update_fields=["is_current_for_date"])

    record_event(
        action=VisibilityAudit.OBSERVATION_PUBLISHED,
        obj=observation,
        actor=actor,
        correlation_id=correlation_id,
        change_summary={
            "source": observation.source.slug,
            "metric": observation.metric,
            "value": observation.value,
            "observation_date": observation.observation_date.isoformat(),
            "batch_id": observation.batch_id,
            "collection_method": observation.collection_method,
            "supersedes_observation_id": supersedes.pk if supersedes is not None else None,
        },
    )
    return observation
