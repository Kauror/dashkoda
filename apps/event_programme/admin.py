"""Read-only admin for the imported event programme.

Inspection only, through the project's shared `ReadOnlyAdmin`. Imported rows are
immutable by construction, so an editable admin could not save them anyway — and
offering the form would suggest a correction belongs here.

There is deliberately **no URL editor**. Whether an event has a public koda.ee
page is a decision made in `DASH_URL_OVERRIDES` in the Chamber's operational
workbook and exported to DashKoda; adding a second place to decide it would give
the dashboard an opinion the workbook could not see.
"""

from django.contrib import admin

from apps.core.admin import ReadOnlyAdmin

from .models import EventProgrammeFeedState, EventProgrammeItem, EventProgrammeSnapshot


@admin.register(EventProgrammeSnapshot)
class EventProgrammeSnapshotAdmin(ReadOnlyAdmin):
    list_display = (
        "export_refreshed_at",
        "is_current",
        "canonical_event_count",
        "dated_event_count",
        "linked_public_url_count",
        "review_required_count",
        "schema_version",
        "imported_at",
    )
    list_filter = ("is_current", "source", "schema_version")
    date_hierarchy = "export_refreshed_at"
    ordering = ("-export_refreshed_at", "-id")
    list_select_related = ("source", "artifact", "import_run")


@admin.register(EventProgrammeItem)
class EventProgrammeItemAdmin(ReadOnlyAdmin):
    list_display = (
        "start_date",
        "end_date",
        "event_name",
        "service_code",
        "tag_label",
        "event_type_label",
        "delivery_mode",
        "event_status",
        "price_status",
        "added_date",
        "planning_lead_days",
        "has_public_url",
        "review_required",
    )
    list_filter = (
        "snapshot__is_current",
        "event_status",
        "event_year",
        "tag_key",
        "event_type_key",
        "delivery_mode",
        "include_status",
        "price_status",
        "public_link_status",
        "review_required",
    )
    search_fields = ("event_name", "service_code", "event_id")
    ordering = ("-start_date", "event_name", "event_id")
    list_select_related = ("snapshot",)

    @admin.display(boolean=True, description="Avalik viide")
    def has_public_url(self, obj) -> bool:
        return bool(obj.public_url)


@admin.register(EventProgrammeFeedState)
class EventProgrammeFeedStateAdmin(ReadOnlyAdmin):
    list_display = (
        "source",
        "last_result",
        "last_checked_at",
        "last_successful_sync_at",
        "last_changed_at",
        "current_snapshot",
    )
    list_filter = ("last_result",)
    list_select_related = ("source", "current_snapshot")
