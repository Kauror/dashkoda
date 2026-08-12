"""Read the public Koda.ee `Hetkel käsil` **archive**.

The archive is the same content type as the current listing, published under the
same card markup, so every parsing primitive here is imported from
:mod:`apps.legal_work.current_topics` rather than rewritten. What differs is
scale and therefore strategy.

**Scale.** 143 listing pages, eight entries each, reaching back to 2016 — about
eleven hundred consultations. Walking the index is cheap: 143 requests, and the
pager publishes its own last page so the end is *read* rather than probed by
fetching until something looks empty. Reading every detail page is not cheap:
that is another eleven hundred requests.

**The card has no year.** This is the fact that shapes everything else. An
archive card prints a day and an abbreviated month and nothing more — `27 dets`
on the page from 2016, `23 juuli` on the newest — so an entry's real date is
knowable only from its detail page. Two consequences:

- ``published_date`` stays null on an index-only row. Inferring a year across a
  decade from a day and a month would be a guess, and a guessed date feeds the
  matcher's chronology contradictions.
- Hydration cannot be targeted by date before it happens. It therefore walks
  newest-first and **stops** once it has seen a page's worth of consecutive
  entries published before the configured window.

**Two modes.** A full walk reads every listing page and settles which entries
are still present. An incremental walk starts at the newest page and stops after
a couple of pages whose entries are all already known and unchanged — a day
never archives sixteen consultations, so that is ample.

Nothing here stores raw HTML, and no page render ever calls it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.utils import timezone

from apps.core.canonical import canonical_checksum
from apps.core.public_http import FetchFailure, PublicFetchError, fetch, is_allowed_public_url
from apps.core.text import to_plain_text

from .current_topics import (
    ACCEPTED_CONTENT_TYPES,
    extract_feedback_deadline,
    extract_named_organization,
    parse_detail,
    parse_listing,
    parse_published_date,
)
from .models import (
    MAX_BODY_TEXT_LENGTH,
    MAX_CANONICAL_URL_LENGTH,
    MAX_LISTING_SUMMARY_LENGTH,
    MAX_ORGANIZATION_LENGTH,
    MAX_TOPIC_TITLE_LENGTH,
)

logger = logging.getLogger("dashkoda.legal_work.archived_topics")

DATASET_KEY = "koda-public-archived-topics"
NORMALISED_SCHEMA_VERSION = "1.0"

# Short machine-readable failure codes. Never a message, never a URL.
FAILURE_NOT_FOUND = "http_404"
FAILURE_REFUSED = "http_refused"
FAILURE_UNAVAILABLE = "unavailable"
FAILURE_UNPARSABLE = "unparsable"


class ArchiveCollectionError(RuntimeError):
    """The archive listing could not be collected or is not usable.

    Carries the transport layer's classification when it wraps one, so a caller
    can tell "the page is gone" from "the host refused us" without reading the
    message. Errors raised by this module's own validation carry no fetch
    failure at all, which is why `failure` is optional.
    """

    def __init__(
        self,
        message: str,
        *,
        failure: FetchFailure | None = None,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.failure = failure
        self.status_code = status_code


# How a transport failure becomes one of this module's stored failure codes.
# Anything absent from the map is `unavailable`: a source that is unreachable
# for a reason we have no separate handling for is, from the archive's point of
# view, simply unavailable and worth retrying on a later bounded run.
_FAILURE_CODES = {
    FetchFailure.NOT_FOUND: FAILURE_NOT_FOUND,
    FetchFailure.REFUSED: FAILURE_REFUSED,
}


def _failure_code(error: ArchiveCollectionError) -> str:
    """Classify a collection failure from structured data, never from prose.

    This used to search the message for `"404"` and for the Estonian word
    `"keeldus"`. Both are display strings: rewording an error message, or
    translating one, silently reclassified every failure that depended on it —
    and a body that happened to contain `404` for an unrelated reason was
    classified as a missing page.
    """
    return _FAILURE_CODES.get(error.failure, FAILURE_UNAVAILABLE)


@dataclass(frozen=True)
class ArchiveListingEntry:
    """One entry as the listing knows it — no date, because the card has none."""

    content_key: str
    canonical_url: str
    title: str
    listing_summary: str
    source_page: int
    source_order: int


@dataclass
class ArchiveDetail:
    """One hydrated detail page, or a recorded failure."""

    canonical_url: str
    status: str
    detail_title: str = ""
    body_text: str = ""
    published_date: dt.date | None = None
    feedback_deadline: dt.date | None = None
    named_organization: str = ""
    content_hash: str = ""
    failure_code: str = ""


@dataclass
class ArchiveIndex:
    """The listing walk's result."""

    entries: tuple[ArchiveListingEntry, ...]
    pages_fetched: int
    reached_end: bool
    stopped_early: bool = False
    seen_urls: set = field(default_factory=set)


def content_key_for(url: str) -> str:
    """The same key the current catalogue uses, so the two agree on identity.

    A consultation keeps its address across the move into the archive, so
    keeping the key derivation identical is what lets the overlap check compare
    the two catalogues at all.
    """
    from .current_topics import content_key_for as current_key

    return current_key(url)


# --------------------------------------------------------------------------
# Listing walk.
# --------------------------------------------------------------------------


def discover_last_page(html: str) -> int | None:
    """The archive's own answer to "how many pages are there?".

    Drupal's pager renders a `pager__item--last` link carrying the final page
    number, so the end of the archive is read in the first request instead of
    discovered by fetching until a page comes back empty. When the markup ever
    stops publishing it, the caller falls back to walking until a page yields no
    cards, bounded by the page cap either way.
    """
    import re

    match = re.search(r"pager__item--last.*?[?&]page=(\d+)", html, re.S)
    if not match:
        return None
    try:
        last = int(match.group(1))
    except ValueError:
        return None
    return last if 0 <= last < settings.KODA_ARCHIVE_MAX_PAGES else None


def collect_archive_index(
    *,
    session=None,
    full: bool,
    known_keys: frozenset[str] = frozenset(),
    known_signatures: dict[str, str] | None = None,
) -> ArchiveIndex:
    """Walk the archive listing and return every entry it advertises.

    ``full`` walks every page. Otherwise the walk stops after
    ``KODA_ARCHIVE_KNOWN_PAGES_BEFORE_STOP`` consecutive pages on which every
    entry is already known *and* unchanged, which is what makes the daily run
    cost two requests instead of 143.
    """
    base = settings.KODA_ARCHIVE_URL
    known_signatures = known_signatures or {}

    entries: list[ArchiveListingEntry] = []
    seen_urls: set[str] = set()
    seen_page_fingerprints: set[str] = set()
    pages_fetched = 0
    consecutive_known = 0
    reached_end = False
    stopped_early = False
    last_page: int | None = None

    for page in range(settings.KODA_ARCHIVE_MAX_PAGES):
        page_url = base if page == 0 else f"{base}?page={page}"
        html = _fetch_html(page_url, session=session)
        pages_fetched += 1

        if page == 0:
            last_page = discover_last_page(html)

        cards = parse_listing(html)
        if not cards:
            reached_end = True
            break

        # A pager that loops would otherwise walk to the cap collecting the same
        # eight entries over and over. Two different pages never carry the same
        # ordered set of links.
        fingerprint = hashlib.sha256(
            "|".join(card["url"] for card in cards).encode("utf-8")
        ).hexdigest()
        if fingerprint in seen_page_fingerprints:
            raise ArchiveCollectionError(
                f"Arhiivi lehekülg {page} kordab juba loetud lehekülge; kogumine katkestati."
            )
        seen_page_fingerprints.add(fingerprint)

        page_all_known = True
        for card in cards:
            absolute = _canonical_absolute(urljoin(base, card["url"]))
            _require_archive_detail_url(absolute)
            if absolute in seen_urls:
                raise ArchiveCollectionError(
                    "Arhiivi loendis kordub sama viide; kogumine katkestati."
                )
            seen_urls.add(absolute)

            title = " ".join(card["title"].split())
            if not title:
                raise ArchiveCollectionError(f"Arhiivi kirjel lehel {page} puudub pealkiri.")
            summary = to_plain_text(card["summary"], limit=MAX_LISTING_SUMMARY_LENGTH)
            key = content_key_for(absolute)

            # "Known" means seen before; "unchanged" additionally means its
            # listing metadata still hashes the same. A key we know but hold no
            # signature for counts as unchanged — the absence of a stored hash
            # is not evidence of an edit, and treating it as one would stop the
            # incremental walk from ever stopping.
            signature = listing_signature(title, summary)
            known_signature = known_signatures.get(key)
            if key not in known_keys or (
                known_signature is not None and known_signature != signature
            ):
                page_all_known = False

            entries.append(
                ArchiveListingEntry(
                    content_key=key,
                    canonical_url=absolute[:MAX_CANONICAL_URL_LENGTH],
                    title=title[:MAX_TOPIC_TITLE_LENGTH],
                    listing_summary=summary,
                    source_page=page,
                    source_order=len(entries),
                )
            )

        if len(entries) > settings.KODA_ARCHIVE_MAX_ITEMS:
            raise ArchiveCollectionError(
                f"Arhiiv ületab lubatud mahu ({settings.KODA_ARCHIVE_MAX_ITEMS} kirjet)."
            )

        if last_page is not None and page >= last_page:
            reached_end = True
            break

        if not full:
            consecutive_known = consecutive_known + 1 if page_all_known else 0
            if consecutive_known >= settings.KODA_ARCHIVE_KNOWN_PAGES_BEFORE_STOP:
                stopped_early = True
                break

        if not _has_next_page(html, page):
            reached_end = True
            break

        _pause()

    if not entries:
        raise ArchiveCollectionError("Arhiivi lehelt ei leitud ühtegi kirjet.")

    logger.info(
        "archived_topics.index pages=%s entries=%s end=%s early=%s",
        pages_fetched,
        len(entries),
        reached_end,
        stopped_early,
    )
    return ArchiveIndex(
        entries=tuple(entries),
        pages_fetched=pages_fetched,
        reached_end=reached_end,
        stopped_early=stopped_early,
        seen_urls=seen_urls,
    )


def listing_signature(title: str, summary: str) -> str:
    """A stable hash of the listing metadata, used to spot an edited entry."""
    return hashlib.sha256(f"{title}\x1f{summary}".encode()).hexdigest()[:32]


# --------------------------------------------------------------------------
# Detail hydration.
# --------------------------------------------------------------------------


def hydrate_detail(url: str, *, session=None) -> ArchiveDetail:
    """Read one archived consultation page.

    A failure is *recorded*, not raised. One dead page among eleven hundred must
    not throw away a whole backfill, so the entry keeps its place in the index
    with a short failure code and is retried on a later bounded run.
    """
    from .models import DetailStatus

    try:
        html = _fetch_html(url, session=session)
    except ArchiveCollectionError as error:
        return ArchiveDetail(
            canonical_url=url,
            status=DetailStatus.FAILED,
            failure_code=_failure_code(error),
        )

    parsed = parse_detail(html)
    body_source = parsed["body"] or parsed["node"]
    title = parsed["title"]
    if not body_source or not title:
        return ArchiveDetail(
            canonical_url=url,
            status=DetailStatus.FAILED,
            failure_code=FAILURE_UNPARSABLE,
        )

    published = parse_published_date(parsed["date_text"])
    intro = parsed["intro"]
    body = to_plain_text(
        " ".join(part for part in (intro, body_source) if part),
        limit=min(settings.KODA_ARCHIVE_BODY_MAX_LENGTH, MAX_BODY_TEXT_LENGTH),
    )
    deadline = extract_feedback_deadline(
        " ".join(part for part in (intro, body) if part), published_on=published
    )
    organization = extract_named_organization(intro, body)

    return ArchiveDetail(
        canonical_url=url,
        status=DetailStatus.HYDRATED,
        detail_title=title[:MAX_TOPIC_TITLE_LENGTH],
        body_text=body,
        published_date=published,
        feedback_deadline=deadline,
        named_organization=organization[:MAX_ORGANIZATION_LENGTH],
        content_hash=hashlib.sha256(f"{title}\x1f{body}\x1f{published}".encode()).hexdigest(),
    )


def hydration_cutoff(today: dt.date | None = None) -> dt.date:
    """The oldest publication date worth reading a detail page for.

    `timezone.localdate()` rather than `date.today()`: the application's day is
    `Europe/Tallinn`, and the container's clock is UTC. Between midnight and
    03:00 Tallinn time the two disagree, so the container-local date moved the
    window by a day for the runs that happen overnight — which is most of them.
    """
    today = today or timezone.localdate()
    return today - dt.timedelta(days=settings.KODA_ARCHIVE_HYDRATION_WINDOW_DAYS)


def canonical_payload(entries, details: dict[str, ArchiveDetail]) -> dict:
    """The normalised document whose checksum decides "has anything changed?".

    Ordered by content key, so the archive re-paginating — which happens every
    time a consultation is added — does not by itself republish the catalogue.
    """
    items = []
    for entry in entries:
        detail = details.get(entry.canonical_url)
        items.append(
            {
                "key": entry.content_key,
                "url": entry.canonical_url,
                "title": entry.title,
                "summary": entry.listing_summary,
                "detail_status": detail.status if detail else "pending",
                "detail_title": detail.detail_title if detail else "",
                "body": detail.body_text if detail else "",
                "published_date": detail.published_date if detail else None,
                "feedback_deadline": detail.feedback_deadline if detail else None,
                "organization": detail.named_organization if detail else "",
            }
        )
    items.sort(key=lambda item: item["key"])
    return {
        "dataset": DATASET_KEY,
        "schema_version": NORMALISED_SCHEMA_VERSION,
        "items": items,
    }


def checksum_for(entries, details) -> tuple[str, int]:
    return canonical_checksum(canonical_payload(entries, details))


# --------------------------------------------------------------------------
# URL rules and transport.
# --------------------------------------------------------------------------


def _canonical_absolute(url: str) -> str:
    """One spelling per consultation.

    The archive links with the bare host on some pages and `www` on others, and
    the current catalogue stores whichever the current listing used. Both
    catalogues normalise to `www.koda.ee` so the overlap check compares equal
    strings rather than two spellings of one page.
    """
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if host == "koda.ee":
        return url.replace("://koda.ee", "://www.koda.ee", 1)
    return url


def _require_archive_detail_url(url: str) -> None:
    """Every rule an archived consultation link must satisfy."""
    if not is_allowed_public_url(url, allowed_hosts=settings.KODA_ALLOWED_HOSTS):
        raise ArchiveCollectionError("Arhiivi viide ei ole koda.ee HTTPS-aadress.")
    parts = urlparse(url)
    if parts.username or parts.password:
        raise ArchiveCollectionError("Arhiivi viide sisaldab kasutajaandmeid.")
    if parts.query:
        raise ArchiveCollectionError("Arhiivi viide sisaldab päringustringi.")
    path = parts.path.rstrip("/")
    if path == urlparse(settings.KODA_CURRENT_TOPICS_URL).path.rstrip("/"):
        raise ArchiveCollectionError("Arhiivi viide osutab hetkel käsil loendile.")
    if path == urlparse(settings.KODA_ARCHIVE_URL).path.rstrip("/"):
        raise ArchiveCollectionError("Arhiivi viide osutab arhiivi loendile.")
    if not f"{path}/".startswith(settings.KODA_CURRENT_TOPICS_PATH_PREFIX):
        raise ArchiveCollectionError("Arhiivi viide ei ole õige teerada.")
    if len(url) > MAX_CANONICAL_URL_LENGTH:
        raise ArchiveCollectionError("Arhiivi viide on lubatust pikem.")


def _fetch_html(url: str, *, session=None) -> str:
    try:
        result = fetch(
            url,
            allowed_hosts=settings.KODA_ALLOWED_HOSTS,
            accept="text/html",
            max_bytes=settings.KODA_ARCHIVE_MAX_BYTES,
            expected_content_types=ACCEPTED_CONTENT_TYPES,
            session=session,
        )
    except PublicFetchError as error:
        raise ArchiveCollectionError(
            str(error), failure=error.failure, status_code=error.status_code
        ) from error
    return result.text()


def _has_next_page(html: str, page: int) -> bool:
    return f"?page={page + 1}" in html


def _pause() -> None:
    """Wait between requests. The archive walk is the one long run we make."""
    pause = settings.KODA_ARCHIVE_REQUEST_PAUSE_SECONDS
    if pause > 0:
        time.sleep(pause)
