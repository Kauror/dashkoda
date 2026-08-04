import os
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
    "apps.legal_work.apps.LegalWorkConfig",
    "apps.membership.apps.MembershipConfig",
    "apps.news.apps.NewsConfig",
    "apps.events.apps.EventsConfig",
    "apps.event_programme.apps.EventProgrammeConfig",
    "apps.visibility.apps.VisibilityConfig",
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

# Legal-work feed.
#
# One recurring collection route: the public read-only sharing link. The
# Microsoft Graph route was retired without ever completing live acceptance, so
# no Entra application, client secret or drive/item identifier is configured
# here any more.
#
# The sharing URL is intentionally optional and blank by default: the web
# application must start, and CI must run, without it. Only the synchronisation
# command requires configuration, and it fails with an explicit message naming
# what is missing — never echoing the value.
LEGAL_WORK_SOURCE_SLUG = "oigusloome-onedrive"

# View-only OneDrive/SharePoint sharing URL for the canonical workbook. Treat
# it as a bearer-style secret: anyone holding it can download the file. It
# belongs only in the deployment environment, never in Git, the database, the
# logs, the audit trail or the interface. Required only by
# `sync_oigusloome_public`.
OIGUSLOOME_PUBLIC_URL = os.environ.get("OIGUSLOOME_PUBLIC_URL", "")
LEGAL_WORK_MAX_DOWNLOAD_BYTES = SOURCE_ARTIFACT_MAX_BYTES

# The Chamber's own event programme, prepared from the operational service-code
# workbook by an Office Script and a scheduled Power Automate flow. DashKoda
# only consumes the result, read-only, over one view-only sharing link.
#
# The same secret handling as the legal-work URL above: bearer-style, deployment
# environment only, required by `sync_event_programme` and by nothing that runs
# during ordinary startup.
EVENT_PROGRAMME_SOURCE_SLUG = "sundmuste-programm-onedrive"
EVENT_PROGRAMME_PUBLIC_URL = os.environ.get("EVENT_PROGRAMME_PUBLIC_URL", "")
EVENT_PROGRAMME_MAX_DOWNLOAD_BYTES = SOURCE_ARTIFACT_MAX_BYTES

# Public Koda.ee feeds.
#
# Three anonymous, read-only public endpoints. No credential exists for any of
# them, so nothing here is a secret and the URLs are ordinary configuration —
# they are stable public canonical addresses, not signed or transient ones.
#
# Collection happens only in `sync_koda_public`. A page render never touches
# these.
KODA_ALLOWED_HOSTS = frozenset({"www.koda.ee", "koda.ee"})

KODA_MEMBERS_SOURCE_SLUG = "koda-public-members"

# The Chamber's own board-report membership history. A separate source from the
# public directory above, deliberately: the two count different things and are
# never merged into one series. Nothing is collected remotely for it — the
# history arrives once as an approved package and every later report is entered
# by a staff user through the admin form.
MEMBERSHIP_INTERNAL_SOURCE_SLUG = "membership-internal-board-reports"

# Ceilings for the one-time historical package. The real package is well under
# half a megabyte; these bound a hostile or corrupt archive long before it is
# parsed, and are checked against the declared *and* the extracted size.
MEMBERSHIP_HISTORY_MAX_PACKAGE_BYTES = 25 * 1024 * 1024
MEMBERSHIP_HISTORY_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MEMBERSHIP_HISTORY_MAX_MEMBER_BYTES = 25 * 1024 * 1024
MEMBERSHIP_HISTORY_MAX_MEMBERS = 64

KODA_NEWS_SOURCE_SLUG = "koda-public-news"
KODA_EVENTS_SOURCE_SLUG = "koda-public-events"

KODA_MEMBERS_URL = "https://www.koda.ee/api/v1/company-list"
KODA_NEWS_URL = "https://www.koda.ee/et/news/feed.xml"
KODA_EVENTS_URL = "https://www.koda.ee/et/sundmused"

# Response caps. The member list is by far the largest of the three; the others
# are small documents and a much lower ceiling is appropriate.
KODA_MEMBERS_MAX_BYTES = 8 * 1024 * 1024
KODA_NEWS_MAX_BYTES = 4 * 1024 * 1024
KODA_EVENTS_MAX_BYTES = 4 * 1024 * 1024

# Membership change guard. A published directory does not lose or gain a large
# fraction of its members overnight, so a movement beyond *both* thresholds is
# treated as a source or parsing fault rather than as news, and the previous
# observation is kept. Both must be exceeded: the absolute floor stops a tiny
# directory tripping the proportional rule, and the proportional rule stops a
# large directory tripping the absolute one.
KODA_MEMBERS_MAX_CHANGE_RATIO = 0.15
KODA_MEMBERS_MAX_CHANGE_ABSOLUTE = 200

# How many items each feed publishes into a snapshot.
KODA_NEWS_MAX_ITEMS = 30
KODA_EVENTS_MAX_ITEMS = 30
KODA_EVENTS_TARGET_ITEMS = 20
KODA_EVENTS_MAX_PAGES = 5
# Detail pages are fetched one per candidate event, so this bounds the whole run.
KODA_EVENTS_MAX_DETAIL_FETCHES = 40

# Plain-text summary ceiling. Long enough to be useful in a list, short enough
# that no article is reproduced.
KODA_SUMMARY_MAX_LENGTH = 400

# A publication timestamp beyond this much clock skew is not a real publication
# date, so the item is refused rather than allowed to pin itself to the top.
KODA_NEWS_MAX_FUTURE_DAYS = 2

# Google Analytics 4.
#
# Read only by the scheduled `sync_ga4` command, which collects one completed
# day of aggregate website traffic. Both settings default to empty and ordinary
# startup provably does not depend on them: the application starts, every page
# renders without either, and the test suite sets neither. No page render ever
# contacts Google.
#
# `GA4_CREDENTIALS_FILE` points at a read-only service-account key mounted
# into the deployment. That file is a credential: it belongs in the server
# environment only and must never reach Git, PostgreSQL, a log line, an audit
# summary or the interface. See `apps/visibility/ga4.py`.
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID", "")
GA4_CREDENTIALS_FILE = os.environ.get("GA4_CREDENTIALS_FILE", "")
