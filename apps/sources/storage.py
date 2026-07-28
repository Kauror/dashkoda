"""Private storage for original source files.

Source artifacts are never public. They live under ``SOURCE_ARTIFACT_ROOT``,
which is outside ``STATIC_ROOT`` and outside every ``STATICFILES_DIRS`` entry,
so WhiteNoise cannot reach them and no URL route exposes them. The only way to
retrieve one is the permission-guarded staff download in ``views.py``.
"""

import os
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.files.storage import FileSystemStorage


class PrivateArtifactStorage(FileSystemStorage):
    """File storage rooted at ``SOURCE_ARTIFACT_ROOT`` with no public URL.

    ``location`` is a plain property rather than the inherited cached one so a
    test can point the root at a temporary directory through
    ``settings.SOURCE_ARTIFACT_ROOT`` without a stale cache.
    """

    @property
    def base_location(self):
        return settings.SOURCE_ARTIFACT_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None

    def url(self, name):
        raise SuspiciousFileOperation(
            "Source artifacts have no public URL. Use the staff download view."
        )


def artifact_upload_path(instance, filename: str) -> str:
    """Build the stored path.

    The client-supplied filename is never used as a path. Only its extension
    survives, checked against the allowlist and lowercased; the name itself is a
    random UUID. The original name is kept as ordinary metadata on the model.
    """
    extension = Path(filename).suffix.lower()
    allowed = settings.SOURCE_ARTIFACT_ALLOWED_EXTENSIONS
    if extension not in allowed:
        raise ValidationError(
            f"Faili laiend ei ole lubatud: {extension or '(puudub)'}. "
            f"Lubatud: {', '.join(sorted(allowed))}."
        )
    return f"sources/{instance.source_id}/{uuid.uuid4().hex}{extension}"
