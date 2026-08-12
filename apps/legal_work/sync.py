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

import logging
from dataclasses import dataclass, field

from django.conf import settings

from apps.audit.services import record_event
from apps.core.feed_commands import (
    EXIT_FAILED,
    EXIT_LOCKED,  # noqa: F401 - re-exported; callers and tests import it from here
    EXIT_OK,
)
from apps.core.feeds import FeedLocked
from apps.core.feeds import advisory_lock as _advisory_lock
from apps.core.feeds import advisory_lock_key as _advisory_lock_key
from apps.legal_work.audit_actions import LegalWorkAudit

from .models import LegalWorkFeedState, SyncResult

logger = logging.getLogger("dashkoda.legal_work.sync")

WORKBOOK_FILENAME = "dashkoda_oigusloome.xlsx"

# Stable, derived from a name so it cannot collide with an ad-hoc integer
# someone else picks later.
ADVISORY_LOCK_NAMESPACE = "dashkoda.legal_work.sync_oigusloome"

# `EXIT_OK`, `EXIT_FAILED` and `EXIT_LOCKED` are imported above from
# `apps.core.feed_commands`, which now owns them: every feed command uses the
# same three. They stay importable from here because callers and tests have
# always taken them from this module.


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


#: The wording this feed has always used when its lock is held. Passed to the
#: shared helper so the operator-visible message is byte-identical to what it
#: was when this module carried its own copy of the lock.
LOCKED_MESSAGE = "Teine sünkroonimine juba käib."

#: This feed's own exception name, kept because `except SyncLocked` reads better
#: beside the feed's code and every caller and test already uses it. It **is**
#: `FeedLocked` rather than a subclass, so a lock taken through the shared
#: helper is caught by either name.
SyncLocked = FeedLocked


def advisory_lock_key(name: str = ADVISORY_LOCK_NAMESPACE) -> int:
    """This feed's lock key, from the canonical derivation."""
    return _advisory_lock_key(name)


def advisory_lock(name: str = ADVISORY_LOCK_NAMESPACE):
    """This feed's session-level lock, from the canonical helper.

    The name stays this module's, which is what keeps the feed independent of
    every other one; only the mechanism is shared.
    """
    return _advisory_lock(name, locked_message=LOCKED_MESSAGE)


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
        action=LegalWorkAudit.SYNC_FAILED,
        obj=state.source,
        correlation_id=correlation_id,
        change_summary={"detail": message[:300]},
    )


def source_slug() -> str:
    return settings.LEGAL_WORK_SOURCE_SLUG
