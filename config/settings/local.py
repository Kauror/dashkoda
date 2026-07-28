import os

from .base import *  # noqa: F403

DEBUG = True
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dashkoda-local-development-only-not-for-production",
)
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
