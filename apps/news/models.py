"""Imported Koda.ee news snapshots and their items.

A snapshot is one complete reading of the public RSS feed. Publication is
all-or-nothing: either the whole snapshot becomes current or nothing changes and
the previous one stays exactly as it was.

Summaries are stored as sanitized plain text and truncated. Full article HTML is
deliberately not stored — this is a dashboard pointing at Koda.ee, not a copy of
it.
"""

from django.db import models
from django.db.models import Q

from apps.core.feeds import FeedResult
from apps.core.immutability import ImmutableWriteGuard
from apps.sources.models import DataSource


class NewsImmutable(RuntimeError):
    """Raised when something tries to rewrite an imported snapshot or item."""


class NewsSnapshot(ImmutableWriteGuard, models.Model):
    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="news_snapshots",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="news_snapshots",
        verbose_name="Algfail",
    )
    import_run = models.OneToOneField(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="news_snapshot",
        verbose_name="Impordikäivitus",
    )
    observed_at = models.DateTimeField(verbose_name="Vaatluse aeg")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")
    item_count = models.PositiveIntegerField(default=0, verbose_name="Uudiseid")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    MUTABLE_FIELDS = frozenset({"is_current"})
    IMMUTABLE_ERROR = NewsImmutable
    IMMUTABLE_MESSAGE = "An imported news snapshot may only change its is_current flag."

    class Meta:
        ordering = ("-observed_at", "-id")
        verbose_name = "Uudiste hetkeseis"
        verbose_name_plural = "Uudiste hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="newssnapshot_one_current_per_source",
            ),
        ]

    def __str__(self) -> str:
        return f"Uudised {self.observed_at:%d.%m.%Y} ({self.item_count})"


class NewsItem(ImmutableWriteGuard, models.Model):
    """One feed entry. Immutable once its snapshot has been written."""

    snapshot = models.ForeignKey(
        NewsSnapshot,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Hetkeseis",
    )
    guid = models.CharField(max_length=500, verbose_name="GUID")
    title = models.TextField(verbose_name="Pealkiri")
    canonical_url = models.URLField(max_length=500, verbose_name="Viide")
    published_at = models.DateTimeField(db_index=True, verbose_name="Avaldatud")
    # The feed does not currently emit a category element. The field exists so a
    # category is preserved if one ever appears; it is never invented.
    category = models.CharField(max_length=120, blank=True, verbose_name="Rubriik")
    summary = models.TextField(blank=True, verbose_name="Kokkuvõte")
    source_order = models.PositiveIntegerField(verbose_name="Järjekord allikas")

    IMMUTABLE_ERROR = NewsImmutable
    IMMUTABLE_MESSAGE = "An imported news item cannot be changed."

    class Meta:
        ordering = ("-published_at", "guid")
        verbose_name = "Uudis"
        verbose_name_plural = "Uudised"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "guid"], name="newsitem_unique_guid_per_snapshot"
            ),
            models.UniqueConstraint(
                fields=["snapshot", "canonical_url"], name="newsitem_unique_url_per_snapshot"
            ),
            models.CheckConstraint(condition=~Q(title=""), name="newsitem_title_required"),
        ]
        indexes = [models.Index(fields=["snapshot", "-published_at"])]

    def __str__(self) -> str:
        return self.title[:80]


class NewsFeedState(models.Model):
    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="news_feed_state",
        verbose_name="Andmeallikas",
    )
    current_snapshot = models.ForeignKey(
        NewsSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Kehtiv hetkeseis",
    )
    last_checked_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Viimati kontrollitud"
    )
    last_successful_sync_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Viimane edukas sünkroonimine"
    )
    last_changed_at = models.DateTimeField(null=True, blank=True, verbose_name="Viimati muutunud")
    last_result = models.CharField(
        max_length=16,
        choices=FeedResult,
        default=FeedResult.NEVER_RUN,
        verbose_name="Viimane tulemus",
    )
    last_error_summary = models.CharField(
        max_length=500, blank=True, verbose_name="Viimane veateade"
    )
    remote_etag = models.CharField(max_length=200, blank=True, verbose_name="Allika etag")
    remote_last_modified = models.CharField(
        max_length=100, blank=True, verbose_name="Allika muutmisaeg"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        verbose_name = "Uudiste andmevoo olek"
        verbose_name_plural = "Uudiste andmevoo olekud"

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"


# The durable catalogue lives beside the snapshot models, as the events app
# does it, so `apps.news.models` remains the one place Django discovers them.
from .public_models import NewsResource, NewsResourceImmutable, TitleOrigin  # noqa: E402,F401
