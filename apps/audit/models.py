from django.conf import settings
from django.db import models
from django.utils import timezone

from .redaction import redact


class AuditEventImmutable(RuntimeError):
    """Raised when something tries to change or remove a recorded event."""


class AuditAction(models.TextChoices):
    """Actions recorded so far.

    Later pull requests add their own values; the field is deliberately a plain
    CharField so a new action never needs a migration in a hurry.
    """

    DATA_SOURCE_CREATED = "data_source.created", "Andmeallikas loodud"
    DATA_SOURCE_UPDATED = "data_source.updated", "Andmeallikas muudetud"
    DATA_SOURCE_DEACTIVATED = "data_source.deactivated", "Andmeallikas deaktiveeritud"
    ARTIFACT_REGISTERED = "source_artifact.registered", "Algfail registreeritud"
    ARTIFACT_DOWNLOADED = "source_artifact.downloaded", "Algfail alla laaditud"
    IMPORT_RUN_CREATED = "import_run.created", "Impordikäivitus loodud"
    IMPORT_RUN_SUCCEEDED = "import_run.succeeded", "Impordikäivitus õnnestus"
    IMPORT_RUN_FAILED = "import_run.failed", "Impordikäivitus ebaõnnestus"
    LEGAL_WORK_SNAPSHOT_IMPORTED = (
        "legal_work.snapshot_imported",
        "Õigusloome hetkeseis imporditud",
    )
    LEGAL_WORK_SNAPSHOT_PUBLISHED = (
        "legal_work.snapshot_published",
        "Õigusloome hetkeseis kehtestatud",
    )
    LEGAL_WORK_SYNC_UNCHANGED = (
        "legal_work.sync_unchanged",
        "Õigusloome sünkroonimine: muutusteta",
    )
    LEGAL_WORK_SYNC_FAILED = (
        "legal_work.sync_failed",
        "Õigusloome sünkroonimine ebaõnnestus",
    )
    MEMBERSHIP_OBSERVATION_IMPORTED = (
        "membership.observation_imported",
        "Liikmete arv imporditud",
    )
    MEMBERSHIP_SYNC_UNCHANGED = (
        "membership.sync_unchanged",
        "Liikmete arv: muutusteta",
    )
    MEMBERSHIP_SYNC_FAILED = (
        "membership.sync_failed",
        "Liikmete arvu sünkroonimine ebaõnnestus",
    )
    # The Chamber's internal board-report history. A separate dataset from the
    # public directory count above, so it gets its own actions rather than
    # reusing them and making the trail ambiguous.
    MEMBERSHIP_HISTORY_IMPORTED = (
        "membership.history_imported",
        "Liikmeskonna ajalugu imporditud",
    )
    MEMBERSHIP_HISTORY_UNCHANGED = (
        "membership.history_unchanged",
        "Liikmeskonna ajalugu: muutusteta",
    )
    MEMBERSHIP_HISTORY_FAILED = (
        "membership.history_failed",
        "Liikmeskonna ajaloo import ebaõnnestus",
    )
    MEMBERSHIP_MANUAL_OBSERVATION_CREATED = (
        "membership.manual_observation_created",
        "Liikmeskonna aruanne käsitsi lisatud",
    )
    MEMBERSHIP_MANUAL_OBSERVATION_SUPERSEDED = (
        "membership.manual_observation_superseded",
        "Liikmeskonna vaatlus asendatud",
    )
    MEMBERSHIP_ISSUE_RESOLVED = (
        "membership.issue_resolved",
        "Liikmeskonna andmeprobleem lahendatud",
    )
    NEWS_SNAPSHOT_IMPORTED = (
        "news.snapshot_imported",
        "Uudiste hetkeseis imporditud",
    )
    NEWS_SYNC_UNCHANGED = (
        "news.sync_unchanged",
        "Uudised: muutusteta",
    )
    NEWS_SYNC_FAILED = (
        "news.sync_failed",
        "Uudiste sünkroonimine ebaõnnestus",
    )
    EVENTS_SNAPSHOT_IMPORTED = (
        "events.snapshot_imported",
        "Sündmuste hetkeseis imporditud",
    )
    EVENTS_SYNC_UNCHANGED = (
        "events.sync_unchanged",
        "Sündmused: muutusteta",
    )
    EVENTS_SYNC_FAILED = (
        "events.sync_failed",
        "Sündmuste sünkroonimine ebaõnnestus",
    )
    # The Chamber's own event programme, prepared from the operational service-code
    # workbook. A different dataset from the public Koda.ee listing above, so it
    # gets its own actions rather than reusing them and making the trail ambiguous.
    EVENT_PROGRAMME_SNAPSHOT_IMPORTED = (
        "event_programme.snapshot_imported",
        "Sündmuste programmi hetkeseis imporditud",
    )
    EVENT_PROGRAMME_SNAPSHOT_PUBLISHED = (
        "event_programme.snapshot_published",
        "Sündmuste programmi hetkeseis kehtestatud",
    )
    EVENT_PROGRAMME_SYNC_UNCHANGED = (
        "event_programme.sync_unchanged",
        "Sündmuste programm: muutusteta",
    )
    EVENT_PROGRAMME_SYNC_FAILED = (
        "event_programme.sync_failed",
        "Sündmuste programmi sünkroonimine ebaõnnestus",
    )
    # The public Koda.ee "Hetkel käsil" catalogue, collected to enrich the
    # legal-work records. A separate source from the workbook feed above, so it
    # gets its own actions: a failure here says nothing about the workbook.
    CURRENT_TOPIC_SNAPSHOT_IMPORTED = (
        "legal_work.current_topic_snapshot_imported",
        "Hetkel käsil hetkeseis imporditud",
    )
    CURRENT_TOPIC_SYNC_UNCHANGED = (
        "legal_work.current_topic_sync_unchanged",
        "Hetkel käsil: muutusteta",
    )
    CURRENT_TOPIC_SYNC_FAILED = (
        "legal_work.current_topic_sync_failed",
        "Hetkel käsil sünkroonimine ebaõnnestus",
    )
    # Derived match results: generated rather than collected. A `matched`
    # decision is what makes a legal topic clickable on the Õigusloome page.
    CURRENT_TOPIC_MATCH_GENERATED = (
        "legal_work.current_topic_match_generated",
        "Õigusloome sobitamine arvutatud",
    )
    CURRENT_TOPIC_MATCH_UNCHANGED = (
        "legal_work.current_topic_match_unchanged",
        "Õigusloome sobitamine: muutusteta",
    )
    CURRENT_TOPIC_MATCH_FAILED = (
        "legal_work.current_topic_match_failed",
        "Õigusloome sobitamine ebaõnnestus",
    )
    # The Koda.ee "Hetkel käsil" **archive**, collected as a fallback source of
    # consultation links. Its own actions: an archive outage says nothing about
    # the current catalogue, and the two must stay separable in the trail.
    ARCHIVED_TOPIC_SNAPSHOT_IMPORTED = (
        "legal_work.archived_topic_snapshot_imported",
        "Arhiivi hetkeseis imporditud",
    )
    ARCHIVED_TOPIC_SYNC_UNCHANGED = (
        "legal_work.archived_topic_sync_unchanged",
        "Arhiiv: muutusteta",
    )
    ARCHIVED_TOPIC_SYNC_FAILED = (
        "legal_work.archived_topic_sync_failed",
        "Arhiivi sünkroonimine ebaõnnestus",
    )
    ARCHIVED_TOPIC_MATCH_GENERATED = (
        "legal_work.archived_topic_match_generated",
        "Arhiivi sobitamine arvutatud",
    )
    ARCHIVED_TOPIC_MATCH_UNCHANGED = (
        "legal_work.archived_topic_match_unchanged",
        "Arhiivi sobitamine: muutusteta",
    )
    ARCHIVED_TOPIC_MATCH_FAILED = (
        "legal_work.archived_topic_match_failed",
        "Arhiivi sobitamine ebaõnnestus",
    )
    # The Chamber's own opinion documents. Private correspondence rather than a
    # public feed, so its summaries carry counts, snapshot ids and digest
    # prefixes only — never a filename, a recipient, a subject, document text or
    # a storage path.
    OPINION_CATALOGUE_IMPORTED = (
        "legal_work.opinion_catalogue_imported",
        "Arvamuste kataloog imporditud",
    )
    OPINION_CATALOGUE_UNCHANGED = (
        "legal_work.opinion_catalogue_unchanged",
        "Arvamuste kataloog: muutusteta",
    )
    OPINION_CATALOGUE_FAILED = (
        "legal_work.opinion_catalogue_failed",
        "Arvamuste kataloogi ehitamine ebaõnnestus",
    )
    OPINION_DOCUMENT_QUARANTINED = (
        "legal_work.opinion_document_quarantined",
        "Arvamusdokument karantiini",
    )
    OPINION_MATCH_GENERATED = (
        "legal_work.opinion_match_generated",
        "Arvamuste sobitamine arvutatud",
    )
    OPINION_MATCH_UNCHANGED = (
        "legal_work.opinion_match_unchanged",
        "Arvamuste sobitamine: muutusteta",
    )
    OPINION_MATCH_FAILED = (
        "legal_work.opinion_match_failed",
        "Arvamuste sobitamine ebaõnnestus",
    )
    # Manually observed audience sizes. A batch event describes one submission;
    # the per-observation events describe what that submission did to each
    # metric, which is what makes a correction auditable on its own.
    VISIBILITY_MANUAL_BATCH_PUBLISHED = (
        "visibility.manual_batch_published",
        "Nähtavuse näitajad käsitsi sisestatud",
    )
    VISIBILITY_OBSERVATION_PUBLISHED = (
        "visibility.observation_published",
        "Nähtavuse vaatlus avaldatud",
    )
    VISIBILITY_OBSERVATION_SUPERSEDED = (
        "visibility.observation_superseded",
        "Nähtavuse vaatlus asendatud",
    )
    # Google Analytics website traffic: the one collected figure in this module,
    # so the only one with the three feed events every other collector has.
    GA4_OBSERVATION_IMPORTED = (
        "visibility.ga4_observation_imported",
        "Veebiliikluse vaatlus imporditud",
    )
    GA4_SYNC_UNCHANGED = (
        "visibility.ga4_sync_unchanged",
        "Veebiliikluse kogumine: muutusteta",
    )
    GA4_SYNC_FAILED = (
        "visibility.ga4_sync_failed",
        "Veebiliikluse kogumine ebaõnnestus",
    )
    # Retention. The one scheduled action that deletes published rows, so it
    # leaves a permanent record of exactly how many and from which family.
    SNAPSHOTS_PRUNED = (
        "sources.snapshots_pruned",
        "Vanad hetkeseisud kustutatud",
    )


class AuditEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AuditEventImmutable("Audit events cannot be updated.")

    def delete(self):
        raise AuditEventImmutable("Audit events cannot be deleted.")


class AuditEventManager(models.Manager.from_queryset(AuditEventQuerySet)):
    def bulk_update(self, *args, **kwargs):
        raise AuditEventImmutable("Audit events cannot be updated.")


class AuditEvent(models.Model):
    """Append-only record of a significant action.

    Immutability is enforced at three levels: this model refuses updates and
    deletes, the manager refuses the bulk paths that would bypass it, and a
    PostgreSQL trigger refuses UPDATE and DELETE on the table itself. See
    `docs/data-model.md` for what that still does not cover.
    """

    timestamp = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="Aeg")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
        verbose_name="Tegija",
        help_text="Tühi tähendab süsteemset tegevust või kustutatud kasutajat.",
    )
    action = models.CharField(max_length=64, db_index=True, verbose_name="Tegevus")
    object_type = models.CharField(
        max_length=100,
        db_index=True,
        verbose_name="Objekti tüüp",
        help_text="Loetav ka siis, kui objekt on hiljem kadunud.",
    )
    object_id = models.CharField(max_length=64, db_index=True, verbose_name="Objekti ID")
    change_summary = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Muudatuse kokkuvõte",
        help_text="Redigeeritud: PIN-e, paroole, võtmeid ja failide sisu ei salvestata.",
    )
    correlation_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Korrelatsiooni ID",
        help_text="Seob ühe päringu või impordi sündmused omavahel.",
    )

    objects = AuditEventManager()

    class Meta:
        ordering = ("-timestamp", "-id")
        verbose_name = "Auditisündmus"
        verbose_name_plural = "Auditisündmused"
        indexes = [
            models.Index(fields=["object_type", "object_id"]),
            models.Index(fields=["action", "-timestamp"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.object_type}#{self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise AuditEventImmutable("Audit events cannot be updated.")
        # Redaction lives here rather than only in the service so that no
        # caller can store an unredacted summary, not even through the ORM.
        self.change_summary = redact(self.change_summary)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditEventImmutable("Audit events cannot be deleted.")
