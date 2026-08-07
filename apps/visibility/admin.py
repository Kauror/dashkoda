"""Read-only admin for the visibility models.

Inspection only, and deliberately so. A published observation is immutable, a
correction must supersede rather than overwrite, and every metric in a submission
has to be written in one transaction — none of which a generic change form can
do, and all of which it would happily break. So the admin *shows* what was
published and links out to the purpose-built workflow for anything that writes.

There is no add button and no delete action anywhere on these models. The custom
form owns publication and the correction route owns revision; a `ModelAdmin`
offering either would be a second, weaker way to change history.
"""

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.core.admin import ReadOnlyAdmin

from .models import (
    Ga4FeedState,
    VisibilityEntryBatch,
    VisibilityObservation,
    WebsiteTrafficObservation,
)


class VisibilityObservationInline(admin.TabularInline):
    """What one submission published, on the submission's own page."""

    model = VisibilityObservation
    extra = 0
    can_delete = False
    fields = (
        "metric",
        "value",
        "observation_date",
        "collection_method",
        "is_current_for_date",
        "supersedes",
        "source",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(VisibilityEntryBatch)
class VisibilityEntryBatchAdmin(ReadOnlyAdmin):
    """One row per manual submission, newest first."""

    list_display = (
        "observation_date",
        "metric_count",
        "created_by",
        "created_at",
        "workflow_links",
    )
    list_filter = ("observation_date", "created_by")
    search_fields = ("content_hash", "note")
    date_hierarchy = "observation_date"
    ordering = ("-observation_date", "-id")
    list_select_related = ("created_by",)
    inlines = (VisibilityObservationInline,)
    actions = None

    @admin.display(description="Näitajaid")
    def metric_count(self, obj):
        if obj is None or obj.pk is None:
            return ""
        return obj.observations.count()

    @admin.display(description="Toimingud")
    def workflow_links(self, obj):
        """Links to the read-only detail page and the correction workflow.

        Corrections happen there and only there, so this admin never needs — and
        never offers — an editable field.
        """
        if obj is None or obj.pk is None:
            return ""
        return format_html(
            '<a href="{}">Vaata</a> &middot; <a href="{}">Paranda</a>',
            reverse("visibility-admin-entry-detail", args=[obj.pk]),
            reverse("visibility-admin-entry-correct", args=[obj.pk]),
        )

    def get_readonly_fields(self, request, obj=None):
        # Django's default `get_fields` includes the readonly fields, so naming
        # the computed columns here is enough to have them rendered on the detail
        # page as well as in the list.
        return [*super().get_readonly_fields(request, obj), "metric_count", "workflow_links"]

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["visibility_manual_entry_url"] = reverse("visibility-admin-entry-new")
        extra_context["visibility_entry_history_url"] = reverse("visibility-admin-entry-list")
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(VisibilityObservation)
class VisibilityObservationAdmin(ReadOnlyAdmin):
    """Every observation ever published, including the superseded ones.

    Superseded rows stay listed rather than being filtered away: "what did we
    think the figure was in June" is a question the audit trail has to be able to
    answer.
    """

    list_display = (
        "observation_date",
        "metric",
        "value",
        "collection_method",
        "is_current_for_date",
        "supersedes",
        "source",
        "published_at",
    )
    list_filter = ("metric", "collection_method", "is_current_for_date", "source")
    # Searchable by what identifies an observation, never by a person.
    search_fields = ("metric", "batch__content_hash", "batch__id")
    date_hierarchy = "observation_date"
    ordering = ("-observation_date", "metric", "-id")
    list_select_related = ("source", "batch", "artifact", "import_run", "supersedes")
    actions = None


@admin.register(WebsiteTrafficObservation)
class WebsiteTrafficObservationAdmin(ReadOnlyAdmin):
    """Read-only view of the rows the scheduled `sync_ga4` command publishes.

    Empty until the deployment configures the collector and its first run
    publishes an observation.
    """

    list_display = (
        "period_start",
        "period_end",
        "sessions",
        "active_users",
        "page_views",
        "is_current",
        "imported_at",
    )
    list_filter = ("is_current", "source")
    date_hierarchy = "period_end"
    ordering = ("-period_end", "-id")
    list_select_related = ("source", "artifact", "import_run")
    actions = None


@admin.register(Ga4FeedState)
class Ga4FeedStateAdmin(ReadOnlyAdmin):
    """What the last GA4 collection attempt found.

    The one place an operator can see that the collector ran, when it last
    succeeded, and — sanitized — why it did not. Until GA4 is enabled in
    production there is no row here at all, which is itself the honest answer.
    """

    list_display = (
        "source",
        "last_result",
        "last_period_end",
        "last_checked_at",
        "last_successful_sync_at",
        "last_changed_at",
    )
    list_filter = ("last_result",)
    list_select_related = ("source", "current_observation")
    actions = None
