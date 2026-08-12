"""Reducing source HTML to bounded, safe plain text.

Four collectors need the same thing: take whatever markup a public page or feed
hands them and end up with prose that can be stored, matched on and shown
without carrying a script, a style block or an unclosed tag along with it.

Like `public_http`, this holds the mechanism and none of the policy. **The
caller states its own limit**, because how much text is worth keeping is a
question about that source -- a listing card's summary, an article body, a
detail page -- and not something a shared helper should decide for it.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser


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
    """Reduce source HTML to plain text, truncated to ``limit`` if given."""
    if not value:
        return ""
    extractor = _TextExtractor()
    extractor.feed(unescape(value))
    extractor.close()
    text = extractor.text
    # A stray tag written as an entity survives unescaping; strip any residue.
    text = " ".join(re.sub(r"<[^>]*>", " ", text).split())
    if limit is not None and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text
