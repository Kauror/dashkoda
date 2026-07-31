"""Routes for the staff-only membership report workflow.

Mounted under `/admin/membership/` *before* the admin site's own patterns, so
the brief's route is the real route rather than something buried inside a model
admin's URL space.

Every view is wrapped in `admin.site.admin_view`, which is what enforces the
staff requirement and sends anyone else to the admin login. The viewer PIN
middleware guards `/admin/` in addition, so a viewer must pass both gates and
has no staff account to pass the second one with.
"""

from django.contrib import admin
from django.urls import path

from .admin_views import manual_report_correct, manual_report_detail, manual_report_new

urlpatterns = [
    path(
        "internal-report/new/",
        admin.site.admin_view(manual_report_new),
        name="membership-admin-report-new",
    ),
    path(
        "internal-report/<int:pk>/correct/",
        admin.site.admin_view(manual_report_correct),
        name="membership-admin-report-correct",
    ),
    path(
        "internal-report/<int:pk>/",
        admin.site.admin_view(manual_report_detail),
        name="membership-admin-report-detail",
    ),
]
