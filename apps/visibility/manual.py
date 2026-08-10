"""Entering audience figures by hand, with the same rules as an import.

A staff user types what a platform's own statistics screen said, sees exactly
what will be saved and what it changes, and confirms. There is no API call, no
credential, no scraper and no schedule. When a collector eventually exists it
can replace this module without touching a single historical row, because both
writers already publish through the same service.

What makes a typed number a first-class record rather than a note:

- the submission becomes **canonical JSON** — sorted keys, fixed separators, the
  date as ISO text, values as plain integers — and is hashed. That hash is the
  batch's content identity and is *unique in the database*, so an accidental
  double submit is recognised as the same submission instead of published twice;
- each contributing source gets a metadata-only artifact carrying its own share
  of that submission under a fixed non-secret label, and an ordinary
  `ImportRun` records the attempt, exactly as a collector's would;
- publication goes through `publishing.publish_observation`, so precedence,
  supersession and the audit trail behave identically for typed and collected
  data.

## Why the artifact payload names its batch

An artifact's identity is `(source, sha256)`, and an import key is derived from
that digest. Two submissions can legitimately carry an identical Smaily reading
while differing elsewhere — correcting only the Facebook figure is the obvious
case — and hashing the source's values alone would make the second one collide
with the first. The per-source payload therefore names the batch it belongs to,
so its identity reads "this source's contribution to *this* submission".

## What a correction is

A correction is never an edit. Re-entering a metric for a date that already has
a value creates a **new** observation that names the old one through
`supersedes`; the old row keeps its number and stays in history, and only its
`is_current_for_date` flag moves. A value entered for a *later* date is not a
correction at all — it is the next point on the trend, and both remain.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.sources.models import SourceArtifact
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    register_external_reference,
    start_import_run,
)

from .bootstrap import ensure_manual_visibility_sources
from .models import (
    MAX_NOTE_LENGTH,
    CollectionMethod,
    VisibilityEntryBatch,
    VisibilityObservation,
)
from .publishing import lock_current, publish_observation
from .registry import (
    ARTIFACT_REFERENCE_PREFIXES,
    METRICS,
    NEWSLETTER_METRICS,
    SUBMISSION_SOURCE_SLUGS,
    VisibilityMetricSpec,
    manual_metrics,
    metrics_for_source,
    spec_for,
)

IMPORTER_NAME = "visibility_manual_entry"
MANUAL_SCHEMA_VERSION = "1.0"
ARTIFACT_MIME_TYPE = "application/json"

# --------------------------------------------------------------------------
# The data-entry check
#
# A follower count can genuinely jump — a viral post, a campaign, a platform
# recount — so a large movement is never refused. It is surfaced so the person
# who typed it looks once more at what they typed, which is the mistake this
# actually catches: a transposed digit or a figure read off the wrong channel.
#
# **Both** thresholds must be exceeded. The proportional rule alone would flag
# 4 → 6 subscribers; the absolute rule alone would flag an ordinary 200-follower
# month on a 12 000-follower page. Neither is a data-entry error.
# --------------------------------------------------------------------------
SUBSTANTIAL_CHANGE_RATIO = Decimal("0.25")
SUBSTANTIAL_CHANGE_ABSOLUTE = 100


class VisibilityEntryError(RuntimeError):
    """The submission could not be published. Nothing was written."""


@dataclass(frozen=True)
class VisibilitySubmission:
    """One filled-in form, before anything has been written.

    `values` holds **only the metrics the user actually supplied**. A metric
    absent from the mapping was left blank, which is not the same as a `0`
    entered on purpose, and the two must not be collapsed anywhere.
    """

    observation_date: date
    values: dict[str, int] = field(default_factory=dict)
    note: str = ""

    @property
    def supplied_metrics(self) -> tuple[str, ...]:
        return tuple(sorted(self.values))


# --------------------------------------------------------------------------
# Canonical form and content identity
# --------------------------------------------------------------------------


def canonical_payload(submission: VisibilitySubmission) -> dict:
    """A deterministic, order-independent description of what was submitted."""
    return {
        "schema": MANUAL_SCHEMA_VERSION,
        "observation_date": submission.observation_date.isoformat(),
        "note": submission.note.strip(),
        # String keys and sorted output, so the same figures always hash the
        # same way whatever order the form fields arrived in.
        "metrics": {key: int(submission.values[key]) for key in sorted(submission.values)},
    }


def _dump(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_json(submission: VisibilitySubmission) -> str:
    return _dump(canonical_payload(submission))


def canonical_sha256(submission: VisibilitySubmission) -> str:
    return hashlib.sha256(canonical_json(submission).encode("utf-8")).hexdigest()


def source_payload(
    submission: VisibilitySubmission, *, source_slug: str, batch_hash: str
) -> dict | None:
    """What one source contributed to one submission, or `None` if nothing."""
    metrics = {
        spec.key: int(submission.values[spec.key])
        for spec in metrics_for_source(source_slug)
        if spec.key in submission.values
    }
    if not metrics:
        return None
    return {
        "schema": MANUAL_SCHEMA_VERSION,
        "batch": batch_hash,
        "source": source_slug,
        "observation_date": submission.observation_date.isoformat(),
        "note": submission.note.strip(),
        "metrics": {key: metrics[key] for key in sorted(metrics)},
    }


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricChange:
    """One submitted metric, against the value it is being compared with.

    The baseline is named rather than assumed. A correction is compared with the
    value it replaces; a new date is compared with the most recent earlier
    reading. Showing a difference without saying what it is a difference *from*
    is the mistake this dataclass exists to prevent.
    """

    spec: VisibilityMetricSpec
    value: int
    baseline: VisibilityObservation | None
    replaces: VisibilityObservation | None

    @property
    def baseline_value(self) -> int | None:
        return self.baseline.value if self.baseline is not None else None

    @property
    def baseline_date(self) -> date | None:
        return self.baseline.observation_date if self.baseline is not None else None

    @property
    def is_correction(self) -> bool:
        return self.replaces is not None

    @property
    def difference(self) -> int | None:
        if self.baseline is None:
            return None
        return self.value - self.baseline.value

    @property
    def difference_pct(self) -> Decimal | None:
        """`None` when there is no baseline, and when the baseline was zero.

        Growth from zero has no meaningful percentage, and printing one would be
        a number no source stated.
        """
        if self.baseline is None or self.baseline.value == 0:
            return None
        return (Decimal(self.difference) / Decimal(self.baseline.value) * 100).quantize(
            Decimal("0.1")
        )

    @property
    def is_decrease(self) -> bool:
        return self.difference is not None and self.difference < 0

    @property
    def is_large_movement(self) -> bool:
        """The documented data-entry check. Both thresholds must be exceeded."""
        if self.baseline is None or self.baseline.value == 0:
            return False
        absolute = abs(self.difference)
        if absolute <= SUBSTANTIAL_CHANGE_ABSOLUTE:
            return False
        ratio = Decimal(absolute) / Decimal(self.baseline.value)
        return ratio > SUBSTANTIAL_CHANGE_RATIO


@dataclass(frozen=True)
class NewsletterEntries:
    """The newsletter figures this submission will publish, list by list.

    There is deliberately no arithmetic here. The three lists are separate
    audiences with an unknown overlap, so there is nothing to derive: the
    preview shows what was typed, under the name of the list it belongs to.
    """

    rows: tuple[tuple[str, int | None], ...] = ()

    @property
    def has_any_value(self) -> bool:
        return any(value is not None for _label, value in self.rows)


@dataclass(frozen=True)
class SubmissionPreview:
    """Everything shown before the user confirms. Saves nothing."""

    observation_date: date
    changes: tuple[MetricChange, ...]
    newsletter: NewsletterEntries
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    content_hash: str
    already_published: VisibilityEntryBatch | None

    @property
    def can_publish(self) -> bool:
        return not self.errors

    @property
    def corrections(self) -> tuple[MetricChange, ...]:
        return tuple(change for change in self.changes if change.is_correction)


def _latest_current_before(metric: str, observation_date: date) -> VisibilityObservation | None:
    """The newest current observation strictly earlier than this date."""
    return (
        VisibilityObservation.objects.filter(
            metric=metric,
            is_current_for_date=True,
            observation_date__lt=observation_date,
        )
        .order_by("-observation_date", "-id")
        .select_related("source")
        .first()
    )


def _latest_current_on_or_before(
    metric: str, observation_date: date
) -> VisibilityObservation | None:
    return (
        VisibilityObservation.objects.filter(
            metric=metric,
            is_current_for_date=True,
            observation_date__lte=observation_date,
        )
        .order_by("-observation_date", "-id")
        .select_related("source")
        .first()
    )


def _effective_value(submission: VisibilitySubmission, metric: str) -> int | None:
    """What this metric will be worth once the submission is published.

    A value in the form wins; otherwise the newest stored reading on or before
    the observation date stands. `None` means nobody has ever entered it, which
    is not zero.
    """
    if metric in submission.values:
        return int(submission.values[metric])
    stored = _latest_current_on_or_before(metric, submission.observation_date)
    return stored.value if stored is not None else None


def build_preview(
    submission: VisibilitySubmission, *, today: date | None = None
) -> SubmissionPreview:
    """Compute everything the confirmation step shows. Writes nothing.

    Hard errors block publication. Warnings do not: an unusual month is still a
    real month, and refusing it would push the user towards typing a number they
    did not read.

    Validation lives here rather than in the form so that the browser and a
    direct POST to the confirmation step apply exactly the same rules.
    """
    today = today or timezone.localdate()
    errors: list[str] = []
    warnings: list[str] = []

    if not submission.values:
        errors.append("Vähemalt üks näitaja tuleb sisestada.")
    if submission.observation_date > today:
        errors.append("Vaatluse kuupäev ei saa olla tulevikus.")
    if len(submission.note.strip()) > MAX_NOTE_LENGTH:
        errors.append(f"Märkus võib olla kuni {MAX_NOTE_LENGTH} tähemärki.")

    for metric, value in submission.values.items():
        if spec_for(metric) is None:
            errors.append(f"Tundmatu näitaja: {metric}.")
        elif int(value) < 0:
            errors.append("Väärtus ei saa olla negatiivne.")

    changes: list[MetricChange] = []
    for spec in METRICS:
        if spec.key not in submission.values:
            continue
        replaces = _latest_current_on_or_before(spec.key, submission.observation_date)
        if replaces is not None and replaces.observation_date != submission.observation_date:
            replaces = None
        baseline = replaces or _latest_current_before(spec.key, submission.observation_date)
        changes.append(
            MetricChange(
                spec=spec,
                value=int(submission.values[spec.key]),
                baseline=baseline,
                replaces=replaces,
            )
        )

    for change in changes:
        if change.is_correction:
            warnings.append(
                f"{change.spec.label}: sama kuupäeva senine väärtus "
                f"{change.replaces.value} asendatakse."
            )
        if change.is_large_movement:
            warnings.append(
                f"{change.spec.label}: muutus võrreldes seisuga "
                f"{change.baseline_date:%d.%m.%Y} on ebatavaliselt suur. "
                "See on sisestuskontroll, mitte väide, et number on vale."
            )
        elif change.is_decrease:
            warnings.append(
                f"{change.spec.label}: väärtus on väiksem kui seisuga "
                f"{change.baseline_date:%d.%m.%Y}."
            )

    newsletter = NewsletterEntries(
        rows=tuple(
            (spec_for(metric).label, _effective_value(submission, metric))
            for metric in manual_metrics(NEWSLETTER_METRICS)
        )
    )

    content_hash = canonical_sha256(submission)
    return SubmissionPreview(
        observation_date=submission.observation_date,
        changes=tuple(changes),
        newsletter=newsletter,
        warnings=tuple(warnings),
        errors=tuple(errors),
        content_hash=content_hash,
        already_published=VisibilityEntryBatch.objects.filter(content_hash=content_hash).first(),
    )


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------


def _publish_source(
    submission: VisibilitySubmission,
    *,
    batch: VisibilityEntryBatch,
    source,
    actor,
) -> int:
    """Write every metric this source contributed. Returns how many."""
    payload = source_payload(submission, source_slug=source.slug, batch_hash=batch.content_hash)
    if payload is None:
        return 0

    body = _dump(payload)
    content_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()

    # Registered through the source service like every other artifact, so the
    # immutability rules and the audit entry are not optional here. There is no
    # file: the content identity *is* the canonical JSON, which the observations
    # already hold in structured form.
    artifact = SourceArtifact.objects.filter(source=source, sha256=content_sha256).first()
    if artifact is None:
        artifact = register_external_reference(
            source=source,
            external_reference=(
                f"{ARTIFACT_REFERENCE_PREFIXES[source.slug]}:{batch.correlation_id}"
            ),
            original_name="visibility-entry.json",
            mime_type=ARTIFACT_MIME_TYPE,
            sha256=content_sha256,
            size_bytes=len(body.encode("utf-8")),
            uploaded_by=actor,
            actor=actor,
            correlation_id=batch.correlation_id,
        )

    run = build_import_run(
        artifact=artifact,
        importer_name=IMPORTER_NAME,
        schema_version=MANUAL_SCHEMA_VERSION,
        dry_run=False,
        initiated_by=actor,
        actor=actor,
        correlation_id=batch.correlation_id,
    )
    start_import_run(run)

    written = 0
    for key in sorted(payload["metrics"]):
        # Taken under a row lock before the replacement exists, because
        # `supersedes` is immutable and has to be right on the first save.
        previous = lock_current(key, submission.observation_date)
        observation = VisibilityObservation(
            batch=batch,
            source=source,
            artifact=artifact,
            import_run=run,
            metric=key,
            value=payload["metrics"][key],
            collection_method=CollectionMethod.MANUAL,
            observation_date=submission.observation_date,
            supersedes=previous,
            is_current_for_date=False,
            created_by=actor,
        )
        # `full_clean` is what enforces metric-to-source agreement, which no
        # database constraint can express. One bad metric aborts the whole batch.
        observation.full_clean(exclude=["published_at"])
        observation.save()
        publish_observation(
            observation,
            supersedes=previous,
            actor=actor,
            correlation_id=batch.correlation_id,
        )
        written += 1

    complete_import_run(run, rows_added=written, actor=actor)
    return written


def publish_submission(
    submission: VisibilitySubmission,
    *,
    actor=None,
    correlation_id: uuid.UUID | None = None,
) -> VisibilityEntryBatch:
    """Validate once more, then write the whole submission atomically.

    The revalidation is not defensive noise: the preview ran against the state
    the user saw, and something may have changed since. Publishing on stale
    validation is exactly how two people overwrite each other.

    Everything after the batch is created happens in one transaction. There is
    no state in which the newsletter figures are published and the social ones
    are not.
    """
    preview = build_preview(submission)
    if preview.errors:
        raise VisibilityEntryError(preview.errors[0])
    if preview.already_published is not None:
        return preview.already_published

    with transaction.atomic():
        try:
            # A savepoint, so losing the race on `content_hash` leaves the outer
            # transaction usable instead of poisoning it.
            with transaction.atomic():
                batch = VisibilityEntryBatch.objects.create(
                    observation_date=submission.observation_date,
                    note=submission.note.strip()[:MAX_NOTE_LENGTH],
                    content_hash=preview.content_hash,
                    correlation_id=correlation_id or uuid.uuid4(),
                    created_by=actor,
                )
        except IntegrityError:
            return VisibilityEntryBatch.objects.get(content_hash=preview.content_hash)

        sources = ensure_manual_visibility_sources(actor=actor, correlation_id=batch.correlation_id)
        written = 0
        for slug in SUBMISSION_SOURCE_SLUGS:
            written += _publish_source(submission, batch=batch, source=sources[slug], actor=actor)

        record_event(
            action=AuditAction.VISIBILITY_MANUAL_BATCH_PUBLISHED,
            obj=batch,
            actor=actor,
            correlation_id=batch.correlation_id,
            change_summary={
                # Aggregate business figures and identifiers only. The note the
                # user typed is stored on the batch and deliberately not copied
                # here, and no form payload is echoed.
                "batch_id": batch.pk,
                "observation_date": batch.observation_date.isoformat(),
                "content_sha256": batch.content_hash,
                "observation_count": written,
                "metrics": {
                    key: int(submission.values[key]) for key in submission.supplied_metrics
                },
                "sources": sorted(
                    {spec_for(key).source_slug for key in submission.supplied_metrics}
                ),
                "corrected_metrics": sorted(change.spec.key for change in preview.corrections),
            },
        )
        return batch
