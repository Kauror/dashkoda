"""Read-only admin for membership observations.

Inspection only: observations are written by the collector and never edited.
There is no individual-member admin because no member record exists, and no
remote-refresh action because collection belongs to the scheduled command.
"""

from django.contrib import admin

from apps.core.admin import ReadOnlyAdmin

from .models import MembershipCountObservation, MembershipFeedState


@admin.register(MembershipCountObservation)
class MembershipCountObservationAdmin(ReadOnlyAdmin):
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
