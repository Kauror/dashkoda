from django.urls import path

from .views import news_overview, news_search_fragment

# `otsi/` answers a keystroke with the archive rows alone. It is an ordinary
# viewer-protected GET; a reader without JavaScript never reaches it, because the
# form on the page above submits to the page itself.
urlpatterns = [
    path("uudised/", news_overview, name="news"),
    path("uudised/otsi/", news_search_fragment, name="news-search"),
]
