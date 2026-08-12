"""The one place an internal membership record becomes the record in use.

Both writers — the one-time historical import and the staff manual form — go
through these functions, so "which observation does a chart read" has a single
definition and a single audit trail.

Publishing never rewrites a fact. It moves exactly two flags:

- `is_preferred_for_date`, which says this row is the one to read for its date;
- `quality_status`, and only ever to `superseded`, which says a correction has
  replaced this row.

Everything else about a published observation is fixed, and the model refuses
any other change. A correction is therefore a new row that points back at the
one it replaces, and the replaced row keeps its numbers, its children and its
place in the audit trail.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.membership.audit_actions import MembershipAudit

from .models import (
    InternalMembershipObservation,
    MembershipMonthlyNewMemberValue,
    MonthlyValueStatus,
    QualityStatus,
)
from .quality import PreferenceCandidate, choose_preferred


def _candidate(observation: InternalMembershipObservation) -> PreferenceCandidate:
    return PreferenceCandidate(
        key=observation.pk,
        source_kind=observation.source_kind,
        extraction_confidence=observation.extraction_confidence,
        quality_status=observation.quality_status,
        # Later evidence wins an otherwise exact tie, and the identifier makes
        # the ordering total so two runs can never disagree.
        tie_breaker=-(observation.pk or 0),
    )


def elect_preferred_for_date(
    *,
    source,
    observation_date,
) -> InternalMembershipObservation | None:
    """Re-run the precedence rules for one date and apply the outcome.

    Superseded rows never compete. Among the rest the best candidate wins, and
    the previous holder is cleared *before* the winner is set so the "one
    preferred per date" constraint is never momentarily violated.
    """
    rows = list(
        InternalMembershipObservation.objects.select_for_update()
        .filter(source=source, observation_date=observation_date)
        .exclude(quality_status=QualityStatus.SUPERSEDED)
    )
    if not rows:
        return None

    by_pk = {row.pk: row for row in rows}
    winner_key = choose_preferred([_candidate(row) for row in rows])
    winner = by_pk[winner_key.key] if winner_key else None

    for row in rows:
        if row.is_preferred_for_date and (winner is None or row.pk != winner.pk):
            row.is_preferred_for_date = False
            row.save(update_fields=["is_preferred_for_date"])

    if winner is not None and not winner.is_preferred_for_date:
        winner.is_preferred_for_date = True
        winner.save(update_fields=["is_preferred_for_date"])
    return winner


def supersede_observation(
    previous: InternalMembershipObservation,
    *,
    replacement: InternalMembershipObservation,
    actor=None,
    correlation_id=None,
) -> InternalMembershipObservation:
    """Retire a published observation in favour of a correction.

    The retired row keeps every reported number. Only its status and its
    preferred flag move, and its child movements and removal reasons are left
    exactly as they were — the history of what the board was told is not edited
    because a later report corrected it.
    """
    if previous.pk == replacement.pk:
        raise ValueError("An observation cannot supersede itself.")

    previous.is_preferred_for_date = False
    previous.save(update_fields=["is_preferred_for_date"])
    previous.quality_status = QualityStatus.SUPERSEDED
    previous.save(update_fields=["quality_status"])

    record_event(
        action=MembershipAudit.MANUAL_OBSERVATION_SUPERSEDED,
        obj=previous,
        actor=actor,
        correlation_id=correlation_id,
        change_summary={
            "source": previous.source.slug,
            "observation_date": previous.observation_date.isoformat(),
            "superseded_observation_id": previous.pk,
            "replacement_observation_id": replacement.pk,
        },
    )
    return previous


@transaction.atomic
def publish_observation(
    observation: InternalMembershipObservation,
    *,
    supersedes: InternalMembershipObservation | None = None,
    actor=None,
    correlation_id=None,
) -> InternalMembershipObservation:
    """Make a saved observation part of the published history.

    `supersedes` is the correction case: the named row is retired first, so it
    cannot win the election that follows.
    """
    if observation.published_at is None:
        # `published_at` is not in MUTABLE_FIELDS, so it is stamped on the
        # instance before the first save rather than updated afterwards. This
        # function is called inside the same transaction that created the row.
        InternalMembershipObservation.objects.filter(pk=observation.pk).update(
            published_at=timezone.now()
        )
        observation.refresh_from_db(fields=["published_at"])

    if supersedes is not None:
        supersede_observation(
            supersedes,
            replacement=observation,
            actor=actor,
            correlation_id=correlation_id,
        )

    elect_preferred_for_date(
        source=observation.source,
        observation_date=observation.observation_date,
    )
    observation.refresh_from_db(fields=["is_preferred_for_date", "quality_status"])
    return observation


@transaction.atomic
def publish_monthly_value(
    value: MembershipMonthlyNewMemberValue,
    *,
    actor=None,
    correlation_id=None,
) -> MembershipMonthlyNewMemberValue:
    """Make a monthly new-member value the current one for its month.

    The previous current value for the same month is retired rather than
    updated, so the earlier figure and the report it came from stay readable.
    A conflict row becomes current in exactly the same way: what makes it a
    conflict is that `new_members` is null, not that it is hidden.
    """
    previous = list(
        MembershipMonthlyNewMemberValue.objects.select_for_update()
        .filter(
            source=value.source,
            calendar_year=value.calendar_year,
            calendar_month=value.calendar_month,
            is_current_for_month=True,
        )
        .exclude(pk=value.pk)
    )
    for row in previous:
        row.is_current_for_month = False
        row.save(update_fields=["is_current_for_month"])
        row.value_status = MonthlyValueStatus.SUPERSEDED
        row.save(update_fields=["value_status"])

    if not value.is_current_for_month:
        value.is_current_for_month = True
        value.save(update_fields=["is_current_for_month"])

    if previous:
        record_event(
            action=MembershipAudit.MANUAL_OBSERVATION_SUPERSEDED,
            obj=value,
            actor=actor,
            correlation_id=correlation_id,
            change_summary={
                "source": value.source.slug,
                "calendar_year": value.calendar_year,
                "calendar_month": value.calendar_month,
                "superseded_value_ids": [row.pk for row in previous],
            },
        )
    return value
