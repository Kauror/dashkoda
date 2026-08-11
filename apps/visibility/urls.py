from django.urls import path

from .views import (
    campaign_history,
    campaign_history_search_fragment,
    newsletter_search_fragment,
    traffic_search_fragment,
    visibility_overview,
)

# The `otsi/` routes answer a keystroke with the results region alone. They are
# ordinary viewer-protected `GET`s, so a reader without JavaScript never reaches
# them — the forms on the pages above submit to the pages themselves.
urlpatterns = [
    path("nahtavus/", visibility_overview, name="visibility"),
    path(
        "nahtavus/otsi/uudiskirjad/",
        newsletter_search_fragment,
        name="visibility-newsletter-search",
    ),
    path(
        "nahtavus/otsi/sisu/",
        traffic_search_fragment,
        name="visibility-traffic-search",
    ),
    path(
        "nahtavus/uudiskirjad/",
        campaign_history,
        name="visibility-campaign-history",
    ),
    path(
        "nahtavus/uudiskirjad/otsi/",
        campaign_history_search_fragment,
        name="visibility-campaign-history-search",
    ),
]
