"""Imported Koda.ee event snapshots and their items.

Time precision is modelled honestly. The public site publishes event dates, and
its structured data currently carries a calendar date with no time component, so
most events legitimately have a date and no clock time. Rather than inventing
`00:00`, the model separates the two:

- `starts_on` / `ends_on` — the calendar dates, always present for a start;
- `starts_at` / `ends_at` — exact timezone-aware instants, set **only** when the
  source states one.

A null `starts_at` therefore means "the source did not say", not "midnight".
"""

from django.db import models
from django.db.models import F, Q

from apps.core.feeds import FeedResult
from apps.sources.models import DataSource


class EventImmutable(RuntimeError):
    """Raised when something tries to rewrite an imported snapshot or item."""


class EventSnapshot(models.Model):
    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="event_snapshots",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="event_snapshots",
        verbose_name="Algfail",
    )
    import_run = models.OneToOneField(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="event_snapshot",
        verbose_name="Impordikäivitus",
    )
    observed_at = models.DateTimeField(verbose_name="Vaatluse aeg")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")
    item_count = models.PositiveIntegerField(default=0, verbose_name="Sündmusi")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-observed_at", "-id")
        verbose_name = "Sündmuste hetkeseis"
        verbose_name_plural = "Sündmuste hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="eventsnapshot_one_current_per_source",
            ),
        ]

    def __str__(self) -> str:
        return f"Sündmused {self.observed_at:%d.%m.%Y} ({self.item_count})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise EventImmutable(
                    "An imported event snapshot may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class EventItem(models.Model):
    snapshot = models.ForeignKey(
        EventSnapshot,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Hetkeseis",
    )
    stable_key = models.CharField(max_length=200, verbose_name="Püsiv võti")
    title = models.TextField(verbose_name="Pealkiri")
    canonical_url = models.URLField(max_length=500, verbose_name="Viide")
    category = models.CharField(max_length=120, blank=True, verbose_name="Kategooria")
    summary = models.TextField(blank=True, verbose_name="Kokkuvõte")
    starts_on = models.DateField(db_index=True, verbose_name="Algab")
    ends_on = models.DateField(null=True, blank=True, verbose_name="Lõpeb")
    # Null means the source did not publish a time, never "midnight".
    starts_at = models.DateTimeField(null=True, blank=True, verbose_name="Algusaeg")
    ends_at = models.DateTimeField(null=True, blank=True, verbose_name="Lõpuaeg")
    location = models.CharField(max_length=200, blank=True, verbose_name="Toimumiskoht")
    source_order = models.PositiveIntegerField(verbose_name="Järjekord allikas")

    class Meta:
        ordering = ("starts_on", "title", "stable_key")
        verbose_name = "Sündmus"
        verbose_name_plural = "Sündmused"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "canonical_url"], name="eventitem_unique_url_per_snapshot"
            ),
            models.UniqueConstraint(
                fields=["snapshot", "stable_key"], name="eventitem_unique_key_per_snapshot"
            ),
            models.CheckConstraint(condition=~Q(title=""), name="eventitem_title_required"),
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(ends_on__gte=F("starts_on")),
                name="eventitem_end_date_not_before_start",
            ),
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True)
                | Q(starts_at__isnull=False, ends_at__gte=F("starts_at")),
                name="eventitem_end_time_not_before_start",
            ),
        ]
        indexes = [models.Index(fields=["snapshot", "starts_on"])]

    def __str__(self) -> str:
        return self.title[:80]

    @property
    def has_exact_start(self) -> bool:
        return self.starts_at is not None

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise EventImmutable("An imported event cannot be changed.")
        return super().save(*args, **kwargs)


class EventFeedState(models.Model):
    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="event_feed_state",
        verbose_name="Andmeallikas",
    )
    current_snapshot = models.ForeignKey(
        EventSnapshot,
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
        verbose_name = "Sündmuste andmevoo olek"
        verbose_name_plural = "Sündmuste andmevoo olekud"

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"


# The durable catalogue of public Koda.ee event pages and its discovery runs.
# Imported here, at the foot, so Django discovers them as ordinary `events`
# models while the definitions stay in their own module. The import is last
# because those models refer back to the ones above.
from .public_models import (  # noqa: E402,F401  (placement is deliberate)
    DiscoveryMode,
    DiscoveryOrigin,
    PublicEventDiscoverySnapshot,
    PublicEventResource,
)
