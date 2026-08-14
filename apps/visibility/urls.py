from django.urls import path

from .views import (
    campaign_history,
    campaign_history_search_fragment,
    koduleht,
    legacy_visibility,
)

# The website surface is `Koduleht` at `/koduleht/` now. The route keeps the name
# `visibility`, so every `reverse("visibility")` in the application — the
# navigation, the overview's channel cards, the tests — resolves to the new
# canonical address without a single caller changing. Product naming and
# internal module naming do not have to match, and renaming the Django app,
# its migration namespace and its model labels to follow a page title would be a
# large change bought with nothing.
#
# `/nahtavus/` is kept as a redirect rather than deleted. A board member who
# bookmarked the website page should arrive at it with their period and their
# section intact rather than at a 404.
#
# The two `uudiskirjad/` routes are kept for the same reason: the send archive
# is a news page now. `/nahtavus/otsi/sisu/` and `/nahtavus/otsi/uudiskirjad/`
# are **not** kept — both were internal live-search fragments that nothing links
# to and nobody bookmarks, and whose whole purpose was to push a `/nahtavus/`
# URL that is now wrong.
urlpatterns = [
    path("koduleht/", koduleht, name="visibility"),
    path("nahtavus/", legacy_visibility, name="visibility-legacy"),
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
