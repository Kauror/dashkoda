"""Audit actions this app records.

Declared here rather than in `apps.audit`, so adding one is a change to the
manual audience figures, GA4 and Smaily and to nothing else.

The stored value is the contract -- production rows carry it and the
append-only trigger means none can be rewritten -- so a value is never
edited, only added.
"""

from __future__ import annotations

from django.db import models


class VisibilityAudit(models.TextChoices):
    GA4_OBSERVATION_IMPORTED = (
        "visibility.ga4_observation_imported",
        "Veebiliikluse vaatlus imporditud",
    )
    GA4_SYNC_FAILED = "visibility.ga4_sync_failed", "Veebiliikluse kogumine ebaõnnestus"
    GA4_SYNC_UNCHANGED = "visibility.ga4_sync_unchanged", "Veebiliikluse kogumine: muutusteta"
    MANUAL_BATCH_PUBLISHED = (
        "visibility.manual_batch_published",
        "Nähtavuse näitajad käsitsi sisestatud",
    )
    OBSERVATION_PUBLISHED = "visibility.observation_published", "Nähtavuse vaatlus avaldatud"
    OBSERVATION_SUPERSEDED = "visibility.observation_superseded", "Nähtavuse vaatlus asendatud"
    SMAILY_OBSERVATION_IMPORTED = (
        "visibility.smaily_observation_imported",
        "Uudiskirjade lugemine imporditud",
    )
    SMAILY_SYNC_FAILED = "visibility.smaily_sync_failed", "Uudiskirjade kogumine ebaõnnestus"
    SMAILY_SYNC_UNCHANGED = "visibility.smaily_sync_unchanged", "Uudiskirjade kogumine: muutusteta"
