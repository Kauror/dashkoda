import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .storage import PrivateArtifactStorage, artifact_upload_path


class ImmutableFieldError(ValidationError):
    """Raised when a registered artifact's immutable identity is changed."""


class SourceType(models.TextChoices):
    """Deliberately generic. The Chamber's own catalogue is not encoded here."""

    DOCUMENT = "document", "Dokument"
    SPREADSHEET = "spreadsheet", "Tabel"
    REGISTRY = "registry", "Register"
    WEBSITE = "website", "Veebileht"
    MANUAL = "manual", "Käsitsi sisestatud"
    OTHER = "other", "Muu"


class AuthorityTier(models.TextChoices):
    """A coarse label only.

    The definitive Chamber authority order is an open decision gate and will be
    confirmed before any real data import. `authority_rank` carries the actual
    ordering; this field is a human-readable grouping, not a ruleset.
    """

    PRIMARY = "primary", "Esmane"
    SECONDARY = "secondary", "Teisene"
    SUPPLEMENTARY = "supplementary", "Täiendav"
    UNCLASSIFIED = "unclassified", "Määramata"


class UpdateFrequency(models.TextChoices):
    DAILY = "daily", "Iga päev"
    WEEKLY = "weekly", "Iga nädal"
    MONTHLY = "monthly", "Iga kuu"
    QUARTERLY = "quarterly", "Iga kvartal"
    ANNUAL = "annual", "Kord aastas"
    IRREGULAR = "irregular", "Ebaregulaarne"
    UNKNOWN = "unknown", "Teadmata"


class AccessLevel(models.TextChoices):
    STAFF_ONLY = "staff_only", "Ainult töötajad"
    RESTRICTED = "restricted", "Piiratud (ainult superkasutaja)"


class ImportStatus(models.TextChoices):
    PENDING = "pending", "Ootel"
    RUNNING = "running", "Töötab"
    SUCCEEDED = "succeeded", "Õnnestus"
    FAILED = "failed", "Ebaõnnestus"

    @classmethod
    def terminal(cls) -> set[str]:
        return {cls.SUCCEEDED, cls.FAILED}


class DataSource(models.Model):
    """A registered origin of information.

    Sources are not deleted once anything references them; they are deactivated.
    Nothing membership-specific belongs here.
    """

    slug = models.SlugField(max_length=64, unique=True, verbose_name="Lühinimi")
    name = models.CharField(max_length=200, verbose_name="Nimi")
    source_type = models.CharField(
        max_length=32,
        choices=SourceType,
        default=SourceType.OTHER,
        verbose_name="Allika tüüp",
    )
    authority_tier = models.CharField(
        max_length=32,
        choices=AuthorityTier,
        default=AuthorityTier.UNCLASSIFIED,
        verbose_name="Autoriteedi tase",
    )
    authority_rank = models.PositiveSmallIntegerField(
        default=100,
        verbose_name="Autoriteedi järk",
        help_text="Väiksem number tähendab kõrgemat autoriteeti. Peab olema positiivne.",
    )
    responsible_person = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Vastutaja",
    )
    expected_update_frequency = models.CharField(
        max_length=32,
        choices=UpdateFrequency,
        default=UpdateFrequency.UNKNOWN,
        verbose_name="Eeldatav uuendussagedus",
    )
    stale_after_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Aegub päevade pärast",
        help_text="Tühi tähendab, et aegumist ei jälgita.",
    )
    description = models.TextField(blank=True, verbose_name="Kirjeldus")
    is_active = models.BooleanField(default=True, verbose_name="Aktiivne")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Loodud")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        ordering = ("authority_rank", "name")
        verbose_name = "Andmeallikas"
        verbose_name_plural = "Andmeallikad"
        constraints = [
            models.CheckConstraint(
                condition=Q(authority_rank__gte=1),
                name="datasource_authority_rank_positive",
            ),
            models.CheckConstraint(
                condition=Q(stale_after_days__isnull=True) | Q(stale_after_days__gte=0),
                name="datasource_stale_after_days_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class SourceArtifact(models.Model):
    """One immutable original file, or one controlled external reference.

    Exactly one of the two is present. The file itself never enters PostgreSQL
    and never becomes publicly reachable.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="artifacts",
        verbose_name="Andmeallikas",
    )
    original_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Algne failinimi",
        help_text="Ainult metaandmed. Salvestatud failitee ei kasuta seda nime.",
    )
    mime_type = models.CharField(max_length=128, blank=True, verbose_name="MIME-tüüp")
    size_bytes = models.PositiveBigIntegerField(default=0, verbose_name="Suurus baitides")
    sha256 = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name="SHA-256",
        help_text="Arvutatakse serveris üleslaaditud sisust. Kliendi väärtust ei usaldata.",
    )
    file = models.FileField(
        upload_to=artifact_upload_path,
        storage=PrivateArtifactStorage(),
        blank=True,
        verbose_name="Privaatne fail",
    )
    external_reference = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Väline viide",
        help_text="Ei tohi sisaldada tunnuseid, tokeneid ega allkirjastatud parameetreid.",
    )
    access_level = models.CharField(
        max_length=32,
        choices=AccessLevel,
        default=AccessLevel.STAFF_ONLY,
        verbose_name="Ligipääsutase",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Registreeritud")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_artifacts",
        verbose_name="Registreerija",
    )

    # Fields that define what the artifact *is*. Changing any of them would
    # break the guarantee that a registered original never changes.
    IMMUTABLE_FIELDS = ("source_id", "file", "sha256", "size_bytes", "external_reference")

    class Meta:
        ordering = ("-uploaded_at", "-id")
        verbose_name = "Algfail"
        verbose_name_plural = "Algfailid"
        permissions = [
            ("download_sourceartifact", "Võib alla laadida algfaili"),
        ]
        constraints = [
            models.CheckConstraint(
                # Exactly one of file / external_reference.
                condition=(
                    Q(file__gt="", external_reference="") | Q(file="", external_reference__gt="")
                ),
                name="sourceartifact_file_xor_external_reference",
            ),
            models.UniqueConstraint(
                fields=["source", "sha256"],
                condition=~Q(sha256=""),
                name="sourceartifact_unique_source_checksum",
            ),
        ]

    def __str__(self) -> str:
        return self.original_name or self.external_reference or f"Algfail {self.pk}"

    @property
    def is_external(self) -> bool:
        return not self.file and bool(self.external_reference)

    def clean(self):
        super().clean()
        has_file = bool(self.file)
        has_reference = bool(self.external_reference.strip())
        if has_file == has_reference:
            raise ValidationError("Määra täpselt üks: kas privaatne fail või väline viide.")
        if has_reference and ("@" in self.external_reference or "?" in self.external_reference):
            raise ValidationError(
                "Väline viide ei tohi sisaldada tunnuseid ega päringuparameetreid."
            )

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            stored = type(self).objects.filter(pk=self.pk).values(*self.IMMUTABLE_FIELDS).first()
            if stored is not None:
                for field in self.IMMUTABLE_FIELDS:
                    current = getattr(self, field)
                    if field == "file":
                        current = current.name or ""
                    if stored[field] != current:
                        raise ImmutableFieldError(
                            f"Registreeritud algfaili välja ei saa muuta: {field}."
                        )
        return super().save(*args, **kwargs)


class ImportRun(models.Model):
    """Registry entry for one import attempt.

    PR-05 provides the registry and its state machine only. No importer exists
    yet, nothing is scheduled, and no domain record is ever written from here.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="import_runs",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        SourceArtifact,
        on_delete=models.PROTECT,
        related_name="import_runs",
        verbose_name="Algfail",
    )
    importer_name = models.CharField(max_length=100, verbose_name="Importija")
    schema_version = models.CharField(max_length=32, verbose_name="Skeemi versioon")
    import_key = models.CharField(max_length=64, db_index=True, verbose_name="Impordivõti")
    dry_run = models.BooleanField(default=True, verbose_name="Proovikäivitus")
    status = models.CharField(
        max_length=32,
        choices=ImportStatus,
        default=ImportStatus.PENDING,
        db_index=True,
        verbose_name="Olek",
    )
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="Alustatud")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Lõpetatud")
    rows_added = models.PositiveIntegerField(default=0, verbose_name="Lisatud ridu")
    rows_skipped = models.PositiveIntegerField(default=0, verbose_name="Vahele jäetud ridu")
    rows_invalid = models.PositiveIntegerField(default=0, verbose_name="Vigaseid ridu")
    warnings = models.JSONField(default=list, blank=True, verbose_name="Hoiatused")
    errors = models.JSONField(default=list, blank=True, verbose_name="Vead")
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_runs",
        verbose_name="Käivitaja",
    )
    correlation_id = models.UUIDField(
        default=uuid.uuid4,
        db_index=True,
        verbose_name="Korrelatsiooni ID",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Loodud")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Impordikäivitus"
        verbose_name_plural = "Impordikäivitused"
        constraints = [
            models.UniqueConstraint(
                # A dry run never blocks a later real import, and a failed run
                # may be retried. Only a successful live import is unique.
                fields=["import_key"],
                condition=Q(status=ImportStatus.SUCCEEDED, dry_run=False),
                name="importrun_unique_successful_live_import",
            ),
            models.CheckConstraint(
                condition=Q(finished_at__isnull=True)
                | Q(started_at__isnull=False, finished_at__gte=models.F("started_at")),
                name="importrun_finished_not_before_started",
            ),
            models.CheckConstraint(
                condition=~Q(status__in=("succeeded", "failed")) | Q(finished_at__isnull=False),
                name="importrun_terminal_requires_finished_at",
            ),
            models.CheckConstraint(
                condition=Q(status__in=("succeeded", "failed")) | Q(finished_at__isnull=True),
                name="importrun_non_terminal_has_no_finished_at",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.importer_name} {self.schema_version} ({self.get_status_display()})"

    @property
    def is_terminal(self) -> bool:
        return self.status in ImportStatus.terminal()

    def clean(self):
        super().clean()
        if self.artifact_id and self.source_id and self.artifact.source_id != self.source_id:
            raise ValidationError("Algfail peab kuuluma samale andmeallikale.")
