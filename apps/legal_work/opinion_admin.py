"""Read-only admin for the private opinion-document catalogue.

Inspection only, and here that is the *product* rather than a precaution.
Everything about a document — whether it was stored, whether it could be read,
what kind of document it is, what its header says — is derived by code from the
bytes and the filename. There is no add, edit, delete, upload, approve,
override, reclassify or retry action, and adding one would create a second,
invisible source of truth that no test could reason about.

A wrong classification is corrected by changing the vocabulary in
`opinion_classification.py` and rebuilding; a failed extraction is corrected by
a new extractor version. Staff inspection informs the rules; it never overrides
a row.

Two things are deliberately withheld even from staff: the full SHA-256, and any
filesystem path. A digest prefix identifies a document for a conversation
without being a lookup key, and the store path is not information an admin page
has any use for.
"""

from django.contrib import admin
from django.utils.html import format_html

from apps.core.admin import ReadOnlyAdmin

from .opinion_models import (
    OpinionCatalogueEntry,
    OpinionCatalogueFeedState,
    OpinionCatalogueSnapshot,
    OpinionDocumentBlob,
    OpinionDocumentExtraction,
)

EXCERPT_LENGTH = 600


def _excerpt(value: str, limit: int = EXCERPT_LENGTH) -> str:
    """Show enough text to recognise a document, never the whole letter."""
    if not value:
        return "—"
    text = value.strip()
    clipped = text[:limit]
    suffix = "…" if len(text) > limit else ""
    return format_html(
        "<div style='white-space:pre-wrap;max-width:60em'>{}{}</div>", clipped, suffix
    )


@admin.register(OpinionCatalogueSnapshot)
class OpinionCatalogueSnapshotAdmin(ReadOnlyAdmin):
    list_display = (
        "observed_at",
        "is_current",
        "entry_count",
        "valid_count",
        "quarantined_count",
        "extracted_count",
        "needs_ocr_count",
        "failed_extraction_count",
        "extractor_version",
        "checksum_prefix",
    )
    list_filter = ("is_current", "extractor_version")
    date_hierarchy = "observed_at"
    ordering = ("-observed_at", "-id")
    list_select_related = ("source",)

    @admin.display(description="Komplekti räsi")
    def checksum_prefix(self, obj):
        return obj.source_manifest_checksum[:12]


@admin.register(OpinionDocumentBlob)
class OpinionDocumentBlobAdmin(ReadOnlyAdmin):
    list_display = (
        "digest_prefix",
        "validation_status",
        "page_count",
        "byte_size",
        "is_encrypted",
        "has_active_content",
        "imported_at",
    )
    list_filter = ("validation_status", "is_encrypted", "has_active_content")
    date_hierarchy = "imported_at"
    ordering = ("-imported_at", "-id")
    # Searching by digest prefix is how an operator follows one document from a
    # log line to its row. The full digest is never displayed.
    search_fields = ("sha256",)

    # Withheld from every rendered page. `ReadOnlyAdmin` makes every model field
    # readonly, and Django builds a change page from the form fields *plus* the
    # readonly ones — so `exclude` alone does not suppress them and the full
    # digest was rendering. `get_fields` is the only place that decides what a
    # change page actually contains.
    HIDDEN_FIELDS = frozenset({"sha256", "storage_key"})

    @admin.display(description="Räsi algus", ordering="sha256")
    def digest_prefix(self, obj):
        return obj.sha256[:12]

    def get_fields(self, request, obj=None):
        return [
            field.name for field in self.model._meta.fields if field.name not in self.HIDDEN_FIELDS
        ] + ["digest_prefix"]

    def get_readonly_fields(self, request, obj=None):
        return self.get_fields(request, obj)

    def get_exclude(self, request, obj=None):
        return tuple(sorted(self.HIDDEN_FIELDS))


@admin.register(OpinionDocumentExtraction)
class OpinionDocumentExtractionAdmin(ReadOnlyAdmin):
    list_display = (
        "blob_digest",
        "status",
        "extractor_version",
        "page_count",
        "detected_date",
        "detected_recipient",
        "created_at",
    )
    list_filter = ("status", "extractor_version")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    list_select_related = ("blob",)
    readonly_fields = ("first_page_excerpt", "text_excerpt")

    @admin.display(description="Faili räsi")
    def blob_digest(self, obj):
        return obj.blob.sha256[:12]

    @admin.display(description="Esilehe algus")
    def first_page_excerpt(self, obj):
        return _excerpt(obj.first_page_text)

    @admin.display(description="Teksti algus")
    def text_excerpt(self, obj):
        return _excerpt(obj.text)

    # The excerpts are the reviewable form. The full columns would put an entire
    # private letter on one page, and `text_sha256` is a full digest.
    HIDDEN_FIELDS = frozenset({"text", "first_page_text", "text_sha256"})

    def get_fields(self, request, obj=None):
        return [
            field.name for field in self.model._meta.fields if field.name not in self.HIDDEN_FIELDS
        ] + ["first_page_excerpt", "text_excerpt"]

    def get_readonly_fields(self, request, obj=None):
        return self.get_fields(request, obj)

    def get_exclude(self, request, obj=None):
        return tuple(sorted(self.HIDDEN_FIELDS))


class MatchableFilter(admin.SimpleListFilter):
    """Whether Phase 2 may consider this entry at all.

    Separates "the document could not be read" from "the document is fine and
    simply has no match yet", which is the first question about any entry that
    never became a link.
    """

    title = "Sobitatav"
    parameter_name = "matchable"

    def lookups(self, request, model_admin):
        return [("yes", "Jah"), ("no", "Ei")]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "yes":
            return queryset.filter(blob__validation_status="valid", extraction__status="extracted")
        if value == "no":
            return queryset.exclude(blob__validation_status="valid", extraction__status="extracted")
        return queryset


@admin.register(OpinionCatalogueEntry)
class OpinionCatalogueEntryAdmin(ReadOnlyAdmin):
    list_display = (
        "display_filename",
        "classification",
        "filename_date",
        "filename_recipient",
        "validation_status",
        "extraction_status",
        "page_count",
        "source_provider",
    )
    list_filter = (
        "classification",
        "source_provider",
        MatchableFilter,
        "blob__validation_status",
        "extraction__status",
    )
    date_hierarchy = "filename_date"
    ordering = ("snapshot", "source_order")
    search_fields = ("display_filename", "filename_subject", "filename_recipient")
    list_select_related = ("blob", "extraction", "snapshot")
    readonly_fields = ("first_page_excerpt", "detected_fields", "digest_prefix")

    @admin.display(description="Kontroll", ordering="blob__validation_status")
    def validation_status(self, obj):
        return obj.blob.get_validation_status_display() if obj.blob else "—"

    @admin.display(description="Lugemine", ordering="extraction__status")
    def extraction_status(self, obj):
        return obj.extraction.get_status_display() if obj.extraction else "—"

    @admin.display(description="Lehti")
    def page_count(self, obj):
        return obj.blob.page_count if obj.blob else "—"

    @admin.display(description="Räsi algus")
    def digest_prefix(self, obj):
        return obj.blob.sha256[:12] if obj.blob else "—"

    @admin.display(description="Esilehe algus")
    def first_page_excerpt(self, obj):
        return _excerpt(obj.extraction.first_page_text if obj.extraction else "")

    @admin.display(description="Dokumendist loetud")
    def detected_fields(self, obj):
        """What the letter says about itself, beside what its name says.

        Shown together on purpose: the disagreements are the interesting part,
        and a reviewer comparing two admin pages compares them wrongly.
        """
        if obj.extraction is None:
            return "—"
        extraction = obj.extraction
        return format_html(
            "<dl style='margin:0'>"
            "<dt>Kuupäev</dt><dd>{}</dd>"
            "<dt>Saaja</dt><dd>{}</dd>"
            "<dt>Teema</dt><dd>{}</dd>"
            "<dt>Meie viide</dt><dd>{}</dd>"
            "<dt>Teie viide</dt><dd>{}</dd>"
            "</dl>",
            extraction.detected_date or "—",
            extraction.detected_recipient or "—",
            extraction.detected_subject or "—",
            extraction.our_reference or "—",
            extraction.their_reference or "—",
        )

    # The source entry key is a path inside the inbox or the archive.
    HIDDEN_FIELDS = frozenset({"source_entry_key"})

    def get_fields(self, request, obj=None):
        return [
            field.name for field in self.model._meta.fields if field.name not in self.HIDDEN_FIELDS
        ] + ["first_page_excerpt", "detected_fields", "digest_prefix"]

    def get_readonly_fields(self, request, obj=None):
        return self.get_fields(request, obj)

    def get_exclude(self, request, obj=None):
        return tuple(sorted(self.HIDDEN_FIELDS))


@admin.register(OpinionCatalogueFeedState)
class OpinionCatalogueFeedStateAdmin(ReadOnlyAdmin):
    list_display = (
        "source",
        "last_result",
        "build_state",
        "manifest_entry_count",
        "processed_entry_count",
        "pending_entry_count",
        "last_checked_at",
        "last_successful_sync_at",
    )
    list_filter = ("last_result", "build_state")
    list_select_related = ("source", "current_snapshot")

    @admin.display(description="Ootel")
    def pending_entry_count(self, obj):
        return obj.pending_entry_count
