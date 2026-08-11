"""Reading the names and dates of articles that scrolled out before DashKoda
watched.

The catalogue records every article the feed shows from now on. It cannot
recover the years of articles that had already rolled past — nothing in this
application ever saw them. Their titles and publication dates are still on the
public site, and this reads them.

Two passes, because they ask opposite questions:

- `discover_news_titles` asks **which measured paths are missing from the
  catalogue**, busiest first, because a title matters where an article is shown
  and an article is shown because it ranks;
- `backfill_news_dates` asks **which catalogue rows have no date**. That
  question only became answerable once the first pass had finished, and it had
  three and a half thousand answers.

**The date claim was wrong for a long time.** This module asserted that a Koda.ee
article page "does not reliably carry a publication date" and catalogued every
recovered article as undated on that basis. It does carry one — schema.org
`datePublished`, timezone-aware, back to at least 2017, on forty of forty pages
sampled. The assertion was never re-checked against the pages, and it cost the
news archive its entire publication history: 3 602 of 3 614 rows undated, so the
period filters had twelve articles to work on.

**Bounded and polite, not a crawl.** Each pass has a ceiling, pauses between
requests, and is resumable — running it again continues where it stopped,
because what is still missing is a query rather than a cursor.

Only pages under the news prefix are fetched, only from Koda.ee, and only the
title and the publication date are kept. No body, no summary, no other page.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.core.public_http import PublicFetchError, fetch
from apps.core.structured_data import find_by_type
from apps.visibility.content_sections import SECTION_NEWS
from apps.visibility.ga4_selectors import get_top_pages

from .catalogue import uncatalogued_paths, undated_paths
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


def parse_published_at(html: str) -> dt.datetime | None:
    """The article's publication moment, from its own JSON-LD.

    Koda.ee article pages describe themselves as `NewsArticle` and carry
    `datePublished` as a timezone-aware ISO 8601 timestamp — `2017-02-20T15:
    47:09+02:00` — going back at least to 2017. Forty of forty sampled pages
    had one.

    That contradicts what this module used to assert. The comment here said the
    page "does not reliably carry a publication date", and on that basis every
    article recovered from the public site was catalogued undated: 3 602 of
    3 614 rows, which left the news archive's publication-period filters with
    twelve articles to work on. The claim was simply never re-checked against
    the pages.

    Three shapes are refused rather than guessed at:

    - **a listing page.** `/en/news` has no `datePublished`, and must not be
      dated from the first article it happens to list;
    - **a date without a time.** Accepted, at midnight in the application's
      timezone, because "published on the 4th" is a true and useful fact;
    - **a moment in the future.** The feed collector refuses anything more than
      `KODA_NEWS_MAX_FUTURE_DAYS` ahead so a mis-dated item cannot pin itself to
      the top forever, and a page read gets the same guard.
    """
    article = find_by_type(html, "NewsArticle") or find_by_type(html, "Article")
    if article is None:
        return None

    raw = article.get("datePublished") or article.get("dateCreated")
    if not isinstance(raw, str) or not raw.strip():
        return None

    text = raw.strip()
    try:
        moment = dt.datetime.fromisoformat(text)
    except ValueError:
        return None

    if timezone.is_naive(moment):
        # A bare date or a naive timestamp is read in the application's own
        # zone, which is the zone the Chamber publishes in.
        moment = timezone.make_aware(moment, timezone.get_current_timezone())

    horizon = timezone.now() + timedelta(days=settings.KODA_NEWS_MAX_FUTURE_DAYS)
    if moment > horizon:
        return None
    return moment


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
                    # Read off the page's own JSON-LD, and `None` when the page
                    # does not state one — an undated row is honest, an invented
                    # date is not.
                    "published_at": parse_published_at(page.text()),
                    "title_origin": TitleOrigin.PAGE,
                    "last_seen_at": now,
                },
            )
        tally.named += 1
        sleep()

    return tally


def backfill_news_dates(
    *, limit: int = MAX_PAGES_PER_RUN, dry_run: bool = False, session=None, sleep=_pause
) -> DiscoveryTally:
    """Read publication dates for catalogued articles that have none.

    A separate pass from `discover_news_titles` because the two ask opposite
    questions. Naming asks "which measured paths are missing from the
    catalogue"; after that backfill ran, the answer is nothing. Dating asks
    "which catalogue rows have no date", and the answer was three and a half
    thousand rows — every article recovered from the public site, because this
    module used to believe the pages carried no date.

    `named` counts rows that gained a date; `unnamed` counts pages that were
    read and still stated none. A page that cannot be fetched keeps whatever row
    it has, exactly as in the title pass: one bad fetch must never blank a date.

    Only `published_at` and `last_seen_at` are written. The title is left alone
    — it is already there, a page read is not more authoritative than the feed
    that may have set it, and re-deciding it here would let one pass quietly
    undo the other.
    """
    tally = DiscoveryTally()
    paths = undated_paths(limit=limit)
    tally.considered = len(paths)
    now = timezone.now()

    for path in paths:
        try:
            page = fetch(
                f"https://www.koda.ee{path}",
                allowed_hosts=settings.KODA_ALLOWED_HOSTS,
                accept="text/html",
                max_bytes=MAX_PAGE_BYTES,
                expected_content_types=HTML_TYPES,
                session=session,
            )
        except PublicFetchError:
            tally.failed += 1
            sleep()
            continue

        tally.fetched += 1
        published_at = parse_published_at(page.text())
        if published_at is None:
            # Read, and genuinely undated. A listing page under the news prefix
            # looks exactly like this and must stay undated.
            tally.unnamed += 1
            sleep()
            continue

        if not dry_run:
            # Re-fetched rather than held from the selection query, because the
            # feed may have catalogued a date in between — and a date the
            # Chamber published outranks one read off the page.
            resource = NewsResource.objects.filter(path=path, published_at__isnull=True).first()
            if resource is not None:
                resource.published_at = published_at
                resource.last_seen_at = now
                # `published_at` is in `MUTABLE_FIELDS`; the canonical URL and
                # the path are not, and this must not touch them.
                resource.save(update_fields=["published_at", "last_seen_at"])
        tally.named += 1
        sleep()

    return tally


__all__ = [
    "MAX_PAGES_PER_RUN",
    "DiscoveryTally",
    "backfill_news_dates",
    "discover_news_titles",
    "parse_published_at",
    "parse_title",
    "unnamed_news_paths",
]
