from django.urls import path

from .opinion_views import opinion_document, opinion_resource
from .views import legal_work_overview

urlpatterns = [
    path("oigusloome/", legal_work_overview, name="legal-work"),
    # Opaque identifiers only. Neither route carries a filename, a storage key,
    # a digest or a database id, and neither accepts a path.
    path(
        "oigusloome/arvamused/<uuid:public_id>/",
        opinion_resource,
        name="opinion-resource",
    ),
    path(
        "oigusloome/arvamused/dokument/<uuid:public_id>/",
        opinion_document,
        name="opinion-document",
    ),
]
