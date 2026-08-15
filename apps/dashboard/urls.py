from django.urls import path

from .views import admin_area, freshness_fragment, overview

# `haldus/` and **not** `admin/`. `/admin/` is Django's own admin site plus the
# staff data-entry routes registered in front of it by `config/urls.py`, and
# taking that prefix — or redirecting any part of it — would break the workflows
# in `apps/core/data_entry.py`. This is an ordinary viewer-facing dashboard page
# that happens to be addressed to maintainers; it is not a second admin site and
# grants nothing.
urlpatterns = [
    path("", overview, name="home"),
    path("haldus/", admin_area, name="dashboard-admin"),
    path("dashboard/varskus/", freshness_fragment, name="dashboard-freshness"),
]
