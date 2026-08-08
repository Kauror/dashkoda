"""What the event matcher decided, stored so a page never has to recompute it.

Follows the opinion matcher's shape — an immutable snapshot of one run, one row
per event — with one deliberate difference, which is where the interesting
design decision lives.

## Reproducibility without a membership table

The opinion matcher pins its exact two inputs with foreign keys, so any stored
match can be reconstructed from the snapshots that produced it. That works
because both of its inputs *are* snapshots.

Here only one input is. `PublicEventResource` is cumulative: it accumulates
across discovery runs and belongs to no snapshot, precisely so a failed crawl
cannot orphan a link. Pinning a discovery run would name the run, not the set of
pages that existed when the matcher looked — those are different things, and the
second is what a score was computed against.

So the input set is pinned by high-water mark instead. `resource_high_water` is
the largest resource id at generation time, and the input set is exactly the
resources with `id <= resource_high_water`. Resources are only ever added, never
renumbered, so that reconstructs the set exactly — one integer, no join table,
and no risk of a membership table disagreeing with the resources themselves.

## Many-to-one is allowed on purpose

`UniqueConstraint(snapshot, event_id)` gives one decision per event per run.
There is deliberately **no** constraint on `resource` alone: one public page may
legitimately serve several programme events. That is not a tolerated defect, it
is the observed shape of the data — recurring trainings share a page, and five
of the workbook's own hand-entered URLs are already shared by two events each.
A uniqueness rule on `resource` would reject data the Chamber has been recording
by hand for years.
"""

from __future__ import annotations

from django.db import models
from django.db.models import F, Q

from apps.events.public_models import PublicEventResource

from .event_matching import MatchDecision
from .models import EventProgrammeSnapshot, SnapshotImmutable


class EventPublicMatchSnapshot(models.Model):
    """One matcher run over one programme snapshot."""

    programme_snapshot = models.ForeignKey(
        EventProgrammeSnapshot,
        on_delete=models.CASCADE,
        related_name="public_match_snapshots",
        verbose_name="Sündmuste programmi hetkeseis",
    )
    #: The largest `PublicEventResource` id this run could see. The input set is
    #: `id <= resource_high_water`; see the module docstring.
    resource_high_water = models.BigIntegerField(default=0, verbose_name="Lehtede ülempiir")
    matcher_version = models.CharField(max_length=64, verbose_name="Sobitaja versioon")
    generated_at = models.DateTimeField(auto_now_add=True, verbose_name="Arvutatud")
    considered_count = models.PositiveIntegerField(default=0, verbose_name="Vaadatud sündmusi")
    matched_count = models.PositiveIntegerField(default=0, verbose_name="Seotud")
    ambiguous_count = models.PositiveIntegerField(default=0, verbose_name="Ebaselgeid")
    unmatched_count = models.PositiveIntegerField(default=0, verbose_name="Sidumata")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-generated_at", "-id")
        verbose_name = "Sündmuste viidete sobitamine"
        verbose_name_plural = "Sündmuste viidete sobitamised"
        constraints = [
            models.UniqueConstraint(
                fields=["programme_snapshot", "resource_high_water", "matcher_version"],
                name="eventpublicmatch_unique_inputs",
            ),
            models.UniqueConstraint(
                models.F("is_current"),
                condition=Q(is_current=True),
                name="eventpublicmatch_one_current",
            ),
            models.CheckConstraint(
                condition=Q(matched_count__lte=F("considered_count")),
                name="eventpublicmatch_matched_within_total",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.generated_at:%d.%m.%Y %H:%M} ({self.matched_count}/{self.considered_count})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise SnapshotImmutable(
                    "A generated event match snapshot may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class EventPublicMatch(models.Model):
    """What the matcher decided about one programme event."""

    snapshot = models.ForeignKey(
        EventPublicMatchSnapshot,
        on_delete=models.CASCADE,
        related_name="matches",
        verbose_name="Sobitamise hetkeseis",
    )
    #: The programme's durable identity, not a row id. Verified stable across
    #: snapshots: 1,188 distinct values with zero drift in name or date.
    event_id = models.CharField(max_length=32, db_index=True, verbose_name="Sündmuse tunnus")
    #: Null for every decision except `matched`. PROTECT rather than CASCADE
    #: because a resource is never deleted; if that ever changed, the error is
    #: the right outcome rather than a silently vanishing match.
    resource = models.ForeignKey(
        PublicEventResource,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="event_matches",
        verbose_name="Avalik leht",
    )
    decision = models.CharField(max_length=16, choices=MatchDecision, verbose_name="Otsus")
    score = models.FloatField(default=0.0, verbose_name="Skoor")
    runner_up_score = models.FloatField(default=0.0, verbose_name="Teise skoor")
    score_margin = models.FloatField(default=0.0, verbose_name="Vahe")
    #: Why, in codes. So an operator can tell a thin name from a missing page
    #: without re-running the matcher.
    evidence_codes = models.JSONField(default=list, blank=True, verbose_name="Tõendikoodid")

    class Meta:
        ordering = ("event_id",)
        verbose_name = "Sündmuse viite otsus"
        verbose_name_plural = "Sündmuse viite otsused"
        constraints = [
            # One decision per event per run. Deliberately nothing on `resource`
            # alone — see the module docstring.
            models.UniqueConstraint(
                fields=["snapshot", "event_id"], name="eventpublicmatch_one_decision_per_event"
            ),
            models.CheckConstraint(
                condition=Q(decision=MatchDecision.MATCHED, resource__isnull=False)
                | ~Q(decision=MatchDecision.MATCHED) & Q(resource__isnull=True),
                name="eventpublicmatch_resource_only_when_matched",
            ),
        ]
        indexes = [models.Index(fields=["snapshot", "decision"])]

    def __str__(self) -> str:
        return f"{self.event_id}: {self.get_decision_display()}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A generated event match cannot be changed.")
        return super().save(*args, **kwargs)
