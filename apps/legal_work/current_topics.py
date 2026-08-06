"""Read the public Koda.ee `Hetkel käsil` listing and the pages it links to.

The collection boundary is fixed and small: the configured listing address, its
own pager, and the detail pages whose canonical path starts with
``/et/meie-moju/hetkel-kasil/``. Nothing else is reachable from here. There is
no URL argument, no form and no setting a viewer or an administrator can point
somewhere new, and the archive — which shares that prefix — is excluded by exact
match because this phase collects only what is currently open.

Three things about the live pages drive the design, all verified against the
site rather than assumed:

- **The listing is paginated.** ``?page=N``, eight cards a page, running to two
  pages today. Reading only the first page silently drops the tail of the
  catalogue, so the pager is followed under a bounded page count.
- **The listing card has no year.** It prints a day and an abbreviated month in
  two separate spans and nothing else. The *detail* page prints a full
  ``dd.mm.yyyy`` in ``current-draft--default--date``, so that is where
  ``published_date`` comes from and the listing supplies only ordering.
- **The titles are editorial prompts, not act names.** "Mida arvad plaanitavatest
  pakendiseaduse muudatustest?" is the headline; the formal instrument is named
  in the intro and the body. Everything the matcher needs is therefore kept:
  title, listing summary and bounded article text, all as plain text.

Parsing uses the standard library only, depth-tracked rather than regex-driven,
so a link elsewhere on the page cannot be mistaken for a catalogue entry. No raw
HTML, script, style or navigation is retained: what leaves this module is
normalised plain text and validated URLs.

Unlike the events calendar, one unreachable detail page **fails the whole run**.
A calendar with a missing event is still a calendar; a catalogue with a hole
would make the matcher report `unmatched` for a legal record whose page merely
happened to time out — and, once matching runs, drop a link the reader had
yesterday. A wrong answer is worse than yesterday's correct one, so the previous
catalogue stays published instead.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from django.conf import settings

from apps.core.canonical import canonical_checksum
from apps.core.public_http import PublicFetchError, fetch, is_allowed_public_url
from apps.news.collector import to_plain_text

from .models import (
    MAX_BODY_TEXT_LENGTH,
    MAX_CANONICAL_URL_LENGTH,
    MAX_LISTING_SUMMARY_LENGTH,
    MAX_ORGANIZATION_LENGTH,
    MAX_TOPIC_TITLE_LENGTH,
)

logger = logging.getLogger("dashkoda.legal_work.current_topics")

DATASET_KEY = "koda-public-current-topics"
NORMALISED_SCHEMA_VERSION = "1.0"

ACCEPTED_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})

# Drupal content-type markers. `node--type-current-draft` is the content type
# itself rather than a styling hook, which makes it the most structural handle
# the markup offers; the teaser and field classes below are scoped *inside* it.
NODE_TYPE_CLASS = "node--type-current-draft"
FULL_VIEW_CLASS = "node--view-mode-full"

TEASER_CLASS = "current-draft--teaser"
TEASER_TITLE_CLASS = "current-draft--teaser--title"
TEASER_CONTENT_CLASS = "current-draft--teaser--content"

DETAIL_DATE_CLASS = "current-draft--default--date"
DETAIL_INTRO_CLASS = "field--intro"
# Both classes are required. `field--name-body` alone also matches the site's
# language-switcher block, which is markup rather than content and would drag
# "Eesti keel English Русский" into every stored body.
DETAIL_BODY_CLASSES = "field--name-body field--type-text-with-summary"

_VOID_TAGS = frozenset({"br", "img", "meta", "link", "input", "hr", "source", "col", "area"})
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "iframe", "svg"})
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "section", "article",
        "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
        "figure", "figcaption", "nav", "aside",
    }
)  # fmt: skip


class CurrentTopicCollectionError(RuntimeError):
    """The current-topic listing could not be collected or is not usable."""


@dataclass(frozen=True)
class CurrentTopicEntry:
    content_key: str
    canonical_url: str
    title: str
    listing_summary: str
    body_text: str
    published_date: dt.date | None
    feedback_deadline: dt.date | None
    named_organization: str
    source_order: int


@dataclass(frozen=True)
class CurrentTopicCollection:
    entries: tuple[CurrentTopicEntry, ...]
    sha256: str
    size_bytes: int
    canonical: dict
    pages_fetched: int
    details_fetched: int


# --------------------------------------------------------------------------
# Estonian calendar and organisation vocabularies.
# --------------------------------------------------------------------------

# Stems, not full words. Estonian writes a deadline in the translative
# ("18. augustiks") and a date in the adessive ("12. märtsil"), and both share
# the genitive stem, so one table serves every form the pages actually use.
MONTH_STEMS: dict[str, int] = {
    "jaanuari": 1,
    "veebruari": 2,
    "märtsi": 3,
    "aprilli": 4,
    "mai": 5,
    "juuni": 6,
    "juuli": 7,
    "augusti": 8,
    "septembri": 9,
    "oktoobri": 10,
    "novembri": 11,
    "detsembri": 12,
}

# Abbreviations as the listing card prints them. Kept for completeness of the
# parser, not because the card's date is stored: it carries no year.
MONTH_ABBREVIATIONS: dict[str, int] = {
    "jaan": 1, "veebr": 2, "märts": 3, "apr": 4, "mai": 5, "juuni": 6,
    "juuli": 7, "aug": 8, "sept": 9, "okt": 10, "nov": 11, "dets": 12,
}  # fmt: skip

_MONTH_ALTERNATION = "|".join(sorted(MONTH_STEMS, key=len, reverse=True))

# "hiljemalt 18. augustiks", "hiljemalt 4. märtsiks", "hiljemalt 12. märtsil".
# Anchored on `hiljemalt` on purpose: an unanchored date pattern would happily
# collect a transposition deadline or a commencement date out of the body and
# present it as the Chamber's own feedback deadline.
DEADLINE_PATTERN = re.compile(
    rf"hiljemalt\D{{0,40}}?(\d{{1,2}})\.\s*({_MONTH_ALTERNATION})(?:ks|l)\b",
    re.IGNORECASE,
)
# The same phrase when the source spells the year out.
DEADLINE_WITH_YEAR_PATTERN = re.compile(
    rf"hiljemalt\D{{0,40}}?(\d{{1,2}})\.\s*({_MONTH_ALTERNATION})(?:ks|l)?\s*(\d{{4}})",
    re.IGNORECASE,
)
DEADLINE_NUMERIC_PATTERN = re.compile(
    r"hiljemalt\D{0,40}?(\d{1,2})\.(\d{1,2})\.(\d{4})",
    re.IGNORECASE,
)

FULL_DATE_PATTERN = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")

# A deadline more than this far beyond publication is not a consultation
# deadline; the pattern found something else and no date is stored.
MAX_DEADLINE_HORIZON_DAYS = 400

# A closed vocabulary of the bodies these pages actually name as the drafter.
# Deterministic and inspectable, which a general name extractor is not: an
# unknown organisation yields no value rather than a guess. Keys are lowercase
# stems matched as substrings, so Estonian case endings — "Rahandusministeeriumis",
# "Kliimaministeeriumi" — are covered without stemming.
#
# Longer stems are tried first, so "majandus- ja kommunikatsiooniministeerium"
# is never shadowed by a shorter partial match.
NAMED_ORGANIZATIONS: tuple[tuple[str, str], ...] = (
    ("majandus- ja kommunikatsiooniministeerium", "Majandus- ja Kommunikatsiooniministeerium"),
    ("majandus- ja tööstusministeerium", "Majandus- ja Tööstusministeerium"),
    ("regionaal- ja põllumajandusministeerium", "Regionaal- ja Põllumajandusministeerium"),
    ("haridus- ja teadusministeerium", "Haridus- ja Teadusministeerium"),
    ("justiits- ja digiministeerium", "Justiits- ja Digiministeerium"),
    ("tarbijakaitse ja tehnilise järelevalve amet", "Tarbijakaitse ja Tehnilise Järelevalve Amet"),
    ("euroopa liidu nõukogu", "Euroopa Liidu Nõukogu"),
    ("maksu- ja tolliamet", "Maksu- ja Tolliamet"),
    ("rahandusministeerium", "Rahandusministeerium"),
    ("kliimaministeerium", "Kliimaministeerium"),
    ("sotsiaalministeerium", "Sotsiaalministeerium"),
    ("kultuuriministeerium", "Kultuuriministeerium"),
    ("kaitseministeerium", "Kaitseministeerium"),
    ("välisministeerium", "Välisministeerium"),
    ("siseministeerium", "Siseministeerium"),
    ("maaeluministeerium", "Maaeluministeerium"),
    ("justiitsministeerium", "Justiitsministeerium"),
    ("euroopa komisjon", "Euroopa Komisjon"),
    ("euroopa parlament", "Euroopa Parlament"),
    ("riigikantselei", "Riigikantselei"),
    ("konkurentsiamet", "Konkurentsiamet"),
    ("päästeamet", "Päästeamet"),
    ("riigikogu", "Riigikogu"),
)


# --------------------------------------------------------------------------
# Parsing.
# --------------------------------------------------------------------------


class _TextRegionParser(HTMLParser):
    """Depth-tracked plain-text capture of named regions.

    A region opens when an element carries *every* class of one requested
    combination (or is one of the requested tags) and closes when that element
    does, so a match is scoped to a subtree rather than to "everything after a
    string in the markup". Scripts, styles and embedded documents are dropped;
    block elements contribute a space, so ``</p><p>`` cannot weld two sentences
    into one word.

    Each region captures its **first** occurrence only. Drupal repeats a node in
    a sideblock further down the page, so a second `current-draft--default--date`
    and a second body exist; taking the first keeps the page's own article and
    stops the duplicate from doubling every stored field.
    """

    def __init__(self, *, classes: dict[str, str], tags: dict[str, str] | None = None):
        super().__init__(convert_charrefs=True)
        self._classes = tuple((frozenset(spec.split()), region) for spec, region in classes.items())
        self._tags = tags or {}
        self.depth = 0
        self._suppress = 0
        self._open: list[tuple[str, int]] = []
        self._finished: set[str] = set()
        self.regions: dict[str, list[str]] = {}

    def _region_for(self, tag: str, attributes: dict) -> str | None:
        present = frozenset((attributes.get("class") or "").split())
        for required, region in self._classes:
            if required <= present and region not in self._finished:
                return region
        candidate = self._tags.get(tag)
        return None if candidate in self._finished else candidate

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._suppress += 1
            return
        region = self._region_for(tag, dict(attrs))
        if region is not None and not any(open_region == region for open_region, _ in self._open):
            self._open.append((region, self.depth))
            self.regions.setdefault(region, [])
        if tag in _BLOCK_TAGS:
            self._append(" ")
        if tag not in _VOID_TAGS:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._suppress = max(0, self._suppress - 1)
            return
        if tag not in _VOID_TAGS:
            self.depth -= 1
        if tag in _BLOCK_TAGS:
            self._append(" ")
        while self._open and self._open[-1][1] >= self.depth:
            self._finished.add(self._open.pop()[0])

    def handle_data(self, data):
        self._append(data)

    def _append(self, text: str) -> None:
        if self._suppress:
            return
        for region, _depth in self._open:
            self.regions[region].append(text)

    def text(self, region: str) -> str:
        return " ".join("".join(self.regions.get(region, [])).split())


class _ListingParser(HTMLParser):
    """Collect one record per `current-draft--teaser` card.

    Scoped to the card, and inside the card scoped again to the title element:
    the pager, the sidebar and the page's own "Arhiiv" link all live outside a
    card, and the card's other links are not the entry.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self._suppress = 0
        self._card_depth: int | None = None
        self._title_depth: int | None = None
        self._content_depth: int | None = None
        self._in_title_link = False
        self._current: dict | None = None
        self.cards: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._suppress += 1
            return
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()

        if self._card_depth is None and TEASER_CLASS in classes:
            self._card_depth = self.depth
            self._current = {"url": "", "title": "", "summary": ""}

        if self._current is not None:
            if TEASER_TITLE_CLASS in classes and self._title_depth is None:
                self._title_depth = self.depth
            if TEASER_CONTENT_CLASS in classes and self._content_depth is None:
                self._content_depth = self.depth
            if tag == "a" and self._title_depth is not None:
                href = (attributes.get("href") or "").strip()
                if href and not self._current["url"]:
                    self._current["url"] = href
                self._in_title_link = True
            if tag in _BLOCK_TAGS and self._content_depth is not None:
                self._current["summary"] += " "

        if tag not in _VOID_TAGS:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._suppress = max(0, self._suppress - 1)
            return
        if tag not in _VOID_TAGS:
            self.depth -= 1
        if tag == "a":
            self._in_title_link = False
        if self._current is not None and tag in _BLOCK_TAGS and self._content_depth is not None:
            self._current["summary"] += " "
        if self._title_depth is not None and self.depth <= self._title_depth:
            self._title_depth = None
        if self._content_depth is not None and self.depth <= self._content_depth:
            self._content_depth = None
        if self._card_depth is not None and self.depth <= self._card_depth:
            if self._current is not None:
                self.cards.append(
                    {
                        "url": self._current["url"].strip(),
                        "title": " ".join(self._current["title"].split()),
                        "summary": " ".join(self._current["summary"].split()),
                    }
                )
            self._card_depth = None
            self._title_depth = None
            self._content_depth = None
            self._in_title_link = False
            self._current = None

    def handle_data(self, data):
        if self._suppress or self._current is None:
            return
        if self._in_title_link:
            self._current["title"] += data
        if self._content_depth is not None:
            self._current["summary"] += data


def parse_listing(html: str) -> list[dict]:
    parser = _ListingParser()
    parser.feed(html)
    parser.close()
    return parser.cards


def parse_detail(html: str) -> dict:
    """Title, publication date, intro and body text from one detail page."""
    parser = _TextRegionParser(
        classes={
            DETAIL_DATE_CLASS: "date",
            DETAIL_INTRO_CLASS: "intro",
            DETAIL_BODY_CLASSES: "body",
            # The whole article, used only when the body field is absent. Both
            # classes are required so the sideblock rendering of the same node
            # cannot stand in for the page's own content.
            f"{NODE_TYPE_CLASS} {FULL_VIEW_CLASS}": "node",
        },
        tags={"h1": "title"},
    )
    parser.feed(html)
    parser.close()
    return {
        "title": parser.text("title")[:MAX_TOPIC_TITLE_LENGTH],
        "date_text": parser.text("date"),
        "intro": parser.text("intro"),
        "body": parser.text("body"),
        "node": parser.text("node"),
    }


# --------------------------------------------------------------------------
# Extraction.
# --------------------------------------------------------------------------


def parse_published_date(text: str) -> dt.date | None:
    """The detail page's own `dd.mm.yyyy`."""
    match = FULL_DATE_PATTERN.search(text or "")
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def extract_feedback_deadline(text: str, *, published_on: dt.date | None) -> dt.date | None:
    """The Chamber's own feedback deadline, or nothing.

    Every form found is resolved and the results compared. One agreed date is
    stored; no date, an unresolvable one, or two pages' worth of disagreement
    stores nothing, because a deadline the matcher half-believes is worse than
    an absent one — the matcher treats a missing deadline as no evidence and a
    conflicting one as a contradiction.
    """
    if not text:
        return None

    resolved: set[dt.date] = set()

    for day, month, year in DEADLINE_NUMERIC_PATTERN.findall(text):
        candidate = _safe_date(int(year), int(month), int(day))
        if candidate is not None:
            resolved.add(candidate)

    for day, stem, year in DEADLINE_WITH_YEAR_PATTERN.findall(text):
        candidate = _safe_date(int(year), MONTH_STEMS[stem.lower()], int(day))
        if candidate is not None:
            resolved.add(candidate)

    if not resolved:
        for day, stem in DEADLINE_PATTERN.findall(text):
            candidate = _resolve_year(int(day), MONTH_STEMS[stem.lower()], published_on)
            if candidate is not None:
                resolved.add(candidate)

    if len(resolved) != 1:
        # Nothing found, or the page states two different deadlines. Either way
        # this is not a fact the page confidently contains.
        return None
    return resolved.pop()


def _resolve_year(day: int, month: int, published_on: dt.date | None) -> dt.date | None:
    """Give a bare `18. augustiks` the only year it can mean.

    A consultation deadline never precedes the announcement, so the publication
    year is tried first and rolled forward once when the date would fall before
    publication — which is how a December announcement names a January deadline.
    Without a publication date there is no calendar context and no date is
    inferred.
    """
    if published_on is None:
        return None
    candidate = _safe_date(published_on.year, month, day)
    if candidate is None:
        return None
    if candidate < published_on:
        candidate = _safe_date(published_on.year + 1, month, day)
        if candidate is None:
            return None
    if (candidate - published_on).days > MAX_DEADLINE_HORIZON_DAYS:
        return None
    return candidate


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def extract_named_organization(*texts: str) -> str:
    """The first known body named, in the order the texts are given.

    The listing summary opens by naming the drafter — "Kliimaministeerium on
    koostanud eelnõu" — so it is searched first and the body only afterwards.
    An organisation outside the vocabulary yields an empty value; this field
    reports what the page said, and never a guess.
    """
    for text in texts:
        if not text:
            continue
        haystack = text.casefold()
        best: tuple[int, str] | None = None
        for stem, display in NAMED_ORGANIZATIONS:
            position = haystack.find(stem)
            if position == -1:
                continue
            if best is None or position < best[0]:
                best = (position, display)
        if best is not None:
            return best[1][:MAX_ORGANIZATION_LENGTH]
    return ""


def content_key_for(url: str) -> str:
    """A stable key for one catalogue entry, derived from its canonical path.

    The path is the entry's identity: Koda.ee publishes one node per
    consultation and never reuses a slug. Hashing it keeps the key a fixed
    width and free of any text.
    """
    path = urlparse(url).path.rstrip("/")
    normalised = unicodedata.normalize("NFC", path).casefold()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------
# Collection.
# --------------------------------------------------------------------------


def collect_current_topics(*, url: str | None = None, session=None, **_ignored):
    """Crawl the current listing and its detail pages, and normalise them."""
    base = url or settings.KODA_CURRENT_TOPICS_URL
    listing_path = urlparse(base).path.rstrip("/")

    candidates: list[dict] = []
    seen_urls: set[str] = set()
    pages = 0

    for page in range(settings.KODA_CURRENT_TOPICS_MAX_PAGES):
        page_url = base if page == 0 else f"{base}?page={page}"
        html = _fetch_html(page_url, session=session)
        pages += 1
        cards = parse_listing(html)
        if not cards:
            break
        for card in cards:
            absolute = urljoin(base, card["url"])
            _require_detail_url(absolute, listing_path=listing_path)
            if absolute in seen_urls:
                raise CurrentTopicCollectionError(
                    "Hetkel käsil loendis kordub sama viide; kogumine katkestati."
                )
            seen_urls.add(absolute)
            candidates.append({**card, "url": absolute})
        if len(candidates) > settings.KODA_CURRENT_TOPICS_MAX_ITEMS:
            raise CurrentTopicCollectionError(
                f"Hetkel käsil loend ületab lubatud mahu "
                f"({settings.KODA_CURRENT_TOPICS_MAX_ITEMS} teemat)."
            )
        if not _has_next_page(html, page):
            break

    if not candidates:
        raise CurrentTopicCollectionError("Hetkel käsil lehelt ei leitud ühtegi teemat.")

    entries: list[CurrentTopicEntry] = []
    for order, candidate in enumerate(candidates):
        detail_html = _fetch_html(candidate["url"], session=session)
        entries.append(_build_entry(candidate, detail_html, order))

    canonical = {
        "dataset": DATASET_KEY,
        "schema_version": NORMALISED_SCHEMA_VERSION,
        # Ordered by the stable content key rather than by listing position, so
        # the site reordering two cards with identical content is correctly
        # reported as unchanged.
        "items": sorted(
            (
                {
                    "key": entry.content_key,
                    "url": entry.canonical_url,
                    "title": entry.title,
                    "summary": entry.listing_summary,
                    "body": entry.body_text,
                    "published_date": entry.published_date,
                    "feedback_deadline": entry.feedback_deadline,
                    "organization": entry.named_organization,
                }
                for entry in entries
            ),
            key=lambda item: item["key"],
        ),
    }
    checksum, size = canonical_checksum(canonical)
    logger.info(
        "current_topics.collect pages=%s details=%s items=%s",
        pages,
        len(entries),
        len(entries),
    )
    return CurrentTopicCollection(
        entries=tuple(entries),
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        pages_fetched=pages,
        details_fetched=len(entries),
    )


def _build_entry(candidate: dict, detail_html: str, order: int) -> CurrentTopicEntry:
    detail = parse_detail(detail_html)
    url = candidate["url"]
    path = urlparse(url).path

    title = detail["title"] or candidate["title"]
    if not title:
        raise CurrentTopicCollectionError(f"Hetkel käsil lehel puudub pealkiri: {path}")

    body_source = detail["body"] or detail["node"]
    if not body_source:
        raise CurrentTopicCollectionError(f"Hetkel käsil lehel puudub sisu: {path}")

    published_date = parse_published_date(detail["date_text"])
    listing_summary = to_plain_text(
        candidate["summary"] or detail["intro"], limit=MAX_LISTING_SUMMARY_LENGTH
    )
    intro = detail["intro"] or listing_summary
    body_text = to_plain_text(
        " ".join(part for part in (intro, body_source) if part),
        limit=min(settings.KODA_CURRENT_TOPICS_BODY_MAX_LENGTH, MAX_BODY_TEXT_LENGTH),
    )

    return CurrentTopicEntry(
        content_key=content_key_for(url),
        canonical_url=url[:MAX_CANONICAL_URL_LENGTH],
        title=title[:MAX_TOPIC_TITLE_LENGTH],
        listing_summary=listing_summary,
        body_text=body_text,
        published_date=published_date,
        feedback_deadline=extract_feedback_deadline(
            " ".join(part for part in (listing_summary, intro, body_text) if part),
            published_on=published_date,
        ),
        named_organization=extract_named_organization(listing_summary, intro, body_text),
        source_order=order,
    )


def _require_detail_url(url: str, *, listing_path: str) -> None:
    """Every rule a catalogue link must satisfy before it is ever requested."""
    if not is_allowed_public_url(url, allowed_hosts=settings.KODA_ALLOWED_HOSTS):
        raise CurrentTopicCollectionError("Hetkel käsil viide ei ole koda.ee HTTPS-aadress.")
    path = urlparse(url).path.rstrip("/")
    if path == listing_path:
        raise CurrentTopicCollectionError("Hetkel käsil viide osutab loendile endale.")
    if path == settings.KODA_CURRENT_TOPICS_ARCHIVE_PATH.rstrip("/"):
        raise CurrentTopicCollectionError("Hetkel käsil arhiiv ei kuulu selle faasi mahtu.")
    if not f"{path}/".startswith(settings.KODA_CURRENT_TOPICS_PATH_PREFIX):
        raise CurrentTopicCollectionError("Hetkel käsil viide ei ole õige teerada.")
    if len(url) > MAX_CANONICAL_URL_LENGTH:
        raise CurrentTopicCollectionError("Hetkel käsil viide on lubatust pikem.")


def _fetch_html(url: str, *, session=None) -> str:
    try:
        result = fetch(
            url,
            allowed_hosts=settings.KODA_ALLOWED_HOSTS,
            accept="text/html",
            max_bytes=settings.KODA_CURRENT_TOPICS_MAX_BYTES,
            expected_content_types=ACCEPTED_CONTENT_TYPES,
            session=session,
        )
    except PublicFetchError as error:
        raise CurrentTopicCollectionError(str(error)) from error
    return result.text()


def _has_next_page(html: str, page: int) -> bool:
    return f"?page={page + 1}" in html
