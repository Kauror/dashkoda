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
