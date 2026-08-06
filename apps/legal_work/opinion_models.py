"""The private catalogue of Chamber opinion documents.

Four things are deliberately kept apart here, because collapsing any two of them
would lose something later work depends on:

**Bytes** (`OpinionDocumentBlob`) are identified by their own SHA-256. The same
letter filed twice under two names is one blob. A blob is never rewritten.

**Reading** (`OpinionDocumentExtraction`) is a *versioned opinion about* bytes.
Improving the text layer means a new extractor version and a new row, never an
edit, so a stored match can always name the exact reading it was based on.

**Cataloguing** (`OpinionCatalogueEntry`) is what one source file claimed: its
path, its filename, and what that filename says about date, recipient and
subject. Filename evidence and document evidence live in separate columns and
neither overwrites the other — when they disagree, that disagreement is
information rather than a problem to resolve.

**Publication** (`OpinionCatalogueSnapshot`) is all-or-nothing. A snapshot
becomes current only once every manifest entry has reached a terminal state, so
a half-finished backfill can never be what a later phase matches against.

No model here holds PDF bytes, a filesystem path, or anything a viewer can turn
into one. Blobs carry a storage *key* derived from the digest; resolving it to a
path happens in `opinion_storage`, under the store root, at read time.
"""

from django.db import models
from django.db.models import F, Q

from apps.sources.models import DataSource

from .models import MAX_ERROR_SUMMARY_LENGTH, SnapshotImmutable
from .opinion_classification import DocumentClassification
from .opinion_pdf import ExtractionStatus, ValidationStatus

MAX_SOURCE_KEY_LENGTH = 400
MAX_FILENAME_LENGTH = 400
MAX_RECIPIENT_LENGTH = 200
MAX_SUBJECT_LENGTH = 500
MAX_REFERENCE_LENGTH = 100
MAX_STORAGE_KEY_LENGTH = 120


class SourceProvider(models.TextChoices):
    """Where a catalogue entry was read from."""

    BOOTSTRAP_ZIP = "bootstrap_zip", "Algarhiiv"
    DIRECTORY = "directory", "Kaust"


class CatalogueBuildState(models.TextChoices):
    """How far the resumable build has got."""

    IDLE = "idle", "Ootel"
    BUILDING = "building", "Töös"
    COMPLETE = "complete", "Valmis"
    FAILED = "failed", "Ebaõnnestus"


class CatalogueResult(models.TextChoices):
    """How the last build ended.

    A superset of the shared `SyncResult`, because this feed has an outcome the
    others do not: a run that did real work, failed at nothing, and still has
    documents left to process. Calling that `unchanged` would be false and
    calling it `imported` would imply a snapshot exists. It is its own answer,
    and it is a success — the command exits zero and the next run continues.
    """

    NEVER_RUN = "never_run", "Pole veel käivitatud"
    IMPORTED = "imported", "Imporditud"
    UNCHANGED = "unchanged", "Muutumatu"
    PARTIAL = "partial", "Osaliselt töödeldud"
    FAILED = "failed", "Ebaõnnestus"


class OpinionDocumentBlob(models.Model):
    """One document's bytes, addressed by their own digest.

    `storage_key` is derived from `sha256` and is redundant with it by
    construction. It is stored anyway so that the store layout can change
    without a migration having to recompute paths, and so an operator can see
    where a blob lives without running code.
    """

    sha256 = models.CharField(max_length=64, unique=True, verbose_name="SHA-256")
    storage_key = models.CharField(max_length=MAX_STORAGE_KEY_LENGTH, verbose_name="Hoiuvõti")
    byte_size = models.PositiveIntegerField(verbose_name="Suurus baitides")
    page_count = models.PositiveSmallIntegerField(default=0, verbose_name="Lehekülgi")
    validation_status = models.CharField(
        max_length=32,
        choices=ValidationStatus,
        db_index=True,
        verbose_name="Kontrolli tulemus",
    )
    is_encrypted = models.BooleanField(default=False, verbose_name="Krüpteeritud")
    has_active_content = models.BooleanField(default=False, verbose_name="Aktiivne sisu")
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Lisatud")

    class Meta:
        ordering = ("-imported_at", "-id")
        verbose_name = "Arvamusdokumendi fail"
        verbose_name_plural = "Arvamusdokumentide failid"
        constraints = [
            models.CheckConstraint(
                condition=Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="opinionblob_sha256_is_lower_hex",
            ),
            models.CheckConstraint(condition=Q(byte_size__gt=0), name="opinionblob_has_bytes"),
            # A blob that passed validation has been parsed, so it knows how many
            # pages it has. This is what stops a quarantined row being treated as
            # readable later.
            models.CheckConstraint(
                condition=~Q(validation_status=ValidationStatus.VALID) | Q(page_count__gt=0),
                name="opinionblob_valid_has_pages",
            ),
            models.CheckConstraint(
                condition=~Q(validation_status=ValidationStatus.VALID)
                | Q(has_active_content=False),
                name="opinionblob_valid_has_no_active_content",
            ),
        ]
        indexes = [models.Index(fields=["validation_status", "imported_at"])]

    def __str__(self) -> str:
        return f"{self.sha256[:12]} ({self.byte_size} B)"

    @property
    def is_valid(self) -> bool:
        return self.validation_status == ValidationStatus.VALID

    @property
    def digest_prefix(self) -> str:
        """The only form of the digest shown to a person or written to a log."""
        return self.sha256[:12]

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A stored opinion blob cannot be changed.")
        return super().save(*args, **kwargs)


class OpinionDocumentExtraction(models.Model):
    """One versioned reading of one blob.

    Unique per (blob, extractor version): re-running the same extractor over the
    same bytes must reuse this row rather than produce a second opinion about
    identical input, which is what makes a repeated catalogue build cheap.
    """

    blob = models.ForeignKey(
        OpinionDocumentBlob,
        on_delete=models.CASCADE,
        related_name="extractions",
        verbose_name="Fail",
    )
    extractor_name = models.CharField(max_length=40, verbose_name="Lugeja")
    extractor_version = models.CharField(max_length=20, verbose_name="Lugeja versioon")
    status = models.CharField(
        max_length=16, choices=ExtractionStatus, db_index=True, verbose_name="Olek"
    )
    text = models.TextField(blank=True, verbose_name="Tekst")
    first_page_text = models.TextField(blank=True, verbose_name="Esilehe tekst")
    text_sha256 = models.CharField(max_length=64, blank=True, verbose_name="Teksti räsi")
    page_count = models.PositiveSmallIntegerField(default=0, verbose_name="Lehekülgi")

    # What the document says about itself, kept apart from what the filename
    # says. Either may be empty and neither is authoritative.
    detected_date = models.DateField(null=True, blank=True, verbose_name="Kirja kuupäev")
    detected_recipient = models.CharField(
        max_length=MAX_RECIPIENT_LENGTH, blank=True, verbose_name="Kirja saaja"
    )
    detected_subject = models.CharField(
        max_length=MAX_SUBJECT_LENGTH, blank=True, verbose_name="Kirja teema"
    )
    detected_reference = models.CharField(
        max_length=MAX_REFERENCE_LENGTH, blank=True, verbose_name="Väljamineku number"
    )
    their_reference = models.CharField(
        max_length=MAX_REFERENCE_LENGTH, blank=True, verbose_name="Teie viide"
    )
    our_reference = models.CharField(
        max_length=MAX_REFERENCE_LENGTH, blank=True, verbose_name="Meie viide"
    )
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Loodud")

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Arvamusdokumendi lugemine"
        verbose_name_plural = "Arvamusdokumentide lugemised"
        constraints = [
            models.UniqueConstraint(
                fields=["blob", "extractor_version"],
                name="opinionextraction_one_per_blob_and_version",
            ),
            # "Extracted" is a claim that there is something to match against.
            models.CheckConstraint(
                condition=~Q(status=ExtractionStatus.EXTRACTED) | ~Q(text=""),
                name="opinionextraction_extracted_has_text",
            ),
        ]
        indexes = [models.Index(fields=["status", "extractor_version"])]

    def __str__(self) -> str:
        return f"{self.blob_id}: {self.get_status_display()}"

    @property
    def is_usable(self) -> bool:
        return self.status == ExtractionStatus.EXTRACTED

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A recorded extraction cannot be changed.")
        return super().save(*args, **kwargs)


class OpinionCatalogueSnapshot(models.Model):
    """One published, complete state of the opinion catalogue.

    `source_manifest_checksum` is computed from the source's own metadata and
    the digests of what it holds, so an unchanged inbox is recognised without
    re-reading a single PDF.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="opinion_catalogue_snapshots",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="opinion_catalogue_snapshots",
        verbose_name="Algfail",
    )
    import_run = models.OneToOneField(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="opinion_catalogue_snapshot",
        verbose_name="Impordikäivitus",
    )
    source_manifest_checksum = models.CharField(max_length=64, verbose_name="Lähtekomplekti räsi")
    extractor_version = models.CharField(max_length=20, verbose_name="Lugeja versioon")
    observed_at = models.DateTimeField(verbose_name="Kogutud")
    entry_count = models.PositiveIntegerField(default=0, verbose_name="Kirjeid")
    valid_count = models.PositiveIntegerField(default=0, verbose_name="Korras")
    quarantined_count = models.PositiveIntegerField(default=0, verbose_name="Karantiinis")
    extracted_count = models.PositiveIntegerField(default=0, verbose_name="Loetud")
    needs_ocr_count = models.PositiveIntegerField(default=0, verbose_name="Vajab OCR-i")
    failed_extraction_count = models.PositiveIntegerField(default=0, verbose_name="Lugemata")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-observed_at", "-id")
        verbose_name = "Arvamuste kataloogi hetkeseis"
        verbose_name_plural = "Arvamuste kataloogi hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="opinioncatalogue_one_current_per_source",
            ),
            models.UniqueConstraint(
                fields=["source", "source_manifest_checksum", "extractor_version"],
                name="opinioncatalogue_unique_manifest_and_extractor",
            ),
            models.CheckConstraint(
                condition=Q(valid_count__lte=F("entry_count")),
                name="opinioncatalogue_valid_within_total",
            ),
            models.CheckConstraint(
                condition=Q(quarantined_count__lte=F("entry_count")),
                name="opinioncatalogue_quarantined_within_total",
            ),
            models.CheckConstraint(
                condition=Q(extracted_count__lte=F("valid_count")),
                name="opinioncatalogue_extracted_within_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"Arvamused {self.observed_at:%d.%m.%Y} ({self.entry_count})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise SnapshotImmutable(
                    "A published opinion catalogue may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class OpinionCatalogueEntry(models.Model):
    """One source file, as this snapshot found it.

    `blob` and `extraction` are nullable on purpose. A quarantined source entry
    is still catalogued — refusing to record it would make a rejected document
    indistinguishable from one that was never there — it simply carries no
    readable document and is excluded from everything downstream.
    """

    snapshot = models.ForeignKey(
        OpinionCatalogueSnapshot,
        on_delete=models.CASCADE,
        related_name="entries",
        verbose_name="Hetkeseis",
    )
    source_provider = models.CharField(
        max_length=20, choices=SourceProvider, verbose_name="Lähteliik"
    )
    # The path *within* the source root or the ZIP. Never an absolute path and
    # never used to build one; `opinion_storage` resolves reads under the store.
    source_entry_key = models.CharField(
        max_length=MAX_SOURCE_KEY_LENGTH, verbose_name="Lähtekirje võti"
    )
    original_filename = models.CharField(
        max_length=MAX_FILENAME_LENGTH, verbose_name="Algne failinimi"
    )
    display_filename = models.CharField(
        max_length=MAX_FILENAME_LENGTH, verbose_name="Kuvatav failinimi"
    )

    filename_date = models.DateField(null=True, blank=True, verbose_name="Nimest kuupäev")
    filename_recipient = models.CharField(
        max_length=MAX_RECIPIENT_LENGTH, blank=True, verbose_name="Nimest saaja"
    )
    filename_subject = models.CharField(
        max_length=MAX_SUBJECT_LENGTH, blank=True, verbose_name="Nimest teema"
    )

    classification = models.CharField(
        max_length=32,
        choices=DocumentClassification,
        default=DocumentClassification.UNKNOWN,
        db_index=True,
        verbose_name="Liik",
    )
    classification_signals = models.JSONField(
        default=list, blank=True, verbose_name="Liigituse tunnused"
    )

    blob = models.ForeignKey(
        OpinionDocumentBlob,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="catalogue_entries",
        verbose_name="Fail",
    )
    extraction = models.ForeignKey(
        OpinionDocumentExtraction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="catalogue_entries",
        verbose_name="Lugemine",
    )
    source_order = models.PositiveIntegerField(verbose_name="Järjekord")
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")

    class Meta:
        ordering = ("snapshot", "source_order")
        verbose_name = "Arvamuste kataloogi kirje"
        verbose_name_plural = "Arvamuste kataloogi kirjed"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "source_entry_key"],
                name="opinionentry_unique_source_key_per_snapshot",
            ),
            models.CheckConstraint(
                condition=~Q(original_filename=""), name="opinionentry_filename_required"
            ),
            # An extraction without its blob would be a reading of nothing.
            models.CheckConstraint(
                condition=Q(extraction__isnull=True) | Q(blob__isnull=False),
                name="opinionentry_extraction_requires_blob",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "classification"]),
            models.Index(fields=["snapshot", "filename_date"]),
        ]

    def __str__(self) -> str:
        return self.display_filename or self.original_filename

    @property
    def is_matchable(self) -> bool:
        """Whether Phase 2 may consider this entry at all."""
        return (
            self.blob_id is not None
            and self.extraction_id is not None
            and self.blob.is_valid
            and self.extraction.is_usable
        )

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A catalogued opinion entry cannot be changed.")
        return super().save(*args, **kwargs)


class OpinionCatalogueFeedState(models.Model):
    """What the last catalogue build found, and what it still owes.

    The progress counters are what make a bounded build resumable in the open:
    an operator can see that 250 of 759 documents are done without reading rows
    or waiting for a snapshot that does not exist yet.
    """

    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="opinion_catalogue_feed_state",
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
        choices=CatalogueResult,
        default=CatalogueResult.NEVER_RUN,
        verbose_name="Viimane tulemus",
    )
    last_error_summary = models.CharField(
        max_length=MAX_ERROR_SUMMARY_LENGTH,
        blank=True,
        verbose_name="Viimane veateade",
        help_text="Puhastatud ja lühendatud. Ei sisalda failinimesid ega teid.",
    )
    build_state = models.CharField(
        max_length=16,
        choices=CatalogueBuildState,
        default=CatalogueBuildState.IDLE,
        verbose_name="Ehituse olek",
    )
    # The manifest the in-progress build is working through. Cleared when a
    # snapshot is published, so a changed source starts a fresh build.
    building_manifest_checksum = models.CharField(
        max_length=64, blank=True, verbose_name="Töös oleva komplekti räsi"
    )
    manifest_entry_count = models.PositiveIntegerField(default=0, verbose_name="Kirjeid kokku")
    processed_entry_count = models.PositiveIntegerField(default=0, verbose_name="Töödeldud")
    current_snapshot = models.ForeignKey(
        OpinionCatalogueSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Kehtiv hetkeseis",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        verbose_name = "Arvamuste andmevoo olek"
        verbose_name_plural = "Arvamuste andmevoo olekud"
        constraints = [
            models.CheckConstraint(
                condition=Q(processed_entry_count__lte=F("manifest_entry_count")),
                name="opinionfeedstate_processed_within_manifest",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"

    @property
    def pending_entry_count(self) -> int:
        return max(self.manifest_entry_count - self.processed_entry_count, 0)
