"""Reconcile the public directory's published codes into the working register.

Its own advisory lock, its own source, its own import run and its own
transaction — so this can fail all week without the member count noticing, and
a change here can never rewrite that series.

## Why this publishes a register rather than a snapshot per run

The directory is checked daily and changes a handful of rows a month. One
snapshot per run would store 3 400 rows to record that four of them moved, and
answering "since when has this member been listed?" would mean walking every
snapshot ever taken. So the rows are a **carry-forward register**: a code seen
today has its `last_seen_at` refreshed, a code seen for the first time is
created, and a code that stops appearing is marked unpublished with the moment
it went — never deleted. `first_seen_at` then answers the "since when" question
directly, and a restored member keeps the date it originally appeared.

The immutable half is still there: each distinct published *set* is registered
as an artifact and an import run carrying its canonical checksum, so what the
directory published on a given day remains provable.

## Reconciliation is not gated on the run being new

A member unpublished on Monday and restored on Tuesday returns the directory to
a byte-identical set, which the import key correctly calls `unchanged` — and
the register still has to bring that row back. So the reconciliation runs on
every successful fetch and the import run is opened only for a set never
published before. The reconciliation is idempotent, which is what makes that
safe: applying it twice to the same set changes only `last_seen_at`.
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
    mark_unchanged,
    start_run,
    touch_checked,
)
from apps.core.feeds import FeedResult, SourceOutcome
from apps.sources.services import complete_import_run, fail_publication

from .audit_actions import MembershipAudit
from .bootstrap import ensure_member_directory_source
from .collector import MembershipCollectionError, is_change_plausible
from .directory_collector import NORMALISED_SCHEMA_VERSION, collect_directory
from .models import MemberDirectoryEntry, MembershipFeedState

logger = logging.getLogger("dashkoda.membership.directory_sync")

IMPORTER_NAME = "koda_member_directory"
EXTERNAL_REFERENCE = "koda-public:company-list-entries"
ARTIFACT_NAME = "koda-company-list-entries.json"
LOCK_NAME = "dashkoda.membership.sync_member_directory"


class DirectoryReconciliation:
    """What one reconciliation did. Counts only; no code is named."""

    __slots__ = ("added", "restored", "moved", "unpublished", "seen")

    def __init__(self):
        self.added = 0
        self.restored = 0
        self.moved = 0
        self.unpublished = 0
        self.seen = 0

    def as_dict(self) -> dict:
        return {
            "added": self.added,
            "restored": self.restored,
            "moved": self.moved,
            "unpublished": self.unpublished,
            "seen": self.seen,
        }

    @property
    def changed(self) -> bool:
        return bool(self.added or self.restored or self.moved or self.unpublished)


def reconcile_entries(source, rows, *, now=None) -> DirectoryReconciliation:
    """Bring the register in line with one observed publication.

    Idempotent by construction: everything below is derived from the observed
    set rather than from a delta, so applying the same set twice moves only
    `last_seen_at`.
    """
    now = now or timezone.now()
    result = DirectoryReconciliation()
    result.seen = len(rows)

    existing = {
        entry.registry_code: entry
        for entry in MemberDirectoryEntry.objects.select_for_update().filter(source=source)
    }
    observed = {row.registry_code: row.profile_path for row in rows}

    created: list[MemberDirectoryEntry] = []
    updated: list[MemberDirectoryEntry] = []

    for code, path in observed.items():
        entry = existing.get(code)
        if entry is None:
            created.append(
                MemberDirectoryEntry(
                    source=source,
                    registry_code=code,
                    profile_path=path,
                    first_seen_at=now,
                    last_seen_at=now,
                    is_published=True,
                )
            )
            result.added += 1
            continue
        if not entry.is_published:
            entry.is_published = True
            entry.unpublished_at = None
            result.restored += 1
        if entry.profile_path != path:
            # Koda.ee renamed the profile's slug. The row is the same member —
            # the registration code says so — so the path is corrected rather
            # than treated as a new member.
            entry.profile_path = path
            result.moved += 1
        entry.last_seen_at = now
        updated.append(entry)

    for code, entry in existing.items():
        if code in observed or not entry.is_published:
            continue
        entry.is_published = False
        entry.unpublished_at = now
        updated.append(entry)
        result.unpublished += 1

    if created:
        MemberDirectoryEntry.objects.bulk_create(created, batch_size=500)
    if updated:
        MemberDirectoryEntry.objects.bulk_update(
            updated,
            ["profile_path", "first_seen_at", "last_seen_at", "is_published", "unpublished_at"],
            batch_size=500,
        )
    return result


def synchronize_member_directory(
    *, dry_run: bool = False, actor=None, collector=None
) -> SourceOutcome:
    """Run one directory collection. Never raises for an ordinary failure."""
    correlation_id = uuid.uuid4()
    source = ensure_member_directory_source(actor=actor, correlation_id=correlation_id)
    state = get_feed_state(MembershipFeedState, source)
    touch_checked(state)

    collect = collector or collect_directory
    published_now = MemberDirectoryEntry.objects.filter(source=source, is_published=True).count()

    try:
        collection = collect(
            etag=state.remote_etag if published_now else "",
            last_modified=state.remote_last_modified if published_now else "",
        )
    except MembershipCollectionError as error:
        return _fail(state, str(error), correlation_id)
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _fail(state, describe_error(error), correlation_id)

    if collection is None:
        # A 304 means the published set is byte-identical to what we last saw,
        # so there is nothing to reconcile.
        return _unchanged(state, None, dry_run=dry_run, correlation_id=correlation_id, seen=None)

    plausible, reason = is_change_plausible(published_now or None, collection.total_rows)
    if not plausible:
        # Fail closed, exactly as the count does: a movement this large is far
        # more likely to be a source or parsing fault than membership news, and
        # unpublishing hundreds of rows on a bad fetch would be the expensive
        # kind of wrong.
        return _fail(state, reason, correlation_id)

    if dry_run:
        return SourceOutcome(
            result=FeedResult.IMPORTED,
            detail=(
                f"Kuivkäivitus: kataloogis {collection.total_rows} kirjet, midagi ei salvestatud."
            ),
            dry_run=True,
            extra={"entries": collection.total_rows},
        )

    artifact, already_published = find_published_artifact(source, collection.sha256, IMPORTER_NAME)
    if already_published:
        # These exact bytes have been published before — a member that left and
        # came back returns the directory to an earlier set. The run is not
        # repeated, but the register still has to match what is published now.
        try:
            with transaction.atomic():
                reconciliation = reconcile_entries(source, collection.rows)
        except Exception as error:  # noqa: BLE001
            return _fail(state, describe_error(error), correlation_id)
        return _unchanged(
            state,
            collection,
            dry_run=False,
            correlation_id=correlation_id,
            seen=reconciliation,
        )

    try:
        artifact, run = start_run(
            source,
            collection,
            existing_artifact=artifact,
            importer_name=IMPORTER_NAME,
            external_reference=EXTERNAL_REFERENCE,
            artifact_name=ARTIFACT_NAME,
            schema_version=NORMALISED_SCHEMA_VERSION,
            dry_run=False,
            actor=actor,
            correlation_id=correlation_id,
        )
    except Exception as error:  # noqa: BLE001
        return _fail(state, describe_error(error), correlation_id)

    try:
        with transaction.atomic():
            reconciliation = reconcile_entries(source, collection.rows)
            complete_import_run(
                run,
                rows_added=reconciliation.added,
                rows_skipped=reconciliation.seen - reconciliation.added,
                actor=actor,
            )
            record_event(
                action=MembershipAudit.DIRECTORY_IMPORTED,
                obj=source,
                actor=actor,
                correlation_id=correlation_id,
                change_summary={
                    "source": source.slug,
                    "sha256": collection.sha256,
                    **reconciliation.as_dict(),
                },
            )
    except Exception as error:  # noqa: BLE001
        fail_publication(run, errors=[{"type": type(error).__name__}], actor=actor)
        return _fail(state, describe_error(error), correlation_id)

    _mark_imported(state, collection)
    logger.info(
        "membership.directory.sync entries=%s added=%s unpublished=%s",
        reconciliation.seen,
        reconciliation.added,
        reconciliation.unpublished,
    )
    return SourceOutcome(
        result=FeedResult.IMPORTED,
        detail=(
            f"Kataloogi kirjed uuendatud: {reconciliation.seen} avaldatud, "
            f"lisandus {reconciliation.added}, kadus {reconciliation.unpublished}."
        ),
        extra={"entries": reconciliation.seen, **reconciliation.as_dict()},
    )


def _mark_imported(state, collection) -> None:
    """Record the publication on the feed state.

    Not `feed_sync.mark_imported`: that one points a feed state at the row it
    just published, and this feed publishes a reconciled register rather than a
    single current row. `current_observation` stays NULL for this source and
    the count's own state keeps it.
    """
    now = timezone.now()
    state.last_result = FeedResult.IMPORTED
    state.last_error_summary = ""
    state.last_successful_sync_at = now
    state.last_changed_at = now
    state.remote_etag = (collection.etag or "")[:200]
    state.remote_last_modified = (collection.last_modified or "")[:100]
    state.save(
        update_fields=[
            "last_result",
            "last_error_summary",
            "last_successful_sync_at",
            "last_changed_at",
            "remote_etag",
            "remote_last_modified",
            "updated_at",
        ]
    )


def _unchanged(state, collection, *, dry_run: bool, correlation_id, seen) -> SourceOutcome:
    published = MemberDirectoryEntry.objects.filter(source=state.source, is_published=True).count()
    if not dry_run:
        mark_unchanged(
            state,
            correlation_id=correlation_id,
            audit_action=MembershipAudit.DIRECTORY_UNCHANGED,
            change_summary={
                "source": state.source.slug,
                "entries": published,
                **(seen.as_dict() if seen is not None else {}),
            },
            etag=collection.etag if collection is not None else None,
            last_modified=collection.last_modified if collection is not None else None,
        )
    return SourceOutcome(
        result=FeedResult.UNCHANGED,
        detail="Kataloogi kirjed ei ole muutunud.",
        dry_run=dry_run,
        extra={"entries": published},
    )


def _fail(state, message: str, correlation_id) -> SourceOutcome:
    return fail_feed(
        state,
        message,
        correlation_id=correlation_id,
        audit_action=MembershipAudit.DIRECTORY_FAILED,
        logger=logger,
    )
