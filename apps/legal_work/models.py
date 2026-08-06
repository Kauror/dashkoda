"""Imported legal-work snapshots and their rows.

The dashboard reads only PostgreSQL. One successful live import writes one
complete, immutable snapshot and atomically becomes the current one; a failed
import leaves the previous current snapshot exactly as it was.

This app deliberately holds no responsible-lawyer field, no member-feedback
counts and no relation to opinion documents. Those are out of scope and are not
modelled "just in case": an absent column cannot leak.

Three datasets live here, and the boundary between them matters:

- the **imported workbook** (`LegalWorkSnapshot`, `LegalWorkItem`) — the
  canonical legal-work feed, unchanged by anything below it;
- the **public current-topic catalogue** (`CurrentTopicSnapshot`,
  `CurrentTopicItem`) — the Koda.ee `Hetkel käsil` listing, collected on its
  own schedule under its own source;
- the **derived match results** (`LegalCurrentTopicMatchSnapshot`,
  `LegalCurrentTopicMatch`) — what a deterministic matcher proposed about the
  first two.

A fourth and fifth dataset — the `Hetkel käsil` **archive** catalogue and its own
match results — live in `archive_models.py` and are imported at the foot of this
module. They are separated because this file already carries three datasets, not
because they are different in kind.

Nothing written by any of them ever reaches a `LegalWorkItem`. The workbook rows
are rebuilt from scratch on every import, so a match result stored on one would
be erased by the next morning's sync; that is not a limitation worked around
here, it is the reason the results live in their own snapshots.
"""

from django.db import models
from django.db.models import F, Q

from apps.sources.models import DataSource

# The workbook uses `;` between codes; the importer normalises them into a list.
MAX_ERROR_SUMMARY_LENGTH = 500

# Bounds on stored public-page text. A URL longer than this is not a canonical
# Koda.ee address, and the two text limits keep a listing summary and an article
# body from growing without a ceiling.
MAX_CANONICAL_URL_LENGTH = 500
MAX_TOPIC_TITLE_LENGTH = 500
MAX_LISTING_SUMMARY_LENGTH = 1000
MAX_BODY_TEXT_LENGTH = 8000
MAX_ORGANIZATION_LENGTH = 200


class SnapshotImmutable(RuntimeError):
    """Raised when something tries to rewrite an imported snapshot or row."""


class SentStatus(models.TextChoices):
    PENDING = "pending", "Ootel"
    SENT = "sent", "Saadetud"
    NOT_SENT = "not_sent", "Ei saadetud"
    INVALID = "invalid", "Vigane"


class SyncResult(models.TextChoices):
    NEVER_RUN = "never_run", "Pole veel käivitatud"
    IMPORTED = "imported", "Imporditud"
    UNCHANGED = "unchanged", "Muutumatu"
    FAILED = "failed", "Ebaõnnestus"


class LegalWorkSnapshot(models.Model):
    """One complete import of the legal-work workbook.

    Everything except `is_current` is fixed once written. `is_current` has to
    move, because publishing a new snapshot retires the previous one, so
    `save()` permits that single field and refuses every other change.
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="legal_work_snapshots",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="legal_work_snapshots",
        verbose_name="Algfail",
    )
    import_run = models.OneToOneField(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="legal_work_snapshot",
        verbose_name="Impordikäivitus",
    )
    schema_version = models.CharField(
        max_length=16,
        verbose_name="Skeemi versioon",
        help_text="Töövihiku enda deklareeritud versioon, mitte importija oma.",
    )
    reporting_date = models.DateField(verbose_name="Andmete seis")
    workbook_generated_at = models.DateTimeField(verbose_name="Töövihik loodud")
    source_file_modified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Lähtefail muudetud",
    )
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Imporditud")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")
    total_record_count = models.PositiveIntegerField(default=0, verbose_name="Kirjeid kokku")
    open_record_count = models.PositiveIntegerField(default=0, verbose_name="Avatud kirjeid")
    sent_record_count = models.PositiveIntegerField(default=0, verbose_name="Välja saadetud")
    warning_record_count = models.PositiveIntegerField(
        default=0, verbose_name="Hoiatustega kirjeid"
    )

    # Only this field may move after a snapshot has been written.
    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-imported_at", "-id")
        verbose_name = "Õigusloome hetkeseis"
        verbose_name_plural = "Õigusloome hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="legalwork_one_current_snapshot_per_source",
            ),
            models.CheckConstraint(
                condition=Q(open_record_count__lte=F("total_record_count")),
                name="legalwork_open_count_within_total",
            ),
            models.CheckConstraint(
                condition=Q(sent_record_count__lte=F("total_record_count")),
                name="legalwork_sent_count_within_total",
            ),
            models.CheckConstraint(
                condition=Q(warning_record_count__lte=F("total_record_count")),
                name="legalwork_warning_count_within_total",
            ),
        ]

    def __str__(self) -> str:
        return f"Õigusloome {self.reporting_date:%d.%m.%Y} ({self.total_record_count})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise SnapshotImmutable(
                    "An imported legal-work snapshot may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class LegalWorkItem(models.Model):
    """One imported row. Immutable once its snapshot has been written."""

    snapshot = models.ForeignKey(
        LegalWorkSnapshot,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Hetkeseis",
    )
    record_id = models.CharField(max_length=64, verbose_name="Kirje ID")
    source_year = models.PositiveSmallIntegerField(verbose_name="Aasta")
    source_nr = models.PositiveIntegerField(null=True, blank=True, verbose_name="Number")
    # The workbook carries long topic descriptions; a bounded CharField would
    # truncate real records.
    topic = models.TextField(verbose_name="Teema")
    act_type = models.CharField(max_length=100, blank=True, verbose_name="Õigusakti liik")
    received_date = models.DateField(null=True, blank=True, verbose_name="Sisse")
    deadline_date = models.DateField(null=True, blank=True, verbose_name="Arvamuse tähtaeg")
    sent_date = models.DateField(null=True, blank=True, verbose_name="Välja")
    sent_status = models.CharField(
        max_length=16,
        choices=SentStatus,
        default=SentStatus.PENDING,
        db_index=True,
        verbose_name="Saatmise olek",
    )
    recipient = models.CharField(max_length=200, blank=True, verbose_name="Kellele")
    # `stage_key` is the workbook's normalised lower-case form of `stage`. It is
    # free text in the source, not a controlled vocabulary, so it carries no
    # choices: the lawyers write their own wording and it must survive intact.
    stage = models.CharField(max_length=200, blank=True, verbose_name="Hetkeseis")
    stage_key = models.CharField(
        max_length=200, blank=True, db_index=True, verbose_name="Seisu võti"
    )
    next_step = models.CharField(max_length=300, blank=True, verbose_name="Järgmiseks")
    is_open = models.BooleanField(db_index=True, verbose_name="Avatud")
    warning_codes = models.JSONField(default=list, blank=True, verbose_name="Hoiatuskoodid")
    source_row = models.PositiveIntegerField(verbose_name="Lähterida")
    refreshed_at = models.DateTimeField(null=True, blank=True, verbose_name="Värskendatud")
    # Schema 1.2. Counts of members, never their identities: the workbook
    # carries only how many answered and how many were asked, and there is no
    # field here capable of holding who they were.
    #
    # `null` is the absence of a count, which an older workbook and an untracked
    # row both produce. It is not `0`: a topic nobody answered and a topic
    # nobody was asked about are different facts, and only the first is a zero.
    feedback_member_count = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Tagasisidet andnud liikmeid"
    )
    feedback_requested_member_count = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Liikmeid, kellelt otse küsiti"
    )

    class Meta:
        ordering = ("-received_date", "topic", "record_id")
        verbose_name = "Õigusloome kirje"
        verbose_name_plural = "Õigusloome kirjed"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "record_id"],
                name="legalworkitem_unique_record_per_snapshot",
            ),
            # `source_row` repeats across years, because it is the row number
            # inside its own year sheet. The year plus the row is what is
            # actually unique in the workbook.
            models.UniqueConstraint(
                fields=["snapshot", "source_year", "source_row"],
                name="legalworkitem_unique_source_row_per_snapshot",
            ),
            models.CheckConstraint(
                condition=Q(source_year__gte=1),
                name="legalworkitem_source_year_positive",
            ),
            models.CheckConstraint(
                condition=Q(source_nr__isnull=True) | Q(source_nr__gte=0),
                name="legalworkitem_source_nr_non_negative",
            ),
            models.CheckConstraint(
                condition=~Q(topic=""),
                name="legalworkitem_topic_required",
            ),
            # A record only claims to have been sent when it carries the date
            # that proves it, and nothing else may pretend to have one.
            models.CheckConstraint(
                condition=(
                    Q(sent_status=SentStatus.SENT, sent_date__isnull=False)
                    | (~Q(sent_status=SentStatus.SENT) & Q(sent_date__isnull=True))
                ),
                name="legalworkitem_sent_date_matches_status",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "is_open"]),
            models.Index(fields=["snapshot", "-sent_date"]),
            models.Index(fields=["snapshot", "-received_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.record_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("An imported legal-work row cannot be changed.")
        return super().save(*args, **kwargs)


class LegalWorkFeedState(models.Model):
    """What the last synchronisation attempt found.

    Deliberately holds no token, no client secret and no signed download URL:
    only non-secret content metadata that lets the next run decide whether it
    needs to download anything at all.
    """

    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="legal_work_feed_state",
        verbose_name="Andmeallikas",
    )
    last_checked_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Viimati kontrollitud"
    )
    last_successful_sync_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Viimane edukas sünkroonimine",
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
        help_text="Puhastatud ja lühendatud. Ei sisalda tokeneid ega failisisu.",
    )
    remote_modified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Kaugfail muudetud",
    )
    remote_etag = models.CharField(max_length=200, blank=True, verbose_name="Kaugfaili etag")
    remote_size_bytes = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        verbose_name="Kaugfaili suurus",
    )
    current_snapshot = models.ForeignKey(
        LegalWorkSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Kehtiv hetkeseis",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        verbose_name = "Õigusloome andmevoo olek"
        verbose_name_plural = "Õigusloome andmevoo olekud"

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"


# --------------------------------------------------------------------------
# The public Koda.ee "Hetkel käsil" catalogue.
#
# A separate source with a separate schedule, published exactly like the other
# public feeds: one complete immutable snapshot per changed collection, one
# current snapshot, a failure keeping the previous one. Nothing here is a
# dashboard metric and nothing here has a viewer page.
#
# What is *not* modelled is as deliberate as what is. There is no field for raw
# HTML, no attachment, no PDF, no opinion document and no archive entry: this
# phase collects the current listing and the detail pages it links to, and a
# column that cannot exist cannot be filled in by a later shortcut.
# --------------------------------------------------------------------------


class CurrentTopicSnapshot(models.Model):
    """One complete collection of the public current-topic listing."""

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="current_topic_snapshots",
        verbose_name="Andmeallikas",
    )
    artifact = models.ForeignKey(
        "sources.SourceArtifact",
        on_delete=models.PROTECT,
        related_name="current_topic_snapshots",
        verbose_name="Algfail",
    )
    import_run = models.OneToOneField(
        "sources.ImportRun",
        on_delete=models.PROTECT,
        related_name="current_topic_snapshot",
        verbose_name="Impordikäivitus",
    )
    observed_at = models.DateTimeField(verbose_name="Kogutud")
    item_count = models.PositiveIntegerField(default=0, verbose_name="Teemasid")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-observed_at", "-id")
        verbose_name = "Koda.ee hetkel käsil hetkeseis"
        verbose_name_plural = "Koda.ee hetkel käsil hetkeseisud"
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="currenttopic_one_current_snapshot_per_source",
            ),
        ]

    def __str__(self) -> str:
        return f"Hetkel käsil {self.observed_at:%d.%m.%Y} ({self.item_count})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise SnapshotImmutable(
                    "A collected current-topic snapshot may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class CurrentTopicItem(models.Model):
    """One `Hetkel käsil` entry, reduced to normalised plain text.

    `published_date` comes from the **detail** page, which prints a full
    `dd.mm.yyyy`. The listing card carries only a day and an abbreviated month
    with no year at all, so reading the date there would mean inferring a year
    on every row; the listing supplies ordering and nothing else.
    """

    snapshot = models.ForeignKey(
        CurrentTopicSnapshot,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Hetkeseis",
    )
    content_key = models.CharField(max_length=64, verbose_name="Sisu võti")
    canonical_url = models.URLField(max_length=MAX_CANONICAL_URL_LENGTH, verbose_name="Aadress")
    title = models.CharField(max_length=MAX_TOPIC_TITLE_LENGTH, verbose_name="Pealkiri")
    listing_summary = models.TextField(blank=True, verbose_name="Loendi kokkuvõte")
    body_text = models.TextField(blank=True, verbose_name="Lehe tekst")
    published_date = models.DateField(null=True, blank=True, verbose_name="Avaldatud")
    # Absent whenever the page does not state one unambiguously. A missing
    # deadline is a valid page, not a rejected one.
    feedback_deadline = models.DateField(null=True, blank=True, verbose_name="Tagasiside tähtaeg")
    named_organization = models.CharField(
        max_length=MAX_ORGANIZATION_LENGTH,
        blank=True,
        verbose_name="Nimetatud asutus",
    )
    source_order = models.PositiveSmallIntegerField(verbose_name="Järjekord")

    class Meta:
        ordering = ("snapshot", "source_order")
        verbose_name = "Koda.ee hetkel käsil teema"
        verbose_name_plural = "Koda.ee hetkel käsil teemad"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "content_key"],
                name="currenttopicitem_unique_key_per_snapshot",
            ),
            models.UniqueConstraint(
                fields=["snapshot", "canonical_url"],
                name="currenttopicitem_unique_url_per_snapshot",
            ),
            models.UniqueConstraint(
                fields=["snapshot", "source_order"],
                name="currenttopicitem_unique_order_per_snapshot",
            ),
            models.CheckConstraint(
                condition=~Q(title=""),
                name="currenttopicitem_title_required",
            ),
            models.CheckConstraint(
                condition=~Q(canonical_url=""),
                name="currenttopicitem_url_required",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A collected current-topic row cannot be changed.")
        return super().save(*args, **kwargs)


class CurrentTopicFeedState(models.Model):
    """What the last current-topic collection found.

    No `etag` and no `last_modified`: the listing is server-rendered HTML with
    no useful validator, exactly as the events calendar already documents. The
    canonical checksum over the normalised fields is what decides whether
    anything changed, so storing validators that would always be empty would
    only suggest a conditional request that never happens.
    """

    source = models.OneToOneField(
        DataSource,
        on_delete=models.PROTECT,
        related_name="current_topic_feed_state",
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
        CurrentTopicSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Kehtiv hetkeseis",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Muudetud")

    class Meta:
        verbose_name = "Hetkel käsil andmevoo olek"
        verbose_name_plural = "Hetkel käsil andmevoo olekud"

    def __str__(self) -> str:
        return f"{self.source.slug}: {self.get_last_result_display()}"


# --------------------------------------------------------------------------
# Derived match results.
#
# These rows are **computed, not collected**: nothing was downloaded and no file
# exists, so there is no source, no artifact and no import run here. The
# identity of a match run is exactly the three things that determine its output
# — the legal snapshot read, the catalogue read, and the matcher version — and
# that triple is a unique constraint rather than a fabricated checksum.
#
# Nothing in this section is read by a viewer page in this phase.
# --------------------------------------------------------------------------


class MatchDecision(models.TextChoices):
    MATCHED = "matched", "Seotud"
    AMBIGUOUS = "ambiguous", "Ebaselge"
    UNMATCHED = "unmatched", "Sidumata"


class LegalCurrentTopicMatchSnapshot(models.Model):
    """One complete matcher run over one legal snapshot and one catalogue.

    Both inputs cascade: a derived result about a snapshot that no longer exists
    is not evidence of anything, so it goes with it rather than protecting the
    snapshot into immortality.
    """

    legal_snapshot = models.ForeignKey(
        LegalWorkSnapshot,
        on_delete=models.CASCADE,
        related_name="current_topic_match_snapshots",
        verbose_name="Õigusloome hetkeseis",
    )
    current_topic_snapshot = models.ForeignKey(
        CurrentTopicSnapshot,
        on_delete=models.CASCADE,
        related_name="match_snapshots",
        verbose_name="Hetkel käsil hetkeseis",
    )
    matcher_version = models.CharField(max_length=32, verbose_name="Sobitaja versioon")
    generated_at = models.DateTimeField(auto_now_add=True, verbose_name="Arvutatud")
    legal_item_count = models.PositiveIntegerField(default=0, verbose_name="Avatud kirjeid")
    matched_count = models.PositiveIntegerField(default=0, verbose_name="Seotud")
    ambiguous_count = models.PositiveIntegerField(default=0, verbose_name="Ebaselgeid")
    unmatched_count = models.PositiveIntegerField(default=0, verbose_name="Sidumata")
    is_current = models.BooleanField(default=False, verbose_name="Kehtiv")

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-generated_at", "-id")
        verbose_name = "Õigusloome sobitamise hetkeseis"
        verbose_name_plural = "Õigusloome sobitamise hetkeseisud"
        constraints = [
            # The whole input identity. Re-running the same matcher over the
            # same two snapshots cannot produce a second row, which is what
            # makes "unchanged" a single `exists()` rather than a checksum.
            models.UniqueConstraint(
                fields=["legal_snapshot", "current_topic_snapshot", "matcher_version"],
                name="legalmatchsnapshot_unique_inputs",
            ),
            # Exactly one current match snapshot exists at a time, globally.
            # Unique over a constant expression restricted to the current rows:
            # a second one cannot be written even if a service forgets to retire
            # the first.
            models.UniqueConstraint(
                models.F("is_current"),
                condition=Q(is_current=True),
                name="legalmatchsnapshot_one_current",
            ),
            models.CheckConstraint(
                condition=Q(matched_count__lte=F("legal_item_count")),
                name="legalmatchsnapshot_matched_within_total",
            ),
            models.CheckConstraint(
                condition=Q(ambiguous_count__lte=F("legal_item_count")),
                name="legalmatchsnapshot_ambiguous_within_total",
            ),
            models.CheckConstraint(
                condition=Q(unmatched_count__lte=F("legal_item_count")),
                name="legalmatchsnapshot_unmatched_within_total",
            ),
        ]

    def __str__(self) -> str:
        return f"Sobitamine {self.matcher_version} ({self.legal_item_count})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise SnapshotImmutable(
                    "A generated match snapshot may only change its is_current flag."
                )
        return super().save(*args, **kwargs)


class LegalCurrentTopicMatch(models.Model):
    """What the matcher decided about one open legal record.

    `best_candidate` is retained for `ambiguous` and `unmatched` rows too, and
    that is deliberate: the candidate a run *rejected*, with the score and the
    evidence that rejected it, is what threshold calibration is made of. Only a
    `matched` decision requires one, and only a `matched` decision is ever
    offered to a viewer.

    Both relations use `related_name="+"`. A reverse accessor from
    `LegalWorkItem` would be the first step towards a selector decorating
    workbook rows with match URLs, and this phase must not reach the viewer.
    """

    snapshot = models.ForeignKey(
        LegalCurrentTopicMatchSnapshot,
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
        CurrentTopicItem,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Parim kandidaat",
    )
    decision = models.CharField(
        max_length=16,
        choices=MatchDecision,
        db_index=True,
        verbose_name="Otsus",
    )
    # Documented scale: 0.00–100.00. Stored as a decimal rather than a float so
    # a threshold comparison means the same thing in PostgreSQL, in Python and
    # in a test.
    score = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="Skoor")
    runner_up_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Teise koha skoor",
        help_text="0 kui teist kandidaati ei olnud.",
    )
    score_margin = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="Vahe")
    candidate_count = models.PositiveSmallIntegerField(default=0, verbose_name="Kandidaate")
    evidence_codes = models.JSONField(default=list, blank=True, verbose_name="Tõendikoodid")

    class Meta:
        ordering = ("-score", "legal_item_id")
        verbose_name = "Õigusloome sobitamise tulemus"
        verbose_name_plural = "Õigusloome sobitamise tulemused"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "legal_item"],
                name="legalmatch_one_decision_per_item",
            ),
            models.CheckConstraint(
                condition=Q(score__gte=0, score__lte=100),
                name="legalmatch_score_within_scale",
            ),
            models.CheckConstraint(
                condition=Q(runner_up_score__gte=0, runner_up_score__lte=100),
                name="legalmatch_runner_up_within_scale",
            ),
            models.CheckConstraint(
                condition=Q(score__gte=F("runner_up_score")),
                name="legalmatch_runner_up_not_above_score",
            ),
            models.CheckConstraint(
                condition=Q(score_margin=F("score") - F("runner_up_score")),
                name="legalmatch_margin_is_score_difference",
            ),
            # A proposed link must name what it proposes.
            models.CheckConstraint(
                condition=~Q(decision=MatchDecision.MATCHED) | Q(best_candidate__isnull=False),
                name="legalmatch_matched_requires_candidate",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "decision"]),
        ]

    def __str__(self) -> str:
        return f"{self.legal_item_id}: {self.get_decision_display()}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise SnapshotImmutable("A generated match row cannot be changed.")
        return super().save(*args, **kwargs)


# The archive catalogue and its matcher results. Imported here, at the foot, so
# Django discovers them as ordinary `legal_work` models while the definitions
# stay in their own module. The import is last because those models refer back
# to the ones above.
from .archive_models import (  # noqa: E402,F401  (placement is deliberate)
    ArchivedTopicFeedState,
    ArchivedTopicItem,
    ArchivedTopicSnapshot,
    DetailStatus,
    LegalArchivedTopicMatch,
    LegalArchivedTopicMatchSnapshot,
)

# The private catalogue of the Chamber's own opinion documents, imported here
# for the same reason and with the same placement rule.
from .opinion_models import (  # noqa: E402,F401  (placement is deliberate)
    CatalogueBuildState,
    CatalogueResult,
    OpinionCatalogueEntry,
    OpinionCatalogueFeedState,
    OpinionCatalogueSnapshot,
    OpinionDocumentBlob,
    OpinionDocumentExtraction,
    SourceProvider,
)
