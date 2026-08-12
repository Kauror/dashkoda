"""Audit actions this app records.

Declared here rather than in `apps.audit`, so adding one is a change to the
legal-work workbook, the Koda.ee catalogues, the matchers and the opinion
store and to nothing else.

The stored value is the contract -- production rows carry it and the
append-only trigger means none can be rewritten -- so a value is never
edited, only added.
"""

from __future__ import annotations

from django.db import models


class LegalWorkAudit(models.TextChoices):
    ARCHIVED_TOPIC_MATCH_FAILED = (
        "legal_work.archived_topic_match_failed",
        "Arhiivi sobitamine ebaõnnestus",
    )
    ARCHIVED_TOPIC_MATCH_GENERATED = (
        "legal_work.archived_topic_match_generated",
        "Arhiivi sobitamine arvutatud",
    )
    ARCHIVED_TOPIC_MATCH_UNCHANGED = (
        "legal_work.archived_topic_match_unchanged",
        "Arhiivi sobitamine: muutusteta",
    )
    ARCHIVED_TOPIC_SNAPSHOT_IMPORTED = (
        "legal_work.archived_topic_snapshot_imported",
        "Arhiivi hetkeseis imporditud",
    )
    ARCHIVED_TOPIC_SYNC_FAILED = (
        "legal_work.archived_topic_sync_failed",
        "Arhiivi sünkroonimine ebaõnnestus",
    )
    ARCHIVED_TOPIC_SYNC_UNCHANGED = "legal_work.archived_topic_sync_unchanged", "Arhiiv: muutusteta"
    CURRENT_TOPIC_MATCH_FAILED = (
        "legal_work.current_topic_match_failed",
        "Õigusloome sobitamine ebaõnnestus",
    )
    CURRENT_TOPIC_MATCH_GENERATED = (
        "legal_work.current_topic_match_generated",
        "Õigusloome sobitamine arvutatud",
    )
    CURRENT_TOPIC_MATCH_UNCHANGED = (
        "legal_work.current_topic_match_unchanged",
        "Õigusloome sobitamine: muutusteta",
    )
    CURRENT_TOPIC_SNAPSHOT_IMPORTED = (
        "legal_work.current_topic_snapshot_imported",
        "Hetkel käsil hetkeseis imporditud",
    )
    CURRENT_TOPIC_SYNC_FAILED = (
        "legal_work.current_topic_sync_failed",
        "Hetkel käsil sünkroonimine ebaõnnestus",
    )
    CURRENT_TOPIC_SYNC_UNCHANGED = (
        "legal_work.current_topic_sync_unchanged",
        "Hetkel käsil: muutusteta",
    )
    OPINION_CATALOGUE_FAILED = (
        "legal_work.opinion_catalogue_failed",
        "Arvamuste kataloogi ehitamine ebaõnnestus",
    )
    OPINION_CATALOGUE_IMPORTED = (
        "legal_work.opinion_catalogue_imported",
        "Arvamuste kataloog imporditud",
    )
    OPINION_CATALOGUE_UNCHANGED = (
        "legal_work.opinion_catalogue_unchanged",
        "Arvamuste kataloog: muutusteta",
    )
    OPINION_DOCUMENT_QUARANTINED = (
        "legal_work.opinion_document_quarantined",
        "Arvamusdokument karantiini",
    )
    OPINION_MATCH_FAILED = "legal_work.opinion_match_failed", "Arvamuste sobitamine ebaõnnestus"
    OPINION_MATCH_GENERATED = "legal_work.opinion_match_generated", "Arvamuste sobitamine arvutatud"
    OPINION_MATCH_UNCHANGED = (
        "legal_work.opinion_match_unchanged",
        "Arvamuste sobitamine: muutusteta",
    )
    PUBLIC_OPINIONS_FAILED = (
        "legal_work.public_opinions_failed",
        "Avaliku arvamuskorpuse kogumine ebaõnnestus",
    )
    PUBLIC_OPINIONS_IMPORTED = (
        "legal_work.public_opinions_imported",
        "Avalik arvamuskorpus imporditud",
    )
    PUBLIC_OPINIONS_UNCHANGED = (
        "legal_work.public_opinions_unchanged",
        "Avalik arvamuskorpus: muutusteta",
    )
    SNAPSHOT_IMPORTED = "legal_work.snapshot_imported", "Õigusloome hetkeseis imporditud"
    SNAPSHOT_PUBLISHED = "legal_work.snapshot_published", "Õigusloome hetkeseis kehtestatud"
    SYNC_FAILED = "legal_work.sync_failed", "Õigusloome sünkroonimine ebaõnnestus"
    SYNC_UNCHANGED = "legal_work.sync_unchanged", "Õigusloome sünkroonimine: muutusteta"
