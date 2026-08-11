from django.urls import path

from .views import campaign_history, visibility_overview

urlpatterns = [
    path("nahtavus/", visibility_overview, name="visibility"),
    path(
        "nahtavus/uudiskirjad/",
        campaign_history,
        name="visibility-campaign-history",
    ),
]
