"""The public Koda.ee opinion source: pages, attached documents, publications.

This is the second opinion source beside the private catalogue, and the two are
deliberately parallel rather than merged. `OpinionCatalogueEntry` records what a
*private source file* claimed — a path, a filename, a provider. The models here
record what *Koda.ee published* — a page, a date, an attachment label. Both may
point at the same `OpinionDocumentBlob`, because the Chamber routinely publishes
the very letter it filed privately, and the same bytes must remain one blob with
two provenances rather than two documents.

The lifecycle follows the archive pattern, not the current-listing pattern. A
public page that leaves today's listing has not stopped existing: its historical
publication is durable provenance. So every snapshot carries the full known
corpus forward — a page or attachment observed once is copied into every later
snapshot with its original `first_seen_at`, and a later 404 moves `is_present`,
never deletes a row. A failed crawl publishes nothing and the previous snapshot
stays current.

No model here holds PDF bytes or a filesystem path. Bytes live in
`OpinionDocumentBlob` under the managed store, exactly as private ones do.
"""

from django.db import models
from django.db.models import F, Q

from apps.sources.models import DataSource

from .models import (
    MAX_CANONICAL_URL_LENGTH,
    MAX_ERROR_SUMMARY_LENGTH,
    MAX_TOPIC_TITLE_LENGTH,
    SnapshotImmutable,
    SyncResult,
)
from .opinion_classification import DocumentClassification
from .opinion_models import (
    MAX_FILENAME_LENGTH,
    MAX_RECIPIENT_LENGTH,
    MAX_SUBJECT_LENGTH,
    OpinionDocumentBlob,
    OpinionDocumentExtraction,
)

MAX_ATTACHMENT_LABEL_LENGTH = 400


class PublicPageType(models.TextChoices):
    """Where on Koda.ee a page presented itself as opinion material."""

    MEIE_ARVAMUS = "meie_arvamus", "Meie arvamus"
    NEWS = "news", "Uudis"
    OTHER = "other", "Muu Koda.ee arvamuskontekst"


class PublicFetchState(models.TextChoices):
    """How far one page or attachment got in this run — or a previous one.

    `CARRIED` is the archive lesson applied: an incremental run never touches
    most pages, and carrying the previous answer forward is a different claim
    from having looked today.
    """

    FETCHED = "fetched", "Loetud"
    CARRIED = "carried", "Eelmisest ülekantud"
    FAILED = "failed", "Ebaõnnestus"


class PublicOpinionSnapshot(models.Model):
    """One published, complete state of the public opinion corpus.

    "Complete" means the accumulated corpus is coherent: every page the run set
    out to read was read or definitively failed, and everything known before is
    carried forward. It never means "the whole website was crawled today" —
    a daily run reads only the listing edge.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="public_opinion_snapshots",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="public_opinion_snapshots",
        verbose_name="Algfail",
    )
    import_run = models.OneToOneField(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="public_opinion_snapshot",
        verbose_name="Impordikäivitus",
    )
    observed_at = models.DateTimeField(verbose_name="Kogutud")
    # A checksum over the normalised accumulated corpus, so an unchanged edge
    # run is recognised without republishing identical rows.
    corpus_checksum = models.CharField(max_length=64, verbose_name="Korpuse räsi")
    page_count = models.PositiveIntegerField(default=0, verbose_name="Lehti")
    document_count = models.PositiveIntegerField(default=0, verbose_name="Dokumente")
    article_only_page_count = models.PositiveIntegerField(
        default=0, verbose_name="Lehti ilma PDF-ita"
    )
    listing_pages_fetched = models.PositiveSmallIntegerField(
        default=0, verbose_name="Loetud loendilehti"
    )
    detail_pages_fetched = models.PositiveIntegerField(default=0, verbose_name="Loetud lehti")
    documents_fetched = models.PositiveIntegerField(default=0, verbose_name="Loetud dokumente")
    new_blob_count = models.PositiveIntegerField(default=0, verbose_name="Uusi faile")
    known_blob_count = models.PositiveIntegerField(default=0, verbose_name="Tuntud faile")
    invalid_document_count = models.PositiveIntegerField(default=0, verbose_name="Vigaseid faile")
    failed_page_count = models.PositiveIntegerField(default=0, verbose_name="Ebaõnnestunud lehti")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-observed_at", "-id")
        verbose_name = "Avaliku arvamusallika hetkeseis"
        verbose_name_plural = "Avaliku arvamusallika hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="publicopinion_one_current_per_source",
            ),
            models.CheckConstraint(
                condition=Q(article_only_page_count__lte=F("page_count")),
                name="publicopinion_article_only_within_pages",
            ),
        ]

    def __str__(self) -> str:
        return f"Koda.ee arvamused {self.observed_at:%d.%m.%Y} ({self.page_count})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise SnapshotImmutable(
                    "A published public opinion snapshot may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class PublicOpinionPage(models.Model):
    """One Koda.ee page holding opinion material, as one snapshot knows it.

    `content_key` is derived from the canonical path, exactly as the
    consultation catalogues derive theirs: the path is the page's identity and
    survives a title edit. `first_seen_at` is copied forward from the snapshot
    that first saw the page, so provenance keeps its original date however many
    snapshots later it is read in.

    A page with no attached PDF is still recorded. It is the Chamber's public
    statement about a position — evidence, not a document — and the distinction
    is preserved by the absence of `PublicOpinionDocument` rows, never by
    inventing one.
    """

    snapshot = models.ForeignKey(
        PublicOpinionSnapshot,
        on_delete=models.CASCADE,
        related_name="pages",
        verbose_name="Hetkeseis",
    )
    content_key = models.CharField(max_length=64, verbose_name="Sisu võti")
    canonical_url = models.URLField(max_length=MAX_CANONICAL_URL_LENGTH, verbose_name="Aadress")
    page_type = models.CharField(
        max_length=16, choices=PublicPageType, db_index=True, verbose_name="Lehe liik"
    )
    title = models.CharField(max_length=MAX_TOPIC_TITLE_LENGTH, verbose_name="Pealkiri")
    listing_summary = models.TextField(blank=True, verbose_name="Loendi kokkuvõte")
    body_text = models.TextField(blank=True, verbose_name="Lehe tekst")
    published_date = models.DateField(null=True, blank=True, verbose_name="Avaldatud")
    # Why this page counts as opinion material: `listed-meie-arvamus`,
    # `opinion-vocabulary`, `opinion-attachment`. Machine-readable, so the
    # boundary between an opinion page and ordinary news stays inspectable.
    opinion_evidence_codes = models.JSONField(
        default=list, blank=True, verbose_name="Arvamustõendid"
    )
    fetch_state = models.CharField(
        max_length=16, choices=PublicFetchState, db_index=True, verbose_name="Lugemise olek"
    )
    # A short machine-readable code — `http_404`, `timeout`, `unparsable` — and
    # never a message, a body or a URL.
    failure_code = models.CharField(max_length=32, blank=True, verbose_name="Vea kood")
    content_hash = models.CharField(max_length=64, blank=True, verbose_name="Sisu räsi")
    first_seen_at = models.DateTimeField(verbose_name="Esmakordselt nähtud")
    last_fetched_at = models.DateTimeField(null=True, blank=True, verbose_name="Viimati loetud")
    # A full listing walk may prove a page gone from Koda.ee; an incremental
    # run never looks, so it carries the previous answer forward. A page that
    # disappears keeps its rows — availability changes, history does not.
    is_present = models.BooleanField(default=True, verbose_name="Koda.ee-s olemas")

    class Meta:
        ordering = ("snapshot", "-published_date", "-id")
        verbose_name = "Avalik arvamusleht"
        verbose_name_plural = "Avalikud arvamuslehed"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "content_key"],
                name="publicopinionpage_unique_key_per_snapshot",
            ),
            models.UniqueConstraint(
                fields=["snapshot", "canonical_url"],
                name="publicopinionpage_unique_url_per_snapshot",
            ),
            models.CheckConstraint(
                condition=~Q(canonical_url=""), name="publicopinionpage_url_required"
            ),
            models.CheckConstraint(condition=~Q(title=""), name="publicopinionpage_title_required"),
            # A fetched page has been read, so it can say when it was published
            # and what it said. Carried and failed rows may not be able to.
            models.CheckConstraint(
                condition=~Q(fetch_state=PublicFetchState.FETCHED) | ~Q(body_text=""),
                name="publicopinionpage_fetched_has_body",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "page_type"]),
            models.Index(fields=["snapshot", "published_date"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_readable(self) -> bool:
        return bool(self.body_text) and self.is_present

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A recorded public opinion page cannot be changed.")
        return super().save(*args, **kwargs)


class PublicOpinionDocument(models.Model):
    """One PDF Koda.ee attached to one page, as one snapshot knows it.

    This is the public counterpart of `OpinionCatalogueEntry` and deliberately
    not that model: an entry describes a private source file's claims, this
    describes a public attachment's. What they share is the destination — both
    point at the same globally deduplicated `OpinionDocumentBlob`, which is what
    makes "the same letter, filed privately and published publicly" one document
    with two provenances.

    `blob` and `extraction` are nullable for the same reason the entry's are:
    an attachment whose download failed or whose bytes were quarantined is
    still provenance — Koda.ee published *something* there — it simply carries
    no readable document and is excluded from matching.
    """

    snapshot = models.ForeignKey(
        PublicOpinionSnapshot,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="Hetkeseis",
    )
    page = models.ForeignKey(
        PublicOpinionPage,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="Leht",
    )
    pdf_url = models.URLField(max_length=MAX_CANONICAL_URL_LENGTH, verbose_name="PDF-i aadress")
    attachment_label = models.CharField(
        max_length=MAX_ATTACHMENT_LABEL_LENGTH, blank=True, verbose_name="Manuse nimi"
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
        related_name="public_documents",
        verbose_name="Fail",
    )
    extraction = models.ForeignKey(
        OpinionDocumentExtraction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="public_documents",
        verbose_name="Lugemine",
    )
    fetch_state = models.CharField(
        max_length=16, choices=PublicFetchState, db_index=True, verbose_name="Lugemise olek"
    )
    failure_code = models.CharField(max_length=32, blank=True, verbose_name="Vea kood")
    first_seen_at = models.DateTimeField(verbose_name="Esmakordselt nähtud")
    is_present = models.BooleanField(default=True, verbose_name="Koda.ee-s olemas")
    source_order = models.PositiveIntegerField(default=0, verbose_name="Järjekord")
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")

    class Meta:
        ordering = ("snapshot", "page", "source_order")
        verbose_name = "Avalik arvamusdokument"
        verbose_name_plural = "Avalikud arvamusdokumendid"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "pdf_url"],
                name="publicopiniondoc_unique_url_per_snapshot",
            ),
            models.CheckConstraint(condition=~Q(pdf_url=""), name="publicopiniondoc_url_required"),
            # An extraction without its blob would be a reading of nothing.
            models.CheckConstraint(
                condition=Q(extraction__isnull=True) | Q(blob__isnull=False),
                name="publicopiniondoc_extraction_requires_blob",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "classification"]),
            models.Index(fields=["snapshot", "filename_date"]),
        ]

    def __str__(self) -> str:
        return self.display_filename or self.attachment_label

    @property
    def is_matchable(self) -> bool:
        """Whether the matcher may consider this document at all."""
        return (
            self.blob_id is not None
            and self.extraction_id is not None
            and self.is_present
            and self.blob.is_valid
            and self.extraction.is_usable
        )

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A recorded public opinion document cannot be changed.")
        return super().save(*args, **kwargs)


class PublicOpinionFeedState(models.Model):
    """What the last public opinion collection did, and where its edge is."""

    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="public_opinion_feed_state",
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
        help_text="Puhastatud ja lühendatud. Ei sisalda aadresse ega failinimesid.",
    )
    # Whether the configured historical window has ever been walked to its end.
    # Incremental runs read only the listing edge and refuse to pretend
    # otherwise while this is false.
    backfill_complete = models.BooleanField(default=False, verbose_name="Ajalugu kogutud")
    current_snapshot = models.ForeignKey(
        PublicOpinionSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Kehtiv hetkeseis",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        verbose_name = "Avaliku arvamusallika olek"
        verbose_name_plural = "Avalike arvamusallikate olekud"

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"
