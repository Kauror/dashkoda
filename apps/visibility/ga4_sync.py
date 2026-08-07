"""Collect one day of GA4 website traffic and publish it.

The same sequence every other feed follows, through the same shared bookkeeping
in :mod:`apps.core.feed_sync`: record the check, collect, recognise the content
by its canonical checksum, open an import run, publish atomically, record the
outcome. Until now `sync_ga4` did all of this inline in the command and had
none of the reliability contract around it — no advisory lock, no feed state, no
audit events, no dry run — so a failure escaped as a cron traceback and left no
trace anybody would find later.

**GA4 is not enabled in production.** There is no property ID, no
service-account key and no schedule; this module exists so that enabling it
later is configuration rather than another architecture. Nothing here runs
unless the command is invoked.

What GA4 does *not* share with the other feeds is worth stating, because
pretending otherwise is how a wrong abstraction gets built:

- there is no document and no validator, so no conditional request. Change is
  decided by a checksum over the normalised reading, exactly as it is for the
  Koda.ee feeds, which have the same problem for a different reason;
- a reporting day is the unit. Re-running the same day is the ordinary case,
  not an error, and must be `unchanged` rather than a duplicate;
- an empty answer is legitimate. A day with no traffic returns no rows, and
  that publishes a reading whose figures are all `None` — an absence of
  measurement, never a zero.

Nothing in this module logs, stores or returns a property ID, a credential
path, an access token or a Google response body.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.core.feed_sync import (
    describe_error,
    fail_feed,
    find_published_artifact,
    get_feed_state,
    mark_imported,
    mark_unchanged,
    publish_current,
    touch_checked,
)
from apps.core.feeds import FeedResult, SourceOutcome
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    fail_import_run,
    register_external_reference,
    start_import_run,
)

from .bootstrap import ensure_ga4_source
from .ga4 import Ga4ApiCollector, Ga4NotConfigured, get_configuration
from .models import Ga4FeedState, WebsiteTrafficObservation

logger = logging.getLogger("dashkoda.visibility.ga4_sync")

IMPORTER_NAME = "ga4_daily"
EXTERNAL_REFERENCE = "ga4:data-api:daily"
ARTIFACT_NAME = "ga4-daily.json"
SCHEMA_VERSION = "1.0"

#: Its own name, so a GA4 run can neither block nor be blocked by any other
#: feed. The key derivation is the shared one in `apps.core.feeds`.
LOCK_NAME = "dashkoda.visibility.sync_ga4"


def default_period() -> date:
    """The previous completed day, in application time.

    `timezone.localdate()` rather than the container's clock: the reporting day
    is a `Europe/Tallinn` day, and a UTC container between midnight and 03:00
    would otherwise ask for the day before the one intended.
    """
    return timezone.localdate() - timedelta(days=1)


def canonical_digest(reading) -> tuple[bytes, str]:
    """The reading's canonical bytes and their SHA-256.

    The digest is over the **normalised reading**, never over the API response:
    Google is free to reorder keys or add fields without that meaning the
    Chamber's website had a different day.
    """
    payload = json.dumps(
        reading.canonical_payload(), sort_keys=True, separators=(",", ":")
    ).encode()
    return payload, hashlib.sha256(payload).hexdigest()


def synchronize_ga4(
    *, dry_run: bool = False, actor=None, collector=None, period: date | None = None
) -> SourceOutcome:
    """Collect one reporting day and publish it if it is new."""
    correlation_id = uuid.uuid4()
    period = period or default_period()
    source = ensure_ga4_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(Ga4FeedState, source)
    touch_checked(state)

    try:
        collect = collector if collector is not None else Ga4ApiCollector(get_configuration())
    except Ga4NotConfigured as error:
        return _fail(state, str(error), correlation_id)
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _fail(state, describe_error(error), correlation_id)

    try:
        reading = collect.collect(period_start=period, period_end=period)
    except Ga4NotConfigured as error:
        return _fail(state, str(error), correlation_id)
    except (OSError, ValueError) as error:
        # A transport failure or a response this application refuses to read.
        # `str(error)` here is our own message, never Google's body.
        return _fail(state, str(error), correlation_id)
    except Exception as error:  # noqa: BLE001
        return _fail(state, describe_error(error), correlation_id)

    payload, digest = canonical_digest(reading)

    artifact, already_published = find_published_artifact(source, digest, IMPORTER_NAME)
    if already_published:
        return _unchanged(state, period=period, dry_run=dry_run, correlation_id=correlation_id)

    if dry_run:
        # Publishes nothing, and — the part that matters — records nothing that
        # would let a later reader believe a live run had succeeded. The check
        # itself is already stamped by `touch_checked`, which is true: the API
        # really was queried.
        return SourceOutcome(
            result=FeedResult.IMPORTED,
            detail="Kuivkäivitus: Google Analytics vastas, midagi ei avaldatud.",
            dry_run=True,
            extra={"period_end": period.isoformat(), "figures_reported": _reported(reading)},
        )

    try:
        artifact = artifact or register_external_reference(
            source=source,
            external_reference=EXTERNAL_REFERENCE,
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
    except Exception as error:  # noqa: BLE001
        return _fail(state, describe_error(error), correlation_id)

    try:
        with transaction.atomic():
            observation = WebsiteTrafficObservation(
                source=source,
                artifact=artifact,
                import_run=run,
                observed_at=timezone.now(),
                period_start=period,
                period_end=period,
                sessions=reading.sessions,
                active_users=reading.active_users,
                page_views=reading.page_views,
                is_current=False,
            )
            observation.save()
            publish_current(observation)
            complete_import_run(run, rows_added=1, actor=actor)
            record_event(
                action=AuditAction.GA4_OBSERVATION_IMPORTED,
                obj=observation,
                actor=actor,
                correlation_id=correlation_id,
                # Aggregates, a checksum and identifiers. No property ID, no
                # credential, no token, no Google response.
                change_summary={
                    "source": source.slug,
                    "sha256": digest,
                    "period_end": period.isoformat(),
                    "observation_id": observation.pk,
                    **_figures(reading),
                },
            )
    except Exception as error:  # noqa: BLE001
        run.refresh_from_db()
        if not run.is_terminal:
            fail_import_run(run, errors=[{"type": type(error).__name__}], actor=actor)
        return _fail(state, describe_error(error), correlation_id)

    mark_imported(state, observation, current_field="current_observation")
    _remember_period(state, period)
    logger.info("ga4.sync imported period_end=%s", period.isoformat())
    return SourceOutcome(
        result=FeedResult.IMPORTED,
        detail="Uus veebiliikluse vaatlus avaldatud.",
        extra={
            "period_end": period.isoformat(),
            "observation_id": observation.pk,
            "figures_reported": _reported(reading),
        },
    )


def _figures(reading) -> dict:
    """The three aggregates, absent ones staying absent.

    For the audit trail only. The command's output deliberately does not carry
    them: a cron log is not where the dashboard's numbers belong, and the audit
    event is the place that already answers "what did this run publish?".
    """
    return {
        "sessions": reading.sessions,
        "active_users": reading.active_users,
        "page_views": reading.page_views,
    }


def _reported(reading) -> bool:
    """Whether GA4 returned any figure at all for the period.

    The one thing about the values an operator needs in a log: a run that
    succeeded against a day with no rows is a success, and looks identical to a
    successful ordinary day unless this says otherwise.
    """
    return any(
        value is not None for value in (reading.sessions, reading.active_users, reading.page_views)
    )


def _remember_period(state, period: date) -> None:
    """Record which reporting day the feed has reached."""
    state.last_period_end = period
    state.save(update_fields=["last_period_end", "updated_at"])


def _unchanged(state, *, period: date, dry_run: bool, correlation_id) -> SourceOutcome:
    """This day's figures are already published.

    The ordinary outcome of running twice, and of a schedule that fires after a
    manual catch-up run. It is a success, not a failure.
    """
    observation = state.current_observation
    if not dry_run:
        mark_unchanged(
            state,
            correlation_id=correlation_id,
            audit_action=AuditAction.GA4_SYNC_UNCHANGED,
            change_summary={"source": state.source.slug, "period_end": period.isoformat()},
        )
        _remember_period(state, period)
    return SourceOutcome(
        result=FeedResult.UNCHANGED,
        detail="Google Analyticsi andmed ei ole muutunud.",
        dry_run=dry_run,
        extra={
            "period_end": period.isoformat(),
            "observation_id": observation.pk if observation else None,
        },
    )


def _fail(state, message: str, correlation_id) -> SourceOutcome:
    """Record the failure and leave the last good observation published."""
    return fail_feed(
        state,
        message,
        correlation_id=correlation_id,
        audit_action=AuditAction.GA4_SYNC_FAILED,
        logger=logger,
    )


__all__ = ["IMPORTER_NAME", "LOCK_NAME", "default_period", "synchronize_ga4"]
