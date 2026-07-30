"""Read the public Koda.ee news RSS feed.

Exactly one feed is used — the canonical `/et/news/feed.xml`. There is a second,
malformed RSS document on the site; falling back to it silently would mean the
dashboard sometimes shows a different, lower-quality dataset without saying so.
When this feed is unavailable the run fails and the previous snapshot stays
published.

Descriptions arrive as HTML. They are reduced to plain text here: scripts,
styles and all markup are removed, entities are resolved, whitespace is
collapsed and the result is truncated. Full article HTML is never stored.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from xml.etree import ElementTree

from django.conf import settings

from apps.core.canonical import canonical_checksum
from apps.core.public_http import PublicFetchError, fetch, is_allowed_public_url

logger = logging.getLogger("dashkoda.news.collector")

DATASET_KEY = "koda-public-news"
NORMALISED_SCHEMA_VERSION = "1.0"

ACCEPTED_CONTENT_TYPES = frozenset(
    {"application/rss+xml", "application/xml", "text/xml", "application/atom+xml"}
)


class NewsCollectionError(RuntimeError):
    """The news feed could not be collected or does not satisfy the contract."""


@dataclass(frozen=True)
class NewsEntry:
    guid: str
    title: str
    canonical_url: str
    published_at: dt.datetime
    category: str
    summary: str
    source_order: int


@dataclass(frozen=True)
class NewsCollection:
    entries: tuple[NewsEntry, ...]
    sha256: str
    size_bytes: int
    canonical: dict
    etag: str
    last_modified: str


class _TextExtractor(HTMLParser):
    """Collect visible text, dropping scripts, styles and every tag."""

    _SKIP = frozenset({"script", "style", "noscript", "template", "iframe"})
    # Elements that imply a break in the prose. Without this, `</p><p>` would
    # run two sentences together into one word.
    _BLOCK = frozenset(
        {
            "p",
            "div",
            "br",
            "li",
            "ul",
            "ol",
            "tr",
            "td",
            "th",
            "section",
            "article",
            "header",
            "footer",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "blockquote",
            "figure",
            "figcaption",
        }
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppress = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._suppress += 1
        elif tag in self._BLOCK:
            self._parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._suppress:
            self._suppress -= 1
        elif tag in self._BLOCK:
            self._parts.append(" ")

    def handle_data(self, data):
        if not self._suppress:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return " ".join("".join(self._parts).split())


def to_plain_text(value: str, *, limit: int | None = None) -> str:
    """Reduce feed HTML to a bounded, safe plain-text summary."""
    if not value:
        return ""
    extractor = _TextExtractor()
    extractor.feed(unescape(value))
    extractor.close()
    text = extractor.text
    # A stray tag written as an entity survives unescaping; strip any residue.
    text = " ".join(re.sub(r"<[^>]*>", " ", text).split())
    limit = settings.KODA_SUMMARY_MAX_LENGTH if limit is None else limit
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def collect_news(
    *, url: str | None = None, etag: str = "", last_modified: str = "", session=None
) -> NewsCollection | None:
    """Fetch and normalise the feed. Returns `None` on a `304`."""
    url = url or settings.KODA_NEWS_URL
    try:
        result = fetch(
            url,
            allowed_hosts=settings.KODA_ALLOWED_HOSTS,
            accept="application/rss+xml, application/xml;q=0.9, */*;q=0.5",
            max_bytes=settings.KODA_NEWS_MAX_BYTES,
            expected_content_types=ACCEPTED_CONTENT_TYPES,
            etag=etag,
            last_modified=last_modified,
            session=session,
        )
    except PublicFetchError as error:
        raise NewsCollectionError(str(error)) from error

    if result.not_modified:
        return None

    entries = parse_feed(result.content)
    canonical = {
        "dataset": DATASET_KEY,
        "schema_version": NORMALISED_SCHEMA_VERSION,
        "items": [
            {
                "guid": entry.guid,
                "title": entry.title,
                "url": entry.canonical_url,
                "published_at": entry.published_at,
                "category": entry.category,
                "summary": entry.summary,
            }
            for entry in entries
        ],
    }
    checksum, size = canonical_checksum(canonical)
    logger.info("news.collect items=%s", len(entries))
    return NewsCollection(
        entries=entries,
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        etag=result.etag,
        last_modified=result.last_modified,
    )


def parse_feed(content: bytes) -> tuple[NewsEntry, ...]:
    """Validate and normalise the RSS document."""
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise NewsCollectionError(f"Uudisvoog ei ole kehtiv XML: {error.msg}.") from error

    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else []
    if not items:
        raise NewsCollectionError("Uudisvoos ei olnud ühtegi kirjet.")

    horizon = _now() + dt.timedelta(days=settings.KODA_NEWS_MAX_FUTURE_DAYS)
    entries: list[NewsEntry] = []
    seen_guids: set[str] = set()
    seen_urls: set[str] = set()

    for order, item in enumerate(items):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip() or link
        published_raw = (item.findtext("pubDate") or "").strip()
        category = (item.findtext("category") or "").strip()

        if not title:
            raise NewsCollectionError(f"Uudisvoo kirjel {order + 1} puudub pealkiri.")
        if not guid:
            raise NewsCollectionError(f"Uudisvoo kirjel {order + 1} puudub GUID.")
        if not _is_koda_url(link):
            raise NewsCollectionError(f"Uudisvoo kirje {order + 1} viide ei ole koda.ee aadress.")
        if not published_raw:
            raise NewsCollectionError(f"Uudisvoo kirjel {order + 1} puudub avaldamise aeg.")

        published_at = _parse_published(published_raw, order)
        if published_at > horizon:
            # One documented rule: an item claiming to be published beyond the
            # tolerated clock skew is refused, so it cannot pin itself to the
            # top of the list forever.
            raise NewsCollectionError(
                f"Uudisvoo kirje {order + 1} avaldamise aeg on liiga kaugel tulevikus."
            )

        if guid in seen_guids:
            raise NewsCollectionError(f"Uudisvoos kordub GUID kirjel {order + 1}.")
        if link in seen_urls:
            raise NewsCollectionError(f"Uudisvoos kordub viide kirjel {order + 1}.")
        seen_guids.add(guid)
        seen_urls.add(link)

        entries.append(
            NewsEntry(
                guid=guid[:500],
                title=title,
                canonical_url=link[:500],
                published_at=published_at,
                category=category[:120],
                summary=to_plain_text(_element_text(item.find("description"))),
                source_order=order,
            )
        )

    # Deterministic: newest first, GUID as the tie-break so two items published
    # in the same second never reorder between runs.
    entries.sort(key=lambda entry: (-entry.published_at.timestamp(), entry.guid))
    return tuple(entries[: settings.KODA_NEWS_MAX_ITEMS])


def _element_text(element) -> str:
    """All text inside an element, including any that XML parsed into children.

    A description is normally escaped or CDATA, so it arrives as plain text. If a
    feed ever emits real child elements instead, `findtext` would silently return
    only the text before the first child and the summary would come out empty —
    so every descendant's text is gathered here.
    """
    if element is None:
        return ""
    return "".join(element.itertext())


def _parse_published(value: str, order: int) -> dt.datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as error:
        raise NewsCollectionError(
            f"Uudisvoo kirje {order + 1} avaldamise aeg ei ole kehtiv."
        ) from error
    if parsed is None:
        raise NewsCollectionError(f"Uudisvoo kirje {order + 1} avaldamise aeg ei ole kehtiv.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _is_koda_url(value: str) -> bool:
    return is_allowed_public_url(value, allowed_hosts=settings.KODA_ALLOWED_HOSTS)


def _now() -> dt.datetime:
    from django.utils import timezone

    return timezone.now()
