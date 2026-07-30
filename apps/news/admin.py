"""Read-only admin for imported news snapshots and items."""

from django.contrib import admin

from apps.core.admin import ReadOnlyAdmin

from .models import NewsFeedState, NewsItem, NewsSnapshot


@admin.register(NewsSnapshot)
class NewsSnapshotAdmin(ReadOnlyAdmin):
    list_display = ("observed_at", "is_current", "item_count", "source", "imported_at")
    list_filter = ("is_current", "source")
    date_hierarchy = "observed_at"
    ordering = ("-observed_at", "-id")
    list_select_related = ("source", "artifact", "import_run")


@admin.register(NewsItem)
class NewsItemAdmin(ReadOnlyAdmin):
    list_display = ("published_at", "title", "category", "snapshot")
    list_filter = ("snapshot__is_current", "category", "published_at")
    search_fields = ("title", "guid", "canonical_url", "summary")
    ordering = ("-published_at", "guid")
    list_select_related = ("snapshot",)


@admin.register(NewsFeedState)
class NewsFeedStateAdmin(ReadOnlyAdmin):
    list_display = ("source", "last_result", "last_checked_at", "last_successful_sync_at")
    list_filter = ("last_result",)
    list_select_related = ("source", "current_snapshot")
