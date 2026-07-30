"""Read-only admin for imported legal-work data.

Everything here is inspection only. Snapshots and rows are written by the
importer and never edited by hand, so no add, change or delete action is
offered. The feed state shows a sanitized diagnostic and never a secret.
"""

from django.contrib import admin

from apps.core.admin import ReadOnlyAdmin

from .models import LegalWorkFeedState, LegalWorkItem, LegalWorkSnapshot


@admin.register(LegalWorkSnapshot)
class LegalWorkSnapshotAdmin(ReadOnlyAdmin):
    list_display = (
        "reporting_date",
        "is_current",
        "total_record_count",
        "open_record_count",
        "sent_record_count",
        "warning_record_count",
        "imported_at",
        "schema_version",
    )
    list_filter = ("is_current", "schema_version", "reporting_date")
    search_fields = ("schema_version",)
    date_hierarchy = "imported_at"
    ordering = ("-imported_at", "-id")
    list_select_related = ("source", "artifact", "import_run")


@admin.register(LegalWorkItem)
class LegalWorkItemAdmin(ReadOnlyAdmin):
    """No lawyer column exists here, because the model has no such field."""

    list_display = (
        "record_id",
        "topic",
        "act_type",
        "received_date",
        "deadline_date",
        "sent_date",
        "sent_status",
        "stage",
        "is_open",
    )
    list_filter = (
        "snapshot__is_current",
        "is_open",
        "sent_status",
        "source_year",
        "received_date",
        "sent_date",
    )
    search_fields = ("topic", "recipient", "record_id")
    ordering = ("-received_date", "topic", "record_id")
    list_select_related = ("snapshot",)


@admin.register(LegalWorkFeedState)
class LegalWorkFeedStateAdmin(ReadOnlyAdmin):
    list_display = (
        "source",
        "last_result",
        "last_checked_at",
        "last_successful_sync_at",
        "last_changed_at",
    )
    list_filter = ("last_result",)
    list_select_related = ("source", "current_snapshot")
