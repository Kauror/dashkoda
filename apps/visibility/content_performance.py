"""Turning a ranked GA4 path into something a board member can read.

A path is honest provenance and poor interface. `/et/sundmused/eesti-islandi-
arifoorum` is a real answer to "which page", and `Eesti–Islandi ärifoorum` is an
answer to the question actually being asked.

The division of labour is strict, and it is the whole reason this module is
separate from the GA4 selectors:

- **GA4 owns the path, the views and the dates.** It knows nothing about what a
  page *is*;
- **DashKoda's content modules own the title, the type and the publication
  date.** A page is a news item because `NewsItem` says so, never because its
  path contains `uudised`.

## Titles are looked up, never invented

A path with no matching content object shows the path. Turning
`/et/teenused/ekspordi-arendamine` into "Ekspordi arendamine" would put a
sentence nobody wrote next to a number somebody measured, and it would look
exactly as authoritative as a real title.

## What can and cannot be named

`PublicEventResource` is a durable catalogue: it keeps a public event page after
the event has passed, so an event path from 2023 still resolves to its title.

News is not durable in the same way. The Koda.ee feed is a rolling window and
retired snapshots are pruned weekly, so DashKoda knows the titles of the current
items and nothing about articles that have rolled out. Those rank correctly and
display as paths. Fixing that means giving news a durable catalogue of its own,
which is a larger change than this one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from .content_sections import ContentSection, section_of
from .ga4_paths import canonical_path, percent_decoded

#: Where a public page lives. Used to turn a stored path back into a link.
PUBLIC_ORIGIN = "https://www.koda.ee"


@dataclass(frozen=True)
class ContentPerformanceRow:
    """One row of the ranking: a page, what it is, and how much it was read."""

    path: str
    page_views: int
    title: str = ""
    type_label: str = ""
    published_on: date | None = None
    event_date: date | None = None

    @property
    def has_known_identity(self) -> bool:
        return bool(self.title)

    @property
    def label(self) -> str:
        """What the row is called: the real title, or the path itself.

        Percent-decoded for display only — `/et/uudised/t%C3%B6%C3%B6turg` reads
        as noise. The key it was matched on is never the decoded form.
        """
        return self.title or percent_decoded(self.path)

    @property
    def url(self) -> str:
        return f"{PUBLIC_ORIGIN}{self.path}"


def _news_titles(paths: Sequence[str]) -> dict[str, tuple[str, date | None]]:
    """Titles for the news paths DashKoda still holds, in one query."""
    from apps.news.models import NewsItem
    from apps.news.selectors import get_current_news_snapshot

    snapshot = get_current_news_snapshot()
    if snapshot is None:
        return {}

    found: dict[str, tuple[str, date | None]] = {}
    for item in NewsItem.objects.filter(snapshot=snapshot).only(
        "canonical_url", "title", "published_at"
    ):
        path = canonical_path(item.canonical_url)
        if path in paths and path not in found:
            moment = item.published_at
            found[path] = (item.title, moment.date() if moment else None)
    return found


def _event_titles(paths: Sequence[str]) -> dict[str, tuple[str, date | None]]:
    """Titles for event paths, from the durable public catalogue, in one query.

    Straight from `PublicEventResource` rather than through
    `EventProgrammeItem`: the catalogue is what knows a public page's title, it
    keeps knowing after the event has passed, and one programme row is not the
    authority on a page that several of them may point at.
    """
    from apps.events.public_models import PublicEventResource

    found: dict[str, tuple[str, date | None]] = {}
    for resource in PublicEventResource.objects.only("canonical_url", "title", "starts_on"):
        path = canonical_path(resource.canonical_url)
        if path in paths and path not in found:
            found[path] = (resource.title, resource.starts_on)
    return found


def describe_pages(
    totals: Iterable, *, section: ContentSection | None = None
) -> tuple[ContentPerformanceRow, ...]:
    """Name each ranked page, using DashKoda's own content as the authority.

    `totals` is whatever `get_top_pages` returned — a bounded slice, so the two
    title lookups below cover a handful of paths rather than a history.
    """
    rows = list(totals)
    if not rows:
        return ()

    paths = {row.path for row in rows}
    news = _news_titles(paths)
    events = _event_titles(paths)

    described = []
    for row in rows:
        kind = section_of(row.path)
        title = ""
        published_on = None
        event_date = None

        if row.path in events:
            title, event_date = events[row.path]
        elif row.path in news:
            title, published_on = news[row.path]

        described.append(
            ContentPerformanceRow(
                path=row.path,
                page_views=row.page_views,
                title=title,
                # The badge names the section, which is a fact about the URL.
                # It is shown whether or not a title was found, because "this
                # is an event page we cannot name" is still worth knowing.
                type_label=(kind.item_label if kind and section and section.is_everything else ""),
                published_on=published_on,
                event_date=event_date,
            )
        )
    return tuple(described)


__all__ = ["PUBLIC_ORIGIN", "ContentPerformanceRow", "describe_pages"]
