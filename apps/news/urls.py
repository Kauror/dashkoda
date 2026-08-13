from django.urls import path

from .views import (
    news_newsletter_search_fragment,
    news_overview,
    news_search_fragment,
    newsletter_history,
    newsletter_history_search_fragment,
)

# The `otsi/` routes answer a keystroke with the results region alone. They are
# ordinary viewer-protected GETs; a reader without JavaScript never reaches them,
# because the forms on the pages above submit to the pages themselves.
#
# The two newsletter routes are news routes because that is where a reader now
# finds the newsletters. What they render still belongs to `apps.visibility` —
# the presenters, the templates and every Smaily query are that app's — and the
# old `/nahtavus/uudiskirjad/` addresses still resolve by redirecting here.
urlpatterns = [
    path("uudised/", news_overview, name="news"),
    path("uudised/otsi/", news_search_fragment, name="news-search"),
    path(
        "uudised/otsi/uudiskirjad/",
        news_newsletter_search_fragment,
        name="news-newsletter-search",
    ),
    path(
        "uudised/uudiskirjad/",
        newsletter_history,
        name="news-newsletter-history",
    ),
    path(
        "uudised/uudiskirjad/otsi/",
        newsletter_history_search_fragment,
        name="news-newsletter-history-search",
    ),
]
