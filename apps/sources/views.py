"""Guarded staff download of a private source artifact.

This is the only way to retrieve an original file. It is reachable only through
the Django admin URL tree, which means a caller must already have passed the
viewer PIN gate and then authenticated as staff. On top of that this view
requires an explicit model permission.

There is no viewer-facing download route, and no public media URL exists.
"""

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404

from .models import AccessLevel, SourceArtifact
from .services import record_artifact_download

DOWNLOAD_PERMISSION = "sources.download_sourceartifact"


def artifact_download(request, pk: int) -> FileResponse:
    if not request.user.has_perm(DOWNLOAD_PERMISSION):
        raise PermissionDenied("Algfaili allalaadimiseks puudub õigus.")

    artifact = SourceArtifact.objects.select_related("source").filter(pk=pk).first()
    if artifact is None:
        raise Http404("Algfaili ei leitud.")

    if artifact.is_external:
        raise Http404("Sellel kirjel ei ole privaatset faili.")

    if artifact.access_level == AccessLevel.RESTRICTED and not request.user.is_superuser:
        raise PermissionDenied("See algfail on piiratud ligipääsuga.")

    record_artifact_download(artifact, actor=request.user)

    response = FileResponse(
        artifact.file.open("rb"),
        as_attachment=True,
        filename=artifact.original_name or f"artifact-{artifact.pk}",
        # Never let a browser sniff or render an original in place.
        content_type="application/octet-stream",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
