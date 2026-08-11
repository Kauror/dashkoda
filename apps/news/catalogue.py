"""Keeping the news catalogue current, and reading titles out of it.

Every sync records what the feed showed, so an article is catalogued the first
time DashKoda ever sees it and stays catalogued after it scrolls out. Nothing
here deletes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from django.utils import timezone

from apps.visibility.ga4_paths import canonical_path

from .categories import parse_category
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


def undated_paths(*, limit: int) -> tuple[str, ...]:
    """Catalogued articles whose publication date is still unknown.

    The inverse of `uncatalogued_paths`, and it exists because the two backfills
    ask opposite questions. Naming articles asks "which measured paths are *not*
    in the catalogue"; dating them asks "which catalogue rows have no date" —
    and after the naming backfill ran, the first question returns nothing while
    the second returns three and a half thousand rows.

    Newest first by discovery, so a run that is interrupted has done the most
    recently found ones rather than an arbitrary slice.
    """
    return tuple(
        NewsResource.objects.filter(published_at__isnull=True)
        .order_by("-first_seen_at", "path")
        .values_list("path", flat=True)[:limit]
    )


def record_categories(
    rows: Iterable[tuple[str, str]], *, now=None, dry_run: bool = False
) -> tuple[int, int, int]:
    """Store whose news each article is. Returns (updated, unchanged, unknown).

    Rows are `(url or path, Koda.ee category value)`. Matching is by canonical
    path, the same join key everything else here uses, so an export may carry
    full URLs, bare paths or a mixture.

    A row naming a path the catalogue does not hold is counted as `unknown` and
    skipped rather than creating anything: this fills in a fact about an article
    DashKoda already knows, and inventing catalogue rows from a spreadsheet
    would let a stale export resurrect articles the site has removed.

    A value that is not one of the two real categories — `arhiiv` and the other
    listing names — leaves the row unclassified rather than storing a third
    kind.
    """
    now = now or timezone.now()
    wanted: dict[str, str] = {}
    unknown = 0
    for raw_url, raw_category in rows:
        path = canonical_path(raw_url)
        category = parse_category(raw_category)
        if not path or not category:
            unknown += 1
            continue
        wanted[path] = category

    if not wanted:
        return (0, 0, unknown)

    updated = unchanged = 0
    existing = NewsResource.objects.filter(path__in=tuple(wanted))
    seen = set()
    for resource in existing:
        seen.add(resource.path)
        category = wanted[resource.path]
        if resource.category == category:
            unchanged += 1
            continue
        updated += 1
        if dry_run:
            # Counted, not written. The flag lives here rather than in the
            # command so a dry run and a real one cannot disagree about what
            # would happen — they are the same code with one branch.
            continue
        resource.category = category
        resource.last_seen_at = now
        # `category` is in `MUTABLE_FIELDS`; identity is not, and this must not
        # touch it.
        resource.save(update_fields=["category", "last_seen_at"])

    return (updated, unchanged, unknown + len(set(wanted) - seen))
