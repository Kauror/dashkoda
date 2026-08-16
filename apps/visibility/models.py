"""How many people the Chamber currently reaches, and when someone counted.

Every `VisibilityObservation` row here is a **manually observed audience size**.
A staff user reads a number off a platform's own screen, types it in, and the
value is published through the same artifact / import-run / audit path an
automated collector uses. That is what let two collectors replace parts of the
form without rewriting a single historical row.

Two sources are now collected rather than typed, and both write into their own
tables beside the manual ones:

- **Google Analytics** — `Ga4DailySnapshot` and friends, through a read-only
  service account. No individual visitor is stored;
- **Smaily** — `SmailyAudienceSnapshot` and `SmailySegmentDaily`, through a
  read-only API client. No subscriber, address, name, open or click is stored,
  and no column here could hold one.

The four social metrics remain manual: Meta, LinkedIn, Instagram and YouTube
have no client in this repository, no credential that would let one exist, and
no field capable of holding a token.

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

Deliberately absent: no subscriber address, no individual follower, no post and
no impression. Campaign opens and clicks are stored, but **only as totals over a
whole send** — `SmailyCampaignStats` has no per-recipient column and there is no
code path that could request one.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.feeds import FeedResult
from apps.sources.models import DataSource

# Sanitized, truncated failure text. Long enough for a useful sentence, short
# enough that nothing resembling a payload can be stored in it.
MAX_ERROR_SUMMARY_LENGTH = 500

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

    # The Chamber sends three newsletters, each to its own list. They replaced
    # a member / non-member / overlap model of a single newsletter, which never
    # matched what actually goes out. Observations recorded under those retired
    # keys are left in the table — they were real readings — but nothing reads
    # them, because no registry entry describes them any more.
    NEWSLETTER_ETEATAJA = "newsletter_eteataja", "e-Teataja"
    NEWSLETTER_ENEWS = "newsletter_enews", "eNews"
    NEWSLETTER_EVESTNIK = "newsletter_evestnik", "e-Vestnik"
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

    # Null for a collected observation. A `VisibilityEntryBatch` is one
    # submission of the manual form — one confirmation page, one idempotency
    # boundary — and a scheduled Smaily reading has none of those. Inventing a
    # batch for it would put a row in the manual-entry history claiming a person
    # typed something. `collection_method` is what tells the two apart, and the
    # artifact and import run give a collected row the provenance a batch gives
    # a typed one.
    batch = models.ForeignKey(
        VisibilityEntryBatch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
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


class Ga4DailySnapshot(models.Model):
    """One immutable GA4 revision of one reporting day.

    The unit is a **reporting day**, not "the latest reading". GA4 revises
    recent days for up to about a week as late hits and identity resolution
    settle, so the same date can be measured several times and the figures can
    legitimately differ. This model keeps every measurement and marks one of
    them current.

    That is why it replaced `WebsiteTrafficObservation`, whose `is_current` was
    unique per *source*: one current row in the whole table, meaning one day. It
    could hold today or history, never both, and re-reading a day would have had
    to overwrite a published fact.

    Two invariants:

    - **exactly one current revision per date.** A partial unique index says so,
      so no bug can produce a day with two truths or a chart that counts a
      Tuesday twice;
    - **a revision is written once.** Only `is_current_for_date` moves after
      publication; a revised day is a *new* row naming the one it replaces, and
      the replaced row keeps its figures for anyone asking what the board was
      shown last month.

    Every metric is nullable because an API that omits one has not reported
    zero. A day GA4 returns no rows for publishes a revision whose figures are
    all absent — an absence of measurement, never a measured zero.

    `engagement_rate` is deliberately **not** a column. It is
    `engaged_sessions / sessions`, and storing a rounded copy of a quotient
    invites two answers to one question; the selectors derive it.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="ga4_daily_snapshots",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="ga4_daily_snapshots",
        verbose_name="Algfail",
    )
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="ga4_daily_snapshots",
        verbose_name="Impordikaivitus",
    )
    report_date = models.DateField(db_index=True, verbose_name="Aruandepaev")
    observed_at = models.DateTimeField(verbose_name="Kogumise aeg")
    checksum = models.CharField(
        max_length=64,
        verbose_name="Kontrollsumma",
        help_text="SHA-256 normaliseeritud paevakomplektist, mitte Google'i vastusest.",
    )
    revision = models.PositiveIntegerField(default=1, verbose_name="Redaktsioon")
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by",
        verbose_name="Asendab",
    )
    is_current_for_date = models.BooleanField(default=False, verbose_name="Kehtiv sel paeval")

    sessions = models.PositiveIntegerField(null=True, blank=True, verbose_name="Külastused")
    active_users = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Aktiivsed kasutajad"
    )
    new_users = models.PositiveIntegerField(null=True, blank=True, verbose_name="Uued kasutajad")
    page_views = models.PositiveIntegerField(null=True, blank=True, verbose_name="Lehevaatamised")
    engaged_sessions = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Kaasatud külastused"
    )
    user_engagement_seconds = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Kaasatuse kestus (s)"
    )

    has_page_detail = models.BooleanField(
        default=False,
        verbose_name="Lehekaupa kogutud",
        help_text=(
            "Kas selle paeva kohta koguti lehtede kaupa read. Vaar tahendab, et "
            "lehtede andmeid ei kusitud - mitte seda, et lehevaatamisi ei olnud."
        ),
    )
    has_channel_detail = models.BooleanField(
        default=False,
        verbose_name="Kanalite kaupa kogutud",
        help_text="Sama vahe: kusimata jaanud ei ole sama mis moodetud null.",
    )

    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    MUTABLE_FIELDS = frozenset({"is_current_for_date"})

    #: Every site-wide figure, for validation and for the canonical payload.
    METRIC_FIELDS = (
        "sessions",
        "active_users",
        "new_users",
        "page_views",
        "engaged_sessions",
        "user_engagement_seconds",
    )

    class Meta:
        ordering = ("-report_date", "-revision", "-id")
        verbose_name = "Google Analyticsi paev"
        verbose_name_plural = "Google Analyticsi paevad"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "report_date"],
                condition=Q(is_current_for_date=True),
                name="ga4daily_one_current_per_date",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="ga4daily_revision_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "-report_date"]),
            models.Index(
                fields=["report_date"],
                condition=Q(is_current_for_date=True),
                name="ga4daily_current_by_date",
            ),
        ]

    def __str__(self) -> str:
        return f"GA4 {self.report_date:%d.%m.%Y} (r{self.revision})"

    @property
    def engagement_rate(self) -> float | None:
        """Engaged sessions as a share of sessions, or `None`.

        Derived rather than stored, and `None` rather than `0.0` when there is
        nothing to divide: no sessions is not an engagement rate of zero.
        """
        if not self.sessions or self.engaged_sessions is None:
            return None
        return self.engaged_sessions / self.sessions

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise VisibilityRecordImmutable(
                    "A published GA4 daily snapshot may only change its is_current_for_date field."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise VisibilityRecordImmutable("A GA4 daily snapshot cannot be deleted.")


class Ga4PageDaily(models.Model):
    """One page's traffic on one reporting day, inside one revision.

    `report_date` is duplicated from the parent deliberately. Every question
    this table answers - an article's first week, the last thirty days, the top
    content of a quarter - filters by date across many pages, and carrying the
    date here keeps that an index range scan rather than a join back to the
    parent for every row. It is written once, in the same transaction as the
    parent, and never moves.

    `path` is canonical (see `apps.visibility.ga4_paths`): no host, no query
    string, no fragment, no trailing slash. `raw_path` keeps what GA4 actually
    said when it differed, so a surprising match can be explained later without
    another API call.

    `active_users` is stored **per page per day** and must never be summed along
    either axis. Two days' users are not twice one day's users, and two pages'
    users are not their sum - the same person read both.
    """

    snapshot = models.ForeignKey(
        Ga4DailySnapshot,
        on_delete=models.CASCADE,
        related_name="pages",
        verbose_name="Paev",
    )
    report_date = models.DateField(verbose_name="Aruandepaev")
    path = models.CharField(max_length=500, verbose_name="Lehe tee")
    raw_path = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Algne tee",
        help_text="Taidetud ainult siis, kui Google'i tee erines kanoonilisest.",
    )
    page_views = models.PositiveIntegerField(verbose_name="Lehevaatamised")
    active_users = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Aktiivsed kasutajad"
    )
    user_engagement_seconds = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Kaasatuse kestus (s)"
    )

    class Meta:
        ordering = ("-report_date", "-page_views", "path")
        verbose_name = "Google Analyticsi lehepaev"
        verbose_name_plural = "Google Analyticsi lehepaevad"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "path"],
                name="ga4page_unique_path_per_snapshot",
            ),
        ]
        indexes = [
            # The shape every content query has: one path, a date range.
            models.Index(fields=["path", "report_date"]),
            models.Index(fields=["report_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.report_date:%d.%m.%Y} {self.path}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise VisibilityRecordImmutable("A GA4 page row cannot be changed.")
        return super().save(*args, **kwargs)


class Ga4ChannelDaily(models.Model):
    """Where one day's sessions came from, inside one revision.

    Session-scoped, so the counts are additive across days: a session belongs to
    exactly one channel. `active_users` is deliberately not stored here for the
    same reason it is never summed anywhere else - a channel's users overlap
    other channels', and a column inviting a `SUM()` is a column that will get
    one.
    """

    snapshot = models.ForeignKey(
        Ga4DailySnapshot,
        on_delete=models.CASCADE,
        related_name="channels",
        verbose_name="Paev",
    )
    report_date = models.DateField(verbose_name="Aruandepaev")
    channel = models.CharField(max_length=120, verbose_name="Kanal")
    sessions = models.PositiveIntegerField(verbose_name="Külastused")
    engaged_sessions = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Kaasatud külastused"
    )

    class Meta:
        ordering = ("-report_date", "-sessions", "channel")
        verbose_name = "Google Analyticsi kanalipaev"
        verbose_name_plural = "Google Analyticsi kanalipaevad"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "channel"],
                name="ga4channel_unique_channel_per_snapshot",
            ),
        ]
        indexes = [
            models.Index(fields=["report_date"]),
            models.Index(fields=["channel", "report_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.report_date:%d.%m.%Y} {self.channel}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise VisibilityRecordImmutable("A GA4 channel row cannot be changed.")
        return super().save(*args, **kwargs)


class Ga4FeedState(models.Model):
    """What the last GA4 collection attempt found.

    The same shape every other feed's state row has, so "last checked",
    "stale after a failure" and the state badge mean one thing across the
    application. GA4 is the only automated source in this module; the manual
    figures beside it have no feed to fall behind and no row here.

    No `etag` and no `last_modified`: this is a JSON API call with a date range,
    not a document fetch with validators, so storing them would suggest a
    conditional request that never happens. Change is decided the way it is for
    every other feed — a canonical checksum over the normalised reading.

    `last_period_end` is the source-identity field: which reporting day was last
    collected. It is what tells an operator whether the schedule has fallen
    behind, which a timestamp alone cannot, because a run that succeeds while
    the API returns nothing new still updates `last_checked_at`.

    Deliberately holds no property ID, no credential path, no access token and
    no response body.
    """

    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="ga4_feed_state",
        verbose_name="Andmeallikas",
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
        max_length=MAX_ERROR_SUMMARY_LENGTH,
        blank=True,
        verbose_name="Viimane veateade",
        help_text=(
            "Puhastatud ja lühendatud. Ei sisalda võtmeid, tokeneid, "
            "property ID-d ega Google'i vastuse sisu."
        ),
    )
    last_period_end = models.DateField(
        null=True,
        blank=True,
        verbose_name="Viimane kogutud päev",
        help_text="Millise aruandepäevani on andmed kogutud.",
    )
    current_snapshot = models.ForeignKey(
        Ga4DailySnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Viimane avaldatud paev",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        verbose_name = "Google Analyticsi andmevoo olek"
        verbose_name_plural = "Google Analyticsi andmevoo olekud"

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"


class SmailyAudienceSnapshot(models.Model):
    """One immutable reading of every Smaily segment, on one day.

    The unit is a **reading day**. Unlike a GA4 reporting day, a subscriber
    count is not a measurement of something that happened during the day — it is
    the size of a list at the moment somebody looked. So a day gets at most one
    current revision, a second reading on the same day that finds a different
    number supersedes the first, and both are kept.

    Two invariants, the same two `Ga4DailySnapshot` holds:

    - **exactly one current revision per date**, enforced by a partial unique
      index, so nothing can produce a day with two truths;
    - **a revision is written once.** Only `is_current_for_date` moves after
      publication; a corrected day is a *new* row naming the one it replaces.

    Deliberately absent: every field that could hold a subscriber. There is no
    email address, no name, no subscriber ID and no per-recipient anything here,
    and there is no column one could be written into. See
    `apps.visibility.smaily` for why that is a property of the schema rather
    than a promise about the collector.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="smaily_audience_snapshots",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="smaily_audience_snapshots",
        verbose_name="Algfail",
    )
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="smaily_audience_snapshots",
        verbose_name="Impordikaivitus",
    )
    observed_on = models.DateField(db_index=True, verbose_name="Lugemise kuupäev")
    observed_at = models.DateTimeField(verbose_name="Kogumise aeg")
    checksum = models.CharField(
        max_length=64,
        verbose_name="Kontrollsumma",
        help_text="SHA-256 normaliseeritud lugemisest, mitte Smaily vastusest.",
    )
    revision = models.PositiveIntegerField(default=1, verbose_name="Redaktsioon")
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by",
        verbose_name="Asendab",
    )
    is_current_for_date = models.BooleanField(default=False, verbose_name="Kehtiv sel päeval")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    MUTABLE_FIELDS = frozenset({"is_current_for_date"})

    class Meta:
        ordering = ("-observed_on", "-revision", "-id")
        verbose_name = "Smaily auditooriumi lugemine"
        verbose_name_plural = "Smaily auditooriumi lugemised"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "observed_on"],
                condition=Q(is_current_for_date=True),
                name="smailyaudience_one_current_per_date",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="smailyaudience_revision_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "-observed_on"]),
            models.Index(
                fields=["observed_on"],
                condition=Q(is_current_for_date=True),
                name="smailyaudience_current_by_date",
            ),
        ]

    def __str__(self) -> str:
        return f"Smaily {self.observed_on:%d.%m.%Y} (r{self.revision})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise VisibilityRecordImmutable(
                    "A published Smaily audience snapshot may only change its "
                    "is_current_for_date field."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise VisibilityRecordImmutable("A Smaily audience snapshot cannot be deleted.")


class SmailySegmentDaily(models.Model):
    """One segment's subscriber count on one reading day, inside one revision.

    Every segment the account holds is stored, not only the four the dashboard
    maps. A list the Chamber starts caring about next year then already has
    history, and which segments make up which newsletter stays a decision
    `apps.visibility.smaily_segments` makes at read time rather than one the
    collector baked into what was written.

    `observed_on` is duplicated from the parent for the same reason
    `Ga4PageDaily.report_date` is: every question here is one segment across a
    date range, and carrying the date makes that an index range scan instead of
    a join back to the parent for every row.

    `subscribers` is what Smaily reports for the segment. It is **not** the
    number of messages a send delivered, and nothing may label it as one — the
    delivered figure lives on a campaign and is a different number.
    """

    snapshot = models.ForeignKey(
        SmailyAudienceSnapshot,
        on_delete=models.CASCADE,
        related_name="segments",
        verbose_name="Lugemine",
    )
    observed_on = models.DateField(verbose_name="Lugemise kuupäev")
    segment_id = models.PositiveIntegerField(verbose_name="Smaily segmendi tunnus")
    name = models.CharField(max_length=200, blank=True, verbose_name="Segmendi nimi")
    subscribers = models.PositiveIntegerField(verbose_name="Tellijaid")

    class Meta:
        ordering = ("-observed_on", "-subscribers", "segment_id")
        verbose_name = "Smaily segmendi lugemine"
        verbose_name_plural = "Smaily segmentide lugemised"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "segment_id"],
                name="smailysegment_unique_per_snapshot",
            ),
        ]
        indexes = [
            # The shape every newsletter query has: one segment, a date range.
            models.Index(fields=["segment_id", "observed_on"]),
            models.Index(fields=["observed_on"]),
        ]

    def __str__(self) -> str:
        return f"{self.observed_on:%d.%m.%Y} {self.name or self.segment_id}"


class SmailyFeedState(models.Model):
    """What the last Smaily collection attempt found.

    The same shape every other feed's state row has, so "last checked", "stale
    after a failure" and the state badge mean one thing across the application.

    Deliberately holds no subdomain, no API username, no password and no
    response body. `last_error_summary` carries only sentences this repository
    wrote — see `apps.visibility.smaily`, where transport failures are replaced
    with our own text precisely so this field cannot accumulate a request URL.
    """

    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="smaily_feed_state",
        verbose_name="Andmeallikas",
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
        max_length=MAX_ERROR_SUMMARY_LENGTH,
        blank=True,
        verbose_name="Viimane veateade",
        help_text=(
            "Puhastatud ja lühendatud. Ei sisalda API kasutajat, parooli, "
            "alamdomeeni ega Smaily vastuse sisu."
        ),
    )
    last_period_end = models.DateField(
        null=True,
        blank=True,
        verbose_name="Viimane kogutud päev",
        help_text="Millise lugemispäevani on andmed kogutud.",
    )
    current_snapshot = models.ForeignKey(
        SmailyAudienceSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Viimane avaldatud lugemine",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        verbose_name = "Smaily andmevoo olek"
        verbose_name_plural = "Smaily andmevoo olekud"

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"


class SmailyCampaign(models.Model):
    """One completed Smaily campaign, catalogued so it keeps its name.

    A **catalogue row**, not a published measurement — the same kind of thing
    `news.NewsResource` is, and updated for the same reason: Smaily's campaign
    list is a window onto recent sends, and a campaign that scrolls out of it
    would otherwise lose its name and its date. What is measured about a
    campaign lives in `SmailyCampaignStats`, which *is* immutable.

    `newsletter` is resolved once, when the campaign is first seen, from the
    template name (see `apps.visibility.smaily_campaigns`). Storing it rather
    than deriving it at read time is deliberate: a template can be renamed or
    deleted afterwards, and a historical chart must not change shape because
    somebody tidied a template list.

    A campaign that is not an issue of one of the three newsletters — an event
    calendar, an Enterprise Europe Network mailing, a one-off invitation — is
    catalogued with `newsletter` blank. It is deliberately not forced into a
    newsletter, because it would then appear in that newsletter's open rate.

    `preview_url` is **presentation metadata, not an archive.** Smaily's own
    field is `template.preview_url`, and it addresses the *template*: on this
    account 364 campaigns share a template with another, one of them eleven
    ways. So two campaigns can point at one preview, and that preview renders
    whatever the template holds today rather than what went out in 2019.
    DashKoda stores the address Smaily gave for the campaign and links to it; it
    does not claim to hold a copy of the newsletter as sent, and it never
    fetches or stores the newsletter HTML to manufacture one.

    Deliberately absent: recipients. No address, no name, no subscriber ID and
    no per-recipient open, click, bounce or unsubscribe, and no column that
    could hold one.
    """

    campaign_id = models.PositiveBigIntegerField(
        unique=True,
        verbose_name="Smaily kampaania tunnus",
    )
    name = models.CharField(max_length=300, blank=True, verbose_name="Pealkiri")
    template_name = models.CharField(
        max_length=300,
        blank=True,
        verbose_name="Malli nimi",
        help_text="Väli, mille järgi uudiskiri tuvastatakse. Silt on kontol alati tühi.",
    )
    template_external_id = models.CharField(
        max_length=40,
        blank=True,
        verbose_name="Smaily malli tunnus",
        help_text=(
            "Smaily malli tunnus, kui mall on veel olemas. Ei ole kampaania "
            "identiteet: mitu kampaaniat võivad kasutada sama malli."
        ),
    )
    preview_url = models.URLField(
        max_length=500,
        blank=True,
        verbose_name="Eelvaate aadress",
        help_text=(
            "Smaily enda eelvaade, kontrollitud enne salvestamist. Tühi, kui mall "
            "on kustutatud — kampaania ja tema statistika jäävad alles. "
            "Eelvaade kuulub MALLILE, mitte kampaaniale: sama malli jagavad "
            "kampaaniad viivad samale lehele ja see näitab malli praegust sisu."
        ),
    )
    newsletter = models.CharField(
        max_length=48,
        blank=True,
        choices=VisibilityMetric,
        db_index=True,
        verbose_name="Uudiskiri",
        help_text="Tühi, kui saadetis ei ole ühegi kolme uudiskirja number.",
    )
    audience = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Sihtrühm",
        help_text="e-Teataja läheb välja kahe saadetisena: liikmed ja mitteliikmed.",
    )
    status = models.CharField(max_length=20, blank=True, verbose_name="Olek")
    created_at = models.DateTimeField(null=True, blank=True, verbose_name="Loodud")
    completed_at = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name="Saadetud"
    )
    first_seen_at = models.DateTimeField(auto_now_add=True, verbose_name="Esmakordselt nähtud")
    last_seen_at = models.DateTimeField(auto_now=True, verbose_name="Viimati nähtud")

    class Meta:
        ordering = ("-completed_at", "-campaign_id")
        verbose_name = "Smaily kampaania"
        verbose_name_plural = "Smaily kampaaniad"
        indexes = [
            # The shape every newsletter query has: one newsletter, newest first.
            models.Index(fields=["newsletter", "-completed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name or self.campaign_id}"

    @property
    def is_newsletter(self) -> bool:
        return bool(self.newsletter)

    @property
    def has_preview(self) -> bool:
        """Whether there is a newsletter to open.

        False for a campaign whose template was deleted — 67 of this account's
        3 194. The row still shows the send and every figure it has; what it
        does not show is a link that would go nowhere.
        """
        return bool(self.preview_url)


class SmailyCampaignStats(models.Model):
    """One immutable revision of one campaign's aggregate statistics.

    A campaign's figures keep moving after it is sent: opens and clicks accrue
    for days, bounces resolve, unsubscribes trickle in. So the same campaign is
    measured several times and the numbers legitimately differ, which is the
    same situation `Ga4DailySnapshot` is in and gets the same treatment — every
    measurement is kept and exactly one is current.

    Every count is nullable because an API that omits a figure has not reported
    zero.

    **No rate is stored.** Smaily returns `opened_percent`, `click_percent` and
    `view_percent`; all three are quotients of the counts beside them, and
    `opened_percent` is a share of *delivered* rather than of *sent*. Storing a
    rounded copy would invite two answers to one question and would lose the
    denominator, which is the part that matters. The properties below derive
    them and each names what it divided by.

    Deliberately absent: every per-recipient field. This is a count over a whole
    send and nothing finer exists here.
    """

    campaign = models.ForeignKey(
        SmailyCampaign,
        on_delete=models.CASCADE,
        related_name="statistics",
        verbose_name="Kampaania",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="smaily_campaign_stats",
        verbose_name="Algfail",
    )
    import_run = models.ForeignKey(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="smaily_campaign_stats",
        verbose_name="Impordikaivitus",
    )
    observed_at = models.DateTimeField(verbose_name="Kogumise aeg")
    checksum = models.CharField(
        max_length=64,
        verbose_name="Kontrollsumma",
        help_text="SHA-256 normaliseeritud arvudest, mitte Smaily vastusest.",
    )
    revision = models.PositiveIntegerField(default=1, verbose_name="Redaktsioon")
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by",
        verbose_name="Asendab",
    )
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")

    total_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="Saadetud")
    delivered_count = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Kohale toimetatud"
    )
    bounce_count = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Tagasi põrkunud"
    )
    opened_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="Avamisi")
    click_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="Klikke")
    unique_click_count = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Unikaalseid klikke"
    )
    view_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="Vaatamisi")
    unique_view_count = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Unikaalseid vaatamisi"
    )
    unsubscribe_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="Loobumisi")
    complaint_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="Kaebusi")
    forward_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="Edasisaatmisi")

    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")

    MUTABLE_FIELDS = frozenset({"is_current"})

    COUNT_FIELDS = (
        "total_count",
        "delivered_count",
        "bounce_count",
        "opened_count",
        "click_count",
        "unique_click_count",
        "view_count",
        "unique_view_count",
        "unsubscribe_count",
        "complaint_count",
        "forward_count",
    )

    class Meta:
        ordering = ("-observed_at", "-revision", "-id")
        verbose_name = "Smaily kampaania statistika"
        verbose_name_plural = "Smaily kampaaniate statistika"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign"],
                condition=Q(is_current=True),
                name="smailycampaignstats_one_current",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="smailycampaignstats_revision_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.campaign_id} (r{self.revision})"

    @property
    def open_rate(self) -> float | None:
        """Opens as a share of **delivered**, or `None`.

        Delivered rather than sent, which is what Smaily's own `opened_percent`
        means. A message that bounced was never open-able, so dividing by sent
        would understate every campaign by its bounce rate.
        """
        if not self.delivered_count or self.opened_count is None:
            return None
        return self.opened_count / self.delivered_count

    @property
    def click_rate(self) -> float | None:
        """Unique clicks as a share of **delivered**, or `None`.

        Unique rather than total: total clicks counts one enthusiastic reader
        opening six links as six, which is a measure of link count rather than
        of interest.
        """
        if not self.delivered_count or self.unique_click_count is None:
            return None
        return self.unique_click_count / self.delivered_count

    @property
    def click_to_open_rate(self) -> float | None:
        """Unique clicks as a share of **opens**, or `None`.

        What share of the people who looked went on to follow something. Its
        denominator is opens, and a caller that mixes it with `click_rate` is
        comparing two different questions.
        """
        if not self.opened_count or self.unique_click_count is None:
            return None
        return self.unique_click_count / self.opened_count

    @property
    def unsubscribe_rate(self) -> float | None:
        if not self.delivered_count or self.unsubscribe_count is None:
            return None
        return self.unsubscribe_count / self.delivered_count

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise VisibilityRecordImmutable(
                    "Published Smaily campaign statistics may only change their is_current field."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise VisibilityRecordImmutable("Smaily campaign statistics cannot be deleted.")


class Ga4PeriodUsers(models.Model):
    """Distinct users over one date range, as Google Analytics counted them.

    Its own table rather than a column, because a period user count is not a
    property of a day. `Ga4DailySnapshot.active_users` answers "how many people
    on this Tuesday"; two Tuesdays' answers cannot be added, so the period
    question has to be asked of GA4 separately with the whole range as its date
    range. This is where those answers are kept.

    **Not an immutable revision.** The daily snapshots are versioned because a
    published figure that GA4 later revises must stay auditable. A row here is a
    cache of one deterministic question and carries no published history: when
    GA4's answer for a settled range changes, the newer answer simply replaces
    it. `fetched_at` says how old the answer is.

    A range whose row is absent has **not been asked**, which is different from
    a range whose answer was zero. Callers distinguish the two: a missing row is
    reported as an unfetched period and never drawn as a zero.
    """

    start_date = models.DateField(verbose_name="Algus")
    end_date = models.DateField(verbose_name="Lõpp")
    active_users = models.PositiveIntegerField(verbose_name="Kasutajad")
    fetched_at = models.DateTimeField(auto_now=True, verbose_name="Päritud")

    class Meta:
        ordering = ("-end_date", "-start_date")
        verbose_name = "Perioodi kasutajad"
        verbose_name_plural = "Perioodi kasutajad"
        constraints = [
            models.UniqueConstraint(
                fields=["start_date", "end_date"],
                name="ga4perioduser_one_row_per_range",
            ),
        ]
        indexes = [
            models.Index(fields=["start_date", "end_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.start_date:%d.%m.%Y}–{self.end_date:%d.%m.%Y}: {self.active_users}"

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError({"end_date": "Vahemiku lõpp ei saa olla enne algust."})
