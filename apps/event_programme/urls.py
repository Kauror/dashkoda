from django.urls import path

from .views import event_programme_overview, programme_search_fragment

urlpatterns = [
    # The route and its name are unchanged. The page's source changed, its
    # address did not, so every existing link and bookmark still works and the
    # sidebar keeps one Sündmused entry rather than gaining a second.
    path("sundmused/", event_programme_overview, name="events"),
    # Answers a keystroke or a select change with the rows alone. An ordinary
    # viewer-protected GET; a reader without JavaScript never reaches it,
    # because the form above submits to the page itself.
    path("sundmused/otsi/", programme_search_fragment, name="event-programme-search"),
]
