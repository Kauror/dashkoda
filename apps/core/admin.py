"""Shared admin base classes.

Imported datasets are inspected in the admin, never edited there, so every
domain app registers its models through this one read-only base instead of
carrying its own copy.
"""

from django.contrib import admin


class ReadOnlyAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
