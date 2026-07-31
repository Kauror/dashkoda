"""Routes for the staff-only visibility entry workflow.

Mounted under `/admin/data-entry/visibility/` *before* the admin site's own
patterns, so these are the real routes rather than something buried inside a
model admin's URL space.

Every view is wrapped in `admin.site.admin_view`, which enforces the active-staff
requirement and sends anyone else to the admin login. The viewer PIN middleware
guards `/admin/` in addition, so a viewer must pass both gates and has no staff
account to pass the second one with.
"""

from django.contrib import admin
from django.urls import path

from .admin_views import entry_correct, entry_detail, entry_list, entry_new

urlpatterns = [
    path(
        "",
        admin.site.admin_view(entry_list),
        name="visibility-admin-entry-list",
    ),
    path(
        "new/",
        admin.site.admin_view(entry_new),
        name="visibility-admin-entry-new",
    ),
    path(
        "<int:pk>/correct/",
        admin.site.admin_view(entry_correct),
        name="visibility-admin-entry-correct",
    ),
    path(
        "<int:pk>/",
        admin.site.admin_view(entry_detail),
        name="visibility-admin-entry-detail",
    ),
]
