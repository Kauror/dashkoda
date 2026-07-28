from django.urls import path

from apps.core.views import liveness

urlpatterns = [
    path("health/live/", liveness, name="health-live"),
]
