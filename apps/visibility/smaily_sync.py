"""Read the Smaily lists and publish the reading as an immutable revision.

The same sequence every other feed follows, through the same shared bookkeeping
in :mod:`apps.core.feed_sync`: record the check, collect, recognise the content
by its canonical checksum, open an import run, publish atomically, record the
outcome.

## The unit is a reading day, and a day can be re-read

A subscriber count is the size of a list at the moment somebody looked, not a
measurement of something that happened during the day. So there is no
reconciliation window and no "wait for the day to finish": today is a perfectly
good day to read, and reading it twice is fine. The second reading either finds
the same numbers — in which case nothing is published and the run reports
`unchanged` — or it finds different ones, and publishes a **new revision** of
that date naming the one it replaces.

That is the one meaningful difference from `ga4_sync`, which must never collect
today because a partial reporting day is wrong by construction.

## What a failure does not do

Nothing published is ever rewritten or removed. A failed run records a sanitized
message on the feed state and leaves the last good reading exactly where it was,
so a dashboard never loses a figure because an API had a bad morning.

## No backfill exists, and cannot

Smaily reports what a segment holds *now*. It has no endpoint that answers "how
many subscribers did this list have last March", so newsletter history starts on
the day collection started and grows forward. Inventing the missing years by
interpolating between two known points would be fabricating the exact series the
board would use to judge whether the newsletters are growing.

Nothing in this module logs, stores or returns the API username, the password,
the account subdomain or a Smaily response body.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.core.feed_sync import (
    fail_feed,
    get_feed_state,
    mark_imported,
    mark_unchanged,
    touch_checked,
)
from apps.core.feeds import FeedResult, SourceOutcome
from apps.sources.models import SourceArtifact
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    publishing_run,
    register_external_reference,
    start_import_run,
)

from .bootstrap import ensure_smaily_source
from .models import (
    CollectionMethod,
    SmailyAudienceSnapshot,
    SmailyFeedState,
    SmailySegmentDaily,
    VisibilityObservation,
)
from .publishing import lock_current, publish_observation
from .smaily import (
    SCHEMA_VERSION,
    SegmentReading,
    SmailyApiClient,
    SmailyNotConfigured,
    SmailyResponseError,
    get_configuration,
)
from .smaily_segments import NewsletterAudience, resolve_all

logger = logging.getLogger("dashkoda.visibility.smaily_sync")

IMPORTER_NAME = "smaily_audience"
ARTIFACT_NAME = "smaily-audience.json"

#: Its own name, so a Smaily run can neither block nor be blocked by any other
#: feed. The key derivation is the shared one in `apps.core.feeds`.
LOCK_NAME = "dashkoda.visibility.sync_smaily"


class ReadingAction:
    """What happened to one reading day. Values are stable output contract."""

    IMPORTED = "imported"
    REVISED = "revised"
    UNCHANGED = "unchanged"


@dataclass
class SyncCounts:
    """Aggregates for the command's JSON output. Never a segment list."""

    segments_read: int = 0
    segment_rows_written: int = 0
    newsletters_available: int = 0
    newsletters_withheld: int = 0
    api_requests: int = 0
    api_retries: int = 0

    def as_dict(self) -> dict:
        return {
            "segments_read": self.segments_read,
            "segment_rows_written": self.segment_rows_written,
            "newsletters_available": self.newsletters_available,
            "newsletters_withheld": self.newsletters_withheld,
            "api_requests": self.api_requests,
            "api_retries": self.api_retries,
        }


@dataclass
class SyncReport:
    observed_on: date | None = None
    action: str = ReadingAction.UNCHANGED
    counts: SyncCounts = field(default_factory=SyncCounts)
    #: Metric keys with no figure this run, and why. Keys and sentences only.
    withheld: dict[str, str] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.action in (ReadingAction.IMPORTED, ReadingAction.REVISED)


def canonical_digest(reading: SegmentReading) -> tuple[bytes, str]:
    """The reading's canonical bytes and their SHA-256.

    The digest is over the **normalised reading**, never over the API response:
    Smaily is free to reorder segments or add a field without that meaning the
    Chamber's lists changed size.
    """
    payload = json.dumps(
        reading.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return payload, hashlib.sha256(payload).hexdigest()


def _transport_failure(error: Exception) -> str:
    """A sanitized sentence for an unexpected failure.

    `describe_error` renders `f"{type}: {error}"`, and an exception raised inside
    `requests` carries the request URL — which names the account's subdomain.
    Neither that nor a response body may reach the feed state, which the admin
    renders.

    The traceback is written to the **container log** first. Sanitizing what is
    stored is right; sanitizing what is *diagnosable* is not, and a bug in this
    module once reached CI disguised as a network problem because the only trace
    of it was this sentence.
    """
    logger.exception("smaily sync failed with an unexpected error")
    return f"Smaily päring ebaõnnestus ({type(error).__name__})."


def synchronize_smaily(
    *,
    dry_run: bool = False,
    actor=None,
    collector=None,
    observed_on: date | None = None,
) -> SourceOutcome:
    """Read every segment once and publish the reading if it has changed."""
    correlation_id = uuid.uuid4()
    day = observed_on or timezone.localdate()

    source = ensure_smaily_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(SmailyFeedState, source)
    touch_checked(state)

    try:
        collect = collector if collector is not None else SmailyApiClient(get_configuration())
    except SmailyNotConfigured as error:
        # Names the missing settings and never their values.
        return _fail(state, str(error), correlation_id)
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _fail(state, _transport_failure(error), correlation_id)

    report = SyncReport(observed_on=day)

    try:
        reading = collect.collect_segments(observed_on=day)
    except SmailyNotConfigured as error:
        return _fail(state, str(error), correlation_id)
    except SmailyResponseError as error:
        # Our own sentence, written in `smaily.py`, safe to record verbatim.
        return _fail(state, str(error), correlation_id)
    except Exception as error:  # noqa: BLE001
        return _fail(state, _transport_failure(error), correlation_id)

    counts = getattr(collect, "counts", None)
    if counts is not None:
        report.counts.api_requests += counts.requests
        report.counts.api_retries += counts.retries
    report.counts.segments_read = len(reading.segments)

    # Resolved before publication so the outcome can say which newsletters have
    # no figure and why, without a second pass over the data.
    audiences = resolve_all(reading)
    for audience in audiences:
        if audience.is_available:
            report.counts.newsletters_available += 1
        else:
            report.counts.newsletters_withheld += 1
            report.withheld[audience.metric] = audience.withheld_reason

    try:
        _publish(
            reading,
            audiences=audiences,
            source=source,
            actor=actor,
            correlation_id=correlation_id,
            dry_run=dry_run,
            report=report,
        )
    except Exception as error:  # noqa: BLE001
        return _fail(state, _transport_failure(error), correlation_id)

    if not dry_run:
        published = _current_for(source, day)
        if report.changed and published is not None:
            mark_imported(state, published, current_field="current_snapshot")
        else:
            mark_unchanged(
                state,
                correlation_id=correlation_id,
                audit_action=AuditAction.SMAILY_SYNC_UNCHANGED,
                change_summary={"source": source.slug, "observed_on": day.isoformat()},
            )
        if state.last_period_end != day:
            state.last_period_end = day
            state.save(update_fields=["last_period_end", "updated_at"])

    logger.info(
        "smaily.sync day=%s action=%s segments=%d withheld=%d",
        day.isoformat(),
        report.action,
        report.counts.segments_read,
        report.counts.newsletters_withheld,
    )

    return SourceOutcome(
        result=FeedResult.IMPORTED if report.changed else FeedResult.UNCHANGED,
        detail=_detail(report, dry_run=dry_run),
        dry_run=dry_run,
        extra={
            "observed_on": day.isoformat(),
            "action": report.action,
            # Metric keys and our own sentences. No figure, no segment name.
            "withheld": dict(report.withheld),
            **report.counts.as_dict(),
        },
    )


def _current_for(source, day: date) -> SmailyAudienceSnapshot | None:
    return SmailyAudienceSnapshot.objects.filter(
        source=source, observed_on=day, is_current_for_date=True
    ).first()


def _publish(
    reading: SegmentReading,
    *,
    audiences: tuple[NewsletterAudience, ...],
    source,
    actor,
    correlation_id,
    dry_run: bool,
    report: SyncReport,
) -> None:
    """Publish one reading, or recognise that it has not changed."""
    day = reading.observed_on
    current = (
        SmailyAudienceSnapshot.objects.filter(
            source=source, observed_on=day, is_current_for_date=True
        )
        .only("id", "checksum", "revision")
        .first()
    )

    payload, digest = canonical_digest(reading)

    if current is not None and current.checksum == digest:
        report.action = ReadingAction.UNCHANGED
        return

    report.action = ReadingAction.REVISED if current is not None else ReadingAction.IMPORTED

    if dry_run:
        return

    artifact = SourceArtifact.objects.filter(source=source, sha256=digest).first()
    if artifact is None:
        artifact = register_external_reference(
            source=source,
            # A fixed, non-secret provenance label. Never the account subdomain.
            external_reference=f"smaily:list-api:{day.isoformat()}",
            original_name=ARTIFACT_NAME,
            mime_type="application/json",
            sha256=digest,
            size_bytes=len(payload),
            actor=actor,
            correlation_id=correlation_id,
        )
    run = build_import_run(
        artifact=artifact,
        importer_name=IMPORTER_NAME,
        schema_version=SCHEMA_VERSION,
        dry_run=False,
        actor=actor,
        correlation_id=correlation_id,
    )
    start_import_run(run)

    with publishing_run(
        run, errors=[{"detail": "Smaily lugemise avaldamine ebaõnnestus."}], actor=actor
    ):
        with transaction.atomic():
            # Locked, because the unique index allows exactly one current
            # revision per date and two runs on the same day would otherwise
            # race to insert the second one.
            locked = (
                SmailyAudienceSnapshot.objects.select_for_update()
                .filter(source=source, observed_on=day, is_current_for_date=True)
                .first()
            )
            snapshot = SmailyAudienceSnapshot(
                source=source,
                artifact=artifact,
                import_run=run,
                observed_on=day,
                observed_at=timezone.now(),
                checksum=digest,
                revision=(locked.revision + 1) if locked is not None else 1,
                supersedes=locked,
                is_current_for_date=False,
            )
            snapshot.save()

            SmailySegmentDaily.objects.bulk_create(
                [
                    SmailySegmentDaily(
                        snapshot=snapshot,
                        observed_on=day,
                        segment_id=row.segment_id,
                        name=row.name,
                        subscribers=row.subscribers,
                    )
                    for row in reading.segments
                ],
                batch_size=500,
            )
            report.counts.segment_rows_written += len(reading.segments)

            if locked is not None:
                locked.is_current_for_date = False
                locked.save(update_fields=["is_current_for_date"])
            snapshot.is_current_for_date = True
            snapshot.save(update_fields=["is_current_for_date"])

            _publish_newsletter_metrics(
                audiences,
                source=source,
                artifact=artifact,
                run=run,
                day=day,
                actor=actor,
                correlation_id=correlation_id,
            )

            complete_import_run(
                run,
                # One snapshot plus one row per segment.
                rows_added=1 + len(reading.segments),
                actor=actor,
            )

    from apps.audit.services import record_event

    record_event(
        action=AuditAction.SMAILY_OBSERVATION_IMPORTED,
        obj=snapshot,
        correlation_id=correlation_id,
        actor=actor,
        change_summary={
            "source": source.slug,
            "observed_on": day.isoformat(),
            "revision": snapshot.revision,
            "segments": len(reading.segments),
            # Metric keys only. No subscriber count reaches the audit trail,
            # because the trail is about provenance, not figures.
            "withheld": sorted(report.withheld),
        },
    )


def _publish_newsletter_metrics(
    audiences: tuple[NewsletterAudience, ...],
    *,
    source,
    artifact,
    run,
    day: date,
    actor,
    correlation_id,
) -> None:
    """Write each newsletter's total into the shared observation table.

    The per-segment rows are the truthful primitive, but every existing reader —
    the overview's channel band, the Nähtavus page, the freshness calculation —
    asks `VisibilityObservation` for "the current figure for this metric". So
    the collected total is published there too, marked `AUTOMATIC`, through the
    same supersession path a typed correction uses. That is exactly the seam
    `CollectionMethod.AUTOMATIC` was added for.

    A **withheld** newsletter publishes nothing at all. It does not publish a
    zero and it does not leave last week's figure looking like today's reading:
    the previous observation simply stays current and stale, which is what an
    unread list actually is.
    """
    for audience in audiences:
        if not audience.is_available:
            continue
        current = lock_current(audience.metric, day)
        if current is not None and current.value == audience.total:
            # The same number on the same date is not a correction. Publishing a
            # revision every day a list did not move would fill the history with
            # supersessions that record nothing.
            continue
        observation = VisibilityObservation(
            batch=None,
            source=source,
            artifact=artifact,
            import_run=run,
            metric=audience.metric,
            value=audience.total,
            collection_method=CollectionMethod.AUTOMATIC,
            observation_date=day,
            # Set here and not only on `publish_observation`: the field is
            # immutable, so it has to be right on the first save or the
            # correction never names what it replaced.
            supersedes=current,
            is_current_for_date=False,
            created_by=actor,
        )
        # Enforces metric-to-source agreement, which no database constraint can
        # express: a newsletter figure filed under a social source is refused.
        observation.full_clean(exclude=["published_at"])
        observation.save()
        publish_observation(
            observation,
            supersedes=current,
            actor=actor,
            correlation_id=correlation_id,
        )


def _fail(state, message: str, correlation_id, *, report: SyncReport | None = None):
    outcome = fail_feed(
        state,
        message,
        correlation_id=correlation_id,
        audit_action=AuditAction.SMAILY_SYNC_FAILED,
        logger=logger,
    )
    if report is not None:
        outcome.extra.update(report.counts.as_dict())
    return outcome


def _detail(report: SyncReport, *, dry_run: bool) -> str:
    prefix = "Proovikäivitus: " if dry_run else ""
    if report.action == ReadingAction.UNCHANGED:
        body = "Smaily nimekirjad ei ole muutunud."
    elif report.action == ReadingAction.REVISED:
        body = f"Smaily lugemine uuendatud ({report.counts.segments_read} segmenti)."
    else:
        body = f"Smaily lugemine imporditud ({report.counts.segments_read} segmenti)."
    if report.counts.newsletters_withheld:
        body += f" {report.counts.newsletters_withheld} uudiskirja jäi avaldamata."
    return prefix + body


__all__ = [
    "ARTIFACT_NAME",
    "IMPORTER_NAME",
    "LOCK_NAME",
    "ReadingAction",
    "SyncCounts",
    "SyncReport",
    "canonical_digest",
    "synchronize_smaily",
]
