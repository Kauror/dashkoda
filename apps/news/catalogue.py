"""Keeping the news catalogue current, and reading titles out of it.

Every sync records what the feed showed, so an article is catalogued the first
time DashKoda ever sees it and stays catalogued after it scrolls out. Nothing
here deletes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from django.utils import timezone

from apps.visibility.ga4_paths import canonical_path

from .public_models import NewsResource, TitleOrigin


def record_feed_items(items: Iterable, *, now=None) -> tuple[int, int]:
    """Catalogue every item a sync just published. Returns (added, refreshed).

    Upsert by canonical path. A title that changed on the Chamber's own feed is
    a correction and is taken; an article already known keeps its identity and
    its first-seen date.

    Called inside the sync's transaction, so a snapshot and the catalogue rows
    it produced either both exist or neither does.
    """
    now = now or timezone.now()
    rows = list(items)
    if not rows:
        return (0, 0)

    by_path = {}
    for item in rows:
        path = canonical_path(getattr(item, "canonical_url", ""))
        if path:
            by_path[path] = item

    if not by_path:
        return (0, 0)

    existing = {
        resource.path: resource for resource in NewsResource.objects.filter(path__in=tuple(by_path))
    }

    added = refreshed = 0
    for path, item in by_path.items():
        resource = existing.get(path)
        if resource is None:
            NewsResource.objects.create(
                canonical_url=item.canonical_url,
                path=path,
                title=item.title,
                published_at=item.published_at,
                title_origin=TitleOrigin.FEED,
                last_seen_at=now,
            )
            added += 1
            continue

        resource.title = item.title
        resource.published_at = item.published_at
        resource.title_origin = TitleOrigin.FEED
        resource.last_seen_at = now
        resource.save(update_fields=["title", "published_at", "title_origin", "last_seen_at"])
        refreshed += 1
    return (added, refreshed)


def titles_for(paths: Sequence[str]) -> dict[str, tuple[str, object]]:
    """Titles and publication dates for canonical paths, in one query.

    The lookup the content ranking uses. A path the catalogue has never seen is
    absent, and the ranking shows the path — which is still the honest answer,
    just a rarer one now.
    """
    if not paths:
        return {}
    return {
        resource.path: (resource.title, resource.published_at)
        for resource in NewsResource.objects.filter(path__in=tuple(paths)).only(
            "path", "title", "published_at"
        )
    }


def uncatalogued_paths(paths: Iterable[str]) -> tuple[str, ...]:
    """Which of these the catalogue cannot name yet, in the order given."""
    wanted = [path for path in paths if path]
    if not wanted:
        return ()
    known = set(NewsResource.objects.filter(path__in=wanted).values_list("path", flat=True))
    return tuple(path for path in wanted if path not in known)


__all__ = ["record_feed_items", "titles_for", "uncatalogued_paths"]
