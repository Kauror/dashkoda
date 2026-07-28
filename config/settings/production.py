from .base import *  # noqa: F403
from .database import postgres_database
from .env import boolean_env, comma_separated_env, positive_int_env, required_env

DEBUG = False
SECRET_KEY = required_env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = comma_separated_env("DJANGO_ALLOWED_HOSTS")
DATABASES = {"default": postgres_database(persistent_connections=True)}
VIEWER_PIN_HASH = required_env("VIEWER_PIN_HASH")
VIEWER_PIN_VERSION = positive_int_env("VIEWER_PIN_VERSION")
VIEWER_RATE_LIMIT_SECRET = required_env("VIEWER_RATE_LIMIT_SECRET")
TRUST_CLOUDFLARE_IP_HEADER = boolean_env("TRUST_CLOUDFLARE_IP_HEADER")
# Required, with no fallback: production must never silently write source
# artifacts to an ephemeral container path or anywhere a static handler serves.
SOURCE_ARTIFACT_ROOT = required_env("SOURCE_ARTIFACT_ROOT")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^health/live/$", r"^health/ready/$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
