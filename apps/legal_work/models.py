"""Imported legal-work snapshots and their rows.

The dashboard reads only PostgreSQL. One successful live import writes one
complete, immutable snapshot and atomically becomes the current one; a failed
import leaves the previous current snapshot exactly as it was.

This app deliberately holds no responsible-lawyer field, no member-feedback
counts and no relation to opinion documents. Those are out of scope and are not
modelled "just in case": an absent column cannot leak.
"""

from django.db import models
from django.db.models import F, Q

from apps.sources.models import DataSource

# The workbook uses `;` between codes; the importer normalises them into a list.
MAX_ERROR_SUMMARY_LENGTH = 500


class SnapshotImmutable(RuntimeError):
    """Raised when something tries to rewrite an imported snapshot or row."""


class SentStatus(models.TextChoices):
    PENDING = "pending", "Ootel"
    SENT = "sent", "Saadetud"
    NOT_SENT = "not_sent", "Ei saadetud"
    INVALID = "invalid", "Vigane"


class SyncResult(models.TextChoices):
    NEVER_RUN = "never_run", "Pole veel käivitatud"
    IMPORTED = "imported", "Imporditud"
    UNCHANGED = "unchanged", "Muutumatu"
    FAILED = "failed", "Ebaõnnestus"


class LegalWorkSnapshot(models.Model):
    """One complete import of the legal-work workbook.

    Everything except `is_current` is fixed once written. `is_current` has to
    move, because publishing a new snapshot retires the previous one, so
    `save()` permits that single field and refuses every other change.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="legal_work_snapshots",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="legal_work_snapshots",
        verbose_name="Algfail",
    )
    import_run = models.OneToOneField(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="legal_work_snapshot",
        verbose_name="Impordikäivitus",
    )
    schema_version = models.CharField(
        max_length=16,
        verbose_name="Skeemi versioon",
        help_text="Töövihiku enda deklareeritud versioon, mitte importija oma.",
    )
    reporting_date = models.DateField(verbose_name="Andmete seis")
    workbook_generated_at = models.DateTimeField(verbose_name="Töövihik loodud")
    source_file_modified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Lähtefail muudetud",
    )
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")
    total_record_count = models.PositiveIntegerField(default=0, verbose_name="Kirjeid kokku")
    open_record_count = models.PositiveIntegerField(default=0, verbose_name="Avatud kirjeid")
    sent_record_count = models.PositiveIntegerField(default=0, verbose_name="Välja saadetud")
    warning_record_count = models.PositiveIntegerField(
        default=0, verbose_name="Hoiatustega kirjeid"
    )

    # Only this field may move after a snapshot has been written.
    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-imported_at", "-id")
        verbose_name = "Õigusloome hetkeseis"
        verbose_name_plural = "Õigusloome hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="legalwork_one_current_snapshot_per_source",
            ),
            models.CheckConstraint(
                condition=Q(open_record_count__lte=F("total_record_count")),
                name="legalwork_open_count_within_total",
            ),
            models.CheckConstraint(
                condition=Q(sent_record_count__lte=F("total_record_count")),
                name="legalwork_sent_count_within_total",
            ),
            models.CheckConstraint(
                condition=Q(warning_record_count__lte=F("total_record_count")),
                name="legalwork_warning_count_within_total",
            ),
        ]

    def __str__(self) -> str:
        return f"Õigusloome {self.reporting_date:%d.%m.%Y} ({self.total_record_count})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise SnapshotImmutable(
                    "An imported legal-work snapshot may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class LegalWorkItem(models.Model):
    """One imported row. Immutable once its snapshot has been written."""

    snapshot = models.ForeignKey(
        LegalWorkSnapshot,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Hetkeseis",
    )
    record_id = models.CharField(max_length=64, verbose_name="Kirje ID")
    source_year = models.PositiveSmallIntegerField(verbose_name="Aasta")
    source_nr = models.PositiveIntegerField(null=True, blank=True, verbose_name="Number")
    # The workbook carries long topic descriptions; a bounded CharField would
    # truncate real records.
    topic = models.TextField(verbose_name="Teema")
    act_type = models.CharField(max_length=100, blank=True, verbose_name="Õigusakti liik")
    received_date = models.DateField(null=True, blank=True, verbose_name="Sisse")
    deadline_date = models.DateField(null=True, blank=True, verbose_name="Arvamuse tähtaeg")
    sent_date = models.DateField(null=True, blank=True, verbose_name="Välja")
    sent_status = models.CharField(
        max_length=16,
        choices=SentStatus,
        default=SentStatus.PENDING,
        db_index=True,
        verbose_name="Saatmise olek",
    )
    recipient = models.CharField(max_length=200, blank=True, verbose_name="Kellele")
    # `stage_key` is the workbook's normalised lower-case form of `stage`. It is
    # free text in the source, not a controlled vocabulary, so it carries no
    # choices: the lawyers write their own wording and it must survive intact.
    stage = models.CharField(max_length=200, blank=True, verbose_name="Hetkeseis")
    stage_key = models.CharField(
        max_length=200, blank=True, db_index=True, verbose_name="Seisu võti"
    )
    next_step = models.CharField(max_length=300, blank=True, verbose_name="Järgmiseks")
    is_open = models.BooleanField(db_index=True, verbose_name="Avatud")
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")
    source_row = models.PositiveIntegerField(verbose_name="Lähterida")
    refreshed_at = models.DateTimeField(null=True, blank=True, verbose_name="Värskendatud")
    # Schema 1.2. Counts of members, never their identities: the workbook
    # carries only how many answered and how many were asked, and there is no
    # field here capable of holding who they were.
    #
    # `null` is the absence of a count, which an older workbook and an untracked
    # row both produce. It is not `0`: a topic nobody answered and a topic
    # nobody was asked about are different facts, and only the first is a zero.
    feedback_member_count = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Tagasisidet andnud liikmeid"
    )
    feedback_requested_member_count = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Liikmeid, kellelt otse küsiti"
    )

    class Meta:
        ordering = ("-received_date", "topic", "record_id")
        verbose_name = "Õigusloome kirje"
        verbose_name_plural = "Õigusloome kirjed"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "record_id"],
                name="legalworkitem_unique_record_per_snapshot",
            ),
            # `source_row` repeats across years, because it is the row number
            # inside its own year sheet. The year plus the row is what is
            # actually unique in the workbook.
            models.UniqueConstraint(
                fields=["snapshot", "source_year", "source_row"],
                name="legalworkitem_unique_source_row_per_snapshot",
            ),
            models.CheckConstraint(
                condition=Q(source_year__gte=1),
                name="legalworkitem_source_year_positive",
            ),
            models.CheckConstraint(
                condition=Q(source_nr__isnull=True) | Q(source_nr__gte=0),
                name="legalworkitem_source_nr_non_negative",
            ),
            models.CheckConstraint(
                condition=~Q(topic=""),
                name="legalworkitem_topic_required",
            ),
            # A record only claims to have been sent when it carries the date
            # that proves it, and nothing else may pretend to have one.
            models.CheckConstraint(
                condition=(
                    Q(sent_status=SentStatus.SENT, sent_date__isnull=False)
                    | (~Q(sent_status=SentStatus.SENT) & Q(sent_date__isnull=True))
                ),
                name="legalworkitem_sent_date_matches_status",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "is_open"]),
            models.Index(fields=["snapshot", "-sent_date"]),
            models.Index(fields=["snapshot", "-received_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.record_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("An imported legal-work row cannot be changed.")
        return super().save(*args, **kwargs)


class LegalWorkFeedState(models.Model):
    """What the last synchronisation attempt found.

    Deliberately holds no token, no client secret and no signed download URL:
    only non-secret content metadata that lets the next run decide whether it
    needs to download anything at all.
    """

    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="legal_work_feed_state",
        verbose_name="Andmeallikas",
    )
    last_checked_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Viimati kontrollitud"
    )
    last_successful_sync_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Viimane edukas sünkroonimine",
    )
    last_changed_at = models.DateTimeField(null=True, blank=True, verbose_name="Viimati muutunud")
    last_result = models.CharField(
        max_length=16,
        choices=SyncResult,
        default=SyncResult.NEVER_RUN,
        verbose_name="Viimane tulemus",
    )
    last_error_summary = models.CharField(
        max_length=MAX_ERROR_SUMMARY_LENGTH,
        blank=True,
        verbose_name="Viimane veateade",
        help_text="Puhastatud ja lühendatud. Ei sisalda tokeneid ega failisisu.",
    )
    remote_modified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Kaugfail muudetud",
    )
    remote_etag = models.CharField(max_length=200, blank=True, verbose_name="Kaugfaili etag")
    remote_size_bytes = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        verbose_name="Kaugfaili suurus",
    )
    current_snapshot = models.ForeignKey(
        LegalWorkSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Kehtiv hetkeseis",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        verbose_name = "Õigusloome andmevoo olek"
        verbose_name_plural = "Õigusloome andmevoo olekud"

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"
