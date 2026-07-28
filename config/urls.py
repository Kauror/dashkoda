from django.contrib import admin
from django.urls import include, path

from apps.access.views import login_view, logout_view, robots
from apps.core.views import liveness, readiness

urlpatterns = [
    path("", include("apps.dashboard.urls")),
    path("sisene/", login_view, name="viewer-login"),
    path("logi-valja/", logout_view, name="viewer-logout"),
    path("robots.txt", robots, name="robots"),
    path("admin/", admin.site.urls),
    path("health/live/", liveness, name="health-live"),
    path("health/ready/", readiness, name="health-ready"),
]
