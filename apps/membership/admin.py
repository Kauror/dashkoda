"""Read-only admin for both membership datasets.

The public directory observation stays exactly as it was: written by the
collector, inspected here, never edited.

The internal board-report models are read-only for the same reason and one more.
A published observation is immutable by design, corrections must supersede
rather than overwrite, and the child rows have to be written in the same
transaction — none of which a generic change form can do. So the admin
*inspects* internal data and links out to the purpose-built entry and correction
views for anything that writes.

The one exception is a data issue's resolution. Someone has to be able to record
that a warning was looked at, so `resolved`, the note and the resolver are
editable while the imported warning itself is not: a resolution says what a
person concluded, and must never be able to change what the source said.
"""

from django.contrib import admin
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.core.admin import ReadOnlyAdmin

from .models import (
    InternalMembershipObservation,
    MembershipCountObservation,
    MembershipDataIssue,
    MembershipFeedState,
    MembershipHistoricalSourceDocument,
    MembershipMetricConflict,
    MembershipMonthlyNewMemberValue,
    MembershipRemovalReason,
    MembershipSizeMovement,
    QualityStatus,
)


@admin.register(MembershipCountObservation)
class MembershipCountObservationAdmin(ReadOnlyAdmin):
    """The public Koda.ee directory count. Unchanged and deliberately separate."""

    list_display = ("observed_at", "total_members", "is_current", "source", "imported_at")
    list_filter = ("is_current", "source")
    date_hierarchy = "observed_at"
    ordering = ("-observed_at", "-id")
    list_select_related = ("source", "artifact", "import_run")


@admin.register(MembershipFeedState)
class MembershipFeedStateAdmin(ReadOnlyAdmin):
    list_display = ("source", "last_result", "last_checked_at", "last_successful_sync_at")
    list_filter = ("last_result",)
    list_select_related = ("source", "current_observation")


class MembershipSizeMovementInline(admin.TabularInline):
    model = MembershipSizeMovement
    extra = 0
    can_delete = False
    fields = ("direction", "size_band_key", "member_count", "total_reported", "warning_codes")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class MembershipRemovalReasonInline(admin.TabularInline):
    model = MembershipRemovalReason
    extra = 0
    can_delete = False
    fields = ("reason_key", "reason_label_raw", "member_count", "removed_total_reported")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(InternalMembershipObservation)
class InternalMembershipObservationAdmin(ReadOnlyAdmin):
    """Inspection, with links to the workflows that are allowed to write."""

    list_display = (
        "observation_date",
        "source_kind",
        "quality_status",
        "is_preferred_for_date",
        "total_members",
        "paid_members",
        "new_members_ytd",
        "extraction_confidence",
        "imported_at",
        "workflow_links",
    )
    list_filter = (
        "quality_status",
        "source_kind",
        "is_preferred_for_date",
        "extraction_confidence",
        "observation_date_precision",
    )
    # Searchable by what identifies a report, never by its contents.
    search_fields = (
        "external_snapshot_id",
        "source_column_label",
        "source_document__document_title",
        "source_document__external_source_id",
    )
    date_hierarchy = "observation_date"
    ordering = ("-observation_date", "-id")
    list_select_related = ("source", "artifact", "import_run", "source_document")
    inlines = (MembershipSizeMovementInline, MembershipRemovalReasonInline)
    actions = None
    change_list_template = "membership/admin/internal_observation_changelist.html"

    @admin.display(description="Toimingud")
    def workflow_links(self, obj):
        if obj is None or obj.pk is None:
            return ""
        detail = reverse("membership-admin-report-detail", args=[obj.pk])
        if obj.quality_status == QualityStatus.SUPERSEDED:
            return format_html('<a href="{}">Vaata</a>', detail)
        correct = reverse("membership-admin-report-correct", args=[obj.pk])
        return format_html(
            '<a href="{}">Vaata</a> &middot; <a href="{}">Loo parandatud versioon</a>',
            detail,
            correct,
        )

    def get_readonly_fields(self, request, obj=None):
        # Django's default `get_fields` includes the readonly fields, so naming
        # the link column here is enough to have it rendered on the detail page
        # as well as in the list.
        return [*super().get_readonly_fields(request, obj), "workflow_links"]

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["membership_manual_entry_url"] = reverse("membership-admin-report-new")
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(MembershipHistoricalSourceDocument)
class MembershipHistoricalSourceDocumentAdmin(ReadOnlyAdmin):
    """Provenance only.

    There is no download action anywhere on this model, because the original
    Word files are not stored — the canonical package is the contract and the
    documents were never copied into this application.
    """

    list_display = (
        "observation_date",
        "document_title",
        "extraction_status",
        "date_confidence",
        "external_source_id",
        "imported_at",
    )
    list_filter = ("extraction_status", "date_confidence", "observation_date_precision")
    search_fields = ("document_title", "external_source_id", "filename")
    date_hierarchy = "observation_date"
    ordering = ("-observation_date", "external_source_id")
    list_select_related = ("source", "import_run")


@admin.register(MembershipMonthlyNewMemberValue)
class MembershipMonthlyNewMemberValueAdmin(ReadOnlyAdmin):
    list_display = (
        "calendar_year",
        "calendar_month",
        "new_members",
        "value_status",
        "is_current_for_month",
        "source_count",
        "imported_at",
    )
    list_filter = ("value_status", "is_current_for_month", "calendar_year")
    ordering = ("-calendar_year", "-calendar_month", "-id")
    list_select_related = ("source", "import_run", "selected_source_document")


@admin.register(MembershipDataIssue)
class MembershipDataIssueAdmin(admin.ModelAdmin):
    """Imported warnings, resolvable but not rewritable."""

    list_display = (
        "warning_code",
        "severity",
        "dataset",
        "record_key",
        "resolved",
        "resolved_by",
        "resolved_at",
    )
    list_filter = ("severity", "resolved", "dataset", "warning_code")
    search_fields = ("warning_code", "record_key", "external_warning_id")
    ordering = ("severity", "dataset", "record_key", "id")
    list_select_related = ("source", "import_run", "source_document", "resolved_by")
    # Exactly the resolution. Everything the import wrote stays read-only.
    fields = (
        "dataset",
        "record_key",
        "warning_code",
        "severity",
        "message",
        "raw_value",
        "suggested_action",
        "source_document",
        "observation",
        "resolved",
        "resolution_note",
        "resolved_by",
        "resolved_at",
    )
    readonly_fields = (
        "dataset",
        "record_key",
        "warning_code",
        "severity",
        "message",
        "raw_value",
        "suggested_action",
        "source_document",
        "observation",
        "resolved_by",
        "resolved_at",
    )
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        """Stamp who resolved it, and record that in the audit trail.

        The resolver and the timestamp are set here rather than being form
        fields, so they describe what actually happened instead of what someone
        typed.

        The save names the resolution fields explicitly: the model now refuses
        any write that could touch what the import put there, and a bare
        `obj.save()` claims to rewrite the whole row.
        """
        became_resolved = obj.resolved and not obj.resolved_at
        if became_resolved:
            obj.resolved_by = request.user
            obj.resolved_at = timezone.now()
        elif not obj.resolved:
            obj.resolved_by = None
            obj.resolved_at = None
        obj.save(update_fields=sorted(obj.MUTABLE_FIELDS))
        if became_resolved:
            record_event(
                action=AuditAction.MEMBERSHIP_ISSUE_RESOLVED,
                obj=obj,
                actor=request.user,
                change_summary={
                    "source": obj.source.slug,
                    "warning_code": obj.warning_code,
                    "severity": obj.severity,
                    "issue_id": obj.pk,
                },
            )


@admin.register(MembershipMetricConflict)
class MembershipMetricConflictAdmin(admin.ModelAdmin):
    """Cross-document disagreements, resolvable in the same narrow way."""

    list_display = (
        "observation_date",
        "metric",
        "distinct_values",
        "resolved",
        "resolved_by",
        "resolved_at",
    )
    list_filter = ("resolved", "metric")
    search_fields = ("metric", "warning_code")
    date_hierarchy = "observation_date"
    ordering = ("-observation_date", "metric")
    list_select_related = ("source", "import_run", "resolved_by")
    fields = (
        "observation_date",
        "metric",
        "warning_code",
        "distinct_values",
        "values_summary",
        "source_document_ids",
        "resolved",
        "resolution_note",
        "resolved_by",
        "resolved_at",
    )
    readonly_fields = (
        "observation_date",
        "metric",
        "warning_code",
        "distinct_values",
        "values_summary",
        "source_document_ids",
        "resolved_by",
        "resolved_at",
    )
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        """Same narrow resolution stamp as a data issue, saved the same way."""
        became_resolved = obj.resolved and not obj.resolved_at
        if became_resolved:
            obj.resolved_by = request.user
            obj.resolved_at = timezone.now()
        elif not obj.resolved:
            obj.resolved_by = None
            obj.resolved_at = None
        obj.save(update_fields=sorted(obj.MUTABLE_FIELDS))
        if became_resolved:
            record_event(
                action=AuditAction.MEMBERSHIP_ISSUE_RESOLVED,
                obj=obj,
                actor=request.user,
                change_summary={
                    "source": obj.source.slug,
                    "metric": obj.metric,
                    "observation_date": obj.observation_date.isoformat(),
                    "conflict_id": obj.pk,
                },
            )
