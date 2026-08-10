"""Reading the titles of articles that scrolled out before DashKoda watched.

The catalogue records every article the feed shows from now on. It cannot
recover the three years of articles that had already rolled past — nothing in
this application ever saw them. Their titles are still on the public site, and
this reads them.

**Bounded and prioritised, not a crawl.** Titles matter where an article is
shown, and an article is shown because it ranks, so the work is ordered by
measured traffic: the most-viewed unnamed article is fetched first. A run has a
ceiling, pauses between requests, and is resumable — running it again continues
where it stopped, because "which paths are still unnamed" is a query rather than
a cursor.

Only pages under the news prefix are fetched, only from Koda.ee, and only their
title is kept. No body, no summary, no other page.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.core.public_http import PublicFetchError, fetch
from apps.visibility.content_sections import SECTION_NEWS
from apps.visibility.ga4_selectors import get_top_pages

from .catalogue import uncatalogued_paths
from .public_models import NewsResource, TitleOrigin

#: How many pages one run may fetch. The events discovery uses the same ceiling
#: for the same reason: a scheduled job that fetches a thousand pages is not a
#: job, it is an incident.
MAX_PAGES_PER_RUN = 150

#: Between requests, so a backfill is a visitor rather than a load test.
REQUEST_PAUSE_SECONDS = 0.5

#: How much of a page is read. A title is in the first few kilobytes; the rest
#: is not wanted and is not downloaded.
MAX_PAGE_BYTES = 512 * 1024

HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})

#: The Chamber's pages end their title with the site name. Kept out of the
#: catalogue: repeated on every row it is noise, and it is not part of the
#: article's name.
SITE_SUFFIXES = (" | Eesti Kaubandus-Tööstuskoda", " - Eesti Kaubandus-Tööstuskoda")

_OG_TITLE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.I
)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class DiscoveryTally:
    """What a run did. Counts only — never a title, a URL or page text."""

    considered: int = 0
    fetched: int = 0
    named: int = 0
    unnamed: int = 0
    failed: int = 0

    def as_dict(self) -> dict:
        return {
            "considered": self.considered,
            "fetched": self.fetched,
            "named": self.named,
            "unnamed": self.unnamed,
            "failed": self.failed,
        }


def parse_title(html: str) -> str:
    """The article's own name, or an empty string.

    `og:title` first: the Chamber's pages set it to the headline alone, while
    `<title>` carries the site name as well. Both are unescaped and stripped of
    the suffix, and a page yielding nothing is left uncatalogued rather than
    catalogued under a guess.
    """
    import html as html_module

    found = _OG_TITLE.search(html)
    if found is None:
        found = _TITLE.search(html)
    if found is None:
        return ""

    title = html_module.unescape(_TAGS.sub("", found.group(1))).strip()
    for suffix in SITE_SUFFIXES:
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    return title


def _pause() -> None:
    if REQUEST_PAUSE_SECONDS > 0:
        time.sleep(REQUEST_PAUSE_SECONDS)


def unnamed_news_paths(*, limit: int, coverage_days: int = 5 * 365) -> tuple[str, ...]:
    """The most-viewed news paths the catalogue cannot name, busiest first.

    Ordered by measured traffic because that is the order they will be read in:
    a ranking's top row matters more than its thousandth.
    """
    end = timezone.localdate()
    start = end - timedelta(days=coverage_days)
    ranked = get_top_pages(
        start=start,
        end=end,
        # A wide net, then filtered to what is still unnamed. The top hundred
        # paths are mostly already catalogued after the first run.
        limit=max(limit * 8, 200),
        prefix=SECTION_NEWS.prefixes,
    )
    return uncatalogued_paths(row.path for row in ranked)[:limit]


def discover_news_titles(
    *, limit: int = MAX_PAGES_PER_RUN, dry_run: bool = False, session=None, sleep=_pause
) -> DiscoveryTally:
    """Fetch and catalogue titles for unnamed news pages."""
    tally = DiscoveryTally()
    paths = unnamed_news_paths(limit=limit)
    tally.considered = len(paths)
    now = timezone.now()

    for path in paths:
        url = f"https://www.koda.ee{path}"
        try:
            page = fetch(
                url,
                allowed_hosts=settings.KODA_ALLOWED_HOSTS,
                accept="text/html",
                max_bytes=MAX_PAGE_BYTES,
                expected_content_types=HTML_TYPES,
                session=session,
            )
        except PublicFetchError:
            # A page that has been taken down is not an error worth stopping
            # for: its traffic is still real and its row still shows the path.
            tally.failed += 1
            sleep()
            continue

        tally.fetched += 1
        title = parse_title(page.text())
        if not title:
            tally.unnamed += 1
            sleep()
            continue

        if not dry_run:
            # `get_or_create`, not `update_or_create`: only unnamed paths reach
            # this loop, but if the feed catalogued one in the meantime its
            # entry is authoritative — the Chamber wrote that title and knows
            # the publication date, and a page read must not overwrite either.
            NewsResource.objects.get_or_create(
                path=path,
                defaults={
                    "canonical_url": url,
                    "title": title,
                    # The page does not reliably carry a publication date, and
                    # inventing one would put a wrong date beside a right title.
                    "published_at": None,
                    "title_origin": TitleOrigin.PAGE,
                    "last_seen_at": now,
                },
            )
        tally.named += 1
        sleep()

    return tally


__all__ = [
    "MAX_PAGES_PER_RUN",
    "DiscoveryTally",
    "discover_news_titles",
    "parse_title",
    "unnamed_news_paths",
]
