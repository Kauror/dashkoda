"""Google Analytics: configuration, the normalised shapes, and the collector.

The only automated visibility source. A **read-only** service account
(`analytics.readonly`) reads completed reporting days from the GA4 Data API;
`apps.visibility.ga4_sync` is the only caller, and publication follows the path
every other source uses — canonical JSON → SHA-256 → metadata-only
`SourceArtifact` → `ImportRun` → immutable rows → audit event. No GA4 response
body is retained.

## Why a range, not a day

The first version asked for one day at a time. Five years is about 1 800 days,
and 1 800 HTTP requests to fill a chart is not a backfill strategy — it is a
quota incident. `date` is a GA4 *dimension*, so one request can return a row per
day for a whole month, and the collector is built around that: a chunk of dates
in, a bundle per date out.

Three reports rather than one, because GA4 refuses some dimension/metric pairs
and because their cardinalities differ by two orders of magnitude:

- **site** — `date` × six metrics. One row per day;
- **pages** — `date` × `pagePath`. Roughly a hundred rows per day on this
  property, so a month is a few thousand and needs pagination;
- **channels** — `date` × `sessionDefaultChannelGroup`. About a dozen a day.

## What is never collected

No `clientId`, no `userId`, no IP address, no demographic breakdown and no
individual anything. Every row is an aggregate over a whole reporting day.

Nothing in this module logs, stores or returns a property ID, a credential path,
an access token or a Google response body.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from django.conf import settings
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from .ga4_paths import UNKNOWN, canonical_path
from .registry import SOURCE_GA4

#: The canonical payload's schema. Bump when the *meaning* of a stored figure
#: changes, because the checksum is what decides "has this day changed" and a
#: silent reinterpretation would read as an unchanged day.
SCHEMA_VERSION = "2.0"

#: Rows per request. GA4 caps `limit` at 250 000; a lower ceiling keeps any one
#: response small enough to parse without holding a month of pages in memory
#: twice, and pagination handles the rest.
PAGE_SIZE = 50_000

#: How long one request may take. A scheduled job that hangs is worse than one
#: that fails: the next run cannot start while this one holds the lock.
REQUEST_TIMEOUT_SECONDS = 120

#: Bounded, never infinite. Retries cover a rate limit or a transient 5xx and
#: nothing else; a credential or a bad request fails immediately, because
#: retrying either just makes the same mistake more often.
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (2, 8, 30)
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

API_ROOT = "https://analyticsdata.googleapis.com/v1beta"

#: Verified against the property before being written down here, not recalled
#: from memory: every name below returned data in a live `runReport`.
SITE_METRICS = (
    "sessions",
    "activeUsers",
    "newUsers",
    "screenPageViews",
    "engagedSessions",
    "userEngagementDuration",
)
PAGE_METRICS = ("screenPageViews", "activeUsers", "userEngagementDuration")
CHANNEL_METRICS = ("sessions", "engagedSessions")


class Ga4NotConfigured(RuntimeError):
    """Raised when something needs GA4 configuration that is not present.

    The message names what is missing and **never echoes a value**: a property
    ID is not a secret, but a credentials path is operational detail and the
    file it points at certainly is.
    """


class Ga4ResponseError(ValueError):
    """A response this application refuses to read.

    Carries our own sentence, never Google's body — the body can contain the
    property ID and, on an auth failure, parts of the credential.
    """


@dataclass(frozen=True)
class Ga4Configuration:
    """What the environment currently provides. Reading it makes no request."""

    property_id: str
    credentials_file: str

    @property
    def is_configured(self) -> bool:
        return bool(self.property_id and self.credentials_file)

    @property
    def missing(self) -> tuple[str, ...]:
        absent = []
        if not self.property_id:
            absent.append("GA4_PROPERTY_ID")
        if not self.credentials_file:
            absent.append("GA4_CREDENTIALS_FILE")
        return tuple(absent)

    def require(self) -> Ga4Configuration:
        """Return self, or explain exactly what an operator must set."""
        if not self.is_configured:
            raise Ga4NotConfigured(
                "Google Analytics ei ole seadistatud. Puuduvad: " + ", ".join(self.missing)
            )
        return self


@dataclass(frozen=True)
class Ga4ConnectionStatus:
    """What the interface may honestly say about GA4 right now.

    `is_connected` requires a published day, not merely configuration. Settings
    being present says an operator intends to connect it; it does not mean a
    single number has ever been collected, and the card must not imply otherwise.
    """

    configuration: Ga4Configuration
    has_observation: bool

    @property
    def is_connected(self) -> bool:
        return self.has_observation

    @property
    def message(self) -> str:
        if self.is_connected:
            return "Google Analytics andmed on avaldatud."
        return "Google Analytics ei ole ühendatud."

    @property
    def detail(self) -> str:
        """Viewer-facing, and deliberately free of digits.

        This string reaches the overview's channel band, and
        `tests/dashboard/test_overview.py` asserts that with nothing connected
        the page contains no digit at all — so that a stray figure can never
        hide among the labels. "GA4" would contribute a `4` and defeat the check
        without meaning anything to a board member, so the property is named in
        full.
        """
        if self.is_connected:
            return ""
        if self.configuration.is_configured:
            return "Seadistus on olemas, kuid ühtegi vaatlust ei ole veel kogutud."
        return (
            "Ühendamiseks on vaja Google Analyticsi property ID-d ja "
            "kirjutusõiguseta teenusekontot."
        )


# ---------------------------------------------------------------------------
# The normalised shapes
#
# Not model instances: the collector's job ends at a validated, hashable
# description of a reporting day, and the publication service turns it into
# rows. Keeping the two apart is what lets the collector be tested without a
# database and replaced without a migration.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageRow:
    """One page's traffic on one day."""

    path: str
    page_views: int
    active_users: int | None = None
    user_engagement_seconds: int | None = None
    #: Only when GA4's own path differed from the canonical one.
    raw_path: str = ""

    def payload(self) -> dict:
        return {
            "path": self.path,
            "page_views": self.page_views,
            "active_users": self.active_users,
            "user_engagement_seconds": self.user_engagement_seconds,
        }


@dataclass(frozen=True)
class ChannelRow:
    """One acquisition channel's sessions on one day."""

    channel: str
    sessions: int
    engaged_sessions: int | None = None

    def payload(self) -> dict:
        return {
            "channel": self.channel,
            "sessions": self.sessions,
            "engaged_sessions": self.engaged_sessions,
        }


@dataclass(frozen=True)
class DayReading:
    """Everything collected about one reporting day.

    Every site figure is optional because a reporting API that omits a metric
    has not reported zero, and this application does not invent the difference.

    `has_page_detail` is not `bool(pages)`. A day with no page rows because the
    site had no traffic and a day whose page report was never requested look
    identical in the data and mean opposite things; the flag keeps them apart,
    which is what lets a backfill add page detail to a day that already has
    site totals without pretending the earlier run measured zero pages.
    """

    report_date: date
    sessions: int | None = None
    active_users: int | None = None
    new_users: int | None = None
    page_views: int | None = None
    engaged_sessions: int | None = None
    user_engagement_seconds: int | None = None
    pages: tuple[PageRow, ...] = ()
    channels: tuple[ChannelRow, ...] = ()
    has_page_detail: bool = False
    has_channel_detail: bool = False

    SITE_FIELDS = (
        "sessions",
        "active_users",
        "new_users",
        "page_views",
        "engaged_sessions",
        "user_engagement_seconds",
    )

    @property
    def has_any_figure(self) -> bool:
        """Whether GA4 reported anything at all for this day."""
        return any(getattr(self, name) is not None for name in self.SITE_FIELDS)

    def validate(self) -> DayReading:
        for name in self.SITE_FIELDS:
            value = getattr(self, name)
            if value is not None and value < 0:
                raise Ga4ResponseError(f"{name} ei saa olla negatiivne.")
        for row in self.pages:
            if row.page_views < 0:
                raise Ga4ResponseError("Lehevaatamiste arv ei saa olla negatiivne.")
        for row in self.channels:
            if row.sessions < 0:
                raise Ga4ResponseError("Seansside arv ei saa olla negatiivne.")
        return self

    def canonical_payload(self) -> dict:
        """Deterministic, JSON-ready, and the only thing that is ever hashed.

        Sorted by key everywhere a set of rows appears, because Google is free
        to return pages in whatever order it likes and an ordering difference is
        not a day that changed. Absent stays `null` rather than becoming `0`,
        for the same reason it stays absent in the database.
        """
        return {
            "schema": SCHEMA_VERSION,
            "source": SOURCE_GA4,
            "report_date": self.report_date.isoformat(),
            "site": {name: getattr(self, name) for name in self.SITE_FIELDS},
            "has_page_detail": self.has_page_detail,
            "has_channel_detail": self.has_channel_detail,
            "pages": [row.payload() for row in sorted(self.pages, key=lambda r: r.path)],
            "channels": [row.payload() for row in sorted(self.channels, key=lambda r: r.channel)],
        }


@dataclass
class CollectionCounts:
    """What a collection actually did, for the command's JSON output.

    Aggregates only: request counts and row counts, never a page list. A
    scheduler log is not a place to print a thousand URLs.
    """

    requests: int = 0
    site_rows: int = 0
    page_rows: int = 0
    channel_rows: int = 0
    retries: int = 0

    def merge(self, other: CollectionCounts) -> None:
        self.requests += other.requests
        self.site_rows += other.site_rows
        self.page_rows += other.page_rows
        self.channel_rows += other.channel_rows
        self.retries += other.retries


@dataclass
class RangeCollection:
    """The bundle a chunk of dates produces."""

    days: dict[date, DayReading] = field(default_factory=dict)
    counts: CollectionCounts = field(default_factory=CollectionCounts)


@runtime_checkable
class Ga4Collector(Protocol):
    """What a collector must offer; :class:`Ga4ApiCollector` is the real one.

    Deliberately narrow: a date range in, normalised readings out. A collector
    that also published would put the decision "does this replace the published
    figure?" inside the transport layer, which is where it has gone wrong in
    every system that tried it.
    """

    def collect_range(
        self,
        *,
        start: date,
        end: date,
        with_pages: bool = True,
        with_channels: bool = True,
    ) -> RangeCollection: ...


class Ga4ApiCollector:
    """Read a bounded range of completed reporting days from the Data API."""

    SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

    def __init__(self, configuration: Ga4Configuration, *, session=None, sleep=time.sleep):
        self.configuration = configuration.require()
        self._session = session
        self._sleep = sleep

    # -- transport ------------------------------------------------------

    @property
    def session(self):
        if self._session is None:
            credentials = service_account.Credentials.from_service_account_file(
                self.configuration.credentials_file, scopes=[self.SCOPE]
            )
            self._session = AuthorizedSession(credentials)
        return self._session

    def _run_report(self, body: dict, counts: CollectionCounts) -> dict:
        """One `runReport`, retried only where retrying can help.

        A 429 or a 5xx is worth waiting out. A 400 means this application asked
        for something GA4 will never answer, and a 401/403 means the credential
        is wrong — repeating either is a way of turning one clear failure into
        four identical ones several minutes apart.
        """
        url = f"{API_ROOT}/properties/{self.configuration.property_id}:runReport"
        last_error: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            counts.requests += 1
            try:
                response = self.session.post(url, json=body, timeout=REQUEST_TIMEOUT_SECONDS)
            except Exception as error:  # noqa: BLE001 - transport, retried below
                last_error = Ga4ResponseError("Google Analyticsi päring ei jõudnud kohale.")
                last_error.__cause__ = error
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as error:
                        raise Ga4ResponseError(
                            "Google Analytics vastas millegagi, mis ei ole JSON."
                        ) from error
                if response.status_code not in RETRY_STATUS:
                    # Deliberately not `response.text`: the body names the
                    # property and, on an auth failure, part of the credential.
                    raise Ga4ResponseError(
                        f"Google Analytics keeldus päringust (HTTP {response.status_code})."
                    )
                last_error = Ga4ResponseError(
                    f"Google Analytics ei vastanud (HTTP {response.status_code})."
                )

            if attempt < MAX_ATTEMPTS - 1:
                counts.retries += 1
                self._sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])

        raise last_error or Ga4ResponseError("Google Analyticsi päring ebaõnnestus.")

    def _paged_rows(self, body: dict, counts: CollectionCounts) -> list[dict]:
        """Every row of a report, following GA4's offset pagination.

        `rowCount` is the total the query matches; `rows` is the page. Looping
        until they meet is the whole contract, and the bound is arithmetic
        rather than a `while True`: a response that never advanced would
        otherwise spin forever against a live quota.
        """
        collected: list[dict] = []
        offset = 0
        while True:
            page = dict(body, limit=PAGE_SIZE, offset=offset)
            document = self._run_report(page, counts)
            rows = _rows(document)
            collected.extend(rows)
            total = _row_count(document)
            offset += len(rows)
            if not rows or offset >= total:
                return collected

    # -- reports --------------------------------------------------------

    def collect_range(
        self,
        *,
        start: date,
        end: date,
        with_pages: bool = True,
        with_channels: bool = True,
    ) -> RangeCollection:
        """Every completed day in `[start, end]`, in as few requests as GA4 allows."""
        if end < start:
            raise ValueError("Vahemiku lõpp ei saa olla enne algust.")

        collection = RangeCollection()
        date_range = [{"startDate": start.isoformat(), "endDate": end.isoformat()}]

        site_rows = self._paged_rows(
            {
                "dateRanges": date_range,
                "dimensions": [{"name": "date"}],
                "metrics": [{"name": name} for name in SITE_METRICS],
            },
            collection.counts,
        )
        collection.counts.site_rows = len(site_rows)

        site: dict[date, dict] = {}
        for row in site_rows:
            day = _row_date(row, 0)
            numbers = _metrics(row, len(SITE_METRICS))
            site[day] = dict(zip(DayReading.SITE_FIELDS, numbers, strict=True))

        pages: dict[date, list[PageRow]] = {}
        if with_pages:
            page_rows = self._paged_rows(
                {
                    "dateRanges": date_range,
                    "dimensions": [{"name": "date"}, {"name": "pagePath"}],
                    "metrics": [{"name": name} for name in PAGE_METRICS],
                },
                collection.counts,
            )
            collection.counts.page_rows = len(page_rows)
            for row in page_rows:
                day = _row_date(row, 0)
                raw = _dimension(row, 1)
                path = canonical_path(raw)
                if path == UNKNOWN:
                    # A row this application cannot name is not a page it can
                    # attribute traffic to. Dropped rather than filed under a
                    # placeholder that would later be summed with real pages.
                    continue
                views, users, seconds = _metrics(row, len(PAGE_METRICS))
                pages.setdefault(day, []).append(
                    PageRow(
                        path=path,
                        page_views=views or 0,
                        active_users=users,
                        user_engagement_seconds=seconds,
                        raw_path=raw if raw != path else "",
                    )
                )

        channels: dict[date, list[ChannelRow]] = {}
        if with_channels:
            channel_rows = self._paged_rows(
                {
                    "dateRanges": date_range,
                    "dimensions": [{"name": "date"}, {"name": "sessionDefaultChannelGroup"}],
                    "metrics": [{"name": name} for name in CHANNEL_METRICS],
                },
                collection.counts,
            )
            collection.counts.channel_rows = len(channel_rows)
            for row in channel_rows:
                day = _row_date(row, 0)
                name = _dimension(row, 1) or "Unassigned"
                sessions, engaged = _metrics(row, len(CHANNEL_METRICS))
                channels.setdefault(day, []).append(
                    ChannelRow(channel=name, sessions=sessions or 0, engaged_sessions=engaged)
                )

        for day in _each_day(start, end):
            figures = site.get(day, {})
            collection.days[day] = DayReading(
                report_date=day,
                pages=_merge_pages(pages.get(day, ())),
                channels=tuple(channels.get(day, ())),
                has_page_detail=with_pages,
                has_channel_detail=with_channels,
                **figures,
            ).validate()

        return collection


# ---------------------------------------------------------------------------
# Reading one `runReport` document. Separated from the request so every shape
# the API can answer with is reachable in a test without a credential.
# ---------------------------------------------------------------------------


def _rows(document) -> list[dict]:
    if not isinstance(document, dict):
        raise Ga4ResponseError("Google Analytics vastus ei olnud ootuspärane dokument.")
    rows = document.get("rows") or []
    if not isinstance(rows, list):
        raise Ga4ResponseError("Google Analytics tagastas ootamatu ridade kuju.")
    return rows


def _row_count(document) -> int:
    """How many rows the query matches in total.

    Absent on a response with no rows, which is not an error: a quiet day
    genuinely matches nothing.
    """
    try:
        return int(document.get("rowCount") or 0)
    except (TypeError, ValueError) as error:
        raise Ga4ResponseError("Google Analytics tagastas loetamatu ridade arvu.") from error


def _dimension(row, index: int) -> str:
    values = row.get("dimensionValues") if isinstance(row, dict) else None
    if not isinstance(values, list) or len(values) <= index:
        raise Ga4ResponseError("Google Analytics tagastas rea ilma nõutud mõõtmeta.")
    value = values[index]
    if not isinstance(value, dict) or "value" not in value:
        raise Ga4ResponseError("Google Analytics tagastas mõõtme ilma väärtuseta.")
    return str(value["value"])


def _row_date(row, index: int) -> date:
    raw = _dimension(row, index)
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except (IndexError, ValueError) as error:
        raise Ga4ResponseError("Google Analytics tagastas kuupäeva, mida ei saa lugeda.") from error


def _metrics(row, count: int) -> tuple[int | None, ...]:
    """The row's metric values as integers, in order.

    GA4 returns every metric as a string, and a rate as a float string. Only
    counts and whole seconds are read here, so a non-integral value is truncated
    towards zero rather than refused — `userEngagementDuration` legitimately
    arrives as `286086.0`.
    """
    values = row.get("metricValues") if isinstance(row, dict) else None
    if not isinstance(values, list):
        raise Ga4ResponseError("Google Analytics tagastas ootamatu näitajate kuju.")
    if len(values) != count:
        raise Ga4ResponseError("Google Analytics ei tagastanud nõutud näitajaid.")

    numbers: list[int | None] = []
    for item in values:
        if not isinstance(item, dict) or "value" not in item:
            raise Ga4ResponseError("Google Analytics tagastas näitaja ilma väärtuseta.")
        raw = item["value"]
        if raw is None or raw == "":
            numbers.append(None)
            continue
        try:
            numbers.append(int(float(raw)))
        except (TypeError, ValueError) as error:
            raise Ga4ResponseError(
                "Google Analytics tagastas veebistatistika väärtuse, mis ei ole arv."
            ) from error
    return tuple(numbers)


def _merge_pages(rows: Iterable[PageRow]) -> tuple[PageRow, ...]:
    """Fold rows that canonicalise to the same path into one.

    GA4 reports `/x`, `/x/` and `/x?utm_source=y` as separate rows; canonical
    identity says they are one page, so their views have to be added rather than
    stored as three rows that violate the per-snapshot uniqueness of a path.

    Additive metrics are summed. `active_users` is **not**: the same person may
    appear under two spellings of one URL, so adding them would overstate the
    audience. The larger is kept, which is the tightest lower bound available
    without asking GA4 a second question.
    """
    merged: dict[str, PageRow] = {}
    for row in rows:
        existing = merged.get(row.path)
        if existing is None:
            merged[row.path] = row
            continue
        merged[row.path] = PageRow(
            path=row.path,
            page_views=existing.page_views + row.page_views,
            active_users=_max_or_none(existing.active_users, row.active_users),
            user_engagement_seconds=_sum_or_none(
                existing.user_engagement_seconds, row.user_engagement_seconds
            ),
            raw_path=existing.raw_path or row.raw_path,
        )
    return tuple(sorted(merged.values(), key=lambda row: row.path))


def _sum_or_none(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def _max_or_none(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _each_day(start: date, end: date) -> Sequence[date]:
    from datetime import timedelta

    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(span + 1)]


def get_configuration() -> Ga4Configuration:
    """Read the two optional settings. Makes no request and touches no file."""
    return Ga4Configuration(
        property_id=getattr(settings, "GA4_PROPERTY_ID", "") or "",
        credentials_file=getattr(settings, "GA4_CREDENTIALS_FILE", "") or "",
    )


def get_connection_status() -> Ga4ConnectionStatus:
    """Whether the website card may show anything yet.

    One indexed existence check. Imported locally so this module stays importable
    without the app registry — a settings check must not depend on models.
    """
    from .models import Ga4DailySnapshot

    return Ga4ConnectionStatus(
        configuration=get_configuration(),
        has_observation=Ga4DailySnapshot.objects.filter(is_current_for_date=True).exists(),
    )


__all__ = [
    "CHANNEL_METRICS",
    "PAGE_METRICS",
    "SCHEMA_VERSION",
    "SITE_METRICS",
    "ChannelRow",
    "CollectionCounts",
    "DayReading",
    "Ga4ApiCollector",
    "Ga4Collector",
    "Ga4Configuration",
    "Ga4ConnectionStatus",
    "Ga4NotConfigured",
    "Ga4ResponseError",
    "PageRow",
    "RangeCollection",
    "get_configuration",
    "get_connection_status",
]
