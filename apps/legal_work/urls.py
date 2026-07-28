from django.urls import path

from .views import legal_work_overview

urlpatterns = [
    path("oigusloome/", legal_work_overview, name="legal-work"),
]
