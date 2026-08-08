"""Imported event-programme snapshots and their rows.

The dashboard reads only PostgreSQL. One successful import writes one complete,
immutable snapshot and atomically becomes the current one; a failed import
leaves the previous current snapshot exactly as it was.

This app deliberately holds no price, no discount and no raw source text. The
export carries all of them, and none is a DashKoda metric: an absent column
cannot leak. It is also distinct from :mod:`apps.events`, which collects the
public Koda.ee listing. That app answers "what did we announce publicly"; this
one answers "what did the Chamber actually run".
"""

from django.db import models
from django.db.models import F, Q

from apps.sources.models import DataSource

MAX_ERROR_SUMMARY_LENGTH = 500


class SnapshotImmutable(RuntimeError):
    """Raised when something tries to rewrite an imported snapshot or row."""


class EventStatus(models.TextChoices):
    PAST = "past", "Toimunud"
    ONGOING = "ongoing", "Käib"
    UPCOMING = "upcoming", "Tulemas"
    # Not a fourth kind of event: the generator could not read a date from the
    # operational sheet, so where this event sits in time is unknown.
    DATE_UNKNOWN = "date_unknown", "Kuupäev teadmata"


class IncludeStatus(models.TextChoices):
    INCLUDED = "YES", "Arvestatud"
    REVIEW = "REVIEW", "Ülevaatamisel"


class PublicLinkStatus(models.TextChoices):
    NOT_LINKED = "not_linked", "Lingita"
    LINKED_LATEST = "linked_embedded_latest", "Lingitud (viimane)"
    LINKED_EARLIER = "linked_embedded_earlier", "Lingitud (varasem)"
    REJECTED_OFF_DOMAIN = "rejected_off_domain", "Tagasi lükatud (võõras domeen)"


class DateParseStatus(models.TextChoices):
    PARSED_SINGLE = "parsed_single", "Üks kuupäev"
    PARSED_RANGE = "parsed_range", "Vahemik"
    UNPARSED = "unparsed", "Lugemata"
    AMBIGUOUS = "ambiguous", "Mitmetähenduslik"
    INVALID = "invalid", "Vigane"


class DeliveryMode(models.TextChoices):
    ONSITE = "onsite", "Kohapeal"
    ONLINE = "online", "Veebis"
    HYBRID = "hybrid", "Hübriid"


class SyncResult(models.TextChoices):
    NEVER_RUN = "never_run", "Pole veel käivitatud"
    IMPORTED = "imported", "Imporditud"
    UNCHANGED = "unchanged", "Muutumatu"
    FAILED = "failed", "Ebaõnnestus"


class EventProgrammeSnapshot(models.Model):
    """One complete import of the event-programme workbook.

    Everything except `is_current` is fixed once written. `is_current` has to
    move, because publishing a new snapshot retires the previous one, so
    `save()` permits that single field and refuses every other change.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="event_programme_snapshots",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="event_programme_snapshots",
        verbose_name="Algfail",
    )
    import_run = models.OneToOneField(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="event_programme_snapshot",
        verbose_name="Impordikäivitus",
    )
    schema_version = models.CharField(
        max_length=16,
        verbose_name="Skeemi versioon",
        help_text="Töövihiku enda deklareeritud versioon, mitte importija oma.",
    )
    generator_version = models.CharField(max_length=32, verbose_name="Generaatori versioon")
    export_refreshed_at = models.DateTimeField(verbose_name="Eksport värskendatud")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")

    canonical_event_count = models.PositiveIntegerField(default=0, verbose_name="Sündmusi kokku")
    # Events the generator could place on the calendar. The remainder are real
    # events whose operational row holds date text nobody could parse.
    dated_event_count = models.PositiveIntegerField(default=0, verbose_name="Kuupäevaga sündmusi")
    linked_public_url_count = models.PositiveIntegerField(default=0, verbose_name="Avaliku lingiga")
    review_required_count = models.PositiveIntegerField(
        default=0, verbose_name="Ülevaatust vajavaid"
    )
    # Reported by the workbook about material this snapshot does not carry:
    # repeats live in DASH_EVENT_OCCURRENCES and excluded rows are not exported.
    repeated_service_code_count = models.PositiveIntegerField(
        default=0, verbose_name="Korduvaid teenusekoode"
    )
    excluded_event_count = models.PositiveIntegerField(default=0, verbose_name="Välja jäetud ridu")
    warning_count = models.PositiveIntegerField(default=0, verbose_name="Hoiatusi")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-export_refreshed_at", "-id")
        verbose_name = "Sündmuste programmi hetkeseis"
        verbose_name_plural = "Sündmuste programmi hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="eventprogramme_one_current_snapshot_per_source",
            ),
            models.CheckConstraint(
                condition=Q(dated_event_count__lte=F("canonical_event_count")),
                name="eventprogramme_dated_count_within_total",
            ),
            models.CheckConstraint(
                condition=Q(linked_public_url_count__lte=F("canonical_event_count")),
                name="eventprogramme_linked_count_within_total",
            ),
            models.CheckConstraint(
                condition=Q(review_required_count__lte=F("canonical_event_count")),
                name="eventprogramme_review_count_within_total",
            ),
        ]

    def __str__(self) -> str:
        return f"Sündmused {self.export_refreshed_at:%d.%m.%Y} ({self.canonical_event_count})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise SnapshotImmutable(
                    "An imported event-programme snapshot may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class EventProgrammeItem(models.Model):
    """One imported event. Immutable once its snapshot has been written."""

    snapshot = models.ForeignKey(
        EventProgrammeSnapshot,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Hetkeseis",
    )
    event_id = models.CharField(max_length=32, verbose_name="Sündmuse ID")
    service_code = models.CharField(max_length=16, db_index=True, verbose_name="Teenuse kood")
    event_name = models.TextField(verbose_name="Nimi")

    # Null means the generator could not read a date, never "no date". The three
    # derived calendar fields are empty for exactly those rows.
    start_date = models.DateField(null=True, blank=True, db_index=True, verbose_name="Algus")
    end_date = models.DateField(null=True, blank=True, verbose_name="Lõpp")
    event_year = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Aasta")
    event_month_key = models.CharField(max_length=7, blank=True, verbose_name="Kuu võti")
    event_month_label = models.CharField(max_length=16, blank=True, verbose_name="Kuu")
    event_quarter = models.CharField(max_length=2, blank=True, verbose_name="Kvartal")
    event_status = models.CharField(
        max_length=16, choices=EventStatus, db_index=True, verbose_name="Seisund"
    )

    # `tag_key` and `event_type_key` come from the hand-maintained DASH_TAG_MAP
    # in the Chamber's operational workbook, so the vocabulary grows whenever
    # someone classifies a new short name. They carry no choices deliberately:
    # a new tag must import, not fail validation.
    tag_key = models.CharField(max_length=64, blank=True, db_index=True, verbose_name="Sildi võti")
    tag_label = models.CharField(max_length=64, blank=True, verbose_name="Silt")
    event_type_key = models.CharField(
        max_length=32, blank=True, db_index=True, verbose_name="Tüübi võti"
    )
    event_type_label = models.CharField(max_length=64, blank=True, verbose_name="Tüüp")
    delivery_mode = models.CharField(
        max_length=16, choices=DeliveryMode, blank=True, verbose_name="Toimumisviis"
    )
    include_status = models.CharField(
        max_length=8, choices=IncludeStatus, verbose_name="Arvestamise olek"
    )

    public_url = models.URLField(max_length=500, blank=True, verbose_name="Avalik viide")
    public_link_status = models.CharField(
        max_length=32, choices=PublicLinkStatus, verbose_name="Lingi olek"
    )

    source_year = models.PositiveSmallIntegerField(verbose_name="Lähteaasta")
    source_sheet = models.CharField(max_length=32, verbose_name="Lähteleht")
    source_row = models.PositiveIntegerField(verbose_name="Lähterida")
    source_occurrence_count = models.PositiveIntegerField(verbose_name="Ridu allikas")

    date_parse_status = models.CharField(
        max_length=16, choices=DateParseStatus, verbose_name="Kuupäeva lugemine"
    )
    review_required = models.BooleanField(db_index=True, verbose_name="Vajab ülevaatust")
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")

    class Meta:
        ordering = ("-start_date", "event_name", "event_id")
        verbose_name = "Programmi sündmus"
        verbose_name_plural = "Programmi sündmused"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "event_id"],
                name="eventprogrammeitem_unique_event_per_snapshot",
            ),
            # The canonical table holds one row per service code; the repeats
            # behind it stay in the workbook's occurrence sheet.
            models.UniqueConstraint(
                fields=["snapshot", "service_code"],
                name="eventprogrammeitem_unique_service_code_per_snapshot",
            ),
            # `source_row` is the row number inside its own annual sheet, so it
            # repeats across years. The sheet plus the row is what is unique.
            models.UniqueConstraint(
                fields=["snapshot", "source_sheet", "source_row"],
                name="eventprogrammeitem_unique_source_row_per_snapshot",
            ),
            models.CheckConstraint(
                condition=~Q(event_name=""),
                name="eventprogrammeitem_name_required",
            ),
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="eventprogrammeitem_end_not_before_start",
            ),
            # An end date without a start date would be a date the dashboard
            # could not place; the parser rejects it and so does the database.
            models.CheckConstraint(
                condition=Q(start_date__isnull=False) | Q(end_date__isnull=True),
                name="eventprogrammeitem_end_requires_start",
            ),
            models.CheckConstraint(
                condition=Q(source_year__gte=1),
                name="eventprogrammeitem_source_year_positive",
            ),
            models.CheckConstraint(
                condition=Q(source_occurrence_count__gte=1),
                name="eventprogrammeitem_occurrence_count_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "-start_date"]),
            models.Index(fields=["snapshot", "event_status"]),
            models.Index(fields=["snapshot", "tag_key"]),
            models.Index(fields=["snapshot", "event_year"]),
        ]

    def __str__(self) -> str:
        return f"{self.service_code} {self.event_name[:60]}"

    @property
    def has_known_date(self) -> bool:
        return self.start_date is not None

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("An imported event-programme row cannot be changed.")
        return super().save(*args, **kwargs)


class EventProgrammeFeedState(models.Model):
    """What the last synchronisation attempt found.

    Deliberately holds no sharing URL, no token and no signed download URL: only
    non-secret content metadata that lets the next run decide whether it needs to
    download anything at all.
    """

    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="event_programme_feed_state",
        verbose_name="Andmeallikas",
    )
    last_checked_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Viimati kontrollitud"
    )
    last_successful_sync_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Viimane edukas sünkroonimine"
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
        null=True, blank=True, verbose_name="Kaugfail muudetud"
    )
    remote_etag = models.CharField(max_length=200, blank=True, verbose_name="Kaugfaili etag")
    remote_size_bytes = models.PositiveBigIntegerField(
        null=True, blank=True, verbose_name="Kaugfaili suurus"
    )
    current_snapshot = models.ForeignKey(
        EventProgrammeSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Kehtiv hetkeseis",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        verbose_name = "Sündmuste programmi andmevoo olek"
        verbose_name_plural = "Sündmuste programmi andmevoo olekud"

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"


# The event matcher's output. Imported here, at the foot, so Django discovers
# them as ordinary `event_programme` models while the definitions stay in their
# own module. The import is last because those models refer back to the ones
# above.
from .event_match_models import (  # noqa: E402,F401  (placement is deliberate)
    EventPublicMatch,
    EventPublicMatchSnapshot,
)
