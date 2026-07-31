from django.contrib import admin
from django.urls import include, path

from apps.access.views import login_view, logout_view, robots
from apps.core.views import liveness, readiness

urlpatterns = [
    path("", include("apps.dashboard.urls")),
    path("", include("apps.legal_work.urls")),
    path("", include("apps.membership.urls")),
    path("", include("apps.news.urls")),
    path("", include("apps.events.urls")),
    path("sisene/", login_view, name="viewer-login"),
    path("logi-valja/", logout_view, name="viewer-logout"),
    path("robots.txt", robots, name="robots"),
    path("", include("apps.visibility.urls")),
    # Listed before the admin site so the staff data-entry routes keep their own
    # addresses instead of living inside a model admin's URL space. Every one of
    # them is wrapped in `admin.site.admin_view`, so the staff requirement is
    # identical to the rest of `/admin/`, and the viewer PIN middleware guards
    # `/admin/` in front of all of it.
    path("admin/membership/", include("apps.membership.admin_urls")),
    path("admin/data-entry/visibility/", include("apps.visibility.admin_urls")),
    path("admin/data-entry/", include("apps.core.admin_urls")),
    path("admin/", admin.site.urls),
    path("health/live/", liveness, name="health-live"),
    path("health/ready/", readiness, name="health-ready"),
]
