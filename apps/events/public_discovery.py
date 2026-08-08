"""Find Koda.ee event pages and keep what was found.

Different job from `collector.py`, which reads the listing at `/et/sundmused`
and publishes the upcoming calendar. That listing shows only what has not
happened yet, so it can never reach the 2018–2024 pages the event programme
needs a link for.

**Discovery reads the sitemap instead.** The index at `/et/sitemap.xml` names
child sitemaps; between them they name every event page directly, including
finished ones. Measured against the live site: 1,516 URLs under
`/et/sundmused/`, and all 54 sampled `/et/sundmused/arhiiv` entries were among
them. The archive listing is therefore a cross-check, not a second crawl.

**Category listings share the event prefix.** `/et/sundmused/koolitused` and
`/et/sundmused/liikmeuritused` are category pages. They are rejected the way the
calendar collector rejects them — by requiring the page to actually present
`Event` structured data — and never by a list of known slugs, which would rot
the moment the site adds a category.

Two properties this module is built around:

**Nothing is ever removed.** A page that 404s today, or that a run simply did
not reach, keeps its row. Discovery only adds and re-observes. That is what
makes a partial run safe and a full backfill resumable: the next run continues
with the URLs still unknown, because "already known" is a database fact rather
than a position in a crawl.

**A run that could not finish says so.** Hitting the per-run detail cap, or
failing to read a child sitemap, sets `is_complete=False` on the snapshot with a
warning code. A partial crawl never passes as complete history.

Nothing here writes an event-programme field. The output is an address and the
facts needed to match it; the workbook remains the authority on what an event
is.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

from apps.core.canonical import canonical_checksum
from apps.core.public_http import PublicFetchError, fetch

from .collector import (
    EventCollectionError,
    is_koda_url,
    parse_event_detail,
    stable_key_for_url,
)
from .public_models import DiscoveryMode, DiscoveryOrigin, PublicEventResource

logger = logging.getLogger("dashkoda.events.public_discovery")

SITEMAP_CONTENT_TYPES = frozenset({"application/xml", "text/xml"})
HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})

_LOC = re.compile(rb"<loc>\s*([^<\s][^<]*?)\s*</loc>", re.IGNORECASE)

# Warning codes. Recorded on the snapshot so an operator can tell a slow
# backfill from a broken one without reading the log.
WARN_DETAIL_CAP = "detail-cap-reached"
WARN_SITEMAP_UNREADABLE = "sitemap-unreadable"
WARN_DETAIL_FAILED = "detail-fetch-failed"


class PublicEventDiscoveryError(RuntimeError):
    """Discovery could not run at all."""


@dataclass
class DiscoveryTally:
    """What one run did. Becomes the snapshot's counters."""

    mode: str
    pages_fetched: int = 0
    urls_seen: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0
    is_complete: bool = True
    warnings: list[str] = field(default_factory=list)

    def warn(self, code: str) -> None:
        if code not in self.warnings:
            self.warnings.append(code)

    def as_dict(self) -> dict:
        """The run's numbers, for a scheduler's log line.

        Counts and flags only. No URL, no title and no page text ever reaches
        this, because it is written to an unattended log an operator greps.
        """
        return {
            "mode": self.mode,
            "pages_fetched": self.pages_fetched,
            "urls_seen": self.urls_seen,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "errors": self.errors,
            "is_complete": self.is_complete,
            "warning_codes": list(self.warnings),
        }


def _content_checksum(
    *,
    title: str,
    starts_on: dt.date,
    ends_on: dt.date | None,
    category: str,
    location: str,
) -> str:
    """Digest the fields a reader would notice changing.

    Deliberately excludes `last_seen_at` and anything about the fetch: two
    observations of an unchanged page must produce the same digest, or every run
    would look like a change.
    """
    digest, _ = canonical_checksum(
        {
            "title": title,
            "starts_on": starts_on.isoformat(),
            "ends_on": ends_on.isoformat() if ends_on else "",
            "category": category,
            "location": location,
        }
    )
    return digest


def _fetch(url: str, *, session, max_bytes: int, accept: str, types: frozenset[str]):
    try:
        return fetch(
            url,
            allowed_hosts=settings.KODA_ALLOWED_HOSTS,
            accept=accept,
            max_bytes=max_bytes,
            expected_content_types=types,
            session=session,
        )
    except PublicFetchError as error:
        raise EventCollectionError(str(error)) from error


def _locations(body: bytes) -> list[str]:
    return [match.group(1).decode("utf-8", errors="replace") for match in _LOC.finditer(body)]


def _is_event_page(url: str) -> bool:
    """Whether a sitemap URL is a candidate event page.

    Cheap structural filter only — it accepts category listings too. What
    actually decides is `parse_event_detail` refusing a page without `Event`
    structured data.
    """
    if not is_koda_url(url):
        return False
    path = urlparse(url).path
    prefix = settings.KODA_EVENT_PAGES_PATH_PREFIX
    if not path.startswith(prefix):
        return False
    # One trailing segment. Deeper paths under the prefix are pagers and
    # filtered views, not event pages.
    return len([segment for segment in path[len(prefix) :].split("/") if segment]) == 1


def sitemap_event_urls(*, session=None, tally: DiscoveryTally) -> list[str]:
    """Every candidate event URL the sitemap names, in sitemap order.

    A child sitemap that cannot be read is counted and skipped rather than
    aborting the run — one unreadable child must not cost the other 1,500 URLs —
    but it does mark the run incomplete.
    """
    index = _fetch(
        settings.KODA_EVENT_PAGES_SITEMAP_URL,
        session=session,
        max_bytes=settings.KODA_EVENT_PAGES_SITEMAP_MAX_BYTES,
        accept="application/xml",
        types=SITEMAP_CONTENT_TYPES,
    )
    tally.pages_fetched += 1

    children = [url for url in _locations(index.content) if is_koda_url(url)]
    if len(children) > settings.KODA_EVENT_PAGES_MAX_SITEMAPS:
        raise EventCollectionError(
            f"Saidikaardi indeks viitab {len(children)} alamkaardile, "
            f"lubatud on {settings.KODA_EVENT_PAGES_MAX_SITEMAPS}."
        )

    seen: dict[str, None] = {}
    for child in children:
        try:
            body = _fetch(
                child,
                session=session,
                max_bytes=settings.KODA_EVENT_PAGES_SITEMAP_MAX_BYTES,
                accept="application/xml",
                types=SITEMAP_CONTENT_TYPES,
            )
        except EventCollectionError as error:
            tally.errors += 1
            tally.is_complete = False
            tally.warn(WARN_SITEMAP_UNREADABLE)
            logger.warning("sitemap child unreadable: %s (%s)", urlparse(child).path, error)
            continue
        tally.pages_fetched += 1
        for url in _locations(body.content):
            if _is_event_page(url) and url not in seen:
                seen[url] = None
        if len(seen) > settings.KODA_EVENT_PAGES_MAX_URLS:
            raise EventCollectionError(
                f"Saidikaart nimetab üle {settings.KODA_EVENT_PAGES_MAX_URLS} sündmuse lehe."
            )
        _pause()

    tally.urls_seen = len(seen)
    return list(seen)


def _pause() -> None:
    delay = settings.KODA_EVENT_PAGES_REQUEST_PAUSE_SECONDS
    if delay > 0:
        time.sleep(delay)


def _due_for_recheck(resource: PublicEventResource, *, now: dt.datetime) -> bool:
    window = dt.timedelta(days=settings.KODA_EVENT_PAGES_RECHECK_AFTER_DAYS)
    return resource.last_seen_at <= now - window


def _select(urls: list[str], *, mode: str, now: dt.datetime) -> tuple[list[str], int]:
    """Split the candidate URLs into what to fetch and what to leave alone.

    A full run reads everything. An incremental run reads what it has never seen
    plus what has gone stale, which on a settled catalogue is almost nothing.
    """
    known = {
        resource.canonical_url: resource
        for resource in PublicEventResource.objects.filter(canonical_url__in=urls)
    }
    if mode == DiscoveryMode.FULL:
        return urls, 0

    wanted = [url for url in urls if url not in known or _due_for_recheck(known[url], now=now)]
    return wanted, len(urls) - len(wanted)


def _observe(
    url: str,
    *,
    html: str,
    origin: str,
    now: dt.datetime,
    tally: DiscoveryTally,
    dry_run: bool = False,
) -> None:
    """Record one page, creating or re-observing its resource.

    Under `dry_run` the page is still parsed and still counted — that is the
    point of the flag, to say what a real run would do — but nothing is written.
    """
    parsed = parse_event_detail(html, url)
    if parsed is None:
        # A category listing or some other page under the prefix. Not an error:
        # the structural filter was never meant to be exact.
        return
    starts_on, ends_on, _starts_at, _ends_at, title, location = parsed
    if not title:
        # A page with dates and no name cannot be matched against a programme
        # row and would only add noise to the catalogue.
        return

    # Category is left blank on purpose. The listing card carries one and the
    # detail page's structured data does not, so a sitemap-discovered page has
    # no honest value for it — and inventing one would be exactly the kind of
    # Koda.ee-sourced business field the programme workbook owns.
    checksum = _content_checksum(
        title=title, starts_on=starts_on, ends_on=ends_on, category="", location=location
    )
    existing = PublicEventResource.objects.filter(canonical_url=url).first()

    if dry_run:
        if existing is None:
            tally.created += 1
        elif existing.content_checksum == checksum:
            tally.unchanged += 1
        else:
            tally.updated += 1
        return

    if existing is None:
        PublicEventResource.objects.create(
            canonical_url=url,
            stable_key=stable_key_for_url(url),
            title=title,
            starts_on=starts_on,
            ends_on=ends_on,
            category="",
            location=location,
            discovered_from=origin,
            content_checksum=checksum,
            last_seen_at=now,
            last_changed_at=now,
        )
        tally.created += 1
        return

    if existing.content_checksum == checksum:
        existing.last_seen_at = now
        existing.save(update_fields=["last_seen_at"])
        tally.unchanged += 1
        return

    existing.title = title
    existing.starts_on = starts_on
    existing.ends_on = ends_on
    existing.location = location
    existing.content_checksum = checksum
    existing.last_seen_at = now
    existing.last_changed_at = now
    existing.save(
        update_fields=[
            "title",
            "starts_on",
            "ends_on",
            "location",
            "content_checksum",
            "last_seen_at",
            "last_changed_at",
        ]
    )
    tally.updated += 1


def discover_public_events(
    *,
    mode: str = DiscoveryMode.INCREMENTAL,
    max_detail_pages: int | None = None,
    dry_run: bool = False,
    session=None,
    urls=None,
) -> DiscoveryTally:
    """Walk the sitemap and record every event page it names.

    `urls` exists for tests and replays: given a list, the sitemap is not
    fetched. Nothing about it is operator input — no command exposes it.
    """
    if mode not in set(DiscoveryMode.values):
        raise PublicEventDiscoveryError(f"Tundmatu režiim: {mode}")

    tally = DiscoveryTally(mode=mode)
    now = timezone.now()
    cap = max_detail_pages or settings.KODA_EVENT_PAGES_MAX_DETAIL_PAGES_PER_RUN

    candidates = (
        list(urls) if urls is not None else sitemap_event_urls(session=session, tally=tally)
    )
    if urls is not None:
        tally.urls_seen = len(candidates)

    wanted, skipped = _select(candidates, mode=mode, now=now)
    if skipped:
        logger.info("%s known pages within the recheck window, left alone", skipped)

    origin = DiscoveryOrigin.SITEMAP
    for url in wanted[:cap]:
        try:
            page = _fetch(
                url,
                session=session,
                max_bytes=settings.KODA_EVENTS_MAX_BYTES,
                accept="text/html",
                types=HTML_CONTENT_TYPES,
            )
        except EventCollectionError as error:
            # A page that will not load keeps whatever row it already has. One
            # bad fetch must never remove a working link.
            tally.errors += 1
            tally.is_complete = False
            tally.warn(WARN_DETAIL_FAILED)
            logger.warning("event page unreadable: %s (%s)", urlparse(url).path, error)
            continue
        tally.pages_fetched += 1
        try:
            _observe(
                url,
                html=page.text(),
                origin=origin,
                now=now,
                tally=tally,
                dry_run=dry_run,
            )
        except EventCollectionError as error:
            tally.errors += 1
            tally.warn(WARN_DETAIL_FAILED)
            logger.warning("event page unusable: %s (%s)", urlparse(url).path, error)
        _pause()

    if len(wanted) > cap:
        tally.is_complete = False
        tally.warn(WARN_DETAIL_CAP)
        logger.info("detail cap reached: %s of %s candidates read this run", cap, len(wanted))

    return tally
