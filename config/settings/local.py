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
