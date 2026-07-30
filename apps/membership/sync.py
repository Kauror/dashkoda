"""Collect the member total and publish it as an immutable observation.

One run is independent of the other two public feeds: it takes its own advisory
lock, its own import run and its own transaction, so a broken news feed can
never stop the member count being updated, and a failure here never touches what
the dashboard is already showing.
"""

from __future__ import annotations

import logging
import uuid

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.core.feeds import FeedResult, SourceOutcome
from apps.sources.models import ImportStatus, SourceArtifact
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    fail_import_run,
    register_external_reference,
    start_import_run,
)

from .bootstrap import ensure_membership_source
from .collector import (
    NORMALISED_SCHEMA_VERSION,
    MembershipCollectionError,
    collect_membership,
    is_change_plausible,
)
from .models import MembershipCountObservation, MembershipFeedState

logger = logging.getLogger("dashkoda.membership.sync")

IMPORTER_NAME = "koda_membership_count"
EXTERNAL_REFERENCE = "koda-public:company-list"
ARTIFACT_NAME = "koda-company-list.json"
ARTIFACT_MIME = "application/json"
LOCK_NAME = "dashkoda.membership.sync_koda_members"


def get_feed_state(source) -> MembershipFeedState:
    state, _created = MembershipFeedState.objects.get_or_create(source=source)
    return state


def synchronize_membership(*, dry_run: bool = False, actor=None, collector=None) -> SourceOutcome:
    """Run one membership collection. Never raises for an ordinary failure."""
    correlation_id = uuid.uuid4()
    source = ensure_membership_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(source)

    state.last_checked_at = timezone.now()
    state.save(update_fields=["last_checked_at", "updated_at"])

    collect = collector or collect_membership
    current = _current_observation(source)

    try:
        collection = collect(
            etag=state.remote_etag if current is not None else "",
            last_modified=state.remote_last_modified if current is not None else "",
        )
    except MembershipCollectionError as error:
        return _fail(state, str(error), correlation_id=correlation_id)
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _fail(
            state,
            f"{type(error).__name__}: {error}".replace("\n", " "),
            correlation_id=correlation_id,
        )

    if collection is None:
        # The source answered 304 and we already publish an observation.
        return _record_unchanged(state, None, dry_run=dry_run, correlation_id=correlation_id)

    existing = SourceArtifact.objects.filter(source=source, sha256=collection.sha256).first()
    if existing is not None and _has_successful_live_import(existing):
        return _record_unchanged(state, collection, dry_run=dry_run, correlation_id=correlation_id)

    plausible, reason = is_change_plausible(
        current.total_members if current else None, collection.total_members
    )
    if not plausible:
        # Fail closed. A movement this large is far more likely to be a source or
        # parsing fault than real membership news, and the previous observation
        # is the safer thing to keep showing.
        return _fail(state, reason, correlation_id=correlation_id)

    try:
        artifact = existing or register_external_reference(
            source=source,
            external_reference=EXTERNAL_REFERENCE,
            original_name=ARTIFACT_NAME,
            mime_type=ARTIFACT_MIME,
            sha256=collection.sha256,
            size_bytes=collection.size_bytes,
            uploaded_by=actor,
            actor=actor,
            correlation_id=correlation_id,
        )
        run = build_import_run(
            artifact=artifact,
            importer_name=IMPORTER_NAME,
            schema_version=NORMALISED_SCHEMA_VERSION,
            dry_run=dry_run,
            initiated_by=actor,
            actor=actor,
            correlation_id=correlation_id,
        )
        start_import_run(run)
    except Exception as error:  # noqa: BLE001
        return _fail(
            state,
            f"{type(error).__name__}: {error}".replace("\n", " "),
            correlation_id=correlation_id,
        )

    if dry_run:
        complete_import_run(run, rows_added=0, rows_skipped=1, actor=actor)
        return SourceOutcome(
            result=FeedResult.IMPORTED,
            detail="Kuivkäivitus: liikmete arv on kehtiv, midagi ei avaldatud.",
            dry_run=True,
            extra={"total_members": collection.total_members},
        )

    try:
        with transaction.atomic():
            observation = MembershipCountObservation(
                source=source,
                artifact=artifact,
                import_run=run,
                observed_at=timezone.now(),
                total_members=collection.total_members,
                is_current=False,
            )
            observation.full_clean(exclude=["is_current"])
            observation.save()
            _publish(observation)
            complete_import_run(run, rows_added=1, actor=actor)
            record_event(
                action=AuditAction.MEMBERSHIP_OBSERVATION_IMPORTED,
                obj=observation,
                actor=actor,
                correlation_id=correlation_id,
                change_summary={
                    "source": source.slug,
                    "sha256": collection.sha256,
                    "total_members": collection.total_members,
                    "observed_at": observation.observed_at.isoformat(),
                    "observation_id": observation.pk,
                },
            )
    except Exception as error:  # noqa: BLE001
        run.refresh_from_db()
        if not run.is_terminal:
            fail_import_run(run, errors=[{"type": type(error).__name__}], actor=actor)
        return _fail(
            state,
            f"{type(error).__name__}: {error}".replace("\n", " "),
            correlation_id=correlation_id,
        )

    _record_imported(state, collection, observation)
    logger.info("membership.sync imported total=%s", collection.total_members)
    return SourceOutcome(
        result=FeedResult.IMPORTED,
        detail="Uus liikmete arv avaldatud.",
        extra={"total_members": collection.total_members},
    )


def _current_observation(source) -> MembershipCountObservation | None:
    return MembershipCountObservation.objects.filter(source=source, is_current=True).first()


def _has_successful_live_import(artifact: SourceArtifact) -> bool:
    return artifact.import_runs.filter(
        importer_name=IMPORTER_NAME, status=ImportStatus.SUCCEEDED, dry_run=False
    ).exists()


def _publish(observation: MembershipCountObservation) -> None:
    retired = (
        MembershipCountObservation.objects.select_for_update()
        .filter(source=observation.source, is_current=True)
        .exclude(pk=observation.pk)
    )
    for previous in retired:
        previous.is_current = False
        previous.save(update_fields=["is_current"])
    observation.is_current = True
    observation.save(update_fields=["is_current"])


def _record_imported(state, collection, observation) -> None:
    now = timezone.now()
    state.last_result = FeedResult.IMPORTED
    state.last_error_summary = ""
    state.last_successful_sync_at = now
    state.last_changed_at = now
    state.remote_etag = collection.etag[:200]
    state.remote_last_modified = collection.last_modified[:100]
    state.current_observation = observation
    state.save(
        update_fields=[
            "last_result",
            "last_error_summary",
            "last_successful_sync_at",
            "last_changed_at",
            "remote_etag",
            "remote_last_modified",
            "current_observation",
            "updated_at",
        ]
    )


def _record_unchanged(state, collection, *, dry_run: bool, correlation_id) -> SourceOutcome:
    total = state.current_observation.total_members if state.current_observation else 0
    if not dry_run:
        with transaction.atomic():
            state.last_result = FeedResult.UNCHANGED
            state.last_error_summary = ""
            state.last_successful_sync_at = timezone.now()
            if collection is not None:
                state.remote_etag = collection.etag[:200]
                state.remote_last_modified = collection.last_modified[:100]
            state.save(
                update_fields=[
                    "last_result",
                    "last_error_summary",
                    "last_successful_sync_at",
                    "remote_etag",
                    "remote_last_modified",
                    "updated_at",
                ]
            )
            record_event(
                action=AuditAction.MEMBERSHIP_SYNC_UNCHANGED,
                obj=state.source,
                correlation_id=correlation_id,
                change_summary={"source": state.source.slug, "total_members": total},
            )
    return SourceOutcome(
        result=FeedResult.UNCHANGED,
        detail="Liikmete arv ei ole muutunud.",
        dry_run=dry_run,
        extra={"total_members": total},
    )


def _fail(state, message: str, *, correlation_id) -> SourceOutcome:
    state.last_result = FeedResult.FAILED
    state.last_error_summary = message[:500]
    state.save(update_fields=["last_result", "last_error_summary", "last_checked_at", "updated_at"])
    record_event(
        action=AuditAction.MEMBERSHIP_SYNC_FAILED,
        obj=state.source,
        correlation_id=correlation_id,
        change_summary={"source": state.source.slug, "detail": message[:300]},
    )
    logger.warning("membership.sync failed: %s", message)
    return SourceOutcome(result=FeedResult.FAILED, detail=message)
