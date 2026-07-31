"""How many people the Chamber currently reaches, and when someone counted.

Every row here is a **manually observed audience size**. Nothing in this module
is collected from a platform: there is no Smaily, Meta, LinkedIn, Instagram,
YouTube or Google Analytics client anywhere in this repository, no credential
that would let one exist, and no field capable of holding a token. A staff user
reads a number off a platform's own screen, types it in, and the value is
published through the same artifact / import-run / audit path an automated
collector would use. That is what will let a collector replace the form later
without rewriting a single historical row.

Three rules shape the schema, and all three are the same rules the internal
membership history already obeys:

- **a published value is immutable.** Only `is_current_for_date` moves. A
  correction is a *new* observation that names the one it replaces, and the
  replaced row keeps its number;
- **a later date never supersedes an earlier one.** Both stay in history.
  Supersession is only ever within one metric on one date;
- **absent is not zero.** A metric nobody has entered has no row at all, and a
  row carrying `0` means somebody counted zero. The two must stay
  distinguishable for the rest of the application's life, which is why the
  metrics are columns of typed rows rather than keys in a JSON blob.

Deliberately absent: no subscriber address, no individual follower, no post,
no impression, no open, no click, and no platform credential.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from apps.sources.models import DataSource

# The note is a short operator remark ("loetud Smaily halduses"), never a place
# to accumulate prose.
MAX_NOTE_LENGTH = 500


class VisibilityRecordImmutable(RuntimeError):
    """Raised when something tries to rewrite or remove a published record."""


class VisibilityMetric(models.TextChoices):
    """The complete, closed vocabulary of audience metrics.

    Closed on purpose. A free-text metric name would turn this app into the
    product-wide key/value store the brief rules out, and would let a typo
    create a second series that looks like the first one.
    """

    NEWSLETTER_MEMBER_RECIPIENTS = (
        "newsletter_member_recipients",
        "Liikmete uudiskirja aktiivsed saajad",
    )
    NEWSLETTER_NONMEMBER_RECIPIENTS = (
        "newsletter_nonmember_recipients",
        "Mitteliikmete uudiskirja aktiivsed saajad",
    )
    NEWSLETTER_OVERLAP_RECIPIENTS = (
        "newsletter_overlap_recipients",
        "Mõlemas nimekirjas olevad saajad",
    )
    FACEBOOK_FOLLOWERS = "facebook_followers", "Facebooki jälgijad"
    LINKEDIN_FOLLOWERS = "linkedin_followers", "LinkedIni jälgijad"
    INSTAGRAM_FOLLOWERS = "instagram_followers", "Instagrami jälgijad"
    # Typographic apostrophe, not the ASCII one. Django escapes `'` to `&#x27;`,
    # and the digits inside that entity survive a plain tag-strip — which would
    # read as a number on a page asserting it shows none.
    YOUTUBE_SUBSCRIBERS = "youtube_subscribers", "YouTube’i tellijad"


class CollectionMethod(models.TextChoices):
    """How the number reached DashKoda.

    `AUTOMATIC` exists so a future collector writes into the same table rather
    than a parallel one. Nothing in this pull request produces it, and the
    viewer wording for `MANUAL` never claims a feed exists.
    """

    MANUAL = "manual", "Käsitsi sisestatud"
    AUTOMATIC = "automatic", "Automaatselt kogutud"


class VisibilityEntryBatch(models.Model):
    """One submission of the manual form.

    It exists so that a submission is one thing: one confirmation page, one
    idempotency boundary, one correlation ID and one all-or-nothing
    transaction. Publishing half a form — the newsletter figures without the
    social ones — is not a state this application can reach.

    `content_hash` is the SHA-256 of the canonical JSON of everything that was
    submitted, and it is unique. That is what makes a double submit harmless at
    the *database* level rather than only in a view: the second attempt loses
    the race and the service returns the first batch.

    It holds no secret and no personal information. The note is a short operator
    remark and is bounded.
    """

    observation_date = models.DateField(db_index=True, verbose_name="Vaatluse kuupäev")
    note = models.CharField(
        max_length=MAX_NOTE_LENGTH,
        blank=True,
        verbose_name="Märkus",
        help_text="Vaba tekst kuni 500 tähemärki. Ei sisalda isikuandmeid ega salajasi väärtusi.",
    )
    content_hash = models.CharField(
        max_length=64,
        unique=True,
        verbose_name="Sisu SHA-256",
        help_text="Kanoonilise JSON-i räsi. Sama sisuga esitust ei avaldata kaks korda.",
    )
    correlation_id = models.UUIDField(
        default=uuid.uuid4,
        db_index=True,
        verbose_name="Korrelatsiooni ID",
        help_text="Seob ühe esituse kõik vaatlused, impordid ja auditisündmused.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visibility_batches",
        verbose_name="Sisestaja",
        help_text="Tühi tähendab kustutatud kasutajat.",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Loodud")

    class Meta:
        ordering = ("-observation_date", "-id")
        verbose_name = "Nähtavuse sisestus"
        verbose_name_plural = "Nähtavuse sisestused"
        indexes = [
            models.Index(fields=["-observation_date", "-id"]),
        ]

    def __str__(self) -> str:
        return f"Nähtavuse sisestus {self.observation_date:%d.%m.%Y}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise VisibilityRecordImmutable("A published visibility batch cannot be changed.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise VisibilityRecordImmutable("A visibility batch cannot be deleted.")


class VisibilityObservationQuerySet(models.QuerySet):
    def delete(self):
        raise VisibilityRecordImmutable("Visibility observations cannot be deleted.")


class VisibilityObservationManager(models.Manager.from_queryset(VisibilityObservationQuerySet)):
    """Blocks the bulk paths that would sidestep the instance-level rules."""

    def bulk_update(self, *args, **kwargs):
        raise VisibilityRecordImmutable("Visibility observations cannot be bulk updated.")


class VisibilityObservation(models.Model):
    """One metric, on one date, as somebody actually observed it.

    The value is fixed at publication. `is_current_for_date` is the only field a
    later correction moves, and it answers exactly one question: is this the row
    to read for this metric on this date? Everything else about the row — the
    number, who entered it, which batch it arrived in, which artifact carries
    its content identity — stays as recorded.
    """

    batch = models.ForeignKey(
        VisibilityEntryBatch,
        on_delete=models.PROTECT,
        related_name="observations",
        verbose_name="Sisestus",
    )
    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="visibility_observations",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="visibility_observations",
        verbose_name="Algfail",
    )
    # A foreign key rather than a one-to-one: one submission produces one import
    # run *per source*, and the newsletter source carries three metrics. Making
    # it one-to-one would force three identical runs for one Smaily reading.
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="visibility_observations",
        verbose_name="Impordikäivitus",
    )
    metric = models.CharField(
        max_length=48,
        choices=VisibilityMetric,
        db_index=True,
        verbose_name="Näitaja",
    )
    value = models.PositiveIntegerField(
        verbose_name="Väärtus",
        help_text="Täisarv. Selgesõneline 0 on päris väärtus; puuduv väärtus ei ole rida.",
    )
    collection_method = models.CharField(
        max_length=16,
        choices=CollectionMethod,
        default=CollectionMethod.MANUAL,
        db_index=True,
        verbose_name="Kogumisviis",
    )
    observation_date = models.DateField(db_index=True, verbose_name="Vaatluse kuupäev")
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by",
        verbose_name="Asendab vaatlust",
        help_text="Ainult sama näitaja ja sama kuupäeva parandus.",
    )
    is_current_for_date = models.BooleanField(
        default=False,
        verbose_name="Kehtiv selle kuupäeva kohta",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visibility_observations",
        verbose_name="Sisestaja",
    )
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="Avaldatud")

    # The only field a correction may move. The number itself never changes.
    MUTABLE_FIELDS = frozenset({"is_current_for_date"})

    objects = VisibilityObservationManager()

    class Meta:
        ordering = ("-observation_date", "metric", "-id")
        verbose_name = "Nähtavuse vaatlus"
        verbose_name_plural = "Nähtavuse vaatlused"
        constraints = [
            # Exactly one readable row per metric per date. The registry pins one
            # source per metric, so the source is not part of the key: including
            # it would let two sources both claim the same day.
            models.UniqueConstraint(
                fields=["metric", "observation_date"],
                condition=Q(is_current_for_date=True),
                name="visibilityobservation_one_current_per_metric_date",
            ),
            # One form, one value per metric. Without this a submission could
            # carry two different Facebook counts and neither would be wrong.
            models.UniqueConstraint(
                fields=["batch", "metric"],
                name="visibilityobservation_unique_metric_per_batch",
            ),
            # `PositiveIntegerField` already refuses a negative at the database
            # level; stating it as a named constraint means the invariant is
            # findable and survives a future field-type change.
            models.CheckConstraint(
                condition=Q(value__gte=0),
                name="visibilityobservation_value_non_negative",
            ),
            # There is deliberately **no** `supersedes != id` check constraint.
            # It would be unenforceable in the one place it matters and harmful
            # everywhere else: `Model.full_clean()` validates check constraints
            # against the in-memory instance, whose `id` is still `None` before
            # the first save, so `supersedes = id` evaluates to SQL NULL and the
            # constraint would reject **every correction** at validation time.
            #
            # Nothing is lost. A row cannot name itself at creation because it
            # has no identifier yet, and it cannot acquire one later because
            # `supersedes` is not in `MUTABLE_FIELDS`. `clean()` and
            # `publishing.supersede_observation` refuse the case explicitly.
        ]
        indexes = [
            models.Index(fields=["metric", "-observation_date"]),
            models.Index(fields=["metric", "is_current_for_date", "-observation_date"]),
            models.Index(fields=["batch", "metric"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_metric_display()} {self.observation_date:%d.%m.%Y}: {self.value}"

    def clean(self):
        """Check the two invariants a database constraint cannot express.

        The metric-to-source mapping lives in `registry.py`, not in a table, so
        PostgreSQL has nothing to join against. It is enforced here and in the
        publication service, and covered by its own test.
        """
        super().clean()
        # Imported lazily: the registry imports this module for its vocabulary.
        from .registry import spec_for

        if self.metric and self.source_id:
            spec = spec_for(self.metric)
            if spec is None:
                raise ValidationError({"metric": "Tundmatu näitaja."})
            if self.source.slug != spec.source_slug:
                raise ValidationError(
                    {"source": f"Näitaja {self.metric} allikas peab olema {spec.source_slug}."}
                )
        if self.supersedes_id:
            if self.pk is not None and self.supersedes_id == self.pk:
                raise ValidationError({"supersedes": "Vaatlus ei saa asendada iseennast."})
            if self.supersedes.metric != self.metric:
                raise ValidationError(
                    {"supersedes": "Parandus peab asendama sama näitaja vaatlust."}
                )
            if self.supersedes.observation_date != self.observation_date:
                raise ValidationError(
                    {"supersedes": "Parandus peab asendama sama kuupäeva vaatlust."}
                )

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise VisibilityRecordImmutable(
                    "A published visibility observation may only change its "
                    "is_current_for_date field."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise VisibilityRecordImmutable("A visibility observation cannot be deleted.")


class WebsiteTrafficObservation(models.Model):
    """Website traffic for one reporting period. **Nothing writes this yet.**

    The model exists so the GA4 integration is a collector plus a migration-free
    publication path rather than a schema redesign, and so the shape of that
    future data is reviewable now. There is no manual entry route for it, no
    Google SDK dependency, no credential and no request: see `ga4.py` for the
    seam and `docs/visibility-manual-entry.md` for what the next pull request
    needs.

    The three figures are nullable because a reporting API that omits one has
    not reported zero, and this application does not invent the difference.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="website_traffic_observations",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="website_traffic_observations",
        verbose_name="Algfail",
    )
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="website_traffic_observations",
        verbose_name="Impordikäivitus",
    )
    observed_at = models.DateTimeField(db_index=True, verbose_name="Vaatluse aeg")
    period_start = models.DateField(verbose_name="Perioodi algus")
    period_end = models.DateField(verbose_name="Perioodi lõpp")
    sessions = models.PositiveIntegerField(null=True, blank=True, verbose_name="Seansid")
    active_users = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Aktiivsed kasutajad"
    )
    page_views = models.PositiveIntegerField(null=True, blank=True, verbose_name="Lehevaatamised")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-period_end", "-id")
        verbose_name = "Veebiliikluse vaatlus"
        verbose_name_plural = "Veebiliikluse vaatlused"
        constraints = [
            models.CheckConstraint(
                condition=Q(period_end__gte=F("period_start")),
                name="websitetraffic_period_end_not_before_start",
            ),
            models.CheckConstraint(
                condition=Q(sessions__isnull=True) | Q(sessions__gte=0),
                name="websitetraffic_sessions_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(active_users__isnull=True) | Q(active_users__gte=0),
                name="websitetraffic_active_users_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(page_views__isnull=True) | Q(page_views__gte=0),
                name="websitetraffic_page_views_non_negative",
            ),
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="websitetraffic_one_current_per_source",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "-period_end"]),
        ]

    def __str__(self) -> str:
        return f"{self.period_start:%d.%m.%Y}–{self.period_end:%d.%m.%Y}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise VisibilityRecordImmutable(
                    "A published website traffic observation may only change its is_current field."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise VisibilityRecordImmutable("A website traffic observation cannot be deleted.")
