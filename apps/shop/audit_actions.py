"""Audit actions this app records.

Declared here rather than in `apps.audit`, so adding one is a change to the
E-pood commerce import and to nothing else.

The stored value is the contract -- production rows carry it and the
append-only trigger means none can be rewritten -- so a value is never
edited, only added.
"""

from __future__ import annotations

from django.db import models


class ShopAudit(models.TextChoices):
    SNAPSHOT_FAILED = "shop.snapshot_failed", "E-poe andmete import ebaõnnestus"
    SNAPSHOT_IMPORTED = "shop.snapshot_imported", "E-poe andmed imporditud"
    SNAPSHOT_UNCHANGED = "shop.snapshot_unchanged", "E-poe andmed: muutusteta"
