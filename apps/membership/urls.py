from django.urls import path

from .views import membership_overview

urlpatterns = [
    path("liikmeskond/", membership_overview, name="membership"),
]
