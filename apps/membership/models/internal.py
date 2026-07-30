"""The Chamber's own membership history, as reported to its board.

This is **not** the public Koda.ee member-directory count. It comes from the
Chamber's internal board reports, it counts something the public directory does
not, and the two series are never joined, averaged or continued into one
another. `MembershipCountObservation` in `public.py` is untouched by everything
here.

Two writers produce these rows and both go through the same domain service:

- the one-time historical package import, which carries fourteen years of
  extracted evidence together with its confidence, warnings and conflicts;
- the staff-only manual form, which is how every future board report is entered
  until an automated route exists.

Reported facts are immutable once published. A correction is a **new** manual
observation that supersedes the previous preferred one; the old row keeps its
values, its children and its audit trail. Only the two state fields named in
`MUTABLE_FIELDS` ever move.

Evidence is kept even when it is not shown. A conflicted metric, a low-
confidence extraction and an impossible value all stay in the database with the
provenance that explains them — the selectors decide what a chart may draw, and
that decision is reversible. Deleting the evidence would not be.

Deliberately absent: no member name, no registration code, no per-member payment
status, no original Word file and no extracted prose. `raw_reference` in the
import package holds sentences lifted out of the source documents and is not
imported; `source_id`, `snapshot_id` and the column label are enough provenance
to audit any number on the page.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.sources.models import DataSource

# A warning message, a values summary and a raw value are diagnostics, not
# content. They are bounded here so no import can turn a quality table into an
# accumulation of source text.
MAX_MESSAGE_LENGTH = 500
MAX_RAW_VALUE_LENGTH = 500


class InternalObservationImmutable(RuntimeError):
    """Raised when something tries to rewrite a published internal record."""


class DatePrecision(models.TextChoices):
    """How exactly the reporting date is known.

    Older board reports name only a year; most name a day.
    """

    DAY = "day", "Päev"
    MONTH = "month", "Kuu"
    YEAR = "year", "Aasta"


class InternalSourceKind(models.TextChoices):
    """Where one observation's numbers came from.

    The distinction drives precedence. A document's own current figures are
    first-hand; the comparison column of a later document restates an earlier
    year and is second-hand, so it is kept for provenance but yields to a direct
    observation for the same date.
    """

    MERGED_SAME_DOCUMENT = "merged_same_document", "Dokumendi enda seis"
    REPORTED_COMPARISON = "reported_comparison", "Võrdlusveerust tuletatud"
    MANUAL = "manual", "Käsitsi sisestatud"


class ExtractionConfidence(models.TextChoices):
    HIGH = "high", "Kõrge"
    MEDIUM = "medium", "Keskmine"
    LOW = "low", "Madal"
    MANUAL_VERIFIED = "manual_verified", "Käsitsi kinnitatud"


class QualityStatus(models.TextChoices):
    """What the observation may be used for.

    `CONFLICTED` and `REVIEW_REQUIRED` are not failures to be cleaned away: the
    row stays, and the selectors omit the affected metric rather than the whole
    observation.
    """

    VERIFIED = "verified", "Kinnitatud"
    PROVISIONAL = "provisional", "Esialgne"
    REVIEW_REQUIRED = "review_required", "Vajab ülevaatamist"
    CONFLICTED = "conflicted", "Vastuoluline"
    SUPERSEDED = "superseded", "Asendatud"


class MonthlyValueStatus(models.TextChoices):
    VERIFIED = "verified", "Kinnitatud"
    PROVISIONAL_CURRENT_MONTH = "provisional_current_month", "Esialgne jooksev kuu"
    CONFLICT = "conflict", "Vastuoluline"
    MANUAL_VERIFIED = "manual_verified", "Käsitsi kinnitatud"
    SUPERSEDED = "superseded", "Asendatud"


class MovementDirection(models.TextChoices):
    JOINED = "joined", "Liitunud"
    REMOVED = "removed", "Lahkunud"


class SizeBand(models.TextChoices):
    """Canonical company-size bands, in the order they are always charted.

    `SUPPORTER` is not a size and is shown separately from the employee bands.
    """

    EMPLOYEES_1_4 = "employees_1_4", "1–4 töötajat"
    EMPLOYEES_5_9 = "employees_5_9", "5–9 töötajat"
    EMPLOYEES_10_19 = "employees_10_19", "10–19 töötajat"
    EMPLOYEES_20_49 = "employees_20_49", "20–49 töötajat"
    EMPLOYEES_50_99 = "employees_50_99", "50–99 töötajat"
    EMPLOYEES_100_249 = "employees_100_249", "100–249 töötajat"
    EMPLOYEES_250_499 = "employees_250_499", "250–499 töötajat"
    EMPLOYEES_500_999 = "employees_500_999", "500–999 töötajat"
    EMPLOYEES_1000_PLUS = "employees_1000_plus", "1000+ töötajat"
    SUPPORTER = "supporter", "Toetajaliige"


# Chart order is a property of the vocabulary, not of a template.
SIZE_BAND_ORDER: tuple[str, ...] = tuple(band.value for band in SizeBand)
EMPLOYEE_SIZE_BANDS: tuple[str, ...] = tuple(
    band for band in SIZE_BAND_ORDER if band != SizeBand.SUPPORTER
)


class RemovalReasonKey(models.TextChoices):
    """Why members left, as the board reports group them.

    `OTHER` carries the label the report actually used. A manually entered
    reason is never quietly folded into one of the known categories.
    """

    DISSOLVED = (
        "dissolved_bankrupt_merged_inactive_missing",
        "Likvideeritud, pankrotis, ühinenud, tegevuseta või kadunud",
    )
    VOLUNTARY_NO_VALUE = (
        "voluntary_no_service_value",
        "Vabatahtlik: ei näe teenuses väärtust",
    )
    VOLUNTARY_FINANCIAL = (
        "voluntary_debt_financial_or_other",
        "Vabatahtlik: võlgnevus, majanduslik või muu põhjus",
    )
    OTHER = "other", "Muu"
    UNKNOWN = "unknown", "Teadmata"


class IssueSeverity(models.TextChoices):
    INFO = "info", "Info"
    WARNING = "warning", "Hoiatus"
    ERROR = "error", "Viga"


class MembershipHistoricalSourceDocument(models.Model):
    """Provenance for one board-report document. Metadata only.

    The Word file itself is never stored, never served and never needed again:
    the canonical CSV package is the contract. What is kept is enough to answer
    "where did this number come from" — the document's identity, its checksum at
    extraction time, and how its reporting date was determined.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="membership_source_documents",
        verbose_name="Andmeallikas",
    )
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="membership_source_documents",
        verbose_name="Impordikäivitus",
    )
    # The package calls this `source_id`. It is `external_source_id` here
    # because `source` is the DataSource foreign key and Django would otherwise
    # have two fields fighting over the `source_id` attribute name. The prefix
    # also matches `external_snapshot_id` and `external_warning_id`: all three
    # are identifiers the package owns, not identifiers this application mints.
    external_source_id = models.CharField(max_length=64, verbose_name="Dokumendi tunnus")
    relative_path = models.CharField(
        max_length=400,
        blank=True,
        verbose_name="Suhteline asukoht",
        help_text="Ainult auditijälg. Ei kuvata tavakasutajale.",
    )
    filename = models.CharField(max_length=255, blank=True, verbose_name="Failinimi")
    extension = models.CharField(max_length=16, blank=True, verbose_name="Laiend")
    file_sha256 = models.CharField(max_length=64, blank=True, verbose_name="Faili SHA-256")
    file_size_bytes = models.PositiveBigIntegerField(default=0, verbose_name="Faili suurus")
    filesystem_modified_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Faili muutmisaeg"
    )
    year_folder = models.CharField(max_length=120, blank=True, verbose_name="Aastakaust")
    month_folder = models.CharField(max_length=120, blank=True, verbose_name="Kuukaust")
    candidate_reason = models.CharField(max_length=200, blank=True, verbose_name="Valiku põhjus")
    extraction_status = models.CharField(max_length=32, blank=True, verbose_name="Eraldamise olek")
    observation_date = models.DateField(null=True, blank=True, verbose_name="Vaatluse kuupäev")
    observation_date_precision = models.CharField(
        max_length=8,
        choices=DatePrecision,
        default=DatePrecision.DAY,
        verbose_name="Kuupäeva täpsus",
    )
    date_source = models.CharField(max_length=64, blank=True, verbose_name="Kuupäeva päritolu")
    date_confidence = models.CharField(
        max_length=16,
        choices=ExtractionConfidence,
        default=ExtractionConfidence.MEDIUM,
        verbose_name="Kuupäeva kindlus",
    )
    document_title = models.CharField(max_length=400, blank=True, verbose_name="Dokumendi pealkiri")
    document_year_claim = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Dokumendi aastaväide"
    )
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")
    notes = models.CharField(max_length=300, blank=True, verbose_name="Märkused")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    class Meta:
        ordering = ("-observation_date", "external_source_id")
        verbose_name = "Liikmeskonna aruandedokument"
        verbose_name_plural = "Liikmeskonna aruandedokumendid"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_source_id"],
                name="membershipsourcedoc_unique_source_id",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "-observation_date"]),
        ]

    def __str__(self) -> str:
        return self.document_title or self.filename or self.external_source_id


class InternalMembershipObservation(models.Model):
    """One reported internal observation, or one row of evidence for a date.

    A single board report can yield two rows: its own current figures
    (`merged_same_document`) and the comparison column restating the previous
    year (`reported_comparison`). Both are stored. Only one row per date is
    marked `is_preferred_for_date`, and that is the one the charts read.

    Nothing here is edited after publication. `quality_status` and
    `is_preferred_for_date` move when a correction supersedes this row; every
    reported number stays exactly as it was recorded.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="internal_membership_observations",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="internal_membership_observations",
        verbose_name="Algfail",
    )
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="internal_membership_observations",
        verbose_name="Impordikäivitus",
    )
    external_snapshot_id = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Paketi kirje tunnus",
        help_text="Ajaloolise paketi snapshot_id. Käsitsi sisestatud kirjel tühi.",
    )
    observation_date = models.DateField(db_index=True, verbose_name="Vaatluse kuupäev")
    observation_date_precision = models.CharField(
        max_length=8,
        choices=DatePrecision,
        default=DatePrecision.DAY,
        verbose_name="Kuupäeva täpsus",
    )
    source_kind = models.CharField(
        max_length=32,
        choices=InternalSourceKind,
        db_index=True,
        verbose_name="Tõendi liik",
    )
    source_column_label = models.CharField(
        max_length=120, blank=True, verbose_name="Lähteveeru silt"
    )
    reported_year = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Aruandeaasta"
    )

    total_members = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Liikmeid kokku"
    )
    paid_members = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Tasunud liikmeid"
    )
    membership_fees_received_eur = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Laekunud liikmemaks (EUR)",
    )
    membership_fee_budget_eur = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Liikmemaksu eelarve (EUR)",
    )
    # Four decimal places, not two. Five historical reports state this figure
    # to three or four places, and `numeric(7,2)` would have rounded them on
    # insert without erroring — silently changing a reported number, which is
    # the one thing this dataset must never do. The extra places cost nothing
    # and the field stores exactly what the board was told.
    membership_fee_collection_pct_reported = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name="Raporteeritud laekumise protsent",
    )
    new_members_ytd = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Uusi liikmeid aasta algusest"
    )
    suspended_members = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Peatatud liikmeid"
    )
    removed_members_ytd = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Väljaarvatuid aasta algusest"
    )

    extraction_confidence = models.CharField(
        max_length=16,
        choices=ExtractionConfidence,
        default=ExtractionConfidence.MEDIUM,
        verbose_name="Eraldamise kindlus",
    )
    quality_status = models.CharField(
        max_length=16,
        choices=QualityStatus,
        default=QualityStatus.VERIFIED,
        db_index=True,
        verbose_name="Kvaliteedi olek",
    )
    is_preferred_for_date = models.BooleanField(
        default=False,
        verbose_name="Eelistatud selle kuupäeva kohta",
    )
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by",
        verbose_name="Asendab vaatlust",
    )
    source_document = models.ForeignKey(
        MembershipHistoricalSourceDocument,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="observations",
        verbose_name="Lähtedokument",
    )
    source_note = models.TextField(blank=True, verbose_name="Märkus")
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="internal_membership_observations",
        verbose_name="Sisestaja",
        help_text="Tühi tähendab imporditud kirjet või kustutatud kasutajat.",
    )
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="Avaldatud")

    # The only two fields a later correction may move. Every reported number is
    # fixed at publication.
    MUTABLE_FIELDS = frozenset({"is_preferred_for_date", "quality_status"})

    class Meta:
        ordering = ("-observation_date", "-id")
        verbose_name = "Sisemine liikmeskonna vaatlus"
        verbose_name_plural = "Sisemised liikmeskonna vaatlused"
        constraints = [
            # The package's own snapshot identifier stays unique inside the
            # source, which is what makes re-importing the same package a no-op
            # rather than a duplication.
            models.UniqueConstraint(
                fields=["source", "external_snapshot_id"],
                condition=~Q(external_snapshot_id=""),
                name="internalobservation_unique_external_snapshot",
            ),
            models.UniqueConstraint(
                fields=["source", "observation_date"],
                condition=Q(is_preferred_for_date=True),
                name="internalobservation_one_preferred_per_date",
            ),
            models.CheckConstraint(
                condition=Q(total_members__isnull=True) | Q(total_members__gt=0),
                name="internalobservation_total_positive_when_present",
            ),
            models.CheckConstraint(
                condition=Q(membership_fees_received_eur__isnull=True)
                | Q(membership_fees_received_eur__gte=0),
                name="internalobservation_fees_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(membership_fee_budget_eur__isnull=True)
                | Q(membership_fee_budget_eur__gte=0),
                name="internalobservation_budget_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(membership_fee_collection_pct_reported__isnull=True)
                | Q(membership_fee_collection_pct_reported__gte=0),
                name="internalobservation_collection_pct_non_negative",
            ),
            # There is deliberately no `paid_members <= total_members`
            # constraint. Fifteen historical rows violate it, and the brief
            # requires them stored as `review_required` with the affected
            # metrics withheld from charts. A constraint here would force the
            # importer to discard real evidence. The manual form refuses the
            # same combination as a hard error, which is where the rule belongs.
        ]
        indexes = [
            models.Index(fields=["source", "-observation_date"]),
            models.Index(fields=["source", "is_preferred_for_date", "-observation_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.observation_date:%d.%m.%Y} ({self.get_source_kind_display()})"

    @property
    def is_manual(self) -> bool:
        return self.source_kind == InternalSourceKind.MANUAL

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise InternalObservationImmutable(
                    "A published internal membership observation may only change its "
                    "quality_status and is_preferred_for_date fields."
                )
        return super().save(*args, **kwargs)


class MembershipSizeMovement(models.Model):
    """How many members joined or left in one company-size band.

    `member_count` is nullable on purpose: a blank cell in a board report means
    "not stated", which is not zero, and the difference has to survive the
    import.
    """

    observation = models.ForeignKey(
        InternalMembershipObservation,
        on_delete=models.CASCADE,
        related_name="size_movements",
        verbose_name="Vaatlus",
    )
    direction = models.CharField(max_length=8, choices=MovementDirection, verbose_name="Suund")
    size_band_key = models.CharField(max_length=32, choices=SizeBand, verbose_name="Suurusklass")
    size_band_label_raw = models.CharField(
        max_length=120, blank=True, verbose_name="Algne klassi silt"
    )
    member_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="Liikmeid")
    total_reported = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Raporteeritud kogusumma"
    )
    extraction_confidence = models.CharField(
        max_length=16,
        choices=ExtractionConfidence,
        default=ExtractionConfidence.MEDIUM,
        verbose_name="Eraldamise kindlus",
    )
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")

    class Meta:
        ordering = ("observation", "direction", "size_band_key")
        verbose_name = "Liikmeskonna liikumine suurusklassiti"
        verbose_name_plural = "Liikmeskonna liikumised suurusklassiti"
        constraints = [
            models.UniqueConstraint(
                fields=["observation", "direction", "size_band_key"],
                name="membershipsizemovement_unique_band_per_direction",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_direction_display()} {self.get_size_band_key_display()}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise InternalObservationImmutable("An imported size movement cannot be changed.")
        return super().save(*args, **kwargs)


class MembershipRemovalReason(models.Model):
    """How many members left for one reported reason.

    A manually entered `other` keeps its own label rather than being mapped into
    a known category, so the admin can see what was actually written.
    """

    observation = models.ForeignKey(
        InternalMembershipObservation,
        on_delete=models.CASCADE,
        related_name="removal_reasons",
        verbose_name="Vaatlus",
    )
    reason_key = models.CharField(max_length=64, choices=RemovalReasonKey, verbose_name="Põhjus")
    reason_label_raw = models.CharField(
        max_length=300, blank=True, verbose_name="Algne põhjuse silt"
    )
    member_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="Liikmeid")
    removed_total_reported = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Raporteeritud väljaarvatuid kokku"
    )
    extraction_confidence = models.CharField(
        max_length=16,
        choices=ExtractionConfidence,
        default=ExtractionConfidence.MEDIUM,
        verbose_name="Eraldamise kindlus",
    )
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")

    class Meta:
        ordering = ("observation", "reason_key", "reason_label_raw")
        verbose_name = "Liikmeskonnast lahkumise põhjus"
        verbose_name_plural = "Liikmeskonnast lahkumise põhjused"
        constraints = [
            # The label is part of the key so that two differently worded
            # `other` reasons can coexist instead of one silently replacing the
            # other.
            models.UniqueConstraint(
                fields=["observation", "reason_key", "reason_label_raw"],
                name="membershipremovalreason_unique_reason_per_observation",
            ),
        ]

    def __str__(self) -> str:
        return self.reason_label_raw or self.get_reason_key_display()

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise InternalObservationImmutable("An imported removal reason cannot be changed.")
        return super().save(*args, **kwargs)


class MembershipMonthlyNewMemberValue(models.Model):
    """How many members joined in one calendar month, as internally reported.

    Three states matter and must stay distinguishable for the rest of the
    application's life:

    - a **verified** value is a number;
    - a **conflict** keeps `new_members` null, because two board reports gave
      different figures for that month and neither has been chosen — it is never
      charted as zero;
    - a month that was never reported has no row at all, which is also not zero.

    An explicitly entered `0` is a real value and stays distinct from all three.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="membership_monthly_new_members",
        verbose_name="Andmeallikas",
    )
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="membership_monthly_new_members",
        verbose_name="Impordikäivitus",
    )
    calendar_year = models.PositiveSmallIntegerField(verbose_name="Aasta")
    calendar_month = models.PositiveSmallIntegerField(verbose_name="Kuu")
    new_members = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Uusi liikmeid",
        help_text="Tühi tähendab vastuolu või teadmata väärtust, mitte nulli.",
    )
    value_status = models.CharField(
        max_length=32,
        choices=MonthlyValueStatus,
        db_index=True,
        verbose_name="Väärtuse olek",
    )
    source_count = models.PositiveIntegerField(default=0, verbose_name="Allikate arv")
    source_ids = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Lähtedokumentide tunnused",
        help_text="Ainult dokumenditunnused. Ei sisalda failiteid ega sisu.",
    )
    earliest_source_observation_date = models.DateField(
        null=True, blank=True, verbose_name="Varaseim lähtevaatlus"
    )
    latest_source_observation_date = models.DateField(
        null=True, blank=True, verbose_name="Hiliseim lähtevaatlus"
    )
    selected_source_document = models.ForeignKey(
        MembershipHistoricalSourceDocument,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="selected_monthly_values",
        verbose_name="Valitud lähtedokument",
    )
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")
    conflicting_values = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Vastuolulised väärtused",
        help_text="Ainult halduri vaade. Ei kuvata tavakasutajale.",
    )
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by",
        verbose_name="Asendab väärtust",
    )
    is_current_for_month = models.BooleanField(default=False, verbose_name="Kehtiv selle kuu kohta")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="membership_monthly_new_members",
        verbose_name="Sisestaja",
    )
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    MUTABLE_FIELDS = frozenset({"is_current_for_month", "value_status"})

    class Meta:
        ordering = ("-calendar_year", "-calendar_month", "-id")
        verbose_name = "Kuu uute liikmete arv"
        verbose_name_plural = "Kuu uute liikmete arvud"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "calendar_year", "calendar_month"],
                condition=Q(is_current_for_month=True),
                name="membershipmonthly_one_current_per_month",
            ),
            models.CheckConstraint(
                condition=Q(calendar_month__gte=1, calendar_month__lte=12),
                name="membershipmonthly_month_in_range",
            ),
            models.CheckConstraint(
                condition=Q(calendar_year__gte=1900),
                name="membershipmonthly_year_plausible",
            ),
            # A conflict is precisely the case where no single value is
            # authoritative, so it may not carry one.
            models.CheckConstraint(
                condition=~Q(value_status=MonthlyValueStatus.CONFLICT)
                | Q(new_members__isnull=True),
                name="membershipmonthly_conflict_has_no_value",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "calendar_year", "calendar_month"]),
        ]

    def __str__(self) -> str:
        return f"{self.calendar_year}-{self.calendar_month:02d}: {self.new_members}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise InternalObservationImmutable(
                    "A published monthly new-member value may only change its "
                    "value_status and is_current_for_month fields."
                )
        return super().save(*args, **kwargs)


class MembershipDataIssue(models.Model):
    """One imported quality warning, and whether a person has dealt with it.

    The warning itself is part of the import and cannot be rewritten; only the
    resolution fields are editable, so "someone looked at this" never turns into
    "the source said something else".
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="membership_data_issues",
        verbose_name="Andmeallikas",
    )
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="membership_data_issues",
        verbose_name="Impordikäivitus",
    )
    external_warning_id = models.CharField(
        max_length=32, blank=True, verbose_name="Paketi hoiatuse tunnus"
    )
    source_document = models.ForeignKey(
        MembershipHistoricalSourceDocument,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="issues",
        verbose_name="Lähtedokument",
    )
    observation = models.ForeignKey(
        InternalMembershipObservation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="issues",
        verbose_name="Vaatlus",
    )
    dataset = models.CharField(max_length=64, db_index=True, verbose_name="Andmestik")
    record_key = models.CharField(max_length=120, blank=True, verbose_name="Kirje võti")
    warning_code = models.CharField(max_length=80, db_index=True, verbose_name="Hoiatuskood")
    severity = models.CharField(
        max_length=16,
        choices=IssueSeverity,
        default=IssueSeverity.INFO,
        db_index=True,
        verbose_name="Raskusaste",
    )
    message = models.CharField(max_length=MAX_MESSAGE_LENGTH, blank=True, verbose_name="Sõnum")
    raw_value = models.CharField(
        max_length=MAX_RAW_VALUE_LENGTH,
        blank=True,
        verbose_name="Algne väärtus",
        help_text="Lühendatud ja puhastatud. Ei sisalda dokumendi teksti.",
    )
    suggested_action = models.CharField(max_length=300, blank=True, verbose_name="Soovitatud samm")
    resolved = models.BooleanField(default=False, db_index=True, verbose_name="Lahendatud")
    resolution_note = models.TextField(blank=True, verbose_name="Lahenduse märkus")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_membership_issues",
        verbose_name="Lahendaja",
    )
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="Lahendatud")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    # Everything the import wrote is fixed; only a human resolution moves.
    MUTABLE_FIELDS = frozenset({"resolved", "resolution_note", "resolved_by", "resolved_at"})

    class Meta:
        ordering = ("severity", "dataset", "record_key", "id")
        verbose_name = "Liikmeskonna andmeprobleem"
        verbose_name_plural = "Liikmeskonna andmeprobleemid"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_warning_id"],
                condition=~Q(external_warning_id=""),
                name="membershipdataissue_unique_external_warning",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "severity", "resolved"]),
        ]

    def __str__(self) -> str:
        return f"{self.warning_code} ({self.get_severity_display()})"


class MembershipMetricConflict(models.Model):
    """Two board reports disagreeing about one metric on one date.

    Kept separate from `MembershipDataIssue` because the key is different — a
    date and a metric rather than a warning identifier — and because the
    selectors query it directly to decide which single metric point to withhold
    from a chart while leaving the rest of that observation visible.

    The import package identifies the disagreeing documents by path; only the
    resolved document identifiers are stored, so no filesystem path enters this
    table.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="membership_metric_conflicts",
        verbose_name="Andmeallikas",
    )
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="membership_metric_conflicts",
        verbose_name="Impordikäivitus",
    )
    observation_date = models.DateField(db_index=True, verbose_name="Vaatluse kuupäev")
    metric = models.CharField(max_length=64, db_index=True, verbose_name="Näitaja")
    warning_code = models.CharField(max_length=80, blank=True, verbose_name="Hoiatuskood")
    distinct_values = models.PositiveSmallIntegerField(
        default=0, verbose_name="Erinevate väärtuste arv"
    )
    values_summary = models.CharField(
        max_length=MAX_MESSAGE_LENGTH,
        blank=True,
        verbose_name="Väärtuste kokkuvõte",
        help_text="Ainult halduri vaade.",
    )
    source_document_ids = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Lähtedokumentide tunnused",
    )
    resolved = models.BooleanField(default=False, db_index=True, verbose_name="Lahendatud")
    resolution_note = models.TextField(blank=True, verbose_name="Lahenduse märkus")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_membership_conflicts",
        verbose_name="Lahendaja",
    )
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="Lahendatud")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    MUTABLE_FIELDS = frozenset({"resolved", "resolution_note", "resolved_by", "resolved_at"})

    class Meta:
        ordering = ("-observation_date", "metric")
        verbose_name = "Liikmeskonna näitaja vastuolu"
        verbose_name_plural = "Liikmeskonna näitajate vastuolud"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "observation_date", "metric"],
                name="membershipmetricconflict_unique_metric_per_date",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "resolved", "-observation_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.observation_date:%d.%m.%Y} {self.metric}"
