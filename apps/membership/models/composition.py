"""Aggregate composition of the member roster.

What kinds of organisations the Chamber's membership is made of: size classes,
counties, sectors, tenure bands and joining years. Two models, and between them
they hold counts and nothing else.

## What these models cannot hold

The roster this is derived from carries a company name, a registry code, a
street address, a director's name, two contact addresses and a free-text
comment. **No field here is capable of holding any of them**, and that is the
privacy guarantee rather than a rule an importer has to remember. There is no
name field to fill in, no identifier column, no JSON blob and no notes column.
An absent column cannot leak.

`category_key` and `category_label` hold vocabulary terms — `employees_10_49`,
`Harjumaa`, `Hulgi- ja jaekaubandus`, `2019` — which come from
`apps/membership/composition.py` and never from a spreadsheet cell. Their length
limits are sized for those terms and would truncate a company name rather than
store one, though nothing writes one there in the first place.

## Why two models rather than one per chart

A table per chart would multiply every time a dimension was added and would put
the schema in the way of an analytical question. A single unconstrained
key/value store would go the other way and let anything be written. These sit
between: one row per snapshot, and one row per (population, dimension,
category), with the vocabularies constrained in code and the combination unique.

## Snapshots supersede, they do not overwrite

A roster export is a dated observation like any other in this application. A
newer export for the same date is a revision: the previous snapshot stops being
current and keeps its rows, its checksum and its place in the audit trail. There
is no delete action and no in-place edit, so a figure the dashboard once showed
can always be traced back to the file that produced it.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from apps.sources.models import DataSource


class CompositionSnapshotImmutable(ValidationError):
    """Raised when something tries to rewrite a published snapshot's facts."""


class MembershipCompositionSnapshot(models.Model):
    """One dated reading of the member roster, as provenance and totals only.

    The workbook itself is never stored. What is kept is its checksum, its row
    count and the date it describes — enough to prove which file produced these
    aggregates and to recognise the identical file again, and not enough to
    reconstruct a single member.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="membership_composition_snapshots",
        verbose_name="Andmeallikas",
    )
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="membership_composition_snapshots",
        verbose_name="Impordikäivitus",
    )
    snapshot_date = models.DateField(verbose_name="Seisuga")
    #: Server-computed SHA-256 of the source workbook. The identity of the
    #: reading, and what makes an identical re-import recognisable.
    source_sha256 = models.CharField(max_length=64, verbose_name="Lähtefaili kontrollsumma")
    source_row_count = models.PositiveIntegerField(verbose_name="Lähtefaili ridade arv")
    #: Which classification vocabulary produced these categories. Two vintages
    #: are never drawn as one series without this being checked first.
    mapping_version = models.CharField(max_length=16, verbose_name="Klassifikaatori versioon")
    sector_mapping_version = models.CharField(
        max_length=16, verbose_name="Tegevusala klassifikaatori versioon"
    )
    #: The middle tenure in days, or `None` when no row carried a usable start
    #: date. Stored rather than derived because the individual tenures it was
    #: computed from are deliberately not kept.
    median_tenure_days = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Mediaanstaaž päevades"
    )
    #: How much of the roster each dimension could classify, as
    #: `{"sector": "97.3", ...}`. Percentages only; no row is named.
    coverage_pct = models.JSONField(default=dict, blank=True, verbose_name="Kaetus")
    is_current = models.BooleanField(default=True, verbose_name="Kehtiv")
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="supersedes",
        verbose_name="Asendatud kirjega",
    )
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    class Meta:
        ordering = ("-snapshot_date", "-imported_at")
        verbose_name = "Liikmeskonna koosseisu hetkeseis"
        verbose_name_plural = "Liikmeskonna koosseisu hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_sha256"],
                name="membershipcompositionsnapshot_unique_source_file",
            ),
            # At most one current snapshot per source. A dashboard that could
            # find two would have to choose between them at read time, and the
            # choice would be invisible to the reader.
            models.UniqueConstraint(
                fields=["source"],
                condition=models.Q(is_current=True),
                name="membershipcompositionsnapshot_one_current_per_source",
            ),
        ]
        indexes = [
            models.Index(
                fields=["source", "is_current", "-snapshot_date"],
                name="mcompsnap_current_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Koosseis {self.snapshot_date}"

    #: The fields a published snapshot may still change. Everything else is the
    #: reading itself and is immutable, exactly as an observation is.
    MUTABLE_FIELDS = frozenset({"is_current", "superseded_by", "superseded_by_id"})

    def save(self, *args, **kwargs):
        """Refuse any edit to a published reading except retiring it.

        A correction is a new snapshot that supersedes this one. Rewriting the
        numbers in place would leave the audit trail pointing at a file that no
        longer explains what the dashboard shows.
        """
        if self.pk is not None:
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and not set(update_fields) <= self.MUTABLE_FIELDS:
                raise CompositionSnapshotImmutable(
                    "Avaldatud koosseisu hetkeseisu ei saa muuta. Paranduseks tuleb "
                    "importida uus hetkeseis, mis selle asendab."
                )
        return super().save(*args, **kwargs)


class MembershipCompositionValue(models.Model):
    """How many members of one population fall in one category of one dimension.

    The grain is (snapshot, population, dimension, category). Nothing smaller is
    stored, and nothing smaller could be: below this grain is a member.
    """

    snapshot = models.ForeignKey(
        MembershipCompositionSnapshot,
        on_delete=models.CASCADE,
        related_name="values",
        verbose_name="Hetkeseis",
    )
    #: Which members this counts — everyone in the snapshot, or the recent
    #: joiners still present in it. The second is never described as "everyone
    #: who joined last year", because members who left again are not in it.
    population = models.CharField(max_length=32, verbose_name="Populatsioon")
    dimension = models.CharField(max_length=24, verbose_name="Mõõde")
    #: A vocabulary term from `composition.py`, never a spreadsheet cell.
    category_key = models.CharField(max_length=32, verbose_name="Kategooria")
    category_label = models.CharField(max_length=96, verbose_name="Kategooria nimi")
    member_count = models.PositiveIntegerField(verbose_name="Liikmete arv")

    class Meta:
        ordering = ("snapshot", "population", "dimension", "category_key")
        verbose_name = "Koosseisu näitaja"
        verbose_name_plural = "Koosseisu näitajad"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "population", "dimension", "category_key"],
                name="membershipcompositionvalue_unique_category",
            ),
        ]
        indexes = [
            models.Index(
                fields=["snapshot", "population", "dimension"],
                name="mcompval_lookup_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.dimension}/{self.category_key}: {self.member_count}"

    def save(self, *args, **kwargs):
        """A published count never changes; a new snapshot replaces it."""
        if self.pk is not None:
            raise CompositionSnapshotImmutable(
                "Koosseisu näitajat ei saa muuta. Uus hetkeseis asendab varasema."
            )
        return super().save(*args, **kwargs)
