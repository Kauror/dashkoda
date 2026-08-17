from django.urls import path

from .views import (
    campaign_history,
    campaign_history_search_fragment,
    koduleht,
    koduleht_search_fragment,
    legacy_visibility,
    mailings,
    mailings_history,
    mailings_history_search_fragment,
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
# The `otsi/` route answers a keystroke with the explorer's results region
# alone. It is an ordinary viewer-protected `GET`, so a reader without
# JavaScript never reaches it — the form on the page submits to the page itself.
# It moved with its page: the old `/nahtavus/otsi/sisu/` and
# `/nahtavus/otsi/uudiskirjad/` addresses are not kept, because both were
# internal fragments nothing links to and nobody bookmarks, and whose whole
# purpose was to push a URL that is now wrong.
#
# ## Otsepostitused
#
# The newsletter section is two routed pages under `/otsepostitused/`: the
# analytics overview and the send archive. It is served from this app because
# this app owns Smaily — the models, the collector, the selectors and the
# segment registry — and the material was only ever *rendered* elsewhere.
#
# The two `nahtavus/uudiskirjad/` routes are kept for the same reason
# `/nahtavus/` is: the send archive has moved twice now, and a saved bookmark
# should arrive rather than 404. They redirect straight to the current address
# rather than through the intermediate `/uudised/uudiskirjad/` one, so no old
# link costs two hops and no chain can close into a loop.
urlpatterns = [
    path("koduleht/", koduleht, name="visibility"),
    path(
        "koduleht/otsi/",
        koduleht_search_fragment,
        name="visibility-traffic-search",
    ),
    path("otsepostitused/", mailings, name="mailings"),
    path("otsepostitused/ajalugu/", mailings_history, name="mailings-history"),
    path(
        "otsepostitused/ajalugu/otsi/",
        mailings_history_search_fragment,
        name="mailings-history-search",
    ),
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
