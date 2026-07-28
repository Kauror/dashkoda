import tempfile
from pathlib import Path

from .base import *  # noqa: F403
from .database import postgres_database

DEBUG = False
SECRET_KEY = "dashkoda-tests-only"
ALLOWED_HOSTS = ["testserver"]
DATABASES = {"default": postgres_database(persistent_connections=False)}
VIEWER_PIN_HASH = "test-suite-overrides-this-hash"
VIEWER_PIN_VERSION = 1
VIEWER_RATE_LIMIT_SECRET = "dashkoda-tests-only-rate-limit-secret"
TRUST_CLOUDFLARE_IP_HEADER = False
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}
# A safe default only. The autouse `private_artifact_root` fixture repoints this
# at a per-test temporary directory, so nothing is left behind.
SOURCE_ARTIFACT_ROOT = str(Path(tempfile.gettempdir()) / "dashkoda-test-source-artifacts")

# Synthetic Graph configuration. No test ever reaches Microsoft: the collector
# is always driven through a mocked transport, and these values exist only so
# configuration loading can be exercised.
MS_GRAPH_TENANT_ID = "synthetic-tenant"
MS_GRAPH_CLIENT_ID = "synthetic-client"
MS_GRAPH_CLIENT_SECRET = "synthetic-not-a-real-secret"
OIGUSLOOME_DRIVE_ID = "synthetic-drive"
OIGUSLOOME_ITEM_ID = "synthetic-item"
MS_GRAPH_MAX_ATTEMPTS = 2
MS_GRAPH_TIMEOUT_SECONDS = 1
