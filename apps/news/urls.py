from django.urls import path

from .views import news_overview

urlpatterns = [
    path("uudised/", news_overview, name="news"),
]
