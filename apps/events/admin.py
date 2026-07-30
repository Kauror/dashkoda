"""Read-only admin for imported event snapshots and items."""

from django.contrib import admin

from apps.core.admin import ReadOnlyAdmin

from .models import EventFeedState, EventItem, EventSnapshot


@admin.register(EventSnapshot)
class EventSnapshotAdmin(ReadOnlyAdmin):
    list_display = ("observed_at", "is_current", "item_count", "source", "imported_at")
    list_filter = ("is_current", "source")
    date_hierarchy = "observed_at"
    ordering = ("-observed_at", "-id")
    list_select_related = ("source", "artifact", "import_run")


@admin.register(EventItem)
class EventItemAdmin(ReadOnlyAdmin):
    list_display = ("starts_on", "starts_at", "title", "category", "location", "snapshot")
    list_filter = ("snapshot__is_current", "category", "starts_on")
    search_fields = ("title", "stable_key", "canonical_url", "location")
    ordering = ("starts_on", "title", "stable_key")
    list_select_related = ("snapshot",)


@admin.register(EventFeedState)
class EventFeedStateAdmin(ReadOnlyAdmin):
    list_display = ("source", "last_result", "last_checked_at", "last_successful_sync_at")
    list_filter = ("last_result",)
    list_select_related = ("source", "current_snapshot")
