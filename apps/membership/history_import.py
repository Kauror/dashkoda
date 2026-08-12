"""The one-time import of the approved historical membership package.

Deterministic, transactional and idempotent, in that order of importance:

- **deterministic** — the same package always produces the same rows, the same
  quality statuses and the same preferred observations. Precedence is computed
  in memory from values the package supplies, never from database ordering;
- **transactional** — every table is written inside one atomic block. A failure
  at row 2 900 of 2 960 leaves the database exactly as it was, and the previous
  data stays correct;
- **idempotent** — the import key is the package digest plus this importer's
  schema version, so running the identical package again reports "unchanged"
  and writes nothing. That is what makes the deployment sequence safe to repeat.

The package file itself is never stored. The artifact registered here is
metadata-only, carrying the server-computed checksum and size — which is what
makes it importable — under a fixed non-secret reference. This follows the
project's existing rule that an artifact is importable when it has a trusted
checksum, not when it still has a file, and it keeps an archive out of the
upload allowlist.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.db import transaction

from apps.audit.services import record_event
from apps.membership.audit_actions import MembershipAudit
from apps.sources.models import ImportRun, ImportStatus, SourceArtifact
from apps.sources.services import (
    build_import_run,
    calculate_import_key,
    complete_import_run,
    fail_publication,
    register_external_reference,
    start_import_run,
)

from .bootstrap import ensure_internal_membership_source
from .models import (
    InternalMembershipObservation,
    InternalSourceKind,
    IssueSeverity,
    MembershipDataIssue,
    MembershipDecisionBatch,
    MembershipDecisionBatchReason,
    MembershipDecisionBatchSizeMovement,
    MembershipHistoricalSourceDocument,
    MembershipMetricConflict,
    MembershipMonthlyNewMemberValue,
    MembershipNewMemberPeriod,
    MembershipNewMemberSizeDistribution,
    MembershipRemovalReason,
    MembershipSizeMovement,
    QualityStatus,
)
from .models.internal import MAX_MESSAGE_LENGTH, MAX_RAW_VALUE_LENGTH
from .package import (
    PACKAGE_SCHEMA_VERSION,
    PackageContractError,
    PackageLimits,
    ParsedPackage,
    read_package,
)
from .quality import MetricFacts, PreferenceCandidate, assess, choose_preferred

IMPORTER_NAME = "membership_history_csv"

BATCH_SIZE = 500

# A fixed, non-secret provenance label. It names what the content was, carries
# no credential and no path, and satisfies the artifact model's rule that an
# external reference contains neither `@` nor `?`.
ARTIFACT_REFERENCE_PREFIX = "package:membership-history"
ARTIFACT_NAME = "dashkoda-membership-history-import-package.zip"
ARTIFACT_MIME_TYPE = "application/zip"


class MembershipHistoryImportError(RuntimeError):
    """The import could not be completed. Nothing was changed."""


@dataclass(frozen=True)
class HistoryImportResult:
    """Aggregate outcome. Deliberately carries no row content."""

    import_run: ImportRun | None
    dry_run: bool
    unchanged: bool
    package_sha256: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def rows_added(self) -> int:
        return sum(self.counts.values())

    def as_json(self) -> dict:
        """What `--json` prints. Counts and identifiers only, never prose."""
        return {
            "importer": IMPORTER_NAME,
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "package_sha256": self.package_sha256,
            "dry_run": self.dry_run,
            "unchanged": self.unchanged,
            "import_run_id": self.import_run.pk if self.import_run else None,
            "correlation_id": (str(self.import_run.correlation_id) if self.import_run else None),
            "rows_added": 0 if (self.dry_run or self.unchanged) else self.rows_added,
            "counts": dict(self.counts),
        }


def _limits() -> PackageLimits:
    return PackageLimits(
        max_package_bytes=settings.MEMBERSHIP_HISTORY_MAX_PACKAGE_BYTES,
        max_uncompressed_bytes=settings.MEMBERSHIP_HISTORY_MAX_UNCOMPRESSED_BYTES,
        max_member_bytes=settings.MEMBERSHIP_HISTORY_MAX_MEMBER_BYTES,
        max_members=settings.MEMBERSHIP_HISTORY_MAX_MEMBERS,
    )


def _bounded(value: str, limit: int) -> str:
    """Diagnostics are bounded and single-line before they reach the database."""
    return value.replace("\n", " ").replace("\r", " ").strip()[:limit]


def _ensure_artifact(source, parsed: ParsedPackage, *, actor, correlation_id) -> SourceArtifact:
    """Register the package's content identity once, and reuse it thereafter.

    The same bytes under the same source are one artifact; the immutable-
    artifact rules forbid registering a duplicate, and re-registering would also
    change the import key and break idempotency.
    """
    existing = SourceArtifact.objects.filter(source=source, sha256=parsed.package_sha256).first()
    if existing is not None:
        return existing
    return register_external_reference(
        source=source,
        external_reference=f"{ARTIFACT_REFERENCE_PREFIX}:{parsed.package_sha256}",
        original_name=ARTIFACT_NAME,
        mime_type=ARTIFACT_MIME_TYPE,
        sha256=parsed.package_sha256,
        size_bytes=parsed.package_size_bytes,
        uploaded_by=actor,
        actor=actor,
        correlation_id=correlation_id,
    )


def _conflicted_metrics_by_date(parsed: ParsedPackage) -> dict[object, set[str]]:
    conflicted: dict[object, set[str]] = {}
    for conflict in parsed.conflicts:
        conflicted.setdefault(conflict.observation_date, set()).add(conflict.metric)
    return conflicted


def _facts(snapshot) -> MetricFacts:
    return MetricFacts(
        total_members=snapshot.total_members,
        paid_members=snapshot.paid_members,
        membership_fees_received_eur=snapshot.membership_fees_received_eur,
        membership_fee_budget_eur=snapshot.membership_fee_budget_eur,
        membership_fee_collection_pct_reported=snapshot.membership_fee_collection_pct,
        new_members_ytd=snapshot.new_members_ytd,
        suspended_members=snapshot.suspended_members,
        removed_members_ytd=snapshot.removed_members_ytd,
    )


def _write_source_documents(
    parsed: ParsedPackage, *, source, run: ImportRun
) -> dict[str, MembershipHistoricalSourceDocument]:
    documents = [
        MembershipHistoricalSourceDocument(
            source=source,
            import_run=run,
            external_source_id=row.source_id,
            relative_path=row.relative_path[:400],
            filename=row.filename[:255],
            extension=row.extension[:16],
            file_sha256=row.file_sha256[:64],
            file_size_bytes=row.file_size_bytes,
            filesystem_modified_at=row.filesystem_modified_at,
            year_folder=row.year_folder[:120],
            month_folder=row.month_folder[:120],
            candidate_reason=row.candidate_reason[:200],
            extraction_status=row.extraction_status[:32],
            observation_date=row.observation_date,
            observation_date_precision=row.observation_date_precision,
            date_source=row.date_source[:64],
            date_confidence=row.date_confidence[:16],
            document_title=row.document_title[:400],
            document_year_claim=row.document_year_claim,
            warning_codes=row.warning_codes,
            notes=row.notes[:300],
        )
        for row in parsed.source_documents
    ]
    MembershipHistoricalSourceDocument.objects.bulk_create(documents, batch_size=BATCH_SIZE)
    return {document.external_source_id: document for document in documents}


def _write_observations(
    parsed: ParsedPackage,
    *,
    source,
    artifact: SourceArtifact,
    run: ImportRun,
    documents: dict[str, MembershipHistoricalSourceDocument],
) -> dict[str, InternalMembershipObservation]:
    """Create every piece of evidence, then mark one preferred row per date.

    Precedence is decided here in memory, from the package's own values, so the
    outcome does not depend on insertion order or on which rows the database
    happens to return first.
    """
    conflicted_by_date = _conflicted_metrics_by_date(parsed)

    observations: list[InternalMembershipObservation] = []
    assessments: dict[str, object] = {}
    for row in parsed.snapshots:
        assessment = assess(
            _facts(row),
            conflicted_metrics=conflicted_by_date.get(row.observation_date, set()),
            extra_warning_codes=tuple(row.warning_codes),
        )
        assessments[row.snapshot_id] = assessment
        observations.append(
            InternalMembershipObservation(
                source=source,
                artifact=artifact,
                import_run=run,
                external_snapshot_id=row.snapshot_id,
                observation_date=row.observation_date,
                observation_date_precision=row.observation_date_precision,
                source_kind=row.source_kind,
                source_column_label=row.source_column_label[:120],
                reported_year=row.reported_year,
                total_members=row.total_members,
                paid_members=row.paid_members,
                membership_fees_received_eur=row.membership_fees_received_eur,
                membership_fee_budget_eur=row.membership_fee_budget_eur,
                membership_fee_collection_pct_reported=row.membership_fee_collection_pct,
                new_members_ytd=row.new_members_ytd,
                suspended_members=row.suspended_members,
                removed_members_ytd=row.removed_members_ytd,
                extraction_confidence=row.extraction_confidence,
                quality_status=assessment.quality_status,
                is_preferred_for_date=False,
                source_document=documents.get(row.source_id),
                warning_codes=list(assessment.warning_codes),
                published_at=run.started_at,
            )
        )

    # One preferred row per date, chosen by the same precedence rules the manual
    # workflow uses. The snapshot identifier is the tie-breaker, so the choice
    # is a property of the package rather than of this particular run.
    by_date: dict[object, list[InternalMembershipObservation]] = {}
    for observation in observations:
        by_date.setdefault(observation.observation_date, []).append(observation)

    for candidates in by_date.values():
        indexed = {row.external_snapshot_id: row for row in candidates}
        winner = choose_preferred(
            [
                PreferenceCandidate(
                    key=row.external_snapshot_id,
                    source_kind=row.source_kind,
                    extraction_confidence=row.extraction_confidence,
                    quality_status=row.quality_status,
                    tie_breaker=row.external_snapshot_id,
                )
                for row in candidates
            ]
        )
        if winner is not None:
            indexed[winner.key].is_preferred_for_date = True

    InternalMembershipObservation.objects.bulk_create(observations, batch_size=BATCH_SIZE)
    return {observation.external_snapshot_id: observation for observation in observations}


def _direct_observations_by_source(
    parsed: ParsedPackage,
    observations: dict[str, InternalMembershipObservation],
) -> dict[str, InternalMembershipObservation]:
    """Map each source document to the observation its own figures produced.

    Size movements and removal reasons are reported once per document, so they
    belong to that document's direct observation — never to a comparison column,
    which restates a different year and has no distribution table of its own.
    """
    direct: dict[str, InternalMembershipObservation] = {}
    for row in parsed.snapshots:
        if row.source_kind != InternalSourceKind.MERGED_SAME_DOCUMENT:
            continue
        if row.source_id in direct:
            raise MembershipHistoryImportError(
                "Lähtedokumendil on mitu otsest vaatlust; pakett ei ole ootuspärane."
            )
        direct[row.source_id] = observations[row.snapshot_id]
    return direct


def _write_children(
    parsed: ParsedPackage,
    *,
    direct: dict[str, InternalMembershipObservation],
) -> tuple[int, int]:
    movements = []
    for row in parsed.movements:
        observation = direct.get(row.source_id)
        if observation is None or observation.observation_date != row.observation_date:
            raise MembershipHistoryImportError(
                "Suurusklassi liikumine ei vasta ühelegi otsesele vaatlusele."
            )
        movements.append(
            MembershipSizeMovement(
                observation=observation,
                direction=row.direction,
                size_band_key=row.size_band_key,
                size_band_label_raw=row.size_band_label_raw[:120],
                member_count=row.member_count,
                total_reported=row.total_reported,
                extraction_confidence=row.extraction_confidence,
                warning_codes=row.warning_codes,
            )
        )
    MembershipSizeMovement.objects.bulk_create(movements, batch_size=BATCH_SIZE)

    reasons = []
    for row in parsed.removal_reasons:
        observation = direct.get(row.source_id)
        if observation is None or observation.observation_date != row.observation_date:
            raise MembershipHistoryImportError(
                "Lahkumise põhjus ei vasta ühelegi otsesele vaatlusele."
            )
        reasons.append(
            MembershipRemovalReason(
                observation=observation,
                reason_key=row.reason_key,
                reason_label_raw=row.reason_label_raw[:300],
                member_count=row.member_count,
                removed_total_reported=row.removed_total_reported,
                extraction_confidence=row.extraction_confidence,
                warning_codes=row.warning_codes,
            )
        )
    MembershipRemovalReason.objects.bulk_create(reasons, batch_size=BATCH_SIZE)
    return len(movements), len(reasons)


def _write_monthly(
    parsed: ParsedPackage,
    *,
    source,
    run: ImportRun,
    documents: dict[str, MembershipHistoricalSourceDocument],
) -> dict[tuple[int, int], MembershipMonthlyNewMemberValue]:
    values = [
        MembershipMonthlyNewMemberValue(
            source=source,
            import_run=run,
            calendar_year=row.calendar_year,
            calendar_month=row.calendar_month,
            # A conflict keeps no value. The package already guarantees this and
            # a database constraint enforces it; charting zero here would be the
            # single most damaging thing this importer could do.
            new_members=row.new_members,
            value_status=row.value_status,
            source_count=row.source_count,
            source_ids=row.source_ids,
            earliest_source_observation_date=row.earliest_source_observation_date,
            latest_source_observation_date=row.latest_source_observation_date,
            selected_source_document=documents.get(row.selected_source_id),
            warning_codes=row.warning_codes,
            conflicting_values=row.conflicting_values,
            is_current_for_month=True,
        )
        for row in parsed.monthly_values
    ]
    MembershipMonthlyNewMemberValue.objects.bulk_create(values, batch_size=BATCH_SIZE)
    # Returned keyed, so the shared size distribution can find its parent month
    # without a second query.
    return {(value.calendar_year, value.calendar_month): value for value in values}


def _write_decision_batches(
    parsed: ParsedPackage,
    *,
    source,
    run: ImportRun,
    documents: dict[str, MembershipHistoricalSourceDocument],
) -> tuple[int, int, int]:
    """Write the decision batches and their two distributions.

    Nothing here touches an observation. A batch is not a year-to-date figure
    and is deliberately not attached to one, however close the dates are —
    attaching it would recreate exactly the confusion this schema exists to end.
    """
    batches = [
        MembershipDecisionBatch(
            source=source,
            import_run=run,
            external_batch_id=row.batch_id[:64],
            source_document=documents.get(row.source_id),
            batch_kind=row.batch_kind,
            as_of_date=row.as_of_date,
            as_of_date_precision=row.as_of_date_precision,
            # Left as the package stated it. A batch whose decision date the
            # source never gave keeps None rather than borrowing the as-of date.
            decision_date=row.decision_date,
            decision_reference=_bounded(row.decision_reference, 120),
            member_count=row.member_count,
            corroborating_document=documents.get(row.corroborating_source_id),
            quality_status=row.quality_status or QualityStatus.VERIFIED,
            extraction_confidence=row.extraction_confidence or "medium",
            warning_codes=row.warning_codes,
        )
        for row in parsed.decision_batches
    ]
    MembershipDecisionBatch.objects.bulk_create(batches, batch_size=BATCH_SIZE)
    by_external = {batch.external_batch_id: batch for batch in batches}

    sizes = [
        MembershipDecisionBatchSizeMovement(
            batch=by_external[row.batch_id],
            size_band_key=row.size_band_key,
            member_count=row.member_count,
            warning_codes=row.warning_codes,
        )
        for row in parsed.decision_batch_sizes
        if row.batch_id in by_external
    ]
    MembershipDecisionBatchSizeMovement.objects.bulk_create(sizes, batch_size=BATCH_SIZE)

    reasons = [
        MembershipDecisionBatchReason(
            batch=by_external[row.batch_id],
            reason_key=row.reason_key,
            member_count=row.member_count,
            warning_codes=row.warning_codes,
        )
        for row in parsed.decision_batch_reasons
        if row.batch_id in by_external
    ]
    MembershipDecisionBatchReason.objects.bulk_create(reasons, batch_size=BATCH_SIZE)
    return len(batches), len(sizes), len(reasons)


def _write_new_member_periods(
    parsed: ParsedPackage,
    *,
    source,
    run: ImportRun,
    documents: dict[str, MembershipHistoricalSourceDocument],
    monthly: dict[tuple[int, int], MembershipMonthlyNewMemberValue],
) -> tuple[int, int]:
    """Write multi-month spans and the size distribution shared with months."""
    periods = [
        MembershipNewMemberPeriod(
            source=source,
            import_run=run,
            external_period_id=row.period_id[:64],
            source_document=documents.get(row.source_id),
            period_scope=row.period_scope,
            period_start=row.period_start,
            period_end=row.period_end,
            new_members=row.new_members,
            extraction_confidence=row.extraction_confidence or "medium",
            warning_codes=row.warning_codes,
        )
        for row in parsed.new_member_periods
    ]
    MembershipNewMemberPeriod.objects.bulk_create(periods, batch_size=BATCH_SIZE)
    by_external = {period.external_period_id: period for period in periods}

    distributions = []
    for row in parsed.new_member_sizes:
        if row.period_id:
            parent = by_external.get(row.period_id)
            if parent is None:
                continue
            distributions.append(
                MembershipNewMemberSizeDistribution(
                    period=parent,
                    size_band_key=row.size_band_key,
                    member_count=row.member_count,
                    warning_codes=row.warning_codes,
                )
            )
            continue
        parent = monthly.get((row.calendar_year, row.calendar_month))
        if parent is None:
            continue
        distributions.append(
            MembershipNewMemberSizeDistribution(
                monthly_value=parent,
                size_band_key=row.size_band_key,
                member_count=row.member_count,
                warning_codes=row.warning_codes,
            )
        )
    MembershipNewMemberSizeDistribution.objects.bulk_create(distributions, batch_size=BATCH_SIZE)
    return len(periods), len(distributions)


def _write_issues(
    parsed: ParsedPackage,
    *,
    source,
    run: ImportRun,
    documents: dict[str, MembershipHistoricalSourceDocument],
) -> int:
    issues = [
        MembershipDataIssue(
            source=source,
            import_run=run,
            external_warning_id=row.warning_id[:32],
            source_document=documents.get(row.source_id),
            dataset=row.dataset[:64],
            record_key=_bounded(row.record_key, 120),
            warning_code=row.warning_code[:80],
            severity=row.severity if row.severity in IssueSeverity.values else IssueSeverity.INFO,
            message=_bounded(row.message, MAX_MESSAGE_LENGTH),
            raw_value=_bounded(row.raw_value, MAX_RAW_VALUE_LENGTH),
            suggested_action=_bounded(row.suggested_action, 300),
        )
        for row in parsed.warnings
    ]
    MembershipDataIssue.objects.bulk_create(issues, batch_size=BATCH_SIZE)
    return len(issues)


def _write_conflicts(parsed: ParsedPackage, *, source, run: ImportRun) -> int:
    conflicts = [
        MembershipMetricConflict(
            source=source,
            import_run=run,
            observation_date=row.observation_date,
            metric=row.metric[:64],
            warning_code=row.warning_code[:80],
            distinct_values=min(row.distinct_values, 32767),
            values_summary=_bounded(row.values_summary, MAX_MESSAGE_LENGTH),
            source_document_ids=row.source_ids,
        )
        for row in parsed.conflicts
    ]
    MembershipMetricConflict.objects.bulk_create(conflicts, batch_size=BATCH_SIZE)
    return len(conflicts)


def _guard_against_a_second_history(source, *, supersede_previous: bool) -> int:
    """Refuse to import a second history on top of an existing one.

    The `unchanged` check keys on importer, schema version and package digest,
    so it recognises *the same package run twice* and nothing else. A rebuilt
    package has a different digest, and raising the importer's schema version
    changes the key even for an identical file. Neither case is caught there,
    and each would write a complete second copy of the history beside the first
    — 296 observations becoming 592, with both preferred for the same dates.

    So the decision is made explicit. Without `supersede_previous` a live import
    into a populated history stops before the transaction opens. With it, the
    previous observations are marked superseded and no longer preferred, which
    are the only two fields a published observation permits changing. Nothing is
    deleted and no value is rewritten: the old rows keep their numbers, their
    children and their place in the audit trail.
    """
    existing = InternalMembershipObservation.objects.filter(source=source).exclude(
        quality_status=QualityStatus.SUPERSEDED
    )
    count = existing.count()
    if count == 0:
        return 0
    if not supersede_previous:
        raise MembershipHistoryImportError(
            "Liikmeskonna ajalugu on juba imporditud "
            f"({count} vaatlust). Uus import kirjutaks teise koopia olemasoleva "
            "kõrvale. Kasuta --supersede-previous, kui uus pakett peab vana "
            "asendama."
        )
    superseded = 0
    for observation in existing.iterator(chunk_size=BATCH_SIZE):
        observation.quality_status = QualityStatus.SUPERSEDED
        observation.is_preferred_for_date = False
        observation.save(update_fields=["quality_status", "is_preferred_for_date"])
        superseded += 1
    return superseded


def _existing_successful_run(import_key: str) -> ImportRun | None:
    return ImportRun.objects.filter(
        import_key=import_key,
        dry_run=False,
        status=ImportStatus.SUCCEEDED,
    ).first()


def _diagnostics(parsed: ParsedPackage) -> list[dict]:
    """Counts per warning code. Never a message, never a value."""
    counts: dict[str, int] = {}
    for warning in parsed.warnings:
        counts[warning.warning_code] = counts.get(warning.warning_code, 0) + 1
    return [{"code": code, "records": total} for code, total in sorted(counts.items())]


def _sanitized(error: Exception) -> dict:
    message = str(error).strip().replace("\n", " ")
    return {"type": type(error).__name__, "message": message[:300]}


def import_history_package(
    path: Path | str,
    *,
    dry_run: bool = True,
    actor=None,
    correlation_id: uuid.UUID | None = None,
    supersede_previous: bool = False,
) -> HistoryImportResult:
    """Validate and, unless this is a dry run, import the approved package.

    A dry run validates the whole contract, records the attempt and writes no
    domain row. A live run writes every table inside one transaction and
    publishes only after all of it succeeded. Repeating an identical live import
    reports `unchanged` and touches nothing.

    `supersede_previous` is required to import a *different* package into a
    history that already holds one; see `_guard_against_a_second_history`. It
    supersedes, never deletes.
    """
    try:
        parsed = read_package(path, limits=_limits())
    except PackageContractError as error:
        raise MembershipHistoryImportError(str(error)) from error

    source = ensure_internal_membership_source(actor=actor, correlation_id=correlation_id)

    import_key = calculate_import_key(IMPORTER_NAME, PACKAGE_SCHEMA_VERSION, parsed.package_sha256)
    already = _existing_successful_run(import_key)
    if already is not None and not dry_run:
        record_event(
            action=MembershipAudit.HISTORY_UNCHANGED,
            obj=already,
            actor=actor,
            correlation_id=already.correlation_id,
            change_summary={
                "source": source.slug,
                "package_sha256": parsed.package_sha256,
                "import_key": import_key,
            },
        )
        return HistoryImportResult(
            import_run=already,
            dry_run=False,
            unchanged=True,
            package_sha256=parsed.package_sha256,
            counts=parsed.row_counts,
        )

    artifact = _ensure_artifact(source, parsed, actor=actor, correlation_id=correlation_id)
    run = build_import_run(
        artifact=artifact,
        importer_name=IMPORTER_NAME,
        schema_version=PACKAGE_SCHEMA_VERSION,
        dry_run=dry_run,
        initiated_by=actor,
        actor=actor,
        correlation_id=correlation_id,
    )
    start_import_run(run)

    try:
        if dry_run:
            complete_import_run(
                run,
                rows_added=0,
                rows_skipped=sum(parsed.row_counts.values()),
                warnings=_diagnostics(parsed),
                actor=actor,
            )
            return HistoryImportResult(
                import_run=run,
                dry_run=True,
                unchanged=False,
                package_sha256=parsed.package_sha256,
                counts=parsed.row_counts,
            )

        with transaction.atomic():
            superseded = _guard_against_a_second_history(
                source, supersede_previous=supersede_previous
            )
            documents = _write_source_documents(parsed, source=source, run=run)
            observations = _write_observations(
                parsed, source=source, artifact=artifact, run=run, documents=documents
            )
            direct = _direct_observations_by_source(parsed, observations)
            movement_count, reason_count = _write_children(parsed, direct=direct)
            monthly = _write_monthly(parsed, source=source, run=run, documents=documents)
            issue_count = _write_issues(parsed, source=source, run=run, documents=documents)
            conflict_count = _write_conflicts(parsed, source=source, run=run)
            batch_count, batch_sizes, batch_reasons = _write_decision_batches(
                parsed, source=source, run=run, documents=documents
            )
            period_count, distribution_count = _write_new_member_periods(
                parsed, source=source, run=run, documents=documents, monthly=monthly
            )

            counts = {
                "source_documents": len(documents),
                "observations": len(observations),
                "size_movements": movement_count,
                "removal_reasons": reason_count,
                "monthly_values": len(monthly),
                "issues": issue_count,
                "conflicts": conflict_count,
                "decision_batches": batch_count,
                "decision_batch_sizes": batch_sizes,
                "decision_batch_reasons": batch_reasons,
                "new_member_periods": period_count,
                "new_member_size_distribution": distribution_count,
                "superseded_observations": superseded,
            }
            complete_import_run(
                run,
                rows_added=sum(counts.values()),
                warnings=_diagnostics(parsed),
                actor=actor,
            )
            record_event(
                action=MembershipAudit.HISTORY_IMPORTED,
                obj=run,
                actor=actor,
                correlation_id=run.correlation_id,
                change_summary={
                    "source": source.slug,
                    "package_sha256": parsed.package_sha256,
                    "import_key": import_key,
                    "counts": counts,
                    "preferred_observations": sum(
                        1 for row in observations.values() if row.is_preferred_for_date
                    ),
                },
            )

        return HistoryImportResult(
            import_run=run,
            dry_run=False,
            unchanged=False,
            package_sha256=parsed.package_sha256,
            counts=counts,
        )

    except Exception as error:
        # The atomic block has already rolled back, so nothing partial survives
        # and the run can be closed honestly.
        fail_publication(run, errors=[_sanitized(error)], actor=actor)
        record_event(
            action=MembershipAudit.HISTORY_FAILED,
            obj=run,
            actor=actor,
            correlation_id=run.correlation_id,
            change_summary={
                "source": source.slug,
                "package_sha256": parsed.package_sha256,
                "error": _sanitized(error),
            },
        )
        if isinstance(error, PackageContractError):
            raise MembershipHistoryImportError(str(error)) from error
        raise


__all__ = [
    "IMPORTER_NAME",
    "HistoryImportResult",
    "MembershipHistoryImportError",
    "import_history_package",
]
