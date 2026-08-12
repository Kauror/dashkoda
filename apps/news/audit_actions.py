"""Audit actions this app records.

Declared here rather than in `apps.audit`, so adding one is a change to the
news feed and its durable article catalogue and to nothing else.

The stored value is the contract -- production rows carry it and the
append-only trigger means none can be rewritten -- so a value is never
edited, only added.
"""

from __future__ import annotations

from django.db import models


class NewsAudit(models.TextChoices):
    SNAPSHOT_IMPORTED = "news.snapshot_imported", "Uudiste hetkeseis imporditud"
    SYNC_FAILED = "news.sync_failed", "Uudiste sünkroonimine ebaõnnestus"
    SYNC_UNCHANGED = "news.sync_unchanged", "Uudised: muutusteta"
