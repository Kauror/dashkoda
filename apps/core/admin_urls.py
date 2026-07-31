"""The `/admin/data-entry/` hub route.

Mounted before the admin site's own patterns so it keeps its own address rather
than living inside a model admin's URL space, and wrapped in
`admin.site.admin_view`, which is what enforces the active-staff requirement and
sends anyone else to the admin login. The viewer PIN middleware guards `/admin/`
in addition, so both gates apply.
"""

from django.contrib import admin
from django.urls import path

from .admin_views import data_entry_hub

urlpatterns = [
    path("", admin.site.admin_view(data_entry_hub), name="data-entry-hub"),
]
