"""Durable legal-matter identity, and what the opinion matcher decided.

Two groups of models with different lifetimes, which is why they are separate.

`LegalMatter` and `OpinionResource` are **durable**: they outlive any snapshot,
because a resource address printed today has to keep working after tomorrow's
workbook import. `LegalMatterAlias` records what each snapshot called a matter,
as provenance an operator can follow back to a spreadsheet row.

`LegalOpinionMatchSnapshot`, `LegalOpinionDecision` and
`LegalOpinionDocumentRelation` are **snapshot-scoped and immutable**, like every
other matcher output in this project. They name the exact legal snapshot and the
exact opinion catalogue they were computed from, so a stale decision is
detectable rather than silently applied to data it never saw.
"""

import uuid

from django.db import models
from django.db.models import F, Q

from .models import LegalWorkItem, LegalWorkSnapshot, MatchDecision, SnapshotImmutable
from .opinion_models import OpinionCatalogueEntry, OpinionCatalogueSnapshot

MAX_TOPIC_SNAPSHOT_LENGTH = 500


class DocumentRole(models.TextChoices):
    """How a document relates to the record it was matched to."""

    PRIMARY = "primary", "Põhidokument"
    JOINT = "joint", "Ühisarvamus"
    SUPPLEMENTARY = "supplementary", "Täiendav"
    FOLLOW_UP = "follow_up", "Järelkiri"
    ANNEX = "annex", "Lisa"
    SUPPORTING = "supporting", "Tugidokument"


class LegalMatter(models.Model):
    """One legal matter, identified by what it is rather than where it sits.

    See `opinion_identity.py` for why `record_id` cannot be this: it is a row
    position, and 128 of 610 production identifiers have denoted materially
    different matters at different times.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    matter_key = models.CharField(max_length=64, unique=True, verbose_name="Identiteedivõti")
    identity_version = models.CharField(max_length=8, verbose_name="Identiteedi versioon")
    # Kept so a resource page can still name the matter after it has left the
    # current workbook. Historical display only; never a lookup key.
    last_known_topic = models.TextField(verbose_name="Viimane teadaolev teema")
    received_date = models.DateField(null=True, blank=True, verbose_name="Sisse")
    first_seen_at = models.DateTimeField(auto_now_add=True, verbose_name="Esmakordselt nähtud")
    # A single snapshot presenting the same key twice makes the identity
    # ambiguous. Such a matter is recorded but never linked.
    has_ambiguous_identity = models.BooleanField(default=False, verbose_name="Mitmetähenduslik")

    MUTABLE_FIELDS = frozenset({"last_known_topic", "has_ambiguous_identity"})

    class Meta:
        ordering = ("-first_seen_at", "-id")
        verbose_name = "Õigusloome asi"
        verbose_name_plural = "Õigusloome asjad"
        constraints = [
            models.CheckConstraint(
                condition=Q(matter_key__regex=r"^[0-9a-f]{64}$"),
                name="legalmatter_key_is_lower_hex",
            ),
        ]

    def __str__(self) -> str:
        return self.last_known_topic[:80] or str(self.public_id)


class LegalMatterAlias(models.Model):
    """What one snapshot called a matter. Provenance, never a lookup key.

    Resolving a matter *by* one of these is exactly the mistake the durable key
    exists to prevent, so nothing in the read path queries this table.
    """

    matter = models.ForeignKey(
        LegalMatter, on_delete=models.CASCADE, related_name="aliases", verbose_name="Asi"
    )
    snapshot = models.ForeignKey(
        LegalWorkSnapshot, on_delete=models.CASCADE, related_name="+", verbose_name="Hetkeseis"
    )
    record_id = models.CharField(max_length=64, verbose_name="Kirje ID")
    source_year = models.PositiveSmallIntegerField(verbose_name="Aasta")
    source_nr = models.PositiveIntegerField(null=True, blank=True, verbose_name="Number")
    source_row = models.PositiveIntegerField(verbose_name="Lähterida")
    observed_at = models.DateTimeField(auto_now_add=True, verbose_name="Nähtud")

    class Meta:
        ordering = ("matter", "-snapshot_id")
        verbose_name = "Asja varasem tunnus"
        verbose_name_plural = "Asjade varasemad tunnused"
        constraints = [
            models.UniqueConstraint(
                fields=["matter", "snapshot"], name="legalmatteralias_one_per_snapshot"
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A recorded matter alias cannot be changed.")
        return super().save(*args, **kwargs)


class OpinionResource(models.Model):
    """The stable internal page a sent legal topic links to.

    Exists once a matter has something worth showing. The address is the opaque
    `public_id`; no sequential database key and no filename ever appears in it.
    """

    matter = models.OneToOneField(
        LegalMatter, on_delete=models.PROTECT, related_name="resource", verbose_name="Asi"
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Loodud")

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Arvamuse ressurss"
        verbose_name_plural = "Arvamuse ressursid"

    def __str__(self) -> str:
        return str(self.public_id)


class LegalOpinionMatchSnapshot(models.Model):
    """One matcher run, and the exact two inputs that produced it."""

    legal_snapshot = models.ForeignKey(
        LegalWorkSnapshot,
        on_delete=models.CASCADE,
        related_name="opinion_match_snapshots",
        verbose_name="Õigusloome hetkeseis",
    )
    opinion_catalogue_snapshot = models.ForeignKey(
        OpinionCatalogueSnapshot,
        on_delete=models.CASCADE,
        related_name="match_snapshots",
        verbose_name="Arvamuste kataloog",
    )
    matcher_version = models.CharField(max_length=64, verbose_name="Sobitaja versioon")
    generated_at = models.DateTimeField(auto_now_add=True, verbose_name="Arvutatud")
    considered_item_count = models.PositiveIntegerField(default=0, verbose_name="Vaadatud kirjeid")
    matched_count = models.PositiveIntegerField(default=0, verbose_name="Seotud")
    ambiguous_count = models.PositiveIntegerField(default=0, verbose_name="Ebaselgeid")
    unmatched_count = models.PositiveIntegerField(default=0, verbose_name="Sidumata")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-generated_at", "-id")
        verbose_name = "Arvamuste sobitamise hetkeseis"
        verbose_name_plural = "Arvamuste sobitamise hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["legal_snapshot", "opinion_catalogue_snapshot", "matcher_version"],
                name="legalopinionmatch_unique_inputs",
            ),
            models.UniqueConstraint(
                models.F("is_current"),
                condition=Q(is_current=True),
                name="legalopinionmatch_one_current",
            ),
            models.CheckConstraint(
                condition=Q(matched_count__lte=F("considered_item_count")),
                name="legalopinionmatch_matched_within_total",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise SnapshotImmutable(
                    "A generated opinion match snapshot may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class LegalOpinionDecision(models.Model):
    """What the matcher decided about one opinion-eligible record."""

    snapshot = models.ForeignKey(
        LegalOpinionMatchSnapshot,
        on_delete=models.CASCADE,
        related_name="decisions",
        verbose_name="Hetkeseis",
    )
    legal_item = models.ForeignKey(
        LegalWorkItem, on_delete=models.CASCADE, related_name="+", verbose_name="Õigusloome kirje"
    )
    matter = models.ForeignKey(
        LegalMatter, on_delete=models.PROTECT, related_name="decisions", verbose_name="Asi"
    )
    decision = models.CharField(
        max_length=16, choices=MatchDecision, db_index=True, verbose_name="Otsus"
    )
    score = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="Skoor")
    runner_up_score = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, verbose_name="Teise koha skoor"
    )
    score_margin = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="Vahe")
    candidate_count = models.PositiveSmallIntegerField(default=0, verbose_name="Kandidaate")
    evidence_codes = models.JSONField(default=list, blank=True, verbose_name="Tõendikoodid")
    contradiction_codes = models.JSONField(default=list, blank=True, verbose_name="Vastuolud")

    class Meta:
        ordering = ("-score", "legal_item_id")
        verbose_name = "Arvamuse sobitamise tulemus"
        verbose_name_plural = "Arvamuse sobitamise tulemused"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "legal_item"],
                name="legalopiniondecision_one_per_item",
            ),
            models.CheckConstraint(
                condition=Q(score__gte=0, score__lte=100),
                name="legalopiniondecision_score_within_scale",
            ),
            models.CheckConstraint(
                condition=Q(score__gte=F("runner_up_score")),
                name="legalopiniondecision_runner_up_not_above_score",
            ),
            models.CheckConstraint(
                condition=Q(score_margin=F("score") - F("runner_up_score")),
                name="legalopiniondecision_margin_is_difference",
            ),
        ]
        indexes = [models.Index(fields=["snapshot", "decision"])]

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A generated opinion decision cannot be changed.")
        return super().save(*args, **kwargs)


class LegalOpinionDocumentRelation(models.Model):
    """One document attached to one decision, in one role.

    A decision has at most one primary document — enforced by a partial unique
    index rather than by the code that writes it, because "at most one" is the
    property the viewer depends on and code can be bypassed.
    """

    decision = models.ForeignKey(
        LegalOpinionDecision,
        on_delete=models.CASCADE,
        related_name="relations",
        verbose_name="Otsus",
    )
    entry = models.ForeignKey(
        OpinionCatalogueEntry, on_delete=models.PROTECT, related_name="+", verbose_name="Dokument"
    )
    role = models.CharField(max_length=16, choices=DocumentRole, verbose_name="Roll")
    is_primary = models.BooleanField(default=False, verbose_name="Põhidokument")
    score = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, verbose_name="Seose skoor"
    )
    evidence_codes = models.JSONField(default=list, blank=True, verbose_name="Tõendikoodid")

    class Meta:
        ordering = ("decision", "-is_primary", "role")
        verbose_name = "Arvamuse dokumendiseos"
        verbose_name_plural = "Arvamuse dokumendiseosed"
        constraints = [
            models.UniqueConstraint(
                fields=["decision", "entry"], name="legalopinionrelation_unique_entry_per_decision"
            ),
            models.UniqueConstraint(
                fields=["decision"],
                condition=Q(is_primary=True),
                name="legalopinionrelation_one_primary_per_decision",
            ),
            models.CheckConstraint(
                condition=~Q(is_primary=True) | Q(role=DocumentRole.PRIMARY),
                name="legalopinionrelation_primary_flag_matches_role",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A generated document relation cannot be changed.")
        return super().save(*args, **kwargs)
