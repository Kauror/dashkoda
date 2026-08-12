"""Audit actions this app records.

Declared here rather than in `apps.audit`, so adding one is a change to the
canonical event programme and its public-link matching and to nothing else.

The stored value is the contract -- production rows carry it and the
append-only trigger means none can be rewritten -- so a value is never
edited, only added.
"""

from __future__ import annotations

from django.db import models


class EventProgrammeAudit(models.TextChoices):
    EVENT_PUBLIC_LINKS_MATCHED = (
        "event_programme.public_links_matched",
        "Sündmuste avalikud viited sobitatud",
    )
    SNAPSHOT_IMPORTED = (
        "event_programme.snapshot_imported",
        "Sündmuste programmi hetkeseis imporditud",
    )
    SNAPSHOT_PUBLISHED = (
        "event_programme.snapshot_published",
        "Sündmuste programmi hetkeseis kehtestatud",
    )
    SYNC_FAILED = "event_programme.sync_failed", "Sündmuste programmi sünkroonimine ebaõnnestus"
    SYNC_UNCHANGED = "event_programme.sync_unchanged", "Sündmuste programm: muutusteta"
