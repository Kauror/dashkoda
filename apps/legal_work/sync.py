"""Shared plumbing for the legal-work workbook feed.

The feed has exactly one recurring collection route — the public read-only
sharing link in :mod:`apps.legal_work.public_sync`. This module holds what that
route (and the manual ``import_oigusloome`` command) needs but which is not
specific to how the bytes arrive: the canonical filename, the advisory lock,
the feed-state helpers, the sanitized failure record and the outcome type the
management command prints.

The whole run is guarded by a PostgreSQL advisory lock, so two overlapping
invocations can never both import. A host-side `flock` is documented as
defence in depth, but the application-level guarantee lives here: it survives
being started from a different host, container or shell.

Failure is never destructive. Whatever goes wrong, the previously published
snapshot stays current and the dashboard keeps showing the last good data with
an honest "last check failed" note.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field

from django.conf import settings
from django.db import connection

from apps.audit.models import AuditAction
from apps.audit.services import record_event

from .models import LegalWorkFeedState, SyncResult

logger = logging.getLogger("dashkoda.legal_work.sync")

WORKBOOK_FILENAME = "dashkoda_oigusloome.xlsx"

# Stable, derived from a name so it cannot collide with an ad-hoc integer
# someone else picks later.
ADVISORY_LOCK_NAMESPACE = "dashkoda.legal_work.sync_oigusloome"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_LOCKED = 3


class SyncLocked(RuntimeError):
    """Another synchronisation is already running."""


@dataclass
class SyncOutcome:
    result: str
    detail: str = ""
    snapshot_id: int | None = None
    reporting_date: str | None = None
    rows_imported: int = 0
    dry_run: bool = False
    warnings: list = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.result == SyncResult.FAILED:
            return EXIT_FAILED
        return EXIT_OK

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "detail": self.detail,
            "snapshot_id": self.snapshot_id,
            "reporting_date": self.reporting_date,
            "rows_imported": self.rows_imported,
            "dry_run": self.dry_run,
            "warnings": self.warnings,
        }


def advisory_lock_key(name: str = ADVISORY_LOCK_NAMESPACE) -> int:
    """A stable signed 64-bit key for `pg_try_advisory_lock`."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


@contextmanager
def advisory_lock(name: str = ADVISORY_LOCK_NAMESPACE):
    """Session-level advisory lock held for the whole run.

    Session level rather than transaction level, because the sync downloads and
    parses outside any transaction and holding one open for that long would pin
    a connection and bloat the database.
    """
    key = advisory_lock_key(name)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = cursor.fetchone()[0]
    if not acquired:
        raise SyncLocked("Teine sünkroonimine juba käib.")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def get_feed_state(source) -> LegalWorkFeedState:
    state, _created = LegalWorkFeedState.objects.get_or_create(source=source)
    return state


def record_failure(state: LegalWorkFeedState, message: str, *, correlation_id) -> None:
    """Record a sanitized failure without touching the published snapshot.

    "The last check failed and the dashboard keeps showing the previous data"
    has to mean one thing, so every writer of a failed legal-work check goes
    through here.
    """
    state.last_result = SyncResult.FAILED
    state.last_error_summary = message[:500]
    state.save(update_fields=["last_result", "last_error_summary", "last_checked_at", "updated_at"])
    record_event(
        action=AuditAction.LEGAL_WORK_SYNC_FAILED,
        obj=state.source,
        correlation_id=correlation_id,
        change_summary={"detail": message[:300]},
    )


def source_slug() -> str:
    return settings.LEGAL_WORK_SOURCE_SLUG
