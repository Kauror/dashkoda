from django.urls import path

from .views import (
    campaign_history,
    campaign_history_search_fragment,
    traffic_search_fragment,
    visibility_overview,
)

# The `otsi/` route answers a keystroke with the results region alone. It is an
# ordinary viewer-protected `GET`, so a reader without JavaScript never reaches
# it — the form on the page above submits to the page itself.
#
# The two `uudiskirjad/` routes are kept as redirects rather than deleted. The
# send archive is a news page now, and a board member who bookmarked it here
# should arrive at it with their newsletter, their search term and their page
# intact rather than at a 404. `/nahtavus/otsi/uudiskirjad/` is not kept: it was
# an internal live-search fragment that nothing links to and nobody bookmarks,
# and its whole purpose was to push a `/nahtavus/` URL that is now wrong.
urlpatterns = [
    path("nahtavus/", visibility_overview, name="visibility"),
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
