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

News now has one too. `apps.news.catalogue` records every article the feed has
ever shown, so an item that scrolled out of the ten-item window years ago is
still nameable long after its snapshot was pruned. Before that existed, a
three-year ranking could count an article's views exactly and then label the row
with its URL.

A path still shows as a path when nothing in the catalogue matches it — an
article published before DashKoda started watching, or a page that is not
content at all. That is the honest answer, just a rarer one now.
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
    #: Every measured view, across the whole of GA4's coverage. `None` in the
    #: ranking, where the question is only ever about the chosen period; set in
    #: a search result, where the two figures answer different questions and
    #: both are shown. `None` rather than `0`, because a page whose total was
    #: never asked for has not been measured at zero.
    total_views: int | None = None

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
    """Titles for news paths, from the durable catalogue, in one query.

    `apps.news.catalogue` keeps a row per article from the first snapshot that
    ever showed it, so an article that scrolled out of the ten-item feed two
    years ago is still nameable. Before it existed this read the current
    snapshot, which meant a three-year ranking could count an article's views
    exactly and then label the row with its URL.
    """
    from apps.news.catalogue import titles_for

    found: dict[str, tuple[str, date | None]] = {}
    for path, (title, published_at) in titles_for(paths).items():
        found[path] = (title, published_at.date() if published_at else None)
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


#: How many catalogue matches one search may contribute. A term like "a" would
#: otherwise name every article the Chamber has ever published; the ranking that
#: follows is paginated anyway, so an unbounded path set buys nothing.
MAX_TITLE_MATCHES = 300


def paths_for_title(term: str, *, limit: int = MAX_TITLE_MATCHES) -> tuple[str, ...]:
    """Canonical paths whose **known** title matches `term`.

    This is what lets a search for "islandi" find
    `/et/sundmused/eesti-islandi-arifoorum`, whose slug does contain it, and
    equally what lets a search for a title find a page whose slug does not.

    Only titles DashKoda already holds on authority: the durable news catalogue
    and the public event catalogue. Nothing is derived from a slug — see
    `describe_pages`, which shows a path when it does not know a name. A search
    that invented "Ekspordi arendamine" from `/et/teenused/ekspordi-arendamine`
    would be inventing the very thing the row is careful not to claim.

    Two bounded queries, both `icontains` on an indexed catalogue. Services have
    no title catalogue yet, and that costs nothing here: a service is still
    found by its path, which is what `search_pages` matches on.
    """
    term = (term or "").strip()
    if not term:
        return ()

    from apps.events.public_models import PublicEventResource
    from apps.news.public_models import NewsResource

    found: list[str] = list(
        NewsResource.objects.filter(title__icontains=term).values_list("path", flat=True)[:limit]
    )
    found.extend(
        canonical_path(url)
        for url in PublicEventResource.objects.filter(title__icontains=term).values_list(
            "canonical_url", flat=True
        )[:limit]
    )
    return tuple(dict.fromkeys(path for path in found if path))


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
                # A ranking row calls it `page_views`; a search match calls it
                # `period_views` and carries a total beside it. Both mean views
                # inside the chosen window, so both land in the same field.
                page_views=getattr(row, "page_views", None) or getattr(row, "period_views", 0),
                total_views=getattr(row, "total_views", None),
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
