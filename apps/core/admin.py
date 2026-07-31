"""Shared admin base classes, and the admin index override.

Imported datasets are inspected in the admin, never edited there, so every
domain app registers its models through this one read-only base instead of
carrying its own copy.
"""

from django.contrib import admin

# The admin index gets one panel pointing at `/admin/data-entry/`, because the
# model app list answers "what tables exist" and a staff user arriving to type in
# this month's figures is asking something else.
#
# Pointing `index_template` at a differently-named template is what lets that
# template `{% extends "admin/index.html" %}` and keep Django's app list through
# `{{ block.super }}`; a project-level `admin/index.html` would shadow the
# original and have nothing left to extend.
admin.site.index_template = "core/admin/index.html"


class ReadOnlyAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
