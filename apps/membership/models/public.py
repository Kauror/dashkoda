"""The total number of published Koda.ee member profiles, over time.

**This module stores an aggregate and nothing else.** The public endpoint
returns one row per member carrying a registration code and a profile URL; the
count collector reads both in memory to count and to detect duplicates, and
discards them. The count series has no row behind it and needs none.

Row-level identity lives elsewhere since August 2026, as a separate product
decision: `register.py` holds the roster's rows and the directory's published
registration codes for the members-list page and its comparison. That module's
docstring is where the boundary of that decision is written down; nothing about
this count changed with it, and the count is still collected, stored and
guarded exactly as before.

There is also deliberately **no "new members this year" figure**. It is not an
accepted DashKoda metric, so it exists in no model, no field, no selector and no
template.
"""

from django.db import models
from django.db.models import Q

from apps.core.feeds import FeedResult
from apps.sources.models import DataSource


class ObservationImmutable(RuntimeError):
    """Raised when something tries to rewrite a recorded observation."""


class MembershipCountObservation(models.Model):
    """One counted total, tied to the artifact and run that produced it.

    Immutable apart from `is_current`, which has to move when a newer
    observation is published.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="membership_observations",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="membership_observations",
        verbose_name="Algfail",
    )
    import_run = models.OneToOneField(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="membership_observation",
        verbose_name="Impordikäivitus",
    )
    observed_at = models.DateTimeField(verbose_name="Vaatluse aeg")
    total_members = models.PositiveIntegerField(verbose_name="Liikmeid kokku")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-observed_at", "-id")
        verbose_name = "Liikmete arvu vaatlus"
        verbose_name_plural = "Liikmete arvu vaatlused"
        constraints = [
            models.CheckConstraint(
                condition=Q(total_members__gt=0),
                name="membershipobservation_total_positive",
            ),
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="membershipobservation_one_current_per_source",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.total_members} liiget ({self.observed_at:%d.%m.%Y})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise ObservationImmutable(
                    "A recorded membership observation may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class MembershipFeedState(models.Model):
    """What the last membership check found.

    Holds only non-secret transport metadata plus a sanitized error summary. No
    member row, no registration code and no profile URL ever reaches it.
    """

    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="membership_feed_state",
        verbose_name="Andmeallikas",
    )
    current_observation = models.ForeignKey(
        MembershipCountObservation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Kehtiv vaatlus",
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
        max_length=500,
        blank=True,
        verbose_name="Viimane veateade",
        help_text="Puhastatud ja lühendatud. Ei sisalda vastuse sisu.",
    )
    remote_etag = models.CharField(max_length=200, blank=True, verbose_name="Allika etag")
    remote_last_modified = models.CharField(
        max_length=100, blank=True, verbose_name="Allika muutmisaeg"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        verbose_name = "Liikmeskonna andmevoo olek"
        verbose_name_plural = "Liikmeskonna andmevoo olekud"

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"
