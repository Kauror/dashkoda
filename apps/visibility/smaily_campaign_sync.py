"""Catalogue completed Smaily campaigns and publish their aggregate statistics.

Two different kinds of thing, deliberately kept apart:

- a **campaign** is catalogued. Smaily's campaign list is a window onto recent
  sends, so a campaign that scrolls out of it would lose its name and its date
  unless something wrote them down. `SmailyCampaign` is that record, and it is
  updated rather than versioned — it is a catalogue, like `news.NewsResource`;
- a campaign's **statistics** are published as immutable revisions. Opens and
  clicks accrue for days after a send, bounces resolve, unsubscribes trickle in.
  The same campaign measured twice legitimately differs, and both measurements
  are kept.

## Why statistics are re-read, and only for a while

A send's figures move for roughly a fortnight and then stop. So each run
re-reads the statistics of every campaign completed within
:data:`STATS_RECONCILIATION_DAYS`, plus any campaign that has no statistics at
all — and nothing else. Re-reading two hundred settled campaigns every night
would be two hundred requests to somebody else's API to learn nothing.

The work is bounded twice over: :data:`MAX_STATS_PER_RUN` caps a single run, and
a campaign whose figures are unchanged publishes nothing. A backfill of the
whole history is the same code with a larger cap, run by hand.

## What is never requested

`detailed=1`. Smaily returns per-recipient rows for it — who opened, who
clicked, from which address — and this application has no field for any of that
and no lawful reason to hold it. The parameter is not sent, and
`apps.visibility.smaily` refuses a response that carries recipient-shaped keys
even so.

Nothing in this module logs, stores or returns the API username, the password,
the account subdomain or a Smaily response body.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.feed_sync import get_feed_state, touch_checked
from apps.core.feeds import FeedResult, SourceOutcome
from apps.sources.models import SourceArtifact
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    publishing_run,
    register_external_reference,
    start_import_run,
)
from apps.visibility.audit_actions import VisibilityAudit

from .bootstrap import ensure_smaily_source
from .models import SmailyCampaign, SmailyCampaignStats, SmailyFeedState
from .smaily import (
    SCHEMA_VERSION,
    CampaignRow,
    CampaignStatsRow,
    SmailyApiClient,
    SmailyNotConfigured,
    SmailyResponseError,
    get_configuration,
)
from .smaily_campaigns import classify

logger = logging.getLogger("dashkoda.visibility.smaily_campaign_sync")

IMPORTER_NAME = "smaily_campaigns"
ARTIFACT_NAME = "smaily-campaigns.json"

#: Its own lock: cataloguing campaigns and reading list sizes are separate runs
#: and neither should be able to block the other.
LOCK_NAME = "dashkoda.visibility.sync_smaily_campaigns"

#: How long after a send its figures are still worth re-reading. Opens and
#: clicks accrue for several days and then stop; a fortnight is comfortably past
#: it. Raising it costs one request per additional campaign in the window.
STATS_RECONCILIATION_DAYS = 14

#: How many campaigns one scheduled run may ask for statistics about. At the
#: paced request rate this is about half a minute of API time. A historical
#: backfill passes a larger value by hand.
MAX_STATS_PER_RUN = 40

#: How many campaigns the list request asks for. The account holds 3 194
#: completed campaigns going back to 2012 and sends roughly 200–270 a year, so
#: this covers the whole population in one bounded request. Listing is cheap —
#: one call, whatever the count; it is the per-campaign statistics that cost a
#: request each, and those are bounded separately.
CAMPAIGN_LIST_LIMIT = 5000


@dataclass
class CampaignCounts:
    """Aggregates for the command's JSON output. Never a campaign list."""

    campaigns_listed: int = 0
    campaigns_catalogued: int = 0
    campaigns_updated: int = 0
    campaigns_unclassified: int = 0
    stats_examined: int = 0
    stats_imported: int = 0
    stats_revised: int = 0
    stats_unchanged: int = 0
    api_requests: int = 0
    api_retries: int = 0

    def as_dict(self) -> dict:
        return {
            "campaigns_listed": self.campaigns_listed,
            "campaigns_catalogued": self.campaigns_catalogued,
            "campaigns_updated": self.campaigns_updated,
            "campaigns_unclassified": self.campaigns_unclassified,
            "stats_examined": self.stats_examined,
            "stats_imported": self.stats_imported,
            "stats_revised": self.stats_revised,
            "stats_unchanged": self.stats_unchanged,
            "api_requests": self.api_requests,
            "api_retries": self.api_retries,
        }


@dataclass
class CampaignReport:
    counts: CampaignCounts = field(default_factory=CampaignCounts)
    payloads: list[dict] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.counts.campaigns_catalogued
            or self.counts.stats_imported
            or self.counts.stats_revised
        )


def canonical_digest(payload: dict) -> tuple[bytes, str]:
    """Canonical bytes and their SHA-256.

    Over the **normalised figures**, never over the API response: Smaily is free
    to reorder keys or add a field without that meaning a campaign performed
    differently.
    """
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return body, hashlib.sha256(body).hexdigest()


def _transport_failure(error: Exception) -> str:
    """A sanitized sentence. A `requests` exception carries the request URL,
    and the URL names the account's subdomain.

    The traceback goes to the container log first: sanitizing what is *stored*
    is right, sanitizing what is diagnosable is not.
    """
    logger.exception("smaily campaign sync failed with an unexpected error")
    return f"Smaily päring ebaõnnestus ({type(error).__name__})."


def synchronize_campaigns(
    *,
    dry_run: bool = False,
    actor=None,
    collector=None,
    limit: int = CAMPAIGN_LIST_LIMIT,
    stats_limit: int = MAX_STATS_PER_RUN,
    today: date | None = None,
) -> SourceOutcome:
    """Catalogue completed campaigns and reconcile recent statistics."""
    correlation_id = uuid.uuid4()
    now_date = today or timezone.localdate()

    source = ensure_smaily_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(SmailyFeedState, source)
    touch_checked(state)

    try:
        collect = collector if collector is not None else SmailyApiClient(get_configuration())
    except SmailyNotConfigured as error:
        return _failure(str(error))
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _failure(_transport_failure(error))

    report = CampaignReport()

    try:
        campaigns = collect.collect_campaigns(limit=limit)
    except SmailyNotConfigured as error:
        return _failure(str(error))
    except SmailyResponseError as error:
        return _failure(str(error))
    except Exception as error:  # noqa: BLE001
        return _failure(_transport_failure(error))

    report.counts.campaigns_listed = len(campaigns)

    if not dry_run:
        _catalogue(campaigns, report=report)
    else:
        for row in campaigns:
            if not SmailyCampaign.objects.filter(campaign_id=row.campaign_id).exists():
                report.counts.campaigns_catalogued += 1
            if not classify(row.template_name, subject=row.name).is_newsletter:
                report.counts.campaigns_unclassified += 1

    wanted = _needs_statistics(now_date, limit=stats_limit)

    for campaign in wanted:
        report.counts.stats_examined += 1
        try:
            stats = collect.collect_campaign_stats(campaign.campaign_id)
        except SmailyResponseError as error:
            # Everything catalogued and published so far stays. One campaign
            # whose statistics cannot be read is not a reason to lose the rest.
            _record_counts(collect, report)
            return _failure(str(error), report=report)
        except Exception as error:  # noqa: BLE001
            _record_counts(collect, report)
            return _failure(_transport_failure(error), report=report)

        _note_stats(campaign, stats, report=report, dry_run=dry_run)

    _record_counts(collect, report)

    if not dry_run and report.payloads:
        try:
            _publish_statistics(
                source=source,
                actor=actor,
                correlation_id=correlation_id,
                report=report,
            )
        except Exception as error:  # noqa: BLE001
            return _failure(_transport_failure(error), report=report)

    logger.info(
        "smaily.campaigns listed=%d catalogued=%d stats_imported=%d stats_revised=%d",
        report.counts.campaigns_listed,
        report.counts.campaigns_catalogued,
        report.counts.stats_imported,
        report.counts.stats_revised,
    )

    return SourceOutcome(
        result=FeedResult.IMPORTED if report.changed else FeedResult.UNCHANGED,
        detail=_detail(report, dry_run=dry_run),
        dry_run=dry_run,
        extra=report.counts.as_dict(),
    )


def _failure(message: str, *, report: CampaignReport | None = None) -> SourceOutcome:
    """A sanitized failure that touches no published row.

    Deliberately **not** `fail_feed`: `SmailyFeedState` describes the audience
    reading, and a campaign run that fails must not make the newsletter figures
    look stale when they were collected successfully an hour earlier.
    """
    logger.warning("smaily campaign sync failed: %s", message)
    extra = report.counts.as_dict() if report is not None else CampaignCounts().as_dict()
    return SourceOutcome(result=FeedResult.FAILED, detail=message, extra=extra)


def _record_counts(collect, report: CampaignReport) -> None:
    counts = getattr(collect, "counts", None)
    if counts is None:
        return
    report.counts.api_requests = counts.requests
    report.counts.api_retries = counts.retries


def _catalogue(campaigns: tuple[CampaignRow, ...], *, report: CampaignReport) -> None:
    """Write down every campaign, classifying the ones seen for the first time.

    An existing row keeps its classification. A template renamed after the fact
    must not silently move last year's issues out of a newsletter's history —
    re-classification is possible but is a deliberate act, not something a
    nightly run does behind an operator's back.
    """
    for row in campaigns:
        existing = SmailyCampaign.objects.filter(campaign_id=row.campaign_id).first()
        if existing is None:
            classification = classify(row.template_name, subject=row.name)
            SmailyCampaign.objects.create(
                campaign_id=row.campaign_id,
                name=row.name,
                template_name=row.template_name,
                template_external_id=row.template_external_id,
                preview_url=row.preview_url,
                newsletter=classification.metric,
                audience=classification.audience,
                status=row.status,
                created_at=row.created_at,
                completed_at=row.completed_at,
            )
            report.counts.campaigns_catalogued += 1
            if not classification.is_newsletter:
                report.counts.campaigns_unclassified += 1
            continue

        changed = []
        for attribute, value in (
            ("name", row.name),
            ("template_name", row.template_name),
            ("template_external_id", row.template_external_id),
            # A preview that has appeared, moved or been validated differently
            # is worth carrying. A preview that has *gone* — the template was
            # deleted — is handled below, because an empty value is skipped by
            # the truthiness test this loop uses for everything else.
            ("preview_url", row.preview_url),
            ("status", row.status),
            ("completed_at", row.completed_at),
        ):
            if value and getattr(existing, attribute) != value:
                setattr(existing, attribute, value)
                changed.append(attribute)
        # A template deleted since the last run: the preview address now goes
        # nowhere, so it is cleared rather than left pointing at a dead page.
        # The campaign and its statistics are untouched — 67 campaigns on this
        # account are in exactly this state and they are still real history.
        if existing.preview_url and not row.preview_url:
            existing.preview_url = ""
            existing.template_external_id = ""
            changed.extend(["preview_url", "template_external_id"])

        # `last_seen_at` is `auto_now`, so saving at all records that Smaily
        # still lists this campaign.
        existing.save(update_fields=[*changed, "last_seen_at"] if changed else ["last_seen_at"])
        if changed:
            report.counts.campaigns_updated += 1
        if not existing.is_newsletter:
            report.counts.campaigns_unclassified += 1


def _needs_statistics(today: date, *, limit: int) -> list[SmailyCampaign]:
    """Campaigns whose figures are worth reading now, newest first.

    Two groups, and nothing else: a campaign with no statistics at all, and one
    completed recently enough that its figures are probably still moving.
    """
    if limit < 1:
        return []
    cutoff = timezone.now() - timedelta(days=STATS_RECONCILIATION_DAYS)
    settled = set(
        SmailyCampaignStats.objects.filter(is_current=True).values_list("campaign_id", flat=True)
    )
    candidates = SmailyCampaign.objects.order_by("-completed_at", "-campaign_id")
    wanted = []
    for campaign in candidates.iterator(chunk_size=200):
        never_measured = campaign.pk not in settled
        still_moving = campaign.completed_at is not None and campaign.completed_at >= cutoff
        if never_measured or still_moving:
            wanted.append(campaign)
        if len(wanted) >= limit:
            break
    return wanted


def _note_stats(
    campaign: SmailyCampaign,
    stats: CampaignStatsRow,
    *,
    report: CampaignReport,
    dry_run: bool,
) -> None:
    """Decide what this reading means, without writing anything yet."""
    if not stats.has_any_figure:
        # Smaily reported nothing measurable. That is not a campaign with zero
        # opens; it is a campaign with no figures, and it publishes no row.
        return

    payload = {
        "schema": SCHEMA_VERSION,
        "kind": "campaign_stats",
        "campaign_id": campaign.campaign_id,
        **stats.payload(),
    }
    _, digest = canonical_digest(payload)

    current = (
        SmailyCampaignStats.objects.filter(campaign=campaign, is_current=True)
        .only("id", "checksum", "revision")
        .first()
    )
    if current is not None and current.checksum == digest:
        report.counts.stats_unchanged += 1
        return

    if current is None:
        report.counts.stats_imported += 1
    else:
        report.counts.stats_revised += 1

    if not dry_run:
        report.payloads.append(
            {"campaign": campaign, "stats": stats, "digest": digest, "payload": payload}
        )


def _publish_statistics(*, source, actor, correlation_id, report: CampaignReport) -> None:
    """Write every pending statistics revision in one run.

    One import run for the whole batch rather than one per campaign: this is a
    single collection, and forty import runs describing one API session would
    make the provenance harder to read rather than more precise.
    """
    body, digest = canonical_digest(
        {
            "schema": SCHEMA_VERSION,
            "kind": "campaign_stats_batch",
            # Sorted by campaign, because the order Smaily listed them in is not
            # part of what was measured.
            "campaigns": sorted(
                (entry["payload"] for entry in report.payloads),
                key=lambda item: item["campaign_id"],
            ),
        }
    )

    artifact = SourceArtifact.objects.filter(source=source, sha256=digest).first()
    if artifact is None:
        artifact = register_external_reference(
            source=source,
            # A fixed, non-secret provenance label. Never the account subdomain.
            external_reference=f"smaily:campaign-api:{timezone.localdate().isoformat()}",
            original_name=ARTIFACT_NAME,
            mime_type="application/json",
            sha256=digest,
            size_bytes=len(body),
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

    written = 0
    with publishing_run(
        run, errors=[{"detail": "Kampaaniate statistika avaldamine ebaõnnestus."}], actor=actor
    ):
        with transaction.atomic():
            for entry in report.payloads:
                campaign = entry["campaign"]
                stats = entry["stats"]
                locked = (
                    SmailyCampaignStats.objects.select_for_update()
                    .filter(campaign=campaign, is_current=True)
                    .first()
                )
                revision = SmailyCampaignStats(
                    campaign=campaign,
                    artifact=artifact,
                    import_run=run,
                    observed_at=timezone.now(),
                    checksum=entry["digest"],
                    revision=(locked.revision + 1) if locked is not None else 1,
                    supersedes=locked,
                    is_current=False,
                    **{name: getattr(stats, name) for name in SmailyCampaignStats.COUNT_FIELDS},
                )
                revision.save()
                if locked is not None:
                    locked.is_current = False
                    locked.save(update_fields=["is_current"])
                revision.is_current = True
                revision.save(update_fields=["is_current"])
                written += 1

            complete_import_run(run, rows_added=written, actor=actor)

    from apps.audit.services import record_event

    record_event(
        action=VisibilityAudit.SMAILY_OBSERVATION_IMPORTED,
        obj=run,
        correlation_id=correlation_id,
        actor=actor,
        change_summary={
            "source": source.slug,
            "kind": "campaign_stats",
            # Counts only. No campaign name, no subject, no figure.
            "revisions_written": written,
            "campaigns_catalogued": report.counts.campaigns_catalogued,
        },
    )


def _detail(report: CampaignReport, *, dry_run: bool) -> str:
    prefix = "Proovikäivitus: " if dry_run else ""
    counts = report.counts
    if not report.changed:
        return prefix + "Smaily kampaaniates ei ole muutusi."
    parts = []
    if counts.campaigns_catalogued:
        parts.append(f"{counts.campaigns_catalogued} uut kampaaniat")
    if counts.stats_imported:
        parts.append(f"{counts.stats_imported} statistikat lisatud")
    if counts.stats_revised:
        parts.append(f"{counts.stats_revised} statistikat uuendatud")
    return prefix + ", ".join(parts) + "."


__all__ = [
    "ARTIFACT_NAME",
    "CAMPAIGN_LIST_LIMIT",
    "IMPORTER_NAME",
    "LOCK_NAME",
    "MAX_STATS_PER_RUN",
    "STATS_RECONCILIATION_DAYS",
    "CampaignCounts",
    "CampaignReport",
    "canonical_digest",
    "synchronize_campaigns",
]
