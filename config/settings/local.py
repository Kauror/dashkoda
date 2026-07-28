import os

from .base import *  # noqa: F403
from .database import postgres_database

DEBUG = True
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dashkoda-local-development-only-not-for-production",
)
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
DATABASES = {"default": postgres_database(persistent_connections=False)}
VIEWER_PIN_HASH = os.environ.get("VIEWER_PIN_HASH", "configure-a-local-pin-hash")
VIEWER_PIN_VERSION = int(os.environ.get("VIEWER_PIN_VERSION", "1"))
VIEWER_RATE_LIMIT_SECRET = os.environ.get(
    "VIEWER_RATE_LIMIT_SECRET",
    "dashkoda-local-only-rate-limit-secret",
)
TRUST_CLOUDFLARE_IP_HEADER = os.environ.get("TRUST_CLOUDFLARE_IP_HEADER", "false").lower() == "true"
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}
# Outside the repository tree's served paths and git-ignored.
SOURCE_ARTIFACT_ROOT = os.environ.get(
    "SOURCE_ARTIFACT_ROOT",
    str(BASE_DIR / ".private-media" / "source-artifacts"),  # noqa: F405
)
