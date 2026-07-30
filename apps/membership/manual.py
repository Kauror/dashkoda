"""Entering one board report by hand, with the same rules as the import.

This is the current process for new data and is deliberately modest: a staff
user types what the board was told, sees exactly what will be saved, and
confirms. There is no upload, no remote fetch and no schedule. When an automated
route eventually exists it can replace this module without touching a single
historical row, because both writers already publish through the same service.

What makes a manual entry a first-class record rather than a note:

- the submitted values are turned into **canonical JSON** — sorted keys, fixed
  separators, dates as ISO text, amounts as exact decimal strings — and hashed.
  That hash is the content identity, so an accidental double submit is
  recognised as the same report instead of published twice;
- a metadata-only artifact carries that identity under a safe reference, and an
  ordinary `ImportRun` records the attempt, exactly as the historical import
  does;
- publication goes through `publishing.publish_observation`, so precedence,
  superseding and the audit trail behave identically for typed and imported
  data.

A correction never edits anything. It is a new observation that names the one it
replaces, and the replaced row keeps its numbers and its children.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction
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

from .bootstrap import ensure_internal_membership_source
from .models import (
    DatePrecision,
    ExtractionConfidence,
    InternalMembershipObservation,
    InternalSourceKind,
    MembershipHistoricalSourceDocument,
    MembershipMonthlyNewMemberValue,
    MembershipRemovalReason,
    MembershipSizeMovement,
    MonthlyValueStatus,
    MovementDirection,
    RemovalReasonKey,
    SizeBand,
)
from .publishing import publish_monthly_value, publish_observation
from .quality import (
    MetricFacts,
    assess,
    collection_is_consistent,
    computed_collection_pct,
)

IMPORTER_NAME = "membership_manual_entry"
MANUAL_SCHEMA_VERSION = "1.0"

ARTIFACT_REFERENCE_PREFIX = "manual:membership-report"
ARTIFACT_MIME_TYPE = "application/json"

# A later report is expected to move the numbers, but not by half. Beyond this
# the form asks for confirmation rather than refusing: an unusual year is still
# a real year.
SUBSTANTIAL_CHANGE_RATIO = Decimal("0.15")


class ManualEntryError(RuntimeError):
    """The report could not be published. Nothing was written."""


@dataclass(frozen=True)
class ManualReport:
    """One submitted board report, before anything has been written."""

    observation_date: date
    reported_year: int | None = None
    document_title: str = ""
    source_note: str = ""
    facts: MetricFacts = field(default_factory=MetricFacts)
    monthly_year: int | None = None
    # Only months the user actually filled in. A month absent from this mapping
    # was left blank, which is not the same as a `0` entered on purpose.
    monthly_new_members: dict[int, int] = field(default_factory=dict)
    joined_by_band: dict[str, int] = field(default_factory=dict)
    removed_by_band: dict[str, int] = field(default_factory=dict)
    size_table_complete: bool = False
    removal_reasons: dict[str, int] = field(default_factory=dict)
    other_reason_label: str = ""
    other_reason_count: int | None = None
    reasons_complete: bool = False
    supersedes_id: int | None = None
    # Correcting a report that was filed under the wrong date is a real need,
    # but it silently moves a point on every chart. It therefore takes its own
    # deliberate tick rather than happening as a side effect of editing a date.
    confirm_date_change: bool = False


@dataclass(frozen=True)
class ManualPreview:
    """Everything shown before the user confirms. Saves nothing."""

    computed_collection_pct: Decimal | None
    reported_collection_pct: Decimal | None
    collection_difference: Decimal | None
    monthly_sum_to_observation_month: int | None
    monthly_vs_new_members_ytd: int | None
    joined_total: int | None
    removed_total: int | None
    reason_total: int | None
    paid_member_share_pct: Decimal | None
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    superseded_observation: InternalMembershipObservation | None
    quality_status: str
    withheld_metrics: frozenset[str]

    @property
    def can_publish(self) -> bool:
        return not self.errors


def _decimal_text(value: Decimal | None) -> str | None:
    """Exact text, never a float. `Decimal("1.50")` and `Decimal("1.5")` are the
    same amount and must hash identically, so the value is normalised first."""
    if value is None:
        return None
    return str(Decimal(value).normalize())


def canonical_payload(report: ManualReport) -> dict:
    """A deterministic, order-independent description of what was submitted."""
    facts = report.facts
    return {
        "schema": MANUAL_SCHEMA_VERSION,
        "observation_date": report.observation_date.isoformat(),
        "reported_year": report.reported_year,
        "document_title": report.document_title.strip(),
        "source_note": report.source_note.strip(),
        "facts": {
            "total_members": facts.total_members,
            "paid_members": facts.paid_members,
            "membership_fees_received_eur": _decimal_text(facts.membership_fees_received_eur),
            "membership_fee_budget_eur": _decimal_text(facts.membership_fee_budget_eur),
            "membership_fee_collection_pct_reported": _decimal_text(
                facts.membership_fee_collection_pct_reported
            ),
            "new_members_ytd": facts.new_members_ytd,
            "suspended_members": facts.suspended_members,
            "removed_members_ytd": facts.removed_members_ytd,
        },
        "monthly_year": report.monthly_year,
        # String keys and sorted output, so the same grid always hashes the same
        # way whatever order the form fields arrived in.
        "monthly_new_members": {
            str(month): count for month, count in sorted(report.monthly_new_members.items())
        },
        "joined_by_band": dict(sorted(report.joined_by_band.items())),
        "removed_by_band": dict(sorted(report.removed_by_band.items())),
        "size_table_complete": report.size_table_complete,
        "removal_reasons": dict(sorted(report.removal_reasons.items())),
        "other_reason_label": report.other_reason_label.strip(),
        "other_reason_count": report.other_reason_count,
        "reasons_complete": report.reasons_complete,
        "supersedes_id": report.supersedes_id,
    }


def canonical_json(report: ManualReport) -> str:
    return json.dumps(
        canonical_payload(report),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_sha256(report: ManualReport) -> str:
    return hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Validation and preview
# --------------------------------------------------------------------------


def _sum_or_none(values) -> int | None:
    values = list(values)
    return sum(values) if values else None


def _previous_preferred(source, observation_date: date) -> InternalMembershipObservation | None:
    return (
        InternalMembershipObservation.objects.filter(
            source=source, is_preferred_for_date=True, observation_date__lt=observation_date
        )
        .order_by("-observation_date")
        .first()
    )


def build_preview(report: ManualReport, *, source=None) -> ManualPreview:
    """Compute everything the confirmation step shows. Writes nothing.

    Hard errors block publication. Warnings do not: an unusual report is still a
    report, and refusing it would push the user towards entering a number they
    do not have.
    """
    source = source or ensure_internal_membership_source()
    facts = report.facts
    errors: list[str] = []
    warnings: list[str] = []

    if facts.paid_members is not None and facts.total_members is not None:
        if facts.paid_members > facts.total_members:
            errors.append("Tasunud liikmeid ei saa olla rohkem kui liikmeid kokku.")

    # A month that has not happened yet cannot have a reported figure.
    if report.monthly_year is not None:
        for month in report.monthly_new_members:
            if report.monthly_year > report.observation_date.year or (
                report.monthly_year == report.observation_date.year
                and month > report.observation_date.month
            ):
                errors.append(
                    "Vaatluse kuupäevast hilisemat kuud ei saa täita: "
                    f"{report.monthly_year}-{month:02d}."
                )

    joined_total = _sum_or_none(report.joined_by_band.values())
    removed_total = _sum_or_none(report.removed_by_band.values())
    reason_total = _sum_or_none(
        list(report.removal_reasons.values())
        + ([report.other_reason_count] if report.other_reason_count is not None else [])
    )

    # Totals are only *checked* when the user says the table is complete. A
    # partially filled table is a normal thing to have and must not be rejected
    # for failing to add up.
    if report.size_table_complete and facts.new_members_ytd is not None:
        if joined_total is not None and joined_total != facts.new_members_ytd:
            errors.append("Täidetud suurusklasside liitunute summa ei klapi uute liikmete arvuga.")
    if report.size_table_complete and facts.removed_members_ytd is not None:
        if removed_total is not None and removed_total != facts.removed_members_ytd:
            errors.append("Täidetud suurusklasside lahkunute summa ei klapi väljaarvatute arvuga.")
    if report.reasons_complete and facts.removed_members_ytd is not None:
        if reason_total is not None and reason_total != facts.removed_members_ytd:
            errors.append("Lahkumise põhjuste summa ei klapi väljaarvatute arvuga.")

    computed = computed_collection_pct(
        facts.membership_fees_received_eur, facts.membership_fee_budget_eur
    )
    reported = facts.membership_fee_collection_pct_reported
    difference = None
    if computed is not None and reported is not None:
        difference = (Decimal(reported) - computed).quantize(Decimal("0.01"))
    consistent = collection_is_consistent(
        reported, facts.membership_fees_received_eur, facts.membership_fee_budget_eur
    )
    if consistent is False:
        warnings.append(
            "Raporteeritud laekumise protsent erineb laekunud summa ja eelarve suhtest."
        )
    elif reported is not None and reported > 100:
        warnings.append(
            "Laekumise protsent on üle 100. See on lubatud, kui laekumine ületab eelarvet."
        )

    monthly_sum = None
    if report.monthly_year == report.observation_date.year and report.monthly_new_members:
        through = [
            count
            for month, count in report.monthly_new_members.items()
            if month <= report.observation_date.month
        ]
        monthly_sum = sum(through) if through else None
    monthly_gap = None
    if monthly_sum is not None and facts.new_members_ytd is not None:
        monthly_gap = monthly_sum - facts.new_members_ytd
        if monthly_gap != 0:
            warnings.append("Kuude summa erineb aasta algusest lisandunute arvust.")

    latest = (
        InternalMembershipObservation.objects.filter(source=source, is_preferred_for_date=True)
        .order_by("-observation_date")
        .first()
    )
    if latest is not None and report.observation_date < latest.observation_date:
        warnings.append("Lisatav aruanne on vanem kui viimane olemasolev vaatlus.")

    previous = _previous_preferred(source, report.observation_date)
    if (
        previous is not None
        and previous.total_members
        and facts.total_members is not None
        and previous.total_members > 0
    ):
        change = abs(Decimal(facts.total_members) - Decimal(previous.total_members)) / Decimal(
            previous.total_members
        )
        if change > SUBSTANTIAL_CHANGE_RATIO:
            warnings.append("Liikmete arv erineb eelmisest vaatlusest märkimisväärselt.")

    if not report.joined_by_band and not report.removed_by_band:
        warnings.append("Suurusklasside jaotus jäi täitmata.")
    if not report.removal_reasons and report.other_reason_count is None:
        warnings.append("Lahkumise põhjused jäid täitmata.")

    paid_share = None
    if facts.total_members and facts.paid_members is not None and facts.total_members > 0:
        paid_share = (Decimal(facts.paid_members) / Decimal(facts.total_members) * 100).quantize(
            Decimal("0.01")
        )

    superseded = None
    if report.supersedes_id is not None:
        superseded = InternalMembershipObservation.objects.filter(
            source=source, pk=report.supersedes_id
        ).first()
        if superseded is None:
            errors.append("Parandatavat vaatlust ei leitud.")
        elif superseded.observation_date != report.observation_date:
            # Not refused outright: correcting a mis-dated report is a real
            # need. It must be a deliberate act, so it needs its own tick.
            if report.confirm_date_change:
                warnings.append("Parandus salvestatakse asendatavast erinevale kuupäevale.")
            else:
                errors.append(
                    "Parandatava vaatluse kuupäev erineb sisestatust. "
                    "Kinnita kuupäeva muutmine eraldi."
                )

    assessment = assess(facts, extra_warning_codes=())

    return ManualPreview(
        computed_collection_pct=computed,
        reported_collection_pct=reported,
        collection_difference=difference,
        monthly_sum_to_observation_month=monthly_sum,
        monthly_vs_new_members_ytd=monthly_gap,
        joined_total=joined_total,
        removed_total=removed_total,
        reason_total=reason_total,
        paid_member_share_pct=paid_share,
        warnings=tuple(warnings),
        errors=tuple(errors),
        superseded_observation=superseded,
        quality_status=assessment.quality_status,
        withheld_metrics=assessment.withheld_metrics,
    )


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------


def _existing_observation(source, content_sha256: str) -> InternalMembershipObservation | None:
    """Recognise a report that has already been published.

    This is what makes a double submit harmless: the second request finds the
    first request's observation and redirects to it instead of creating a
    duplicate.
    """
    return (
        InternalMembershipObservation.objects.filter(source=source, artifact__sha256=content_sha256)
        .select_related("source")
        .first()
    )


def _document_for(
    report: ManualReport, *, source, run
) -> MembershipHistoricalSourceDocument | None:
    title = report.document_title.strip()
    if not title:
        return None
    return MembershipHistoricalSourceDocument.objects.create(
        source=source,
        import_run=run,
        external_source_id=f"manual:{uuid.uuid4()}",
        document_title=title[:400],
        extraction_status="manual",
        observation_date=report.observation_date,
        observation_date_precision=DatePrecision.DAY,
        date_source="manual_entry",
        date_confidence=ExtractionConfidence.MANUAL_VERIFIED,
        document_year_claim=report.reported_year,
    )


def _write_children(observation: InternalMembershipObservation, report: ManualReport) -> int:
    movements: list[MembershipSizeMovement] = []
    for direction, by_band in (
        (MovementDirection.JOINED, report.joined_by_band),
        (MovementDirection.REMOVED, report.removed_by_band),
    ):
        total = _sum_or_none(by_band.values())
        movements.extend(
            MembershipSizeMovement(
                observation=observation,
                direction=direction,
                size_band_key=band,
                size_band_label_raw=SizeBand(band).label,
                member_count=count,
                total_reported=total,
                extraction_confidence=ExtractionConfidence.MANUAL_VERIFIED,
            )
            for band, count in sorted(by_band.items())
        )
    MembershipSizeMovement.objects.bulk_create(movements)

    removed_total = report.facts.removed_members_ytd
    reasons = [
        MembershipRemovalReason(
            observation=observation,
            reason_key=key,
            reason_label_raw=RemovalReasonKey(key).label,
            member_count=count,
            removed_total_reported=removed_total,
            extraction_confidence=ExtractionConfidence.MANUAL_VERIFIED,
        )
        for key, count in sorted(report.removal_reasons.items())
    ]
    if report.other_reason_count is not None:
        # The label the user wrote is kept as written. Folding it into a known
        # category would quietly change what the board actually reported.
        reasons.append(
            MembershipRemovalReason(
                observation=observation,
                reason_key=RemovalReasonKey.OTHER,
                reason_label_raw=report.other_reason_label.strip()[:300],
                member_count=report.other_reason_count,
                removed_total_reported=removed_total,
                extraction_confidence=ExtractionConfidence.MANUAL_VERIFIED,
            )
        )
    MembershipRemovalReason.objects.bulk_create(reasons)
    return len(movements) + len(reasons)


def _write_monthly(
    report: ManualReport,
    *,
    source,
    run,
    actor,
    correlation_id,
) -> int:
    if report.monthly_year is None or not report.monthly_new_members:
        return 0
    written = 0
    for month, count in sorted(report.monthly_new_members.items()):
        value = MembershipMonthlyNewMemberValue.objects.create(
            source=source,
            import_run=run,
            calendar_year=report.monthly_year,
            calendar_month=month,
            new_members=count,
            value_status=MonthlyValueStatus.MANUAL_VERIFIED,
            source_count=1,
            source_ids=[],
            created_by=actor,
            is_current_for_month=False,
        )
        publish_monthly_value(value, actor=actor, correlation_id=correlation_id)
        written += 1
    return written


@transaction.atomic
def publish_manual_report(
    report: ManualReport,
    *,
    actor=None,
    correlation_id: uuid.UUID | None = None,
) -> InternalMembershipObservation:
    """Validate once more, then write the whole report atomically.

    The revalidation is not defensive noise: the preview ran against the state
    the user saw, and something may have changed since. Publishing on stale
    validation is exactly how two people overwrite each other.
    """
    source = ensure_internal_membership_source(actor=actor, correlation_id=correlation_id)
    preview = build_preview(report, source=source)
    if preview.errors:
        raise ManualEntryError(preview.errors[0])

    content_sha256 = canonical_sha256(report)
    duplicate = _existing_observation(source, content_sha256)
    if duplicate is not None:
        return duplicate

    artifact = SourceArtifact.objects.filter(source=source, sha256=content_sha256).first()
    if artifact is None:
        # Registered through the source service like every other artifact, so
        # the immutability rules and the audit entry are not optional here.
        # There is no file: the content identity *is* the canonical JSON, which
        # the observation and its children already hold in structured form.
        artifact = register_external_reference(
            source=source,
            external_reference=f"{ARTIFACT_REFERENCE_PREFIX}:{uuid.uuid4()}",
            original_name="membership-report.json",
            mime_type=ARTIFACT_MIME_TYPE,
            sha256=content_sha256,
            size_bytes=len(canonical_json(report).encode("utf-8")),
            uploaded_by=actor,
            actor=actor,
            correlation_id=correlation_id,
        )

    run = build_import_run(
        artifact=artifact,
        importer_name=IMPORTER_NAME,
        schema_version=MANUAL_SCHEMA_VERSION,
        dry_run=False,
        initiated_by=actor,
        actor=actor,
        correlation_id=correlation_id,
    )
    start_import_run(run)

    assessment = assess(report.facts)
    observation = InternalMembershipObservation.objects.create(
        source=source,
        artifact=artifact,
        import_run=run,
        observation_date=report.observation_date,
        observation_date_precision=DatePrecision.DAY,
        source_kind=InternalSourceKind.MANUAL,
        source_column_label="",
        reported_year=report.reported_year or report.observation_date.year,
        total_members=report.facts.total_members,
        paid_members=report.facts.paid_members,
        membership_fees_received_eur=report.facts.membership_fees_received_eur,
        membership_fee_budget_eur=report.facts.membership_fee_budget_eur,
        membership_fee_collection_pct_reported=(
            report.facts.membership_fee_collection_pct_reported
        ),
        new_members_ytd=report.facts.new_members_ytd,
        suspended_members=report.facts.suspended_members,
        removed_members_ytd=report.facts.removed_members_ytd,
        extraction_confidence=ExtractionConfidence.MANUAL_VERIFIED,
        quality_status=assessment.quality_status,
        supersedes=preview.superseded_observation,
        source_document=_document_for(report, source=source, run=run),
        source_note=report.source_note.strip(),
        warning_codes=list(assessment.warning_codes),
        created_by=actor,
        published_at=timezone.now(),
    )

    child_rows = _write_children(observation, report)
    monthly_rows = _write_monthly(
        report, source=source, run=run, actor=actor, correlation_id=correlation_id
    )

    publish_observation(
        observation,
        supersedes=preview.superseded_observation,
        actor=actor,
        correlation_id=run.correlation_id,
    )
    complete_import_run(run, rows_added=1 + child_rows + monthly_rows, actor=actor)

    record_event(
        action=AuditAction.MEMBERSHIP_MANUAL_OBSERVATION_CREATED,
        obj=observation,
        actor=actor,
        correlation_id=run.correlation_id,
        change_summary={
            # Aggregate facts and identifiers only. The note the user typed is
            # stored on the observation and deliberately not copied here.
            "source": source.slug,
            "observation_date": observation.observation_date.isoformat(),
            "reported_year": observation.reported_year,
            "observation_id": observation.pk,
            "content_sha256": content_sha256,
            "child_rows": child_rows,
            "monthly_rows": monthly_rows,
            "supersedes_observation_id": (
                preview.superseded_observation.pk if preview.superseded_observation else None
            ),
        },
    )
    return observation
