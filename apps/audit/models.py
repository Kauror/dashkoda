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
