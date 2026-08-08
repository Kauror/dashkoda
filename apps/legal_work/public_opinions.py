"""Reading the public Koda.ee opinion surfaces: listings, articles, attachments.

Verified against the live site rather than assumed, like every Koda.ee
collector before it:

- **`Meie arvamus` is a filtered news view.** Its cards are `node--type-news`
  teasers and every detail page lives under ``/et/uudised/``. Being listed
  there is the strongest public signal that an article is opinion material,
  but it is a *view*, not the universe — an opinion article can exist in the
  news listing without the tag, so both listings are walked and the union is
  the corpus.
- **Only the news listing dates its cards.** A `Meie arvamus` teaser prints a
  day and a month with no year, identically in 2026 and 2016; the news card
  and the detail page print a full ``dd.mm.yyyy``. Date-bounded walking
  therefore trusts news cards and detail pages, never `Meie arvamus` teasers.
- **Attachments are direct file links.** A `btn--file` anchor inside the
  article's own `field--name-ekt-content-files` region, pointing under
  ``/sites/default/files/``. The filenames follow the Chamber's own opinion
  naming convention — date, recipient, subject — so the private filename
  parser reads them unchanged.

Parsing is standard-library and depth-tracked, like the consultation
collectors: a region is a subtree, a link elsewhere on the page cannot be
mistaken for an attachment, and nothing retains raw HTML.

What counts as opinion material is evidence, recorded per page: listed in
`Meie arvamus`, opinion vocabulary in the title or body, or an attachment that
parses as an opinion letter. Ordinary news naming a statute is none of those.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

from django.conf import settings

from apps.core.public_http import PublicFetchError, fetch, is_allowed_public_url
from apps.news.collector import to_plain_text

from .current_topics import FULL_DATE_PATTERN
from .models import MAX_CANONICAL_URL_LENGTH, MAX_TOPIC_TITLE_LENGTH
from .opinion_classification import OPINION_PATTERNS, normalise_for_classification
from .opinion_filenames import parse_opinion_filename

ACCEPTED_HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
ACCEPTED_PDF_CONTENT_TYPES = frozenset({"application/pdf", "application/octet-stream"})

# Drupal structural handles, all verified against the live markup.
MEIE_ARVAMUS_CARD_CLASS = "meie-arvamus--teaser"
MEIE_ARVAMUS_TITLE_CLASS = "meie-arvamus--teaser--title"
MEIE_ARVAMUS_SUMMARY_CLASS = "current-draft--teaser--content"

NEWS_CARD_CLASS = "news--teaser"
NEWS_TITLE_CLASS = "news--teaser--title"
NEWS_DATE_CLASS = "news--teaser--group-header--date"
NEWS_CATEGORY_CLASS = "news--teaser--group-header--category"

DETAIL_NODE_CLASSES = ("node--type-news", "node--view-mode-full")
DETAIL_TITLE_CLASS = "news--default--title"
DETAIL_DATE_CLASS = "news--default--date"
ATTACHMENT_LINK_CLASS = "btn--file"

# Evidence codes, stored on the page so the boundary stays inspectable.
EVIDENCE_LISTED_MEIE_ARVAMUS = "listed-meie-arvamus"
EVIDENCE_OPINION_VOCABULARY = "opinion-vocabulary"
EVIDENCE_OPINION_ATTACHMENT = "opinion-attachment"

# The Chamber announcing its own position, as the articles actually word it.
# Complements the document-classification vocabulary, which is about what a
# *document* is; these phrases are about what an *article* reports.
POSITION_PHRASES = (
    re.compile(r"\bkoda\s+(toetab|ei\s+toeta|leiab|teeb\s+ettepaneku)\w{0,3}\b"),
    re.compile(r"\bkoja\s+(hinnangul|arvates|ettepanek\w{0,3}|arvamus\w{0,3})\b"),
    re.compile(r"\bkaubanduskoda\s+(toetab|ei\s+toeta|leiab|esitas)\w{0,3}\b"),
    re.compile(r"\besitas\w{0,3}\s+arvamus\w{0,3}\b"),
    re.compile(r"\bsaatis\w{0,3}\s+arvamus\w{0,3}\b"),
)

_VOID_TAGS = frozenset({"br", "img", "meta", "link", "input", "hr", "source", "col", "area"})
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "iframe", "svg"})
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "section", "article",
        "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
        "figure", "figcaption", "nav", "aside",
    }
)  # fmt: skip


class PublicOpinionCollectionError(RuntimeError):
    """A public opinion surface could not be collected or is not usable."""


@dataclass(frozen=True)
class ListingCard:
    """One teaser card, whichever listing it came from."""

    url: str
    title: str
    summary: str = ""
    card_date: dt.date | None = None
    category: str = ""


@dataclass(frozen=True)
class PageAttachment:
    """One file link found inside an article's own attachment region."""

    url: str
    label: str


@dataclass(frozen=True)
class PageDetail:
    """One article page, reduced to plain text and validated links."""

    title: str
    published_date: dt.date | None
    body_text: str
    attachments: tuple[PageAttachment, ...]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


class _CardParser(HTMLParser):
    """Collect one record per teaser card, scoped to the card's subtree.

    Configured with the card class and the region classes to capture inside
    it, so the same depth-tracking serves both listings. The pager, the
    sidebar and the cover live outside a card and never reach a record.
    """

    def __init__(self, *, card_class: str, title_class: str, regions: dict[str, str]):
        super().__init__(convert_charrefs=True)
        self._card_class = card_class
        self._title_class = title_class
        self._region_classes = regions
        self.depth = 0
        self._suppress = 0
        self._card_depth: int | None = None
        self._title_depth: int | None = None
        self._in_title_link = False
        self._open_regions: dict[str, int] = {}
        self._current: dict | None = None
        self.cards: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._suppress += 1
            return
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()

        if self._card_depth is None and self._card_class in classes:
            self._card_depth = self.depth
            self._current = {"url": "", "title": ""}
            for region in self._region_classes.values():
                self._current[region] = ""

        if self._current is not None:
            if self._title_class in classes and self._title_depth is None:
                self._title_depth = self.depth
            for spec, region in self._region_classes.items():
                if spec in classes and region not in self._open_regions:
                    self._open_regions[region] = self.depth
            if tag == "a" and self._title_depth is not None:
                href = (attributes.get("href") or "").strip()
                if href and not self._current["url"]:
                    self._current["url"] = href
                self._in_title_link = True
            if tag in _BLOCK_TAGS:
                for region in self._open_regions:
                    self._current[region] += " "

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
        if self._current is not None and tag in _BLOCK_TAGS:
            for region in self._open_regions:
                self._current[region] += " "
        for region, depth in list(self._open_regions.items()):
            if self.depth <= depth:
                del self._open_regions[region]
        if self._title_depth is not None and self.depth <= self._title_depth:
            self._title_depth = None
        if self._card_depth is not None and self.depth <= self._card_depth:
            if self._current is not None:
                record = {key: " ".join(value.split()) for key, value in self._current.items()}
                self.cards.append(record)
            self._card_depth = None
            self._title_depth = None
            self._in_title_link = False
            self._open_regions = {}
            self._current = None

    def handle_data(self, data):
        if self._suppress or self._current is None:
            return
        if self._in_title_link:
            self._current["title"] += data
        for region in self._open_regions:
            self._current[region] += data


class _DetailParser(HTMLParser):
    """One article page: title, date, article text, and its own file links.

    Everything is scoped to the first `node--type-news node--view-mode-full`
    subtree. The same node rendered again in a sideblock further down cannot
    double the text, and a file link in a sidebar cannot become an attachment.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self._suppress = 0
        self._node_depth: int | None = None
        self._node_seen = False
        self._title_depth: int | None = None
        self._date_depth: int | None = None
        self._link_href: str | None = None
        self._link_label: list[str] = []
        self.title_parts: list[str] = []
        self.date_parts: list[str] = []
        self.body_parts: list[str] = []
        self.attachments: list[PageAttachment] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._suppress += 1
            return
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()

        if (
            self._node_depth is None
            and not self._node_seen
            and all(marker in classes for marker in DETAIL_NODE_CLASSES)
        ):
            self._node_depth = self.depth
            self._node_seen = True

        if self._node_depth is not None:
            if DETAIL_TITLE_CLASS in classes and self._title_depth is None:
                self._title_depth = self.depth
            if DETAIL_DATE_CLASS in classes and self._date_depth is None:
                self._date_depth = self.depth
            if tag == "a" and ATTACHMENT_LINK_CLASS in classes:
                self._link_href = (attributes.get("href") or "").strip()
                self._link_label = []
            if tag in _BLOCK_TAGS:
                self.body_parts.append(" ")

        if tag not in _VOID_TAGS:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._suppress = max(0, self._suppress - 1)
            return
        if tag not in _VOID_TAGS:
            self.depth -= 1
        if tag == "a" and self._link_href is not None:
            label = " ".join("".join(self._link_label).split())
            self.attachments.append(PageAttachment(url=self._link_href, label=label))
            self._link_href = None
            self._link_label = []
        if self._node_depth is not None and tag in _BLOCK_TAGS:
            self.body_parts.append(" ")
        if self._title_depth is not None and self.depth <= self._title_depth:
            self._title_depth = None
        if self._date_depth is not None and self.depth <= self._date_depth:
            self._date_depth = None
        if self._node_depth is not None and self.depth <= self._node_depth:
            self._node_depth = None

    def handle_data(self, data):
        if self._suppress or self._node_depth is None:
            return
        if self._title_depth is not None:
            self.title_parts.append(data)
        if self._date_depth is not None:
            self.date_parts.append(data)
        if self._link_href is not None:
            self._link_label.append(data)
        self.body_parts.append(data)


def parse_meie_arvamus_listing(html: str) -> list[ListingCard]:
    """Cards from one `Meie arvamus` listing page. No dates — the card has none."""
    parser = _CardParser(
        card_class=MEIE_ARVAMUS_CARD_CLASS,
        title_class=MEIE_ARVAMUS_TITLE_CLASS,
        regions={MEIE_ARVAMUS_SUMMARY_CLASS: "summary"},
    )
    parser.feed(html)
    parser.close()
    return [
        ListingCard(url=card["url"], title=card["title"], summary=card.get("summary", ""))
        for card in parser.cards
        if card["url"]
    ]


def parse_news_listing(html: str) -> list[ListingCard]:
    """Cards from one news listing page, each with its full card date."""
    parser = _CardParser(
        card_class=NEWS_CARD_CLASS,
        title_class=NEWS_TITLE_CLASS,
        regions={NEWS_DATE_CLASS: "date", NEWS_CATEGORY_CLASS: "category"},
    )
    parser.feed(html)
    parser.close()
    cards = []
    for card in parser.cards:
        if not card["url"]:
            continue
        cards.append(
            ListingCard(
                url=card["url"],
                title=card["title"],
                card_date=_parse_full_date(card.get("date", "")),
                category=card.get("category", ""),
            )
        )
    return cards


def parse_news_detail(html: str, *, base_url: str) -> PageDetail:
    """Title, date, bounded text and validated attachment links from one article."""
    parser = _DetailParser()
    parser.feed(html)
    parser.close()

    attachments = []
    seen: set[str] = set()
    for attachment in parser.attachments:
        absolute = urljoin(base_url, attachment.url)
        if not is_attachment_url(absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        attachments.append(PageAttachment(url=absolute, label=attachment.label))

    title = " ".join("".join(parser.title_parts).split())
    return PageDetail(
        title=title[:MAX_TOPIC_TITLE_LENGTH],
        published_date=_parse_full_date(" ".join(parser.date_parts)),
        body_text=to_plain_text(
            "".join(parser.body_parts), limit=settings.KODA_OPINIONS_BODY_MAX_LENGTH
        ),
        attachments=tuple(attachments),
    )


def _parse_full_date(text: str) -> dt.date | None:
    match = FULL_DATE_PATTERN.search(text or "")
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# URL rules
# --------------------------------------------------------------------------


def is_article_url(url: str) -> bool:
    """Whether a listing link is a Koda.ee news article this collector reads."""
    if not is_allowed_public_url(url, allowed_hosts=settings.KODA_ALLOWED_HOSTS):
        return False
    if len(url) > MAX_CANONICAL_URL_LENGTH:
        return False
    path = urlparse(url).path.rstrip("/")
    prefix = settings.KODA_OPINIONS_ARTICLE_PATH_PREFIX
    return f"{path}/".startswith(prefix) and path != prefix.rstrip("/")


def is_attachment_url(url: str) -> bool:
    """Whether a file link is a Koda.ee-hosted PDF this collector may fetch."""
    if not is_allowed_public_url(url, allowed_hosts=settings.KODA_ALLOWED_HOSTS):
        return False
    if len(url) > MAX_CANONICAL_URL_LENGTH:
        return False
    parts = urlparse(url)
    if not parts.path.startswith(settings.KODA_OPINIONS_FILE_PATH_PREFIX):
        return False
    return unquote(parts.path).lower().endswith(".pdf")


def canonical_article_url(url: str) -> str:
    """One address per article: scheme and host normalised, no query, no fragment."""
    parts = urlparse(url)
    path = parts.path.rstrip("/")
    return f"https://{(parts.hostname or '').lower()}{path}"


# --------------------------------------------------------------------------
# Opinion evidence
# --------------------------------------------------------------------------


def opinion_evidence(
    *,
    listed_in_meie_arvamus: bool,
    title: str,
    body_text: str,
    attachments: tuple[PageAttachment, ...] | list[PageAttachment],
) -> list[str]:
    """Why this page counts as opinion material. Empty means it does not.

    Being listed under `Meie arvamus` is Koda.ee's own editorial statement and
    suffices alone. Elsewhere two independent kinds of evidence exist: the
    article wording the Chamber's position, and an attachment whose filename
    parses as an opinion letter. A statute named in ordinary news is neither.
    """
    codes: list[str] = []
    if listed_in_meie_arvamus:
        codes.append(EVIDENCE_LISTED_MEIE_ARVAMUS)

    haystack = normalise_for_classification(f"{title} {body_text}")
    if any(pattern.search(haystack) for pattern in POSITION_PHRASES) or any(
        pattern.search(normalise_for_classification(title)) for pattern in OPINION_PATTERNS
    ):
        codes.append(EVIDENCE_OPINION_VOCABULARY)

    for attachment in attachments:
        parsed = parse_opinion_filename(attachment_filename(attachment))
        if parsed.date is not None and parsed.subject:
            codes.append(EVIDENCE_OPINION_ATTACHMENT)
            break

    return codes


def attachment_filename(attachment: PageAttachment) -> str:
    """The filename an attachment link names, decoded from its URL path."""
    path = unquote(urlparse(attachment.url).path)
    name = path.rsplit("/", 1)[-1]
    return name or attachment.label


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


def fetch_html(url: str, *, session=None) -> str:
    try:
        result = fetch(
            url,
            allowed_hosts=settings.KODA_ALLOWED_HOSTS,
            accept="text/html",
            max_bytes=settings.KODA_OPINIONS_MAX_HTML_BYTES,
            expected_content_types=ACCEPTED_HTML_CONTENT_TYPES,
            session=session,
        )
    except PublicFetchError as error:
        raise PublicOpinionCollectionError(str(error)) from error
    return result.text()


def fetch_pdf(url: str, *, session=None) -> bytes:
    """Fetch one attachment. The caller validates the bytes; this validates the transport."""
    try:
        result = fetch(
            url,
            allowed_hosts=settings.KODA_ALLOWED_HOSTS,
            accept="application/pdf",
            max_bytes=settings.LEGAL_OPINION_MAX_PDF_BYTES,
            expected_content_types=ACCEPTED_PDF_CONTENT_TYPES,
            session=session,
        )
    except PublicFetchError as error:
        raise PublicOpinionCollectionError(str(error)) from error
    return result.content
