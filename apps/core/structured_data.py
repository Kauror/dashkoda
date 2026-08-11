"""Reading schema.org JSON-LD out of a public page.

Koda.ee describes its own content in `application/ld+json` blocks, and two
collectors now depend on that: events read `Event`, news read `NewsArticle`.
Both need the same three unglamorous things — find the blocks, survive the ones
that are not valid JSON, and flatten `@graph` — so they share them rather than
each carrying a copy that drifts.

Nothing here fetches anything. It takes markup that a collector already has and
returns the entries it describes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

_SCRIPT = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S)


def json_ld_blocks(html: str) -> list:
    """Every parseable JSON-LD block, in document order.

    A block that is not valid JSON is skipped rather than raised on. A page with
    one broken block and one good one is common, and the good one is still the
    truth about the page.
    """
    blocks = []
    for raw in _SCRIPT.findall(html):
        try:
            blocks.append(json.loads(raw.strip()))
        except ValueError, TypeError:
            continue
    return blocks


def json_ld_entries(html: str) -> Iterator[dict]:
    """Every described thing on the page, `@graph` flattened.

    A block may be one object, a list of objects, or an object whose `@graph`
    holds the list — all three appear on Koda.ee, so a caller that handled only
    the first would work on some pages and quietly find nothing on others.
    """
    for block in json_ld_blocks(html):
        entries = block.get("@graph", []) if isinstance(block, dict) else block
        if isinstance(entries, dict):
            entries = [entries]
        for entry in entries or []:
            if isinstance(entry, dict):
                yield entry


def find_by_type(html: str, wanted: str) -> dict | None:
    """The first entry whose `@type` mentions `wanted`.

    Substring rather than equality: `@type` is legitimately a list, and a page
    may describe itself as `["NewsArticle", "Article"]`.
    """
    for entry in json_ld_entries(html):
        if wanted in str(entry.get("@type", "")):
            return entry
    return None


__all__ = ["find_by_type", "json_ld_blocks", "json_ld_entries"]
