"""Shared vocabulary and locking for the public feed collectors.

Three domain apps publish from three public sources. They share the words for
"what happened on the last check" and the mechanism for "only one run at a
time", so those live here rather than being written out three times.

Everything domain-specific — what a valid record is, what gets published, what
the dashboard shows — stays in the domain app. This module deliberately holds no
model, no query and no business rule.

The legal-work module keeps its own copy of the advisory-lock helper. It works,
it is covered by its own tests, and collapsing the two to save a dozen lines
would put a working feed at risk for no product gain.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass, field

from django.db import connection, models


class FeedResult(models.TextChoices):
    """How a source's last check ended."""

    NEVER_RUN = "never_run", "Pole veel käivitatud"
    IMPORTED = "imported", "Imporditud"
    UNCHANGED = "unchanged", "Muutumatu"
    FAILED = "failed", "Ebaõnnestus"


class FeedLocked(RuntimeError):
    """Another run already holds this source's lock."""


class FeedSummaryMixin:
    """The shared freshness vocabulary of the per-feed summary dataclasses.

    A concrete summary provides two things: a ``feed_state`` attribute holding
    the feed's state row (or ``None``) and a ``has_data`` property saying
    whether anything is currently published. Everything below derives from
    those, so "stale", "failed" and the state badge mean exactly the same thing
    on every page.
    """

    @property
    def last_checked_at(self):
        return self.feed_state.last_checked_at if self.feed_state else None

    @property
    def last_successful_sync_at(self):
        return self.feed_state.last_successful_sync_at if self.feed_state else None

    @property
    def last_result(self) -> str:
        return self.feed_state.last_result if self.feed_state else FeedResult.NEVER_RUN

    @property
    def last_sync_failed(self) -> bool:
        return self.last_result == FeedResult.FAILED

    @property
    def is_stale_after_failure(self) -> bool:
        """Showing older data because the newest check did not succeed."""
        return self.last_sync_failed and self.has_data

    @property
    def state_label(self) -> str:
        if not self.has_data:
            return "Ühendamata"
        return "Vananenud" if self.last_sync_failed else "Ühendatud"

    @property
    def state_variant(self) -> str:
        if not self.has_data:
            return "neutral"
        return "warning" if self.last_sync_failed else "success"


def advisory_lock_key(name: str) -> int:
    """A stable signed 64-bit key for `pg_try_advisory_lock`.

    Derived from a name so it cannot collide with an ad-hoc integer someone
    picks later, and so each source gets its own independent lock.
    """
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big", signed=True)


@contextmanager
def advisory_lock(name: str):
    """Session-level advisory lock held for one source's whole run.

    Per source, not per command: a slow events crawl must not stop membership
    from being collected, and neither may collide with the legal-work
    synchronisation, which uses its own separate name.

    Session level rather than transaction level, because collection happens
    outside any transaction and holding one open across HTTP calls would pin a
    connection.
    """
    key = advisory_lock_key(name)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = cursor.fetchone()[0]
    if not acquired:
        raise FeedLocked(f"Allika {name} sünkroonimine juba käib.")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


@dataclass
class SourceOutcome:
    """What one source's run produced. Never carries source content."""

    result: str
    detail: str = ""
    dry_run: bool = False
    # Domain-specific, non-sensitive counters: a member total, an item count.
    extra: dict = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.result in (FeedResult.IMPORTED, FeedResult.UNCHANGED)

    def as_dict(self) -> dict:
        payload = {"result": self.result, "detail": self.detail}
        if self.dry_run:
            payload["dry_run"] = True
        payload.update(self.extra)
        return payload
