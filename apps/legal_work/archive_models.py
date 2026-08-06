"""The public Koda.ee `Hetkel käsil` **archive**, and what the matcher made of it.

A second catalogue, separate from the current one on purpose. The archive holds
every consultation the Chamber has been asked about — around eleven hundred
entries spanning a decade — while the current listing holds fewer than ten.

Sharing one table would mean sharing rarity statistics, and a word that is
genuinely rare among seven live consultations is unremarkable among eleven
hundred. Sharing thresholds would be worse: the base rate here is two orders of
magnitude harsher, so a score that means "almost certainly the same instrument"
against the current listing means very little against the archive.

What the two catalogues *do* share is the canonical URL. A consultation keeps its
address when it moves from the current listing into the archive — verified
against the live site — and that is what makes the fallback a continuation
rather than a new link appearing from nowhere.

These models live beside `models.py` rather than inside it because that module
already carries three datasets; a fourth would make it the longest file in the
repository. They are imported from `models.py`, so Django discovers them
normally and `legal_work.ArchivedTopicItem` is their label as usual.
"""

from django.db import models
from django.db.models import F, Q

from apps.sources.models import DataSource

from .models import (
    MAX_CANONICAL_URL_LENGTH,
    MAX_ERROR_SUMMARY_LENGTH,
    MAX_ORGANIZATION_LENGTH,
    MAX_TOPIC_TITLE_LENGTH,
    LegalCurrentTopicMatchSnapshot,
    LegalWorkItem,
    LegalWorkSnapshot,
    MatchDecision,
    SnapshotImmutable,
    SyncResult,
)


class DetailStatus(models.TextChoices):
    """How much of an archive entry has actually been read."""

    PENDING = "pending", "Ootel"
    HYDRATED = "hydrated", "Loetud"
    FAILED = "failed", "Ebaõnnestus"


class ArchivedTopicSnapshot(models.Model):
    """One published state of the archive index, plus whatever is hydrated.

    `backfill_complete` answers "is there work left that a link depends on?" —
    the listing index covers every page, **every priority candidate for the
    current eligible legal population is read or has definitively failed**, and
    the recent background window is complete. It does **not** mean all eleven
    hundred detail pages were fetched, and it can legitimately return to false
    when a new legal snapshot introduces a record whose candidate has never been
    read.

    `index_complete` is the narrower claim that the listing walk reached the end.

    Matching never waits for either: a hydrated entry is matchable the moment it
    exists. These flags tell an operator whether the crawl still has work to do.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="archived_topic_snapshots",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="archived_topic_snapshots",
        verbose_name="Algfail",
    )
    import_run = models.OneToOneField(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="archived_topic_snapshot",
        verbose_name="Impordikäivitus",
    )
    observed_at = models.DateTimeField(verbose_name="Kogutud")
    item_count = models.PositiveIntegerField(default=0, verbose_name="Kirjeid indeksis")
    detailed_item_count = models.PositiveIntegerField(default=0, verbose_name="Loetud lehti")
    pending_detail_count = models.PositiveIntegerField(default=0, verbose_name="Lugemata lehti")
    failed_detail_count = models.PositiveIntegerField(default=0, verbose_name="Ebaõnnestunud lehti")
    pages_fetched = models.PositiveSmallIntegerField(default=0, verbose_name="Loetud loendilehti")
    # How much of the work that a *link* depends on is done. A priority
    # candidate is an archive page some currently eligible legal record might
    # need, at any age; these three make that visible without reading the rows.
    priority_candidate_count = models.PositiveIntegerField(
        default=0, verbose_name="Prioriteetseid kandidaate"
    )
    priority_detailed_count = models.PositiveIntegerField(
        default=0, verbose_name="Prioriteetseid loetud"
    )
    priority_pending_count = models.PositiveIntegerField(
        default=0, verbose_name="Prioriteetseid lugemata"
    )
    index_complete = models.BooleanField(default=False, verbose_name="Indeks täielik")
    backfill_complete = models.BooleanField(default=False, verbose_name="Täielik")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-observed_at", "-id")
        verbose_name = "Hetkel käsil arhiivi hetkeseis"
        verbose_name_plural = "Hetkel käsil arhiivi hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="archivedtopic_one_current_snapshot_per_source",
            ),
            models.CheckConstraint(
                condition=Q(detailed_item_count__lte=F("item_count")),
                name="archivedtopic_detailed_within_total",
            ),
        ]

    def __str__(self) -> str:
        return f"Arhiiv {self.observed_at:%d.%m.%Y} ({self.item_count})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise SnapshotImmutable(
                    "A collected archive snapshot may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class ArchivedTopicItem(models.Model):
    """One archived consultation: indexed cheaply, hydrated on demand.

    The listing gives a title, a summary and an ordering. It does **not** give a
    usable date. The archive card prints a day and an abbreviated month with no
    year — identically on the newest page and on the page from 2016 — so reading
    a date there would be a guess across a decade. `published_date` therefore
    stays null until the detail page, which prints a full `dd.mm.yyyy`, has been
    read.

    That is also why `detail_status` gates matching. An index-only row has an
    editorial headline and nothing the matcher can date, weigh or contradict, and
    Koda.ee headlines are questions rather than instrument names.
    """

    snapshot = models.ForeignKey(
        ArchivedTopicSnapshot,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Hetkeseis",
    )
    content_key = models.CharField(max_length=64, verbose_name="Sisu võti")
    canonical_url = models.URLField(max_length=MAX_CANONICAL_URL_LENGTH, verbose_name="Aadress")
    title = models.CharField(max_length=MAX_TOPIC_TITLE_LENGTH, verbose_name="Loendi pealkiri")
    listing_summary = models.TextField(blank=True, verbose_name="Loendi kokkuvõte")
    source_page = models.PositiveSmallIntegerField(default=0, verbose_name="Loendileht")
    source_order = models.PositiveIntegerField(verbose_name="Järjekord")
    # A full refresh may prove an entry gone; a daily incremental run never
    # touches most pages, so it carries the previous answer forward rather than
    # inferring absence from not having looked.
    is_present = models.BooleanField(default=True, verbose_name="Arhiivis olemas")

    detail_status = models.CharField(
        max_length=16,
        choices=DetailStatus,
        default=DetailStatus.PENDING,
        db_index=True,
        verbose_name="Lehe olek",
    )
    detail_title = models.CharField(
        max_length=MAX_TOPIC_TITLE_LENGTH, blank=True, verbose_name="Lehe pealkiri"
    )
    body_text = models.TextField(blank=True, verbose_name="Lehe tekst")
    published_date = models.DateField(null=True, blank=True, verbose_name="Avaldatud")
    feedback_deadline = models.DateField(null=True, blank=True, verbose_name="Tagasiside tähtaeg")
    named_organization = models.CharField(
        max_length=MAX_ORGANIZATION_LENGTH, blank=True, verbose_name="Nimetatud asutus"
    )
    detail_content_hash = models.CharField(max_length=64, blank=True, verbose_name="Lehe räsi")
    detail_fetched_at = models.DateTimeField(null=True, blank=True, verbose_name="Leht loetud")
    # A short machine-readable code — `http_404`, `timeout`, `unparsable` — and
    # never a message, a body or a URL.
    detail_failure_code = models.CharField(max_length=32, blank=True, verbose_name="Vea kood")

    class Meta:
        ordering = ("snapshot", "source_order")
        verbose_name = "Hetkel käsil arhiivi teema"
        verbose_name_plural = "Hetkel käsil arhiivi teemad"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "content_key"],
                name="archivedtopicitem_unique_key_per_snapshot",
            ),
            models.UniqueConstraint(
                fields=["snapshot", "canonical_url"],
                name="archivedtopicitem_unique_url_per_snapshot",
            ),
            models.CheckConstraint(condition=~Q(title=""), name="archivedtopicitem_title_required"),
            models.CheckConstraint(
                condition=~Q(canonical_url=""), name="archivedtopicitem_url_required"
            ),
            # "Hydrated" means readable. A row cannot claim to have been read
            # and carry no text, which is what would let an index-only entry
            # slip into matching.
            models.CheckConstraint(
                condition=~Q(detail_status=DetailStatus.HYDRATED) | ~Q(body_text=""),
                name="archivedtopicitem_hydrated_has_body",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "detail_status"]),
            models.Index(fields=["snapshot", "source_page"]),
        ]

    def __str__(self) -> str:
        return self.detail_title or self.title

    @property
    def is_matchable(self) -> bool:
        return self.detail_status == DetailStatus.HYDRATED and self.is_present

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A collected archive row cannot be changed.")
        return super().save(*args, **kwargs)


class ArchivedTopicFeedState(models.Model):
    """What the last archive collection found, and how far the backfill got."""

    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="archived_topic_feed_state",
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
        choices=SyncResult,
        default=SyncResult.NEVER_RUN,
        verbose_name="Viimane tulemus",
    )
    last_error_summary = models.CharField(
        max_length=MAX_ERROR_SUMMARY_LENGTH,
        blank=True,
        verbose_name="Viimane veateade",
        help_text="Puhastatud ja lühendatud. Ei sisalda lehe sisu ega aadresse.",
    )
    current_snapshot = models.ForeignKey(
        ArchivedTopicSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Kehtiv hetkeseis",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        verbose_name = "Arhiivi andmevoo olek"
        verbose_name_plural = "Arhiivi andmevoo olekud"

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"


class LegalArchivedTopicMatchSnapshot(models.Model):
    """One archive matcher run, and the exact three inputs that produced it.

    The third input is the unusual one: this snapshot records the
    **current-topic match snapshot** it deferred to. The archive is a *fallback*,
    so which records it was even allowed to consider depends on what the current
    matcher decided. When the current matcher runs again, this run's population
    is no longer the right one, and pinning the dependency here is what makes
    that detectable instead of silent.
    """

    legal_snapshot = models.ForeignKey(
        LegalWorkSnapshot,
        on_delete=models.CASCADE,
        related_name="archived_topic_match_snapshots",
        verbose_name="Õigusloome hetkeseis",
    )
    archived_topic_snapshot = models.ForeignKey(
        ArchivedTopicSnapshot,
        on_delete=models.CASCADE,
        related_name="match_snapshots",
        verbose_name="Arhiivi hetkeseis",
    )
    current_topic_match_snapshot = models.ForeignKey(
        LegalCurrentTopicMatchSnapshot,
        on_delete=models.CASCADE,
        related_name="archive_fallback_snapshots",
        verbose_name="Hetkel käsil sobitamine",
    )
    matcher_version = models.CharField(max_length=48, verbose_name="Sobitaja versioon")
    generated_at = models.DateTimeField(auto_now_add=True, verbose_name="Arvutatud")
    considered_item_count = models.PositiveIntegerField(default=0, verbose_name="Vaadatud kirjeid")
    matched_count = models.PositiveIntegerField(default=0, verbose_name="Seotud")
    ambiguous_count = models.PositiveIntegerField(default=0, verbose_name="Ebaselgeid")
    unmatched_count = models.PositiveIntegerField(default=0, verbose_name="Sidumata")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-generated_at", "-id")
        verbose_name = "Arhiivi sobitamise hetkeseis"
        verbose_name_plural = "Arhiivi sobitamise hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "legal_snapshot",
                    "archived_topic_snapshot",
                    "current_topic_match_snapshot",
                    "matcher_version",
                ],
                name="legalarchivematch_unique_inputs",
            ),
            models.UniqueConstraint(
                models.F("is_current"),
                condition=Q(is_current=True),
                name="legalarchivematch_one_current",
            ),
            models.CheckConstraint(
                condition=Q(matched_count__lte=F("considered_item_count")),
                name="legalarchivematch_matched_within_total",
            ),
        ]

    def __str__(self) -> str:
        return f"Arhiivi sobitamine {self.matcher_version} ({self.considered_item_count})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise SnapshotImmutable(
                    "A generated archive match snapshot may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class LegalArchivedTopicMatch(models.Model):
    """What the archive matcher decided about one consultation-eligible record."""

    snapshot = models.ForeignKey(
        LegalArchivedTopicMatchSnapshot,
        on_delete=models.CASCADE,
        related_name="matches",
        verbose_name="Sobitamise hetkeseis",
    )
    legal_item = models.ForeignKey(
        LegalWorkItem,
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name="Õigusloome kirje",
    )
    best_candidate = models.ForeignKey(
        ArchivedTopicItem,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Parim kandidaat",
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

    class Meta:
        ordering = ("-score", "legal_item_id")
        verbose_name = "Arhiivi sobitamise tulemus"
        verbose_name_plural = "Arhiivi sobitamise tulemused"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "legal_item"],
                name="legalarchivematch_one_decision_per_item",
            ),
            models.CheckConstraint(
                condition=Q(score__gte=0, score__lte=100),
                name="legalarchivematch_score_within_scale",
            ),
            models.CheckConstraint(
                condition=Q(runner_up_score__gte=0, runner_up_score__lte=100),
                name="legalarchivematch_runner_up_within_scale",
            ),
            models.CheckConstraint(
                condition=Q(score__gte=F("runner_up_score")),
                name="legalarchivematch_runner_up_not_above_score",
            ),
            models.CheckConstraint(
                condition=Q(score_margin=F("score") - F("runner_up_score")),
                name="legalarchivematch_margin_is_score_difference",
            ),
            models.CheckConstraint(
                condition=~Q(decision=MatchDecision.MATCHED) | Q(best_candidate__isnull=False),
                name="legalarchivematch_matched_requires_candidate",
            ),
        ]
        indexes = [models.Index(fields=["snapshot", "decision"])]

    def __str__(self) -> str:
        return f"{self.legal_item_id}: {self.get_decision_display()}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A generated archive match row cannot be changed.")
        return super().save(*args, **kwargs)
