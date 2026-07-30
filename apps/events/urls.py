from django.urls import path

from .views import events_overview

urlpatterns = [
    path("sundmused/", events_overview, name="events"),
]
