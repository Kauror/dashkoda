"""A durable catalogue of Koda.ee event pages.

`EventSnapshot` answers "what is publicly announced right now" and deliberately
drops events that have finished. That is the right shape for a calendar and the
wrong shape for linking, because the event programme reaches back to 2018 and
its rows have to resolve against pages whose events ended years ago.

So this is a second, cumulative layer beside it. A `PublicEventResource` is one
canonical Koda.ee event page, discovered once and then kept: it is not deleted
when the event passes, when the page stops appearing in a listing, or when a
later request returns 404. A page that existed is provenance, and one transient
fetch failure must never silently remove a working link.

That makes a resource deliberately *unlike* a snapshot row. A published snapshot
is immutable because it records what a source said at one moment; a resource
records the page itself, and a page can legitimately be corrected upstream. What
may not change is its identity — `canonical_url`, `stable_key` and
`first_seen_at` sit outside `MUTABLE_FIELDS`.

`PublicEventDiscoverySnapshot` records the **run**, not its membership.
Resources do not hang off it, so a crawl that fails halfway leaves the known
catalogue exactly as it was rather than orphaning it.

Nothing here carries event-programme meaning. These rows describe public pages;
the programme workbook remains the authority on what an event *is*.
"""

import uuid

from django.db import models
from django.db.models import F, Q

from apps.sources.models import DataSource

from .models import EventImmutable

MAX_WARNING_CODES = 50


class DiscoveryMode(models.TextChoices):
    """The shape of a discovery run."""

    FULL = "full", "Täielik"
    INCREMENTAL = "incremental", "Uuendus"


class DiscoveryOrigin(models.TextChoices):
    """Where a page was first reached.

    Kept for provenance only. It never decides what may be matched later: a page
    found in the archive and a page found in today's listing are equally real.
    """

    SITEMAP = "sitemap", "Saidikaart"
    ARCHIVE = "archive", "Arhiiv"
    CURRENT = "current", "Jooksev nimekiri"


class PublicEventResource(models.Model):
    """One canonical Koda.ee event page, kept after its event has passed."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    canonical_url = models.URLField(max_length=500, unique=True, verbose_name="Viide")
    stable_key = models.CharField(max_length=200, unique=True, verbose_name="Püsiv võti")
    title = models.TextField(verbose_name="Pealkiri")
    starts_on = models.DateField(db_index=True, verbose_name="Algab")
    ends_on = models.DateField(null=True, blank=True, verbose_name="Lõpeb")
    category = models.CharField(max_length=120, blank=True, verbose_name="Kategooria")
    location = models.CharField(max_length=200, blank=True, verbose_name="Toimumiskoht")
    discovered_from = models.CharField(
        max_length=16, choices=DiscoveryOrigin, verbose_name="Leidmisviis"
    )
    # A digest of the descriptive fields below, so a re-observation that says the
    # same thing is not recorded as a change and `last_changed_at` stays honest.
    content_checksum = models.CharField(max_length=64, verbose_name="Sisu räsi")
    first_seen_at = models.DateTimeField(auto_now_add=True, verbose_name="Esmakordselt nähtud")
    last_seen_at = models.DateTimeField(verbose_name="Viimati nähtud")
    last_changed_at = models.DateTimeField(null=True, blank=True, verbose_name="Viimati muutunud")

    # A public page may be corrected; its identity may not. `canonical_url`,
    # `stable_key`, `public_id` and `first_seen_at` are absent on purpose.
    MUTABLE_FIELDS = frozenset(
        {
            "title",
            "starts_on",
            "ends_on",
            "category",
            "location",
            "discovered_from",
            "content_checksum",
            "last_seen_at",
            "last_changed_at",
        }
    )

    class Meta:
        ordering = ("-starts_on", "title", "stable_key")
        verbose_name = "Avalik sündmuse leht"
        verbose_name_plural = "Avalikud sündmuste lehed"
        constraints = [
            models.CheckConstraint(condition=~Q(title=""), name="publicevent_title_required"),
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(ends_on__gte=F("starts_on")),
                name="publicevent_end_date_not_before_start",
            ),
        ]
        indexes = [models.Index(fields=["starts_on", "stable_key"])]

    def __str__(self) -> str:
        return f"{self.starts_on:%d.%m.%Y} {self.title[:60]}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise EventImmutable(
                    "A public event page keeps its identity; only "
                    f"{sorted(self.MUTABLE_FIELDS)} may be re-observed."
                )
        return super().save(*args, **kwargs)


class PublicEventDiscoverySnapshot(models.Model):
    """One discovery run: what happened, not what exists.

    Resources are cumulative and do not belong to a run, so a partial or failed
    crawl cannot remove anything. `is_current` marks the newest run that reached
    a terminal state, and is the only field that may change afterwards.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="public_event_discoveries",
        verbose_name="Andmeallikas",
    )
    mode = models.CharField(max_length=16, choices=DiscoveryMode, verbose_name="Režiim")
    observed_at = models.DateTimeField(verbose_name="Vaatluse aeg")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")
    pages_fetched = models.PositiveIntegerField(default=0, verbose_name="Laaditud lehti")
    urls_seen = models.PositiveIntegerField(default=0, verbose_name="Nähtud viiteid")
    resources_created = models.PositiveIntegerField(default=0, verbose_name="Uusi lehti")
    resources_updated = models.PositiveIntegerField(default=0, verbose_name="Muutunud lehti")
    resources_unchanged = models.PositiveIntegerField(default=0, verbose_name="Muutumatuid lehti")
    # False when the run could not read every page it set out to read. Recorded
    # rather than hidden, so a partial crawl never passes as complete history.
    is_complete = models.BooleanField(default=True, verbose_name="Täielik")
    error_count = models.PositiveIntegerField(default=0, verbose_name="Vigu")
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Loodud")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-observed_at", "-id")
        verbose_name = "Avalike lehtede avastusjooks"
        verbose_name_plural = "Avalike lehtede avastusjooksud"
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="publiceventdiscovery_one_current_per_source",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_mode_display()} {self.observed_at:%d.%m.%Y %H:%M}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise EventImmutable(
                    "A discovery run is immutable; only its is_current flag may change."
                )
        return super().save(*args, **kwargs)
