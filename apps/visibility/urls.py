from django.urls import path

from .views import visibility_overview

urlpatterns = [
    path("nahtavus/", visibility_overview, name="visibility"),
]
