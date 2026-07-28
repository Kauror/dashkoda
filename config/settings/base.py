from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core.apps.CoreConfig",
    "apps.access.apps.AccessConfig",
    "apps.dashboard.apps.DashboardConfig",
    "apps.sources.apps.SourcesConfig",
    "apps.audit.apps.AuditConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.access.middleware.SecurityHeadersMiddleware",
    "apps.access.middleware.ViewerAccessMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

LANGUAGE_CODE = "et"
TIME_ZONE = "Europe/Tallinn"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# `static/build/` holds the compiled frontend bundle; it is produced by
# `npm run build` and is never edited or committed by hand.
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_AGE = 60 * 60 * 24 * 7
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

VIEWER_SESSION_AUTHENTICATED_KEY = "viewer_authenticated"
VIEWER_SESSION_VERSION_KEY = "viewer_pin_version"

# Private source artifacts.
#
# `SOURCE_ARTIFACT_ROOT` is defined per environment and must never point inside
# `STATIC_ROOT` or any `STATICFILES_DIRS` entry: WhiteNoise must not be able to
# reach an original file, and there is no media URL at all.
#
# Uploads are stored, never parsed. The allowlist is limited to the inert
# document and data formats this project actually receives; anything
# executable, archived or scriptable is rejected.
SOURCE_ARTIFACT_MAX_BYTES = 25 * 1024 * 1024
SOURCE_ARTIFACT_ALLOWED_EXTENSIONS = frozenset(
    {
        ".csv",
        ".tsv",
        ".txt",
        ".json",
        ".xml",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
    }
)
