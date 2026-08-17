"""The member register: roster rows and the published directory's identities.

**This module is the deliberate exception to the membership app's old claim
that no row-level member data is stored anywhere in it.** The claim was true
until August 2026 and `composition.py` still honours it — aggregates only. The
product then grew a members-list page and a roster-versus-directory
comparison, and both need rows: a list cannot be drawn from counts, and two
sources cannot be compared per member without a per-member identity. The
decision to store rows was Kaur's, made for that page, and this docstring is
where the boundary of that decision is written down.

## What is stored, and what deliberately is not

A register entry holds what the list page shows and what the comparison
joins on: name, legal form, member number, status, registry code, county,
city, country, employee count, membership start date, NACE code and label,
and the public website address. Every one of those is either registry-public
information or an organisational fact about a company.

The roster export carries more, and the rest is **deliberately not modelled**:
the street address and postal code, the general and billing e-mail addresses,
the phone and fax numbers, the director's name and personal e-mail, the VAT
number and the free-text comment. There is no column any of them would fit
in, which keeps the guarantee structural — an absent column cannot leak. A
director's name in particular is personal data the dashboard has no question
for; if a future page needs a new column, adding it must be as deliberate as
this module was.

The directory side stores even less, because the product needs even less: a
registration code and a profile path per published member profile, plus when
each was first and last seen. Names, counties and phone numbers are visible
on Koda.ee but are not collected — the roster provides the name for every
matched code, and an unmatched code links to its own public profile.

## Three membership sources, still never merged

The register does not change the standing rule: the public directory count,
the internal board-report history and the roster are different measurements.
A register snapshot's row count is **not** a membership total and is never
added to, subtracted from or continued with either series. The comparison
this module enables is an *identity* comparison — which codes appear in
which source — and its output is always two labelled, dated sets, never one
reconciled number.

## Lifecycle

Roster snapshots follow the composition model exactly: a snapshot is an
immutable dated reading, a corrected export supersedes rather than
overwrites, and nothing is deleted. Entries belong to their snapshot and are
as immutable as it is.

`MemberDirectoryEntry` is different on purpose: it is a **working register**
reconciled to the latest successful fetch, the same carry-forward shape the
opinion catalogue's page corpus uses. Rows gain `first_seen_at` when a code
first appears, have `last_seen_at` refreshed on every sighting, and are
marked unpublished — never deleted — when the directory stops listing them.
Each *distinct* observed set is recorded as an immutable import run with its
canonical checksum; the reconciliation itself must also run when the set
returns to a previously-seen state, which is why applying it is not gated on
the run existing (a member unpublished for a day and then restored returns
the directory to old bytes, and the row still has to come back).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from apps.sources.models import DataSource


class RegisterImmutable(ValidationError):
    """Raised when something tries to rewrite a published register reading."""


class MemberRegisterSnapshot(models.Model):
    """One dated reading of the member roster, with its rows kept.

    The file itself is never stored; its checksum, row count and stated date
    are. Unlike the composition snapshot beside it, the rows survive — that
    is this model's whole purpose — but the reading is exactly as immutable:
    a correction is a new snapshot that supersedes this one.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="member_register_snapshots",
        verbose_name="Andmeallikas",
    )
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="member_register_snapshots",
        verbose_name="Impordikäivitus",
    )
    snapshot_date = models.DateField(verbose_name="Seisuga")
    source_sha256 = models.CharField(max_length=64, verbose_name="Lähtefaili kontrollsumma")
    source_row_count = models.PositiveIntegerField(verbose_name="Lähtefaili ridade arv")
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
        verbose_name = "Liikmete nimekirja hetkeseis"
        verbose_name_plural = "Liikmete nimekirja hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_sha256"],
                name="memberregistersnapshot_unique_source_file",
            ),
            models.UniqueConstraint(
                fields=["source"],
                condition=models.Q(is_current=True),
                name="memberregistersnapshot_one_current_per_source",
            ),
        ]
        indexes = [
            models.Index(
                fields=["source", "is_current", "-snapshot_date"],
                name="mregsnap_current_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Liikmete nimekiri {self.snapshot_date}"

    #: What a published snapshot may still change: being retired in favour of a
    #: newer reading. The reading itself is immutable.
    MUTABLE_FIELDS = frozenset({"is_current", "superseded_by", "superseded_by_id"})

    def save(self, *args, **kwargs):
        if self.pk is not None:
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and not set(update_fields) <= self.MUTABLE_FIELDS:
                raise RegisterImmutable(
                    "Avaldatud nimekirja hetkeseisu ei saa muuta. Paranduseks tuleb "
                    "importida uus hetkeseis, mis selle asendab."
                )
        return super().save(*args, **kwargs)


class MemberRegisterEntry(models.Model):
    """One member organisation as one dated roster export described it.

    A row states what the export said on the snapshot's date — it is a fact
    about a file, not a live record, which is why entries are immutable and
    carry no updated-at. The columns are the deliberate subset the module
    docstring lists; personal contact data has no column here.
    """

    snapshot = models.ForeignKey(
        MemberRegisterSnapshot,
        on_delete=models.CASCADE,
        related_name="entries",
        verbose_name="Hetkeseis",
    )
    name = models.CharField(max_length=200, verbose_name="Nimi")
    legal_form = models.CharField(max_length=16, blank=True, verbose_name="Vorm")
    member_number = models.CharField(max_length=16, blank=True, verbose_name="Liikmenumber")
    #: A vocabulary term from `composition.py` — regular/suspended/supporter/
    #: unknown — so the register and the composition charts classify a status
    #: with the same rule.
    status_key = models.CharField(max_length=16, verbose_name="Staatus")
    #: The roster's own wording, kept because the vocabulary key is a
    #: classification and the reader is owed the source's words.
    status_label = models.CharField(max_length=32, blank=True, verbose_name="Staatus allikas")
    #: Digits only. ``None`` when the roster row carried no readable code —
    #: never an empty string, so the per-snapshot uniqueness below cannot
    #: collide two codeless rows.
    registry_code = models.CharField(
        max_length=16, null=True, blank=True, verbose_name="Registrikood"
    )
    county = models.CharField(max_length=64, blank=True, verbose_name="Maakond")
    city = models.CharField(max_length=64, blank=True, verbose_name="Linn või vald")
    country = models.CharField(max_length=64, blank=True, verbose_name="Riik")
    employees = models.PositiveIntegerField(null=True, blank=True, verbose_name="Töötajaid")
    membership_start = models.DateField(null=True, blank=True, verbose_name="Liikmelisus algas")
    nace_code = models.CharField(max_length=8, blank=True, verbose_name="EMTAK/NACE kood")
    nace_label = models.CharField(max_length=160, blank=True, verbose_name="Tegevusala")
    #: As the roster wrote it, scheme and all or neither. Rendering decides how
    #: to make it a link; storing a "repaired" URL would invent data.
    website = models.CharField(max_length=200, blank=True, verbose_name="Veebileht")

    class Meta:
        ordering = ("snapshot", "name", "id")
        verbose_name = "Liikmete nimekirja kirje"
        verbose_name_plural = "Liikmete nimekirja kirjed"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "registry_code"],
                name="memberregisterentry_unique_code_per_snapshot",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "name"], name="mregentry_name_idx"),
            models.Index(fields=["snapshot", "status_key"], name="mregentry_status_idx"),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RegisterImmutable(
                "Nimekirja kirjet ei saa muuta. Uus hetkeseis asendab varasema."
            )
        return super().save(*args, **kwargs)


class MemberDirectoryEntry(models.Model):
    """One registration code the public Koda.ee directory publishes, over time.

    A working register, not an observation log: the sync reconciles these rows
    to the latest successful fetch, so the table always answers "which codes
    does the directory publish right now, and since when". A code the
    directory stops listing is marked unpublished and keeps its history;
    nothing is deleted, and a code that returns is republished with its
    original `first_seen_at` intact.

    Deliberately thin — a code and a path. The name belongs to the roster row
    with the same code; a code the roster does not know is shown by its
    public profile link until the next roster import names it.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="member_directory_entries",
        verbose_name="Andmeallikas",
    )
    registry_code = models.CharField(max_length=16, verbose_name="Registrikood")
    #: The profile's path on koda.ee, e.g. ``/et/liige/heisi-it-ou``. A path
    #: rather than a URL: the host is configuration, not data.
    profile_path = models.CharField(max_length=300, verbose_name="Profiili aadress")
    first_seen_at = models.DateTimeField(verbose_name="Esmakordselt nähtud")
    last_seen_at = models.DateTimeField(verbose_name="Viimati nähtud")
    is_published = models.BooleanField(default=True, verbose_name="Avaldatud")
    unpublished_at = models.DateTimeField(null=True, blank=True, verbose_name="Kataloogist kadunud")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        ordering = ("registry_code",)
        verbose_name = "Avaliku kataloogi kirje"
        verbose_name_plural = "Avaliku kataloogi kirjed"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "registry_code"],
                name="memberdirectoryentry_unique_code_per_source",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "is_published"], name="mdirentry_published_idx"),
        ]

    def __str__(self) -> str:
        state = "avaldatud" if self.is_published else "kataloogist kadunud"
        return f"{self.registry_code} ({state})"
