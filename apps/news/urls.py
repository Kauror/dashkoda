from django.urls import path

from .views import (
    legacy_newsletter_history,
    legacy_newsletter_history_search,
    legacy_newsletter_search,
    news_overview,
    news_search_fragment,
)

# The `otsi/` route answers a keystroke with the results region alone. It is an
# ordinary viewer-protected GET; a reader without JavaScript never reaches it,
# because the form on the page above submits to the page itself.
#
# ## The newsletter routes are redirects now
#
# The newsletters were a focus of this page and a page beneath it. They are
# `Otsepostitused` at `/otsepostitused/`, served by `apps.visibility`, which
# owned the models, the collector and every Smaily query all along. Nothing in
# this app renders a newsletter any more.
#
# The three addresses below are kept because they are real bookmarks — one of
# them is where the send archive lived for two moves running. Each redirects to
# the address that now answers it and nothing here redirects to anything in this
# app, so no chain can close into a loop. `/uudised/?fookus=uudiskirjad` is
# handled inside `news_overview` rather than here, because a query parameter is
# not something a URL pattern can match on.
urlpatterns = [
    path("uudised/", news_overview, name="news"),
    path("uudised/otsi/", news_search_fragment, name="news-search"),
    path(
        "uudised/otsi/uudiskirjad/",
        legacy_newsletter_search,
        name="news-newsletter-search",
    ),
    path(
        "uudised/uudiskirjad/",
        legacy_newsletter_history,
        name="news-newsletter-history",
    ),
    path(
        "uudised/uudiskirjad/otsi/",
        legacy_newsletter_history_search,
        name="news-newsletter-history-search",
    ),
]
