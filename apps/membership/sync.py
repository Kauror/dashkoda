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

from apps.audit.services import record_event
from apps.core.feed_sync import (
    describe_error,
    fail_feed,
    find_published_artifact,
    get_feed_state,
    mark_imported,
    mark_unchanged,
    publish_current,
    start_run,
    touch_checked,
)
from apps.core.feeds import FeedResult, SourceOutcome
from apps.membership.audit_actions import MembershipAudit
from apps.sources.services import complete_import_run, fail_publication

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
LOCK_NAME = "dashkoda.membership.sync_koda_members"


def synchronize_membership(*, dry_run: bool = False, actor=None, collector=None) -> SourceOutcome:
    """Run one membership collection. Never raises for an ordinary failure."""
    correlation_id = uuid.uuid4()
    source = ensure_membership_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(MembershipFeedState, source)
    touch_checked(state)

    collect = collector or collect_membership
    current = MembershipCountObservation.objects.filter(source=source, is_current=True).first()

    try:
        collection = collect(
            etag=state.remote_etag if current is not None else "",
            last_modified=state.remote_last_modified if current is not None else "",
        )
    except MembershipCollectionError as error:
        return _fail(state, str(error), correlation_id)
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _fail(state, describe_error(error), correlation_id)

    if collection is None:
        # The source answered 304 and we already publish an observation.
        return _unchanged(state, None, dry_run=dry_run, correlation_id=correlation_id)

    artifact, already_published = find_published_artifact(source, collection.sha256, IMPORTER_NAME)
    if already_published:
        return _unchanged(state, collection, dry_run=dry_run, correlation_id=correlation_id)

    plausible, reason = is_change_plausible(
        current.total_members if current else None, collection.total_members
    )
    if not plausible:
        # Fail closed. A movement this large is far more likely to be a source or
        # parsing fault than real membership news, and the previous observation
        # is the safer thing to keep showing.
        return _fail(state, reason, correlation_id)

    try:
        artifact, run = start_run(
            source,
            collection,
            existing_artifact=artifact,
            importer_name=IMPORTER_NAME,
            external_reference=EXTERNAL_REFERENCE,
            artifact_name=ARTIFACT_NAME,
            schema_version=NORMALISED_SCHEMA_VERSION,
            dry_run=dry_run,
            actor=actor,
            correlation_id=correlation_id,
        )
    except Exception as error:  # noqa: BLE001
        return _fail(state, describe_error(error), correlation_id)

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
            publish_current(observation)
            complete_import_run(run, rows_added=1, actor=actor)
            record_event(
                action=MembershipAudit.OBSERVATION_IMPORTED,
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
        fail_publication(run, errors=[{"type": type(error).__name__}], actor=actor)
        return _fail(state, describe_error(error), correlation_id)

    mark_imported(
        state,
        observation,
        current_field="current_observation",
        etag=collection.etag,
        last_modified=collection.last_modified,
    )
    logger.info("membership.sync imported total=%s", collection.total_members)
    return SourceOutcome(
        result=FeedResult.IMPORTED,
        detail="Uus liikmete arv avaldatud.",
        extra={"total_members": collection.total_members},
    )


def _unchanged(state, collection, *, dry_run: bool, correlation_id) -> SourceOutcome:
    total = state.current_observation.total_members if state.current_observation else 0
    if not dry_run:
        mark_unchanged(
            state,
            correlation_id=correlation_id,
            audit_action=MembershipAudit.SYNC_UNCHANGED,
            change_summary={"source": state.source.slug, "total_members": total},
            etag=collection.etag if collection is not None else None,
            last_modified=collection.last_modified if collection is not None else None,
        )
    return SourceOutcome(
        result=FeedResult.UNCHANGED,
        detail="Liikmete arv ei ole muutunud.",
        dry_run=dry_run,
        extra={"total_members": total},
    )


def _fail(state, message: str, correlation_id) -> SourceOutcome:
    return fail_feed(
        state,
        message,
        correlation_id=correlation_id,
        audit_action=MembershipAudit.SYNC_FAILED,
        logger=logger,
    )
