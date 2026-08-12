"""A durable catalogue of Koda.ee news pages, kept after the feed forgets them.

The Koda.ee news feed is a rolling window of ten items, and retired
`NewsSnapshot` rows are pruned after a week. So DashKoda knew the titles of the
current ten articles and nothing whatever about the thousands that had scrolled
past — which is why a three-year traffic ranking could count an article's views
precisely and then label the row `/et/uudised/riigikogu-vottis-vastu-…`.

`NewsResource` is the same answer `PublicEventResource` gives for events: one
row per canonical public page, written once and re-observed thereafter, outliving
the snapshot it was first seen in.

Two things it is deliberately **not**:

- **not a view counter.** No traffic figure is stored here. Views are derived
  from GA4 facts at read time; a number cached on a content row goes stale the
  moment the next sync publishes a revision;
- **not a copy of the article.** Title and publication date, which is what a
  ranking row needs to be readable. No body, no summary, no author.

Rows are never deleted. `apps/sources/retention.py` prunes snapshots; this is a
catalogue, and the whole point of it is that it survives them.
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.core.immutability import ImmutableWriteGuard


#: Where a title came from. A feed entry is authoritative — the Chamber wrote it
#: — while a page fetch is a reading of the public site, and the two are worth
#: telling apart when one contradicts the other.
class TitleOrigin(models.TextChoices):
    FEED = "feed", "Uudisvoost"
    PAGE = "page", "Avalikult lehelt"


class NewsResourceImmutable(RuntimeError):
    """Raised when something tries to rewrite a resource's identity."""


class NewsResource(ImmutableWriteGuard, models.Model):
    """One canonical Koda.ee news page, kept after it leaves the feed."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    canonical_url = models.URLField(max_length=500, unique=True, verbose_name="Viide")
    #: The canonical path, stored so a GA4 join is an indexed equality match
    #: rather than a URL parsed on every row of every ranking.
    path = models.CharField(max_length=500, unique=True, db_index=True, verbose_name="Tee")
    title = models.TextField(verbose_name="Pealkiri")
    published_at = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name="Avaldatud"
    )
    title_origin = models.CharField(
        max_length=8, choices=TitleOrigin, verbose_name="Pealkirja allikas"
    )
    #: Whose news this is — the Chamber's own or a partner's. Blank where
    #: DashKoda has not been told, which is a real state and not a third
    #: category: nothing public exposes this field, so an article is classified
    #: only once it has been read from Koda.ee.
    category = models.CharField(max_length=32, blank=True, db_index=True, verbose_name="Kategooria")
    first_seen_at = models.DateTimeField(auto_now_add=True, verbose_name="Esmakordselt nähtud")
    last_seen_at = models.DateTimeField(verbose_name="Viimati nähtud")

    #: Identity is fixed; the description may be corrected.
    MUTABLE_FIELDS = frozenset(
        {"title", "published_at", "title_origin", "category", "last_seen_at"}
    )
    IMMUTABLE_ERROR = NewsResourceImmutable
    IMMUTABLE_MESSAGE = "A news resource's canonical URL and path may not change."
    ALLOW_UNRESTRICTED_SAVE = True

    class Meta:
        ordering = ("-published_at", "path")
        verbose_name = "Uudise avalik leht"
        verbose_name_plural = "Uudiste avalikud lehed"
        indexes = [models.Index(fields=["path"])]

    def __str__(self) -> str:
        return self.title[:80] or self.path


__all__ = ["NewsResource", "NewsResourceImmutable", "TitleOrigin"]
