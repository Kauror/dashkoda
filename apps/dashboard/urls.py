from django.urls import path

from .views import freshness_fragment, overview

urlpatterns = [
    path("", overview, name="home"),
    path("dashboard/varskus/", freshness_fragment, name="dashboard-freshness"),
]
