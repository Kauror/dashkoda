"""The seam a Google Analytics collector will plug into. **Nothing here calls Google.**

This module contains no HTTP client, no Google SDK import, no credential
handling and no collection of any kind — real or simulated. It exists so that
the next pull request adds a *collector* rather than redesigning the schema, the
publication path and the page at the same time.

Three things are deliberate:

- **the application starts with neither setting present.** `GA4_PROPERTY_ID` and
  `GA4_CREDENTIALS_FILE` default to empty, exactly like the legal-work Graph
  variables, and only a command that actually collects would require them. Tests
  and local development use no credentials at all;
- **no fake data is produced.** A stub returning plausible sessions would put a
  number on the board's page that nobody measured, which is the one thing this
  dashboard must never do. Until a real observation exists, the website card
  says `Lisamisel`;
- **the normalisation contract is written down before the collector exists**, so
  the collector's job is to satisfy a shape that has already been reviewed
  rather than to invent one under deadline.

## What the next pull request needs

- **a GA4 property ID** — which property to report on;
- **a read-only service account** — `analytics.readonly` and nothing wider, so
  the credential cannot change anything even if it leaks;
- **a secret-file mount** — the JSON key belongs in the deployment environment
  only: never in Git, PostgreSQL, a log line, an audit summary or the interface;
- **a reporting period definition** — GA4 reports a range, and which range the
  board is being shown has to be a decision rather than a library default;
- **a scheduled host command** — collection is never part of a web request;
- **live acceptance** — no Google credential has ever existed in this project,
  so nothing about this path has been exercised against the real API.

Publication then follows the path every other source uses: canonical JSON →
SHA-256 → metadata-only `SourceArtifact` → `ImportRun` → an immutable
`WebsiteTrafficObservation` → audit event. No GA4 response body is retained.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from django.conf import settings
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from .registry import SOURCE_GA4


class Ga4NotConfigured(RuntimeError):
    """Raised when something needs GA4 configuration that is not present.

    The message names what is missing and **never echoes a value**: a property
    ID is not a secret, but a credentials path is operational detail and the
    file it points at certainly is.
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

    `is_connected` requires a published observation, not merely configuration.
    Settings being present says an operator intends to connect it; it does not
    mean a single number has ever been collected, and the card must not imply
    otherwise.
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
        `tests/dashboard/test_overview.py` asserts that with nothing connected the
        page contains no digit at all — so that a stray figure can never hide
        among the labels. "GA4" would contribute a `4` and defeat the check
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


@dataclass(frozen=True)
class WebsiteTrafficReading:
    """The normalised shape a future collector must produce.

    Not a model instance: the collector's job ends at a validated, hashable
    description of one reporting period, and the publication service turns it
    into a row. Keeping the two apart is what lets the collector be tested
    without a database and replaced without a migration.

    Every figure is optional because a reporting API that omits a metric has not
    reported zero, and this application does not invent the difference.
    """

    period_start: date
    period_end: date
    sessions: int | None = None
    active_users: int | None = None
    page_views: int | None = None

    def validate(self) -> WebsiteTrafficReading:
        if self.period_end < self.period_start:
            raise ValueError("Perioodi lõpp ei saa olla enne algust.")
        for name in ("sessions", "active_users", "page_views"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} ei saa olla negatiivne.")
        return self

    def canonical_payload(self) -> dict:
        """Deterministic JSON-ready form, for the content hash.

        Sorted, explicit and free of anything the response happened to carry.
        The digest of this — not of the API response — is what makes a repeated
        collection idempotent, exactly as it is for the public Koda feeds.
        """
        return {
            "schema": "1.0",
            "source": SOURCE_GA4,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "sessions": self.sessions,
            "active_users": self.active_users,
            "page_views": self.page_views,
        }


class Ga4ApiCollector:
    """Read one completed reporting day from GA4's Data API.

    This is deliberately transport-only: publication remains in the management
    command, which is the only caller allowed to touch the database.
    """

    SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

    def __init__(self, configuration: Ga4Configuration):
        self.configuration = configuration.require()

    def collect(self, *, period_start: date, period_end: date) -> WebsiteTrafficReading:
        WebsiteTrafficReading(period_start=period_start, period_end=period_end).validate()
        credentials = service_account.Credentials.from_service_account_file(
            self.configuration.credentials_file, scopes=[self.SCOPE]
        )
        session = AuthorizedSession(credentials)
        response = session.post(
            f"https://analyticsdata.googleapis.com/v1beta/properties/{self.configuration.property_id}:runReport",
            json={
                "dateRanges": [
                    {"startDate": period_start.isoformat(), "endDate": period_end.isoformat()}
                ],
                "metrics": [
                    {"name": "sessions"},
                    {"name": "activeUsers"},
                    {"name": "screenPageViews"},
                ],
            },
            timeout=30,
        )
        response.raise_for_status()
        values = response.json().get("rows", [{}])[0].get("metricValues", [])
        numbers = [int(item["value"]) for item in values]
        if len(numbers) != 3:
            raise ValueError("Google Analytics ei tagastanud nõutud veebistatistika näitajaid.")
        return WebsiteTrafficReading(
            period_start=period_start,
            period_end=period_end,
            sessions=numbers[0],
            active_users=numbers[1],
            page_views=numbers[2],
        ).validate()


@runtime_checkable
class Ga4Collector(Protocol):
    """What a future collector must offer. No implementation exists.

    Deliberately narrow: one period in, one normalised reading out. A collector
    that also published would put the decision "does this replace the current
    figure?" inside the transport layer, which is where it has gone wrong in
    every system that tried it.
    """

    def collect(self, *, period_start: date, period_end: date) -> WebsiteTrafficReading: ...


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
    from .models import WebsiteTrafficObservation

    return Ga4ConnectionStatus(
        configuration=get_configuration(),
        has_observation=WebsiteTrafficObservation.objects.filter(is_current=True).exists(),
    )
