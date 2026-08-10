"""Smaily: configuration, the normalised shapes, and the read-only collector.

The Chamber sends three newsletters through Smaily, and until now the size of
each list was a number somebody read off Smaily's screen and typed into a form.
This module reads the same numbers through Smaily's HTTP API so they stop going
stale between data-entry sessions.

## Read-only, and structurally so

Every method here issues `GET` and nothing else. There is no `POST`, `PUT`,
`PATCH` or `DELETE` anywhere in this module, and no code path that could build
one: :meth:`SmailyApiClient._get` is the only request function and its method is
a literal. Smaily's API user has no permission model of its own — the credential
that can read a list can also delete one — so the constraint that this
integration cannot write has to be a property of *our* code. It is.

## What is never collected

No email address, no name, no phone number, no subscriber ID, no per-recipient
open, click, bounce or unsubscribe, no IP address and no device identifier. The
two endpoints used return aggregates by construction:

- `GET /api/list.php` — one row per segment: id, name, subscriber count;
- `GET /api/campaign.php` — campaign metadata, and with an `id` the campaign's
  **aggregate** statistics. Smaily returns per-recipient detail only when asked
  with `detailed=1`, which this module never sends and
  :func:`_reject_recipient_detail` refuses to parse if it ever arrives anyway.

## Nothing here logs a credential

The API password is a bearer-equivalent secret. It is read from the environment,
held only in the `Authorization` header, and never written to a log line, an
exception message, an audit summary, an artifact reference or the interface.
Transport errors are replaced with our own sentence for the same reason
`apps.visibility.ga4` does it: a `requests` exception carries the request URL,
and while the URL holds no password it does name the account's subdomain.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

import requests
from django.conf import settings
from django.utils import timezone

#: The canonical payload's schema. Bump when the *meaning* of a stored figure
#: changes: the checksum is what decides "has this day changed", and a silent
#: reinterpretation would read as an unchanged day.
SCHEMA_VERSION = "1.0"

#: Smaily documents 5 requests per second per IP and answers 429 beyond it.
#: A quarter-second floor between requests keeps a backfill comfortably under
#: that without needing the retry path to do the pacing.
MIN_REQUEST_INTERVAL_SECONDS = 0.25

#: How long one request may take. A scheduled job that hangs is worse than one
#: that fails: the next run cannot start while this one holds the lock.
REQUEST_TIMEOUT_SECONDS = 60

#: Bounded, never infinite. Retries cover a rate limit or a transient 5xx and
#: nothing else — a bad credential fails immediately, because retrying it just
#: makes the same mistake four times against an account we do not own.
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (2, 8, 30)
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

#: Campaigns per page. Smaily accepts `limit=0` for "all", which is exactly the
#: unbounded response a scheduled job should not ask for.
CAMPAIGN_PAGE_SIZE = 200

#: A Smaily subdomain is a DNS label. Validated rather than trusted, because it
#: is interpolated into the request host: a value like `x.evil.example/` would
#: otherwise move every request — and the `Authorization` header with it — to
#: somebody else's server.
_SUBDOMAIN_PATTERN = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")

#: The only endpoints this integration knows. A method cannot reach any other
#: path: `_get` looks its argument up here.
ENDPOINT_SEGMENTS = "list.php"
ENDPOINT_CAMPAIGNS = "campaign.php"
_ENDPOINTS = frozenset({ENDPOINT_SEGMENTS, ENDPOINT_CAMPAIGNS})

#: Keys that would mean Smaily had returned recipient-level detail. Present only
#: in a `detailed=1` response, which this module never requests — so seeing one
#: means something changed at Smaily's end, and the correct response is to stop
#: rather than to store it.
_RECIPIENT_DETAIL_KEYS = frozenset({"addresses", "subscribers", "recipients", "contacts"})


class SmailyNotConfigured(RuntimeError):
    """Raised when something needs Smaily configuration that is not present.

    The message names the missing environment variables and **never echoes a
    value**: the subdomain and username are operational detail and the password
    is a secret.
    """


class SmailyResponseError(ValueError):
    """A response this application refuses to read.

    Carries our own sentence, never Smaily's body. On an authentication failure
    the body can quote the request, and a body that unexpectedly contains
    recipient detail must not be echoed into a log while being rejected.
    """


@dataclass(frozen=True)
class SmailyConfiguration:
    """What the environment currently provides. Reading it makes no request."""

    subdomain: str
    username: str
    password: str

    @property
    def is_configured(self) -> bool:
        return bool(self.subdomain and self.username and self.password)

    @property
    def missing(self) -> tuple[str, ...]:
        absent = []
        if not self.subdomain:
            absent.append("SMAILY_SUBDOMAIN")
        if not self.username:
            absent.append("SMAILY_API_USERNAME")
        if not self.password:
            absent.append("SMAILY_API_PASSWORD")
        return tuple(absent)

    @property
    def api_root(self) -> str:
        """The account's API root. HTTPS, and on the account's own host.

        Validating the subdomain here rather than at the request keeps the one
        place a hostname is constructed also the one place it is checked.
        """
        if not _SUBDOMAIN_PATTERN.match(self.subdomain):
            raise SmailyNotConfigured(
                "SMAILY_SUBDOMAIN peab olema lihtne alamdomeeni nimi "
                "(tähed, numbrid ja sidekriips)."
            )
        return f"https://{self.subdomain}.sendsmaily.net/api"

    def require(self) -> SmailyConfiguration:
        """Return self, or explain exactly what an operator must set."""
        if not self.is_configured:
            raise SmailyNotConfigured(
                "Smaily ei ole seadistatud. Puuduvad: " + ", ".join(self.missing)
            )
        return self


def get_configuration() -> SmailyConfiguration:
    return SmailyConfiguration(
        subdomain=(getattr(settings, "SMAILY_SUBDOMAIN", "") or "").strip().lower(),
        username=(getattr(settings, "SMAILY_API_USERNAME", "") or "").strip(),
        password=(getattr(settings, "SMAILY_API_PASSWORD", "") or "").strip(),
    )


@dataclass(frozen=True)
class SmailyConnectionStatus:
    """What the interface may honestly say about Smaily right now.

    `is_connected` requires a published reading, not merely configuration.
    Settings being present says an operator intends to connect it; it does not
    mean a single number has ever been collected.
    """

    configuration: SmailyConfiguration
    has_observation: bool

    @property
    def is_connected(self) -> bool:
        return self.has_observation

    @property
    def message(self) -> str:
        if self.is_connected:
            return "Smaily uudiskirjade andmed on avaldatud."
        return "Smaily ei ole ühendatud."

    @property
    def detail(self) -> str:
        """Viewer-facing, and deliberately free of digits.

        The overview's channel band asserts that an unconnected channel shows no
        number at all, so a stray figure cannot hide among the labels.
        """
        if self.is_connected:
            return ""
        if self.configuration.is_configured:
            return "Seadistus on olemas, kuid ühtegi lugemist ei ole veel kogutud."
        return "Ühendamiseks on vaja Smaily API kasutajat kirjutusõiguseta kasutuses."


# ---------------------------------------------------------------------------
# The normalised shapes
#
# Not model instances: the collector's job ends at a validated, hashable
# description of what Smaily reported, and the publication service turns it into
# rows. Keeping the two apart is what lets the collector be tested without a
# database and replaced without a migration.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentRow:
    """One Smaily segment and how many subscribers it holds.

    `subscribers` is what Smaily reports for the segment. It is not the number
    of messages a send delivered, and the interface must never label it as one.
    """

    segment_id: int
    name: str
    subscribers: int

    def payload(self) -> dict:
        return {
            "segment_id": self.segment_id,
            "name": self.name,
            "subscribers": self.subscribers,
        }


@dataclass(frozen=True)
class SegmentReading:
    """Every segment Smaily reported, at one moment.

    The reading is deliberately *all* segments rather than only the four the
    dashboard maps. A segment that gains a subscriber the Chamber cares about is
    then already in history when somebody asks, and mapping stays a decision the
    registry makes rather than one the collector bakes into what was stored.
    """

    observed_on: date
    segments: tuple[SegmentRow, ...] = ()

    @property
    def has_any_figure(self) -> bool:
        return bool(self.segments)

    def validate(self) -> SegmentReading:
        seen: set[int] = set()
        for row in self.segments:
            if row.subscribers < 0:
                raise SmailyResponseError("Tellijate arv ei saa olla negatiivne.")
            if row.segment_id in seen:
                raise SmailyResponseError("Smaily tagastas sama segmendi kaks korda.")
            seen.add(row.segment_id)
        return self

    def by_id(self) -> dict[int, SegmentRow]:
        return {row.segment_id: row for row in self.segments}

    def canonical_payload(self) -> dict:
        """Deterministic, JSON-ready, and the only thing that is ever hashed.

        Sorted by segment ID, because Smaily is free to return segments in
        whatever order it likes and an ordering difference is not a list that
        changed.
        """
        return {
            "schema": SCHEMA_VERSION,
            "kind": "segments",
            "observed_on": self.observed_on.isoformat(),
            "segments": [
                row.payload() for row in sorted(self.segments, key=lambda r: r.segment_id)
            ],
        }


@dataclass(frozen=True)
class CampaignRow:
    """One completed campaign, as `campaign.php` lists it.

    `template_name` matters more than it looks. Smaily's campaigns carry no tags
    on this account — all two hundred are untagged — and the subject line is
    written for readers, so the template name is the only field that reliably
    says which newsletter an issue belongs to. See
    `apps.visibility.smaily_campaigns`.
    """

    campaign_id: int
    name: str
    template_name: str = ""
    status: str = ""
    created_at: datetime | None = None
    completed_at: datetime | None = None

    def payload(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "template_name": self.template_name,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


@dataclass(frozen=True)
class CampaignStatsRow:
    """One campaign's **aggregate** statistics.

    Every field is a count over the whole send. There is no per-recipient
    anything here and there is no code path that could ask for it: Smaily
    returns recipient detail only for `detailed=1`, which is never sent.

    Smaily also returns `opened_percent`, `click_percent` and `view_percent`.
    None of them is kept. They are quotients of the counts beside them — and
    `opened_percent` is a share of *delivered*, not of *sent*, which is exactly
    the kind of denominator that gets lost when a rounded copy is stored. The
    selectors derive the rates and name their denominator.
    """

    campaign_id: int
    total_count: int | None = None
    delivered_count: int | None = None
    bounce_count: int | None = None
    opened_count: int | None = None
    click_count: int | None = None
    unique_click_count: int | None = None
    view_count: int | None = None
    unique_view_count: int | None = None
    unsubscribe_count: int | None = None
    complaint_count: int | None = None
    forward_count: int | None = None

    COUNT_FIELDS = (
        "total_count",
        "delivered_count",
        "bounce_count",
        "opened_count",
        "click_count",
        "unique_click_count",
        "view_count",
        "unique_view_count",
        "unsubscribe_count",
        "complaint_count",
        "forward_count",
    )

    @property
    def has_any_figure(self) -> bool:
        return any(getattr(self, name) is not None for name in self.COUNT_FIELDS)

    def payload(self) -> dict:
        return {name: getattr(self, name) for name in self.COUNT_FIELDS}


@dataclass
class CollectionCounts:
    """What a collection actually did, for the command's JSON output.

    Aggregates only: request counts and row counts, never a segment or campaign
    list. A scheduler log is not a place to print the Chamber's mailing lists.
    """

    requests: int = 0
    segment_rows: int = 0
    campaign_rows: int = 0
    stats_rows: int = 0
    retries: int = 0

    def merge(self, other: CollectionCounts) -> None:
        self.requests += other.requests
        self.segment_rows += other.segment_rows
        self.campaign_rows += other.campaign_rows
        self.stats_rows += other.stats_rows
        self.retries += other.retries


@runtime_checkable
class SmailyCollector(Protocol):
    """What a collector must offer; :class:`SmailyApiClient` is the real one.

    Deliberately narrow. A collector that also published would put the decision
    "does this replace the published figure?" inside the transport layer, which
    is where it has gone wrong in every system that tried it.
    """

    def collect_segments(self, *, observed_on: date | None = None) -> SegmentReading: ...


def _reject_recipient_detail(payload) -> None:
    """Refuse a response carrying per-recipient data.

    Nothing this module sends can ask for it. If one arrives anyway, Smaily has
    changed what an endpoint returns by default, and storing whatever came back
    would quietly put personal data into a database that has no field for it and
    no lawful basis to hold it. The body is **not** included in the error.
    """
    if isinstance(payload, dict):
        found = _RECIPIENT_DETAIL_KEYS.intersection(payload)
        if found:
            raise SmailyResponseError(
                "Smaily vastus sisaldas saajate tasemel andmeid, mida DashKoda ei küsinud "
                "ega tohi salvestada. Kogumine peatati."
            )


def _as_int(value, field_name: str) -> int:
    """Smaily returns counts as JSON numbers or as decimal strings."""
    if isinstance(value, bool):
        raise SmailyResponseError(f"Väli {field_name} ei ole arv.")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise SmailyResponseError(f"Väli {field_name} ei ole arv.")


class SmailyApiClient:
    """The real collector. One session, paced requests, bounded retries.

    Constructed with a configuration rather than reading settings itself, so a
    test can build one without touching the environment and the command can fail
    with a clear message before any request is attempted.
    """

    def __init__(self, configuration: SmailyConfiguration, *, session=None):
        self.configuration = configuration.require()
        self._root = self.configuration.api_root
        self._session = session if session is not None else requests.Session()
        self._session.auth = (self.configuration.username, self.configuration.password)
        self._last_request_at = 0.0
        self.counts = CollectionCounts()

    # -- transport ---------------------------------------------------------

    def _pace(self) -> None:
        """Hold the documented rate limit without relying on the retry path."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
            time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        self._last_request_at = time.monotonic()

    def _get(self, endpoint: str, **params):
        """The only request this module makes. `GET`, and only to a known path.

        The endpoint is looked up in a fixed set rather than interpolated from a
        caller's string, so no argument can steer a request at another path.
        """
        if endpoint not in _ENDPOINTS:
            raise SmailyResponseError("Tundmatu Smaily lõpp-punkt.")

        url = f"{self._root}/{endpoint}"
        last_status: int | None = None

        for attempt in range(MAX_ATTEMPTS):
            self._pace()
            self.counts.requests += 1
            try:
                response = self._session.get(
                    url,
                    params={k: v for k, v in params.items() if v is not None},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    allow_redirects=False,
                )
            except requests.RequestException as error:
                # Never `str(error)`: it carries the request URL.
                raise SmailyResponseError(
                    f"Smaily päring ebaõnnestus ({type(error).__name__})."
                ) from None

            status = response.status_code
            if status == 200:
                # Smaily answers JSON with a `text/html` content type, so the
                # body is parsed by us rather than trusted by header.
                try:
                    payload = response.json()
                except ValueError:
                    raise SmailyResponseError("Smaily vastus ei olnud loetav JSON.") from None
                _reject_recipient_detail(payload)
                return payload

            last_status = status
            if status in RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
                self.counts.retries += 1
                time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
                continue

            if status in (401, 403):
                raise SmailyResponseError(
                    "Smaily ei võtnud API kasutajat vastu. Kontrolli seadistust."
                )
            break

        raise SmailyResponseError(f"Smaily vastas ootamatu olekuga ({last_status}).")

    # -- collection --------------------------------------------------------

    def collect_segments(self, *, observed_on: date | None = None) -> SegmentReading:
        """Read every segment and its subscriber count. One request."""
        payload = self._get(ENDPOINT_SEGMENTS)
        rows = _segment_rows(payload)
        self.counts.segment_rows += len(rows)
        return SegmentReading(
            observed_on=observed_on or timezone.localdate(),
            segments=rows,
        ).validate()

    def collect_campaigns(self, *, limit: int = CAMPAIGN_PAGE_SIZE) -> tuple[CampaignRow, ...]:
        """List completed campaigns, newest first.

        Only `COMPLETED`. A draft has no statistics and a cancelled campaign was
        never sent; neither belongs in a record of what the Chamber published.

        `limit` is always a number. Smaily accepts `limit=0` for "every campaign
        ever", which is precisely the unbounded response a scheduled job should
        never ask a shared API for.
        """
        if limit < 1:
            raise SmailyResponseError("Kampaaniate piirarv peab olema vähemalt 1.")
        payload = self._get(
            ENDPOINT_CAMPAIGNS,
            status="COMPLETED",
            limit=limit,
            sort_order="DESC",
        )
        rows = _campaign_rows(payload)
        self.counts.campaign_rows += len(rows)
        return rows

    def collect_campaign_stats(self, campaign_id: int) -> CampaignStatsRow:
        """Read one campaign's aggregate statistics. One request.

        **`detailed` is deliberately not passed.** Smaily's default is the
        aggregate form; sending `detailed=1` would return a row per recipient,
        which this application has no field for and no reason to hold. Omitting
        the parameter rather than sending `detailed=0` keeps it impossible for a
        typo to flip.
        """
        payload = self._get(ENDPOINT_CAMPAIGNS, id=int(campaign_id))
        row = _campaign_stats(payload, campaign_id)
        self.counts.stats_rows += 1
        return row


def _segment_rows(payload) -> tuple[SegmentRow, ...]:
    """Normalise `list.php` into rows, refusing anything unrecognisable.

    Smaily has returned a bare list on this account; the dict forms are accepted
    because an API that gains a pagination envelope should not silently start
    reporting that the Chamber has no lists.
    """
    if isinstance(payload, dict):
        payload = payload.get("segments") or payload.get("data") or payload.get("list")
    if not isinstance(payload, list):
        raise SmailyResponseError("Smaily segmentide vastus ei olnud loend.")

    rows = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise SmailyResponseError("Smaily segment ei olnud objekt.")
        if "id" not in entry or "subscribers_count" not in entry:
            raise SmailyResponseError("Smaily segmendil puudus tunnus või tellijate arv.")
        rows.append(
            SegmentRow(
                segment_id=_as_int(entry["id"], "id"),
                name=str(entry.get("name") or "").strip()[:200],
                subscribers=_as_int(entry["subscribers_count"], "subscribers_count"),
            )
        )
    return tuple(rows)


def _as_optional_int(value, field_name: str) -> int | None:
    """A count Smaily may simply not report. Absent stays absent.

    A missing figure is not a measured zero, and the two must stay
    distinguishable for the same reason they do everywhere else here.
    """
    if value is None or value == "":
        return None
    return _as_int(value, field_name)


def _as_datetime(value) -> datetime | None:
    """Smaily's `YYYY-MM-DD HH:MM:SS`, made aware in application time.

    Naive rather than UTC: the timestamps are the Chamber's own send times as
    Smaily's interface shows them, and reading them as UTC would move every
    campaign two or three hours and occasionally onto the previous day.
    """
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
    raise SmailyResponseError("Smaily kuupäev ei olnud loetavas vormingus.")


def _campaign_rows(payload) -> tuple[CampaignRow, ...]:
    """Normalise the campaign list, refusing anything unrecognisable."""
    if isinstance(payload, dict):
        payload = payload.get("campaigns") or payload.get("data") or payload.get("list")
    if not isinstance(payload, list):
        raise SmailyResponseError("Smaily kampaaniate vastus ei olnud loend.")

    rows = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise SmailyResponseError("Smaily kampaania ei olnud objekt.")
        if "id" not in entry:
            raise SmailyResponseError("Smaily kampaanial puudus tunnus.")
        template = entry.get("template")
        template_name = ""
        if isinstance(template, dict):
            template_name = str(template.get("name") or "").strip()
        elif isinstance(template, str):
            template_name = template.strip()
        rows.append(
            CampaignRow(
                campaign_id=_as_int(entry["id"], "id"),
                name=str(entry.get("name") or "").strip()[:300],
                template_name=template_name[:300],
                status=str(entry.get("status") or "").strip()[:20],
                created_at=_as_datetime(entry.get("created_at")),
                completed_at=_as_datetime(entry.get("completed_at")),
            )
        )
    return tuple(rows)


def _campaign_stats(payload, campaign_id: int) -> CampaignStatsRow:
    """Normalise one campaign's aggregate statistics."""
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, dict):
        raise SmailyResponseError("Smaily kampaania statistika ei olnud objekt.")
    # Belt and braces: `_get` already refuses a recipient-detail body, and this
    # is the one endpoint that could ever produce one.
    _reject_recipient_detail(payload)
    return CampaignStatsRow(
        campaign_id=campaign_id,
        **{
            name: _as_optional_int(payload.get(name), name)
            for name in CampaignStatsRow.COUNT_FIELDS
        },
    )


__all__ = [
    "BACKOFF_SECONDS",
    "CAMPAIGN_PAGE_SIZE",
    "ENDPOINT_CAMPAIGNS",
    "ENDPOINT_SEGMENTS",
    "MAX_ATTEMPTS",
    "SCHEMA_VERSION",
    "CampaignRow",
    "CampaignStatsRow",
    "CollectionCounts",
    "SegmentReading",
    "SegmentRow",
    "SmailyApiClient",
    "SmailyCollector",
    "SmailyConfiguration",
    "SmailyConnectionStatus",
    "SmailyNotConfigured",
    "SmailyResponseError",
    "get_configuration",
]
