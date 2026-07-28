from .base import *  # noqa: F403
from .database import postgres_database

DEBUG = False
SECRET_KEY = "dashkoda-tests-only"
ALLOWED_HOSTS = ["testserver"]
DATABASES = {"default": postgres_database(persistent_connections=False)}
