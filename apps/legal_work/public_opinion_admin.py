"""Read-only admin for the public Koda.ee opinion corpus.

Inspection only, like every other imported dataset: the collector writes,
staff read, and there is no approve, override or manual-URL action. What staff
review here is the *boundary* — which pages counted as opinion material and
why, which attachments failed, which public bytes duplicated private ones —
so the evidence rules in `public_opinions.py` can be corrected in code.

Nothing here exposes a storage key, a filesystem path or a full digest.
"""

from django.contrib import admin
from django.db.models import Count

from apps.core.admin import ReadOnlyAdmin

from .public_opinion_models import (
    PublicFetchState,
    PublicOpinionDocument,
    PublicOpinionFeedState,
    PublicOpinionPage,
    PublicOpinionSnapshot,
)


@admin.register(PublicOpinionSnapshot)
class PublicOpinionSnapshotAdmin(ReadOnlyAdmin):
    list_display = (
        "observed_at",
        "page_count",
        "document_count",
        "article_only_page_count",
        "new_blob_count",
        "invalid_document_count",
        "failed_page_count",
        "is_current",
    )
    list_filter = ("is_current",)
    ordering = ("-observed_at", "-id")


class PageDocumentPresenceFilter(admin.SimpleListFilter):
    title = "Dokumendid"
    parameter_name = "documents"

    def lookups(self, request, model_admin):
        return [("with", "PDF-iga"), ("without", "Ilma PDF-ita")]

    def queryset(self, request, queryset):
        value = self.value()
        annotated = queryset.annotate(document_total=Count("documents"))
        if value == "with":
            return annotated.filter(document_total__gt=0)
        if value == "without":
            return annotated.filter(document_total=0)
        return queryset


@admin.register(PublicOpinionPage)
class PublicOpinionPageAdmin(ReadOnlyAdmin):
    list_display = (
        "title",
        "page_type",
        "published_date",
        "evidence",
        "fetch_state",
        "is_present",
        "snapshot",
    )
    list_filter = (
        "page_type",
        "fetch_state",
        "is_present",
        PageDocumentPresenceFilter,
        "snapshot__is_current",
    )
    list_select_related = ("snapshot",)
    search_fields = ("title",)
    ordering = ("-published_date", "-id")

    @admin.display(description="Tõendid")
    def evidence(self, obj):
        return ", ".join(obj.opinion_evidence_codes or []) or "—"


class DocumentDuplicationFilter(admin.SimpleListFilter):
    """The dedup review: which public bytes were already known privately."""

    title = "Kattuvus"
    parameter_name = "duplication"

    def lookups(self, request, model_admin):
        return [
            ("private_identical", "Sama fail privaatallikas"),
            ("public_only", "Ainult avalik"),
            ("failed", "Lugemata"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "private_identical":
            return queryset.filter(blob__catalogue_entries__isnull=False).distinct()
        if value == "public_only":
            return queryset.filter(blob__isnull=False, blob__catalogue_entries__isnull=True)
        if value == "failed":
            return queryset.filter(fetch_state=PublicFetchState.FAILED)
        return queryset


@admin.register(PublicOpinionDocument)
class PublicOpinionDocumentAdmin(ReadOnlyAdmin):
    list_display = (
        "display_filename",
        "classification",
        "filename_date",
        "fetch_state",
        "failure_code",
        "digest_prefix",
        "is_present",
        "snapshot",
    )
    list_filter = (
        "classification",
        "fetch_state",
        "is_present",
        DocumentDuplicationFilter,
        "snapshot__is_current",
    )
    list_select_related = ("snapshot", "blob", "page")
    search_fields = ("display_filename", "attachment_label")
    ordering = ("-filename_date", "-id")

    @admin.display(description="Räsi algus")
    def digest_prefix(self, obj):
        return obj.blob.digest_prefix if obj.blob_id else "—"


@admin.register(PublicOpinionFeedState)
class PublicOpinionFeedStateAdmin(ReadOnlyAdmin):
    list_display = (
        "source",
        "last_result",
        "backfill_complete",
        "last_checked_at",
        "last_successful_sync_at",
        "last_changed_at",
        "last_error_summary",
    )
    list_select_related = ("source",)
