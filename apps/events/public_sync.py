"""Run public event-page discovery and record what it did.

Much lighter than `sync.py` beside it, and deliberately so. That module imports
a document: it stores the source bytes as an artifact, publishes an immutable
snapshot and swaps `is_current` so readers move between whole consistent
versions. Here there is no document. Discovery walks a sitemap and a few hundred
pages, and its output is a *cumulative* catalogue that no snapshot owns.

So this writes one row describing the run and nothing else. Resources are
created and re-observed as they are read, outside any single transaction,
because a crawl that dies at page 300 should keep the first 299 pages rather
than throw them away — they are correct, and re-fetching them costs the public
site 299 requests to learn nothing.

The consequence is that `is_current` here means "the most recent run that
finished", not "the version readers see". Nothing reads through it.
"""

from __future__ import annotations

import logging
import uuid

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event

from .public_bootstrap import ensure_event_pages_source
from .public_discovery import DiscoveryTally, discover_public_events
from .public_models import DiscoveryMode, PublicEventDiscoverySnapshot

logger = logging.getLogger("dashkoda.events.public_sync")

LOCK_NAME = "dashkoda.events.discover_public_event_pages"
LOCKED_MESSAGE = "Avalike sündmuste lehtede avastamine juba käib."


def _record(source, tally: DiscoveryTally, *, observed_at) -> PublicEventDiscoverySnapshot:
    """Write the run's row and make it the current one.

    In a transaction because the two statements are one fact: exactly one run
    per source may be current, and a crash between clearing the old flag and
    setting the new one would leave none.
    """
    with transaction.atomic():
        PublicEventDiscoverySnapshot.objects.filter(source=source, is_current=True).update(
            is_current=False
        )
        return PublicEventDiscoverySnapshot.objects.create(
            source=source,
            mode=tally.mode,
            observed_at=observed_at,
            is_current=True,
            pages_fetched=tally.pages_fetched,
            urls_seen=tally.urls_seen,
            resources_created=tally.created,
            resources_updated=tally.updated,
            resources_unchanged=tally.unchanged,
            is_complete=tally.is_complete,
            error_count=tally.errors,
            warning_codes=list(tally.warnings),
        )


def discover_event_pages(
    *,
    mode: str = DiscoveryMode.INCREMENTAL,
    max_detail_pages: int | None = None,
    dry_run: bool = False,
    actor=None,
    discover=None,
) -> DiscoveryTally:
    """Discover public event pages and record the run.

    The caller holds `LOCK_NAME`, as it does for every other feed here, so the
    command owns both the contention message and the exit code.

    A dry run still crawls — that is the only way to know what discovery would
    find — but writes no resources and no snapshot. It is a read of the public
    site, which is what `--dry-run` means for every other Koda.ee feed here.
    """
    correlation_id = uuid.uuid4()
    source = ensure_event_pages_source(actor=actor, correlation_id=correlation_id)
    run = discover or discover_public_events

    observed_at = timezone.now()
    tally = run(mode=mode, max_detail_pages=max_detail_pages, dry_run=dry_run)

    if dry_run:
        return tally

    snapshot = _record(source, tally, observed_at=observed_at)

    record_event(
        action=AuditAction.EVENT_PAGES_DISCOVERED,
        obj=snapshot,
        actor=actor,
        correlation_id=correlation_id,
        change_summary={
            "source": source.slug,
            "mode": tally.mode,
            "urls_seen": tally.urls_seen,
            "pages_fetched": tally.pages_fetched,
            "created": tally.created,
            "updated": tally.updated,
            "unchanged": tally.unchanged,
            "is_complete": tally.is_complete,
            "error_count": tally.errors,
            "warning_codes": tally.warnings,
            "snapshot_id": snapshot.pk,
        },
    )
    logger.info(
        "public event discovery (%s): %s seen, %s fetched, +%s ~%s =%s, complete=%s",
        tally.mode,
        tally.urls_seen,
        tally.pages_fetched,
        tally.created,
        tally.updated,
        tally.unchanged,
        tally.is_complete,
    )
    return tally
