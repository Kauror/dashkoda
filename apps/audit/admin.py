from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only view of the audit trail. Nothing may be added or changed here."""

    list_display = ("timestamp", "action", "object_type", "object_id", "actor", "correlation_id")
    list_filter = ("action", "object_type", "timestamp")
    search_fields = ("action", "object_type", "object_id", "correlation_id", "actor__username")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp", "-id")
    list_select_related = ("actor",)

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
