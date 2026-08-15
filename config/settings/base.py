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
    "apps.shop.apps.ShopConfig",
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
                "apps.dashboard.version.build_version",
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

# A workbook feed refuses an import whose row count collapses against the
# snapshot currently on the dashboard: below this fraction of the current count,
# the import fails and the last good data stays published. A generator that
# silently stops producing most of its records is the failure this catches -- it
# has happened, and the smaller dataset was accepted without complaint because
# nothing compared it with what came before.
#
# Growth is never blocked, and neither is a first import. An intended shrink is
# published by passing `--allow-collapse` once; the guard is a question, not a
# ceiling.
FEED_COLLAPSE_MIN_RATIO = 0.5

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

# Aggregate composition of the member roster. A third membership source, and
# again never merged with the other two: it describes what kinds of
# organisations the membership is made of, not how many there are. The roster
# itself holds personal data and is never stored — only counts are.
MEMBERSHIP_COMPOSITION_SOURCE_SLUG = "membership-roster-composition"

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

# Koda.ee "Hetkel käsil" current-topic catalogue.
#
# A fourth public, anonymous, read-only Koda.ee endpoint, collected only by the
# scheduled `sync_legal_current_topics` command. It exists to enrich the
# legal-work records with a public address; it is not a dashboard metric, has no
# viewer page and is deliberately absent from `current_freshness()`.
#
# One fixed endpoint and nothing else. There is no `--url` option, no form and
# no per-run setting: the listing address below and the detail pages it links to
# are the entire collection boundary.
KODA_CURRENT_TOPICS_SOURCE_SLUG = "koda-public-current-topics"
KODA_CURRENT_TOPICS_URL = "https://www.koda.ee/et/meie-moju/hetkel-kasil"

# Every collected detail page must sit under this path. The archive below shares
# the prefix and is excluded by exact match, because it is a listing of finished
# consultations and this phase collects only what is currently open.
KODA_CURRENT_TOPICS_PATH_PREFIX = "/et/meie-moju/hetkel-kasil/"
KODA_CURRENT_TOPICS_ARCHIVE_PATH = "/et/meie-moju/hetkel-kasil/arhiiv"

# The listing is paginated: `?page=N`, eight cards a page, and today it runs to
# two pages. Following the pager is not optional — stopping at the first page
# silently drops the tail of the catalogue.
KODA_CURRENT_TOPICS_MAX_PAGES = 5
# A conservative ceiling on the whole catalogue. A listing beyond this is
# refused rather than truncated: a page that suddenly presents ten times its
# usual size is a site or parsing change, and publishing an arbitrary prefix of
# it would quietly lose records.
KODA_CURRENT_TOPICS_MAX_ITEMS = 50
KODA_CURRENT_TOPICS_MAX_BYTES = 4 * 1024 * 1024

# Detail-page prose ceiling. Long enough that the formal act name, the affected
# parties and the substantive changes all survive into the matcher, short enough
# that no article is reproduced.
KODA_CURRENT_TOPICS_BODY_MAX_LENGTH = 6000

# The Koda.ee "Hetkel käsil" **archive**, used as a fallback source of
# consultation links once a page has left the current listing.
#
# A consultation keeps its canonical URL when it moves from the current listing
# into the archive — verified against the live site — so the fallback continues
# an existing link rather than inventing a new one.
KODA_ARCHIVE_SOURCE_SLUG = "koda-public-archived-topics"
KODA_ARCHIVE_URL = "https://www.koda.ee/et/meie-moju/hetkel-kasil/arhiiv"

# The archive runs to 143 pages of eight entries — about 1140 consultations
# reaching back to 2016. The pager publishes its own last page, so the end is
# read rather than probed; this cap is a guard against a pager that starts
# lying, with generous headroom over the observed size.
KODA_ARCHIVE_MAX_PAGES = 400
KODA_ARCHIVE_MAX_ITEMS = 5000
KODA_ARCHIVE_MAX_BYTES = 4 * 1024 * 1024

# Pacing. The archive backfill is the only place DashKoda makes a long run of
# requests to a third party, so it waits between them. Nothing about the
# dashboard is time-critical, and this keeps a full index walk to a polite few
# minutes.
KODA_ARCHIVE_REQUEST_PAUSE_SECONDS = 0.5

# **How far back detail pages are read.**
#
# The archive listing card carries a day and an abbreviated month and *no year* —
# identically on the newest page and on the page from 2016 — so an entry's real
# date is knowable only from its detail page. Reading all eleven hundred would be
# a thousand requests for consultations no live legal record can still be about.
#
# So hydration walks newest-first and stops once it has seen a page's worth of
# consecutive entries published before the window. One year reaches roughly
# archive page 20, or about 170 entries.
#
# `backfill_complete` means "the index is whole and every entry *inside this
# window* has been read or has definitively failed" — not that all 1140 were
# fetched. Widening the window is a settings change plus more bounded runs; no
# schema changes.
KODA_ARCHIVE_HYDRATION_WINDOW_DAYS = 365
KODA_ARCHIVE_WINDOW_STOP_AFTER_OLDER = 8

# Detail requests one run may make. The operator raises it with
# `--max-detail-pages` for the initial backfill; the daily job needs far less,
# because only newly archived entries are unhydrated.
KODA_ARCHIVE_MAX_DETAIL_PAGES_PER_RUN = 60

# Daily incremental: stop after this many consecutive listing pages whose every
# entry is already known and unchanged. Two pages is sixteen consultations, far
# more than a day ever archives, and the `--full` mode still walks everything.
KODA_ARCHIVE_KNOWN_PAGES_BEFORE_STOP = 2

KODA_ARCHIVE_BODY_MAX_LENGTH = 6000

# The durable catalogue of public Koda.ee **event pages**.
#
# A fifth public, anonymous, read-only Koda.ee endpoint, and a different job
# from `KODA_EVENTS_*` above. That one publishes the upcoming calendar and drops
# events once they finish; this one accumulates event *pages* and keeps them, so
# the event programme — which reaches back to 2018 — can be given a public link
# long after the event happened.
#
# It exists only to attach an address. It never supplies an event's name, date,
# type, delivery mode, tag, service code or inclusion status: the event
# programme workbook remains the sole authority on all of those.
KODA_EVENT_PAGES_SOURCE_SLUG = "koda-public-event-pages"

# Discovery reads the sitemap rather than walking the listing. The listing
# publishes upcoming events only, and `/et/sundmused/arhiiv` is paginated prose;
# the sitemap names every event page directly. Verified against the live site:
# 1,516 event URLs, and all 54 sampled archive entries were present in it.
KODA_EVENT_PAGES_SITEMAP_URL = "https://www.koda.ee/et/sitemap.xml"
KODA_EVENT_PAGES_PATH_PREFIX = "/et/sundmused/"

# The sitemap is an index of child sitemaps. Both are XML and small; the caps
# bound a hostile or broken index long before anything is parsed.
KODA_EVENT_PAGES_SITEMAP_MAX_BYTES = 8 * 1024 * 1024
KODA_EVENT_PAGES_MAX_SITEMAPS = 60
# A sitemap listing far more event URLs than the site has is a source or parsing
# fault. Refused rather than truncated, so a bad read never looks like a crawl.
KODA_EVENT_PAGES_MAX_URLS = 10000

# **Category listings share the event path prefix.** `/et/sundmused/koolitused`
# and `/et/sundmused/liikmeuritused` are category pages, not events. They are
# rejected the same way the calendar collector rejects them — by requiring the
# detail page to actually present `Event` structured data — never by a hardcoded
# list of slugs, which would rot the moment a category is added.
#
# Detail requests one run may make. A full backfill is resumable: resources are
# cumulative, so the next run simply continues with the URLs still unknown.
KODA_EVENT_PAGES_MAX_DETAIL_PAGES_PER_RUN = 150

# How long a known page is trusted before an incremental run re-reads it. Past
# events do not change, so re-reading is about catching corrections on pages for
# events that have not happened yet; the window below is generous for that and
# keeps the daily job small.
KODA_EVENT_PAGES_RECHECK_AFTER_DAYS = 30

# Pacing, as for the archive backfill: this is a long run of requests to a third
# party and nothing about it is time-critical.
KODA_EVENT_PAGES_REQUEST_PAUSE_SECONDS = 0.5

# --------------------------------------------------------------------------
# Chamber opinion documents
# --------------------------------------------------------------------------
#
# The Chamber's outgoing opinion letters, as PDFs. Unlike every other feed
# these are **private**: they are read from a fixed directory on the host, not
# from a URL, and they are never served from a public path. The two roots below
# are the only places the catalogue ever reads from or writes to.
#
# `SOURCE_ROOT` is evidence and is mounted read-only in production. It may hold
# the bootstrap ZIP, loose PDFs, or year folders of PDFs.
#
# `STORE_ROOT` is the managed store DashKoda owns: content-addressed immutable
# blobs, written atomically and never overwritten. A source file disappearing
# never removes a managed blob.
#
# There is deliberately no path or URL option on any command. Both roots are
# configuration, so no operator or viewer input can steer a read or a write.
LEGAL_OPINION_SOURCE_SLUG = "chamber-opinion-documents"
LEGAL_OPINION_SOURCE_ROOT = os.environ.get("LEGAL_OPINION_SOURCE_ROOT", "/data/opinions/source")
LEGAL_OPINION_STORE_ROOT = os.environ.get("LEGAL_OPINION_STORE_ROOT", "/data/opinions/store")
LEGAL_OPINION_BOOTSTRAP_ZIP_NAME = os.environ.get(
    "LEGAL_OPINION_BOOTSTRAP_ZIP_NAME", "Opinions.zip"
)

# Caps. The observed bootstrap catalogue is 759 PDFs, max 1.77 MB and 58 pages,
# so these sit far above real documents while still refusing anything absurd.
LEGAL_OPINION_MAX_PDF_BYTES = 32 * 1024 * 1024
LEGAL_OPINION_MAX_PAGES = 400
LEGAL_OPINION_MAX_SOURCE_ENTRIES = 20000
LEGAL_OPINION_MAX_ZIP_RATIO = 200.0

# Bounded resumable build. Validation, copying and extraction of one document
# are independent, so a run does a slice and the next run continues. The
# snapshot is published only once every manifest entry has a terminal state.
LEGAL_OPINION_MAX_DOCUMENTS_PER_RUN = 250

# Stored text. Enough for matching and later staff search, bounded so one
# 58-page document cannot dominate the table.
LEGAL_OPINION_TEXT_MAX_LENGTH = 40000
LEGAL_OPINION_FIRST_PAGE_MAX_LENGTH = 8000

# `needs_ocr` signals. A document with no extractable text, or implausibly
# little for its page count, is recorded as needing OCR and excluded from
# matching. It is never OCR'd automatically and never rendered to images.
LEGAL_OPINION_MIN_CHARS_PER_PAGE = 120
LEGAL_OPINION_MAX_REPLACEMENT_RATIO = 0.02

# A recurring-source file is only read once it has stopped changing, so a
# half-copied PDF is never hashed into the store.
LEGAL_OPINION_MIN_STABLE_AGE_SECONDS = 60

# --------------------------------------------------------------------------
# Public Koda.ee opinions
# --------------------------------------------------------------------------
#
# The second opinion source: what Koda.ee itself publishes. `Meie arvamus` is
# a filtered view over news nodes — its detail pages live under /et/uudised/ —
# so both listings are walked and the union is the corpus. Attachment PDFs are
# direct file links under /sites/default/files/ and reuse the private opinion
# byte limits, validation and store.
KODA_OPINIONS_SOURCE_SLUG = "koda-public-opinions"
KODA_OPINIONS_MEIE_ARVAMUS_URL = "https://www.koda.ee/et/meie-arvamus"
KODA_OPINIONS_NEWS_URL = "https://www.koda.ee/et/uudised"
# Two detail-page prefixes, both real: recent opinion articles are news nodes
# under /et/uudised/, older ones are meie-arvamus nodes under
# /et/meie-arvamus/. The listing roots themselves are never articles.
KODA_OPINIONS_ARTICLE_PATH_PREFIXES = ("/et/uudised/", "/et/meie-arvamus/")
KODA_OPINIONS_FILE_PATH_PREFIX = "/sites/default/files/"

# The historical window this project activates. Older Koda.ee opinion history
# exists but is deliberately not collected; see docs/legal-opinion-public-source.md.
KODA_OPINIONS_FROM_YEAR = 2025

# Bounds. The `Meie arvamus` view is ~44 pages for its whole history and the
# news listing reaches the 2025 boundary well inside a hundred pages; the cap
# refuses a runaway walk, not a legitimate one.
KODA_OPINIONS_MAX_LISTING_PAGES = 120
KODA_OPINIONS_MAX_HTML_BYTES = 4 * 1024 * 1024
KODA_OPINIONS_BODY_MAX_LENGTH = 6000

# Incremental edge. A daily run reads this many pages of each listing edge and
# re-reads articles published inside the overlap window, because Koda.ee
# attaches the letter a day or two after publishing the article.
KODA_OPINIONS_INCREMENTAL_LISTING_PAGES = 3
KODA_OPINIONS_INCREMENTAL_OVERLAP_DAYS = 14

# Politeness between consecutive requests to the one allowed host.
KODA_OPINIONS_REQUEST_PAUSE_SECONDS = 0.5

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

# Smaily newsletter audiences.
#
# Read only by the scheduled `sync_smaily` command, which reads the size of each
# mailing list. All three settings default to empty and ordinary startup
# provably does not depend on them: the application starts, every page renders
# without them, and the test suite sets none. No page render ever contacts
# Smaily.
#
# `SMAILY_API_PASSWORD` is a bearer-equivalent secret and Smaily's API users
# have no permission model — the credential that can read a list can also delete
# one. It belongs in the server environment only and must never reach Git,
# PostgreSQL, a log line, an audit summary or the interface. That the
# integration cannot write is a property of our code, not of the credential:
# see `apps/visibility/smaily.py`.
SMAILY_SUBDOMAIN = os.environ.get("SMAILY_SUBDOMAIN", "")
SMAILY_API_USERNAME = os.environ.get("SMAILY_API_USERNAME", "")
SMAILY_API_PASSWORD = os.environ.get("SMAILY_API_PASSWORD", "")


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
#
# Every collector already logs what it did — `legal_work.public_sync completed
# rows=… size=…`, `current_topics.sync imported items=…`, `ga4.sync imported
# period_end=…`. Eighteen modules do it, under `dashkoda.*` names.
#
# None of it was reaching anywhere. With no `LOGGING` setting, Django configures
# handlers for `django` and `django.server` and nothing else, so an `INFO` record
# from `dashkoda.legal_work.public_sync` had no handler to reach and was
# discarded. The scheduled jobs write a JSON line through the command's own
# stdout and that was the whole of the operational record; anything a collector
# noticed on the way there was lost.
#
# So: DashKoda's own namespaces log at INFO to stderr, which the container
# runtime and the cron wrappers already capture. Everything else stays where it
# was, because the point is to hear this application, not to turn on every
# library's INFO stream — `urllib3` alone would narrate each connection, and
# `google.auth` narrates credential handling, which is the last thing that
# should become chatty.
#
# What must never appear in a log line is a token, a credential path, a sharing
# URL or file content. That is a property of the call sites, not of this
# configuration, and `tests/core/test_logging.py` checks the ones that matter.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "dashkoda": {
            # The timestamp is the host's; the wrappers add their own Tallinn
            # stamp to the log file, so this one exists to order records within
            # a single run rather than to be read on its own.
            "format": "{asctime} {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "formatter": "dashkoda",
        },
    },
    "loggers": {
        # This application, and only this application, is verbose.
        "dashkoda": {
            "handlers": ["stderr"],
            "level": "INFO",
            # Django's default root handler would otherwise print each record a
            # second time.
            "propagate": False,
        },
        # Chatty at INFO and useful only when something is wrong. `google` covers
        # `google.auth`, which narrates credential handling.
        "urllib3": {"level": "WARNING"},
        "requests": {"level": "WARNING"},
        "google": {"level": "WARNING"},
        "google_auth_httplib2": {"level": "WARNING"},
        "asyncio": {"level": "WARNING"},
    },
}
