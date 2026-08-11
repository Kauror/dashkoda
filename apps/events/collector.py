"""Read the public Koda.ee events calendar.

Strategy, in the order the brief requires — structured data first, visual text
never:

1. the listing at ``/et/sundmused`` is parsed for its **event teaser cards**;
2. each card yields a canonical URL, a title and a category;
3. the detail page is fetched and its **schema.org ``Event`` JSON-LD** supplies
   the authoritative title, dates and location;
4. a class-scoped ``event--default--date`` on the detail page is the documented
   fallback when JSON-LD is absent.

Two traps this avoids, both verified against the live site:

- **Category pages share the event URL prefix.** ``/et/sundmused/koolitused``
  and ``/et/sundmused/liikmeuritused`` are category listings, not events, and a
  naive scrape of every ``/et/sundmused/`` link collects them. Extraction is
  therefore scoped to teaser cards, and an entry is kept only if its detail page
  actually presents ``Event`` JSON-LD or the fallback date field.
- **The site publishes dates, not times.** The JSON-LD ``startDate`` currently
  carries a calendar date with no clock time, so events are stored date-only and
  ``starts_at`` stays null. A time is never inferred from prose.

Parsing uses the standard library only. The authoritative payload is JSON, and
the listing needs nothing more than class-scoped element extraction, so no HTML
parsing dependency is added.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import zoneinfo
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from django.conf import settings

from apps.core.canonical import canonical_checksum
from apps.core.public_http import PublicFetchError, fetch, is_allowed_public_url
from apps.core.structured_data import find_by_type
from apps.news.collector import to_plain_text

logger = logging.getLogger("dashkoda.events.collector")

DATASET_KEY = "koda-public-events"
NORMALISED_SCHEMA_VERSION = "1.0"

ACCEPTED_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})

TALLINN = zoneinfo.ZoneInfo("Europe/Tallinn")

TEASER_CLASS = "event--teaser"
# The event's own link lives in the card's title element. Every teaser card also
# contains a link to its *category* listing, which shares the `/et/sundmused/`
# prefix, so "the first link in the card" would collect category pages instead
# of events. The title element is what actually identifies the event.
TITLE_CLASS = "event--teaser--title"
FALLBACK_DATE_CLASS = "event--default--date"
FALLBACK_LOCATION_CLASS = "event--default--location"

_VOID_TAGS = frozenset({"br", "img", "meta", "link", "input", "hr", "source", "col", "area"})


class EventCollectionError(RuntimeError):
    """The events calendar could not be collected or is not usable."""


@dataclass(frozen=True)
class EventEntry:
    stable_key: str
    title: str
    canonical_url: str
    category: str
    summary: str
    starts_on: dt.date
    ends_on: dt.date | None
    starts_at: dt.datetime | None
    ends_at: dt.datetime | None
    location: str
    source_order: int


@dataclass(frozen=True)
class EventCollection:
    entries: tuple[EventEntry, ...]
    sha256: str
    size_bytes: int
    canonical: dict
    pages_fetched: int
    details_fetched: int
    skipped_non_events: int
    skipped_past: int


class _TeaserCardParser(HTMLParser):
    """Collect the event links and categories inside teaser cards only.

    Depth-tracked rather than regex-based, so a category link elsewhere on the
    page — including the sidebar calendar widget — cannot be mistaken for an
    event.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self._card_depth: int | None = None
        self._title_depth: int | None = None
        self._in_title_link = False
        self._in_category_link = False
        self.cards: list[dict] = []
        self._current: dict | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()

        if self._card_depth is None and TEASER_CLASS in classes:
            self._card_depth = self.depth
            self._current = {"url": "", "title": "", "category": ""}

        if self._current is not None:
            if TITLE_CLASS in classes and self._title_depth is None:
                self._title_depth = self.depth
            if tag == "a":
                href = attributes.get("href") or ""
                if href.startswith("/et/sundmused/"):
                    if self._title_depth is not None:
                        # Inside the title element: this is the event itself.
                        if not self._current["url"]:
                            self._current["url"] = href
                        self._in_title_link = True
                    elif not self._current["category"]:
                        # The card's other `/et/sundmused/` link is its category
                        # listing. Its text is the category name.
                        self._in_category_link = True

        if tag not in _VOID_TAGS:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag not in _VOID_TAGS:
            self.depth -= 1
        if tag == "a":
            self._in_title_link = False
            self._in_category_link = False
        if self._title_depth is not None and self.depth <= self._title_depth:
            self._title_depth = None
        if self._card_depth is not None and self.depth <= self._card_depth:
            if self._current and self._current["url"]:
                self.cards.append(self._current)
            self._card_depth = None
            self._title_depth = None
            self._in_title_link = False
            self._in_category_link = False
            self._current = None

    def handle_data(self, data):
        if self._current is None:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title_link and not self._current["title"]:
            self._current["title"] = text
        elif self._in_category_link and not self._current["category"]:
            self._current["category"] = text


def collect_events(*, url: str | None = None, session=None, **_ignored) -> EventCollection:
    """Crawl the calendar and normalise it. Never returns `None`.

    The listing is HTML with no useful validator, so conditional requests are not
    attempted here; the canonical checksum over the normalised events is what
    decides whether anything changed.
    """
    base = url or settings.KODA_EVENTS_URL
    today = dt.datetime.now(TALLINN).date()

    candidates: list[dict] = []
    seen_urls: set[str] = set()
    pages = 0

    for page in range(settings.KODA_EVENTS_MAX_PAGES):
        page_url = base if page == 0 else f"{base}?page={page}"
        html = _fetch_html(page_url, session=session)
        pages += 1
        cards = _parse_cards(html)
        if not cards:
            break
        for card in cards:
            absolute = urljoin(base, card["url"])
            if absolute in seen_urls:
                continue
            seen_urls.add(absolute)
            candidates.append({**card, "url": absolute})
        if len(candidates) >= settings.KODA_EVENTS_TARGET_ITEMS:
            break
        if not _has_next_page(html, page):
            break

    if not candidates:
        raise EventCollectionError("Sündmuste lehelt ei leitud ühtegi sündmuskaarti.")

    entries: list[EventEntry] = []
    details = 0
    skipped_non_events = 0
    skipped_past = 0

    for candidate in candidates[: settings.KODA_EVENTS_MAX_DETAIL_FETCHES]:
        if not is_koda_url(candidate["url"]):
            skipped_non_events += 1
            continue
        try:
            detail_html = _fetch_html(candidate["url"], session=session)
        except EventCollectionError:
            # One unreachable detail page must not lose the whole calendar. The
            # event is skipped for this run and reappears when the page returns.
            logger.warning("events.collect detail page unavailable; skipping one entry")
            skipped_non_events += 1
            continue
        details += 1

        parsed = parse_event_detail(detail_html, candidate["url"])
        if parsed is None:
            # A category listing, not an event.
            skipped_non_events += 1
            continue

        starts_on, ends_on, starts_at, ends_at, title, location = parsed
        effective_end = ends_on or starts_on
        if effective_end < today:
            skipped_past += 1
            continue

        entries.append(
            EventEntry(
                stable_key=stable_key_for_url(candidate["url"]),
                title=(title or candidate["title"] or "").strip(),
                canonical_url=candidate["url"],
                category=to_plain_text(candidate.get("category", ""), limit=120),
                summary="",
                starts_on=starts_on,
                ends_on=ends_on,
                starts_at=starts_at,
                ends_at=ends_at,
                location=location[:200],
                source_order=0,
            )
        )

    if not entries:
        raise EventCollectionError("Ühtegi tulevast sündmust ei leitud.")

    entries.sort(key=lambda item: (item.starts_on, item.title, item.stable_key))
    entries = [
        EventEntry(**{**vars(entry), "source_order": index})
        for index, entry in enumerate(entries[: settings.KODA_EVENTS_MAX_ITEMS])
    ]

    canonical = {
        "dataset": DATASET_KEY,
        "schema_version": NORMALISED_SCHEMA_VERSION,
        "items": [
            {
                "key": entry.stable_key,
                "title": entry.title,
                "url": entry.canonical_url,
                "category": entry.category,
                "starts_on": entry.starts_on,
                "ends_on": entry.ends_on,
                "starts_at": entry.starts_at,
                "ends_at": entry.ends_at,
                "location": entry.location,
            }
            for entry in entries
        ],
    }
    checksum, size = canonical_checksum(canonical)
    logger.info(
        "events.collect pages=%s details=%s kept=%s skipped_past=%s skipped_non_events=%s",
        pages,
        details,
        len(entries),
        skipped_past,
        skipped_non_events,
    )
    return EventCollection(
        entries=tuple(entries),
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        pages_fetched=pages,
        details_fetched=details,
        skipped_non_events=skipped_non_events,
        skipped_past=skipped_past,
    )


def _fetch_html(url: str, *, session=None) -> str:
    try:
        result = fetch(
            url,
            allowed_hosts=settings.KODA_ALLOWED_HOSTS,
            accept="text/html",
            max_bytes=settings.KODA_EVENTS_MAX_BYTES,
            expected_content_types=ACCEPTED_CONTENT_TYPES,
            session=session,
        )
    except PublicFetchError as error:
        raise EventCollectionError(str(error)) from error
    return result.text()


def _parse_cards(html: str) -> list[dict]:
    parser = _TeaserCardParser()
    parser.feed(html)
    parser.close()
    return parser.cards


def _has_next_page(html: str, page: int) -> bool:
    return f"?page={page + 1}" in html


def _find_event(html: str) -> dict | None:
    """The page's own `Event` description, if it has one.

    The JSON-LD reading itself lives in `apps.core.structured_data`, shared with
    the news collector: both need blocks found, unparseable ones survived and
    `@graph` flattened, and two copies of that would drift.
    """
    return find_by_type(html, "Event")


def parse_event_detail(html: str, url: str):
    """Return dates, title and location, or `None` when this is not an event."""
    event = _find_event(html)
    title = ""
    location = ""
    starts_on = ends_on = None
    starts_at = ends_at = None

    if event is not None:
        title = str(event.get("name") or "").strip()
        location = _location_name(event.get("location"))
        starts_on, starts_at = _parse_schema_datetime(event.get("startDate"))
        ends_on, ends_at = _parse_schema_datetime(event.get("endDate"))

    if starts_on is None:
        starts_on = _fallback_date(html)
        if starts_on is None:
            return None
        if not location:
            location = _class_text(html, FALLBACK_LOCATION_CLASS)

    if ends_on is not None and ends_on < starts_on:
        raise EventCollectionError(f"Sündmuse lõpp on enne algust: {urlparse(url).path}")
    if starts_at is not None and ends_at is not None and ends_at < starts_at:
        raise EventCollectionError(f"Sündmuse lõpuaeg on enne algusaega: {urlparse(url).path}")

    return starts_on, ends_on, starts_at, ends_at, title, location


def _parse_schema_datetime(value) -> tuple[dt.date | None, dt.datetime | None]:
    """A schema.org date or datetime, without inventing precision.

    A bare ``YYYY-MM-DD`` yields a date and **no** time. Only a value that
    actually carries a clock time produces a timestamp, interpreted in
    Europe/Tallinn when it has no offset of its own.
    """
    if not value or not isinstance(value, str):
        return None, None
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return dt.date.fromisoformat(text), None
        except ValueError:
            return None, None
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TALLINN)
    return parsed.astimezone(TALLINN).date(), parsed


def _location_name(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and value:
        return _location_name(value[0])
    return ""


def _class_text(html: str, class_name: str) -> str:
    match = re.search(
        rf'<[^>]*class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)<', html, re.S
    )
    return to_plain_text(match.group(1), limit=200) if match else ""


def _fallback_date(html: str) -> dt.date | None:
    """The documented fallback: a `dd.mm.yyyy` in the detail page's date field."""
    text = _class_text(html, FALLBACK_DATE_CLASS)
    match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


# The three names below are public because `public_discovery` reads the same
# pages from a different direction and must reach them the same way. Two
# readings of one page that disagreed about its key, its dates or whether its
# host is allowed would be worse than the duplication.


def stable_key_for_url(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1][:200]


def is_koda_url(value: str) -> bool:
    return is_allowed_public_url(value, allowed_hosts=settings.KODA_ALLOWED_HOSTS)
