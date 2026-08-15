"""Audit actions this app records.

Declared here rather than in `apps.audit`, so adding one is a change to the
board-report history and the public directory count and to nothing else.

The stored value is the contract -- production rows carry it and the
append-only trigger means none can be rewritten -- so a value is never
edited, only added.
"""

from __future__ import annotations

from django.db import models


class MembershipAudit(models.TextChoices):
    COMPOSITION_IMPORTED = (
        "membership.composition_imported",
        "Liikmeskonna koosseis imporditud",
    )
    COMPOSITION_UNCHANGED = (
        "membership.composition_unchanged",
        "Liikmeskonna koosseis: muutusteta",
    )
    HISTORY_FAILED = "membership.history_failed", "Liikmeskonna ajaloo import ebaõnnestus"
    HISTORY_IMPORTED = "membership.history_imported", "Liikmeskonna ajalugu imporditud"
    HISTORY_UNCHANGED = "membership.history_unchanged", "Liikmeskonna ajalugu: muutusteta"
    ISSUE_RESOLVED = "membership.issue_resolved", "Liikmeskonna andmeprobleem lahendatud"
    MANUAL_OBSERVATION_CREATED = (
        "membership.manual_observation_created",
        "Liikmeskonna aruanne käsitsi lisatud",
    )
    MANUAL_OBSERVATION_SUPERSEDED = (
        "membership.manual_observation_superseded",
        "Liikmeskonna vaatlus asendatud",
    )
    OBSERVATION_IMPORTED = "membership.observation_imported", "Liikmete arv imporditud"
    SYNC_FAILED = "membership.sync_failed", "Liikmete arvu sünkroonimine ebaõnnestus"
    SYNC_UNCHANGED = "membership.sync_unchanged", "Liikmete arv: muutusteta"
