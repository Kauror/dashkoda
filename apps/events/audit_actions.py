"""Audit actions this app records.

Declared here rather than in `apps.audit`, so adding one is a change to the
public events calendar and the durable event-page catalogue and to nothing
else.

The stored value is the contract -- production rows carry it and the
append-only trigger means none can be rewritten -- so a value is never
edited, only added.
"""

from __future__ import annotations

from django.db import models


class EventsAudit(models.TextChoices):
    EVENT_PAGES_DISCOVERED = "events.pages_discovered", "Avalikud sündmuste lehed läbi vaadatud"
    SNAPSHOT_IMPORTED = "events.snapshot_imported", "Sündmuste hetkeseis imporditud"
    SYNC_FAILED = "events.sync_failed", "Sündmuste sünkroonimine ebaõnnestus"
    SYNC_UNCHANGED = "events.sync_unchanged", "Sündmused: muutusteta"
