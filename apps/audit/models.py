from django.conf import settings
from django.db import models
from django.utils import timezone

from .redaction import redact


class AuditEventImmutable(RuntimeError):
    """Raised when something tries to change or remove a recorded event."""


class AuditAction(models.TextChoices):
    """The actions the source, artifact and import registry records.

    Each domain declares its own in its own `audit_actions.py`, so a feature adds
    an action without touching this file -- sixteen of this module's eighteen
    commits were exactly that. The field is deliberately a plain `CharField`
    with no `choices`, which is what lets an action live anywhere: the admin
    filter is built from the values actually present in the table.
    """

    DATA_SOURCE_CREATED = "data_source.created", "Andmeallikas loodud"
    DATA_SOURCE_UPDATED = "data_source.updated", "Andmeallikas muudetud"
    DATA_SOURCE_DEACTIVATED = "data_source.deactivated", "Andmeallikas deaktiveeritud"
    ARTIFACT_REGISTERED = "source_artifact.registered", "Algfail registreeritud"
    ARTIFACT_DOWNLOADED = "source_artifact.downloaded", "Algfail alla laaditud"
    IMPORT_RUN_CREATED = "import_run.created", "Impordikäivitus loodud"
    IMPORT_RUN_SUCCEEDED = "import_run.succeeded", "Impordikäivitus õnnestus"
    IMPORT_RUN_FAILED = "import_run.failed", "Impordikäivitus ebaõnnestus"
    # The Chamber's internal board-report history. A separate dataset from the
    # public directory count above, so it gets its own actions rather than
    # reusing them and making the trail ambiguous.
    # Discovery of the durable public event-page catalogue. Separate from the
    # calendar actions above because it is a different job on a different
    # schedule: that one publishes what is upcoming, this one accumulates
    # addresses for events that have already happened.
    # The Chamber's own event programme, prepared from the operational service-code
    # workbook. A different dataset from the public Koda.ee listing above, so it
    # gets its own actions rather than reusing them and making the trail ambiguous.
    # Attaching public page addresses to programme events. A separate action
    # from the import above because it changes no programme field — only which
    # public page an event is understood to point at.
    # The public Koda.ee "Hetkel käsil" catalogue, collected to enrich the
    # legal-work records. A separate source from the workbook feed above, so it
    # gets its own actions: a failure here says nothing about the workbook.
    # Derived match results: generated rather than collected. A `matched`
    # decision is what makes a legal topic clickable on the Õigusloome page.
    # The Koda.ee "Hetkel käsil" **archive**, collected as a fallback source of
    # consultation links. Its own actions: an archive outage says nothing about
    # the current catalogue, and the two must stay separable in the trail.
    # The Chamber's own opinion documents. Private correspondence rather than a
    # public feed, so its summaries carry counts, snapshot ids and digest
    # prefixes only — never a filename, a recipient, a subject, document text or
    # a storage path.
    # The public Koda.ee opinion corpus. Summaries carry counts, snapshot ids
    # and checksum prefixes only — never a URL, a title, a filename or text.
    # Manually observed audience sizes. A batch event describes one submission;
    # the per-observation events describe what that submission did to each
    # metric, which is what makes a correction auditable on its own.
    # Google Analytics website traffic: the one collected figure in this module,
    # so the only one with the three feed events every other collector has.
    # Smaily newsletter audiences: the second collected figure in this module.
    # The summaries carry segment counts and withheld metric keys, never an
    # address, a subscriber or a credential.
    # Retention. The one scheduled action that deletes published rows, so it
    # leaves a permanent record of exactly how many and from which family.
    SNAPSHOTS_PRUNED = (
        "sources.snapshots_pruned",
        "Vanad hetkeseisud kustutatud",
    )
    # E-pood. The manual commerce package, which publishes product metadata and
    # aggregated daily facts all-or-nothing.


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
