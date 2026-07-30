"""The historical import: deterministic, transactional, idempotent."""

from __future__ import annotations

from datetime import date

import pytest
from django.conf import settings

from apps.audit.models import AuditAction, AuditEvent
from apps.membership.history_import import (
    IMPORTER_NAME,
    MembershipHistoryImportError,
    import_history_package,
)
from apps.membership.models import (
    InternalMembershipObservation,
    InternalSourceKind,
    MembershipCountObservation,
    MembershipDataIssue,
    MembershipHistoricalSourceDocument,
    MembershipMetricConflict,
    MembershipMonthlyNewMemberValue,
    MembershipRemovalReason,
    MembershipSizeMovement,
    MonthlyValueStatus,
    QualityStatus,
)
from apps.sources.models import ImportRun, ImportStatus, SourceArtifact

from .package_factory import (
    SNAP_A_DIRECT,
    SNAP_B_COMPARISON,
    SOURCE_A,
    build_package,
    default_snapshots,
)

pytestmark = pytest.mark.django_db


def test_dry_run_validates_and_writes_nothing(package_path):
    result = import_history_package(package_path, dry_run=True)

    assert result.dry_run is True
    assert result.unchanged is False
    assert result.counts["snapshots"] == 3
    assert InternalMembershipObservation.objects.count() == 0
    assert MembershipHistoricalSourceDocument.objects.count() == 0
    assert MembershipMonthlyNewMemberValue.objects.count() == 0
    assert result.import_run.status == ImportStatus.SUCCEEDED
    assert result.import_run.dry_run is True


def test_live_import_writes_every_table(imported_package):
    assert imported_package.unchanged is False
    assert MembershipHistoricalSourceDocument.objects.count() == 2
    assert InternalMembershipObservation.objects.count() == 3
    assert MembershipSizeMovement.objects.count() == 8
    assert MembershipRemovalReason.objects.count() == 2
    assert MembershipMonthlyNewMemberValue.objects.count() == 4
    assert MembershipDataIssue.objects.count() == 2
    assert MembershipMetricConflict.objects.count() == 1


def test_repeating_an_identical_import_changes_nothing(package_path, imported_package):
    before = InternalMembershipObservation.objects.count()

    repeated = import_history_package(package_path, dry_run=False)

    assert repeated.unchanged is True
    assert InternalMembershipObservation.objects.count() == before
    assert repeated.import_run.pk == imported_package.import_run.pk
    assert AuditEvent.objects.filter(action=AuditAction.MEMBERSHIP_HISTORY_UNCHANGED).exists()


def test_dry_run_then_live_import_works(package_path):
    import_history_package(package_path, dry_run=True)
    result = import_history_package(package_path, dry_run=False)

    assert result.unchanged is False
    assert InternalMembershipObservation.objects.count() == 3


def test_failed_import_leaves_the_database_untouched(tmp_path, db):
    """A package that breaks *after* several tables were written must roll back.

    Making the 2024 reading a comparison leaves its size movements with no
    direct observation to attach to. The source documents and all three
    observations are written before that is discovered, which is exactly the
    case where a non-transactional importer would leave debris.
    """
    snapshots = default_snapshots()
    snapshots[0]["source_kind"] = "reported_comparison"
    broken = build_package(tmp_path / "broken.zip", snapshots=snapshots)

    with pytest.raises(MembershipHistoryImportError):
        import_history_package(broken, dry_run=False)

    assert MembershipHistoricalSourceDocument.objects.count() == 0
    assert InternalMembershipObservation.objects.count() == 0
    assert MembershipSizeMovement.objects.count() == 0
    assert MembershipMonthlyNewMemberValue.objects.count() == 0

    failed = ImportRun.objects.filter(status=ImportStatus.FAILED).first()
    assert failed is not None
    assert AuditEvent.objects.filter(action=AuditAction.MEMBERSHIP_HISTORY_FAILED).exists()


def test_invalid_package_never_creates_an_import_run(tmp_path):
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip at all")

    with pytest.raises(MembershipHistoryImportError):
        import_history_package(broken, dry_run=False)

    assert ImportRun.objects.filter(importer_name=IMPORTER_NAME).count() == 0


# --------------------------------------------------------------------------
# Quality and precedence
# --------------------------------------------------------------------------


def test_direct_observation_outranks_comparison_for_the_same_date(imported_package):
    """Both are stored; only the first-hand reading is preferred."""
    direct = InternalMembershipObservation.objects.get(external_snapshot_id=SNAP_A_DIRECT)
    comparison = InternalMembershipObservation.objects.get(external_snapshot_id=SNAP_B_COMPARISON)

    assert direct.observation_date == comparison.observation_date
    assert direct.is_preferred_for_date is True
    assert comparison.is_preferred_for_date is False
    # The evidence that lost is kept, not deleted.
    assert comparison.source_kind == InternalSourceKind.REPORTED_COMPARISON
    assert comparison.total_members == 3199


def test_one_preferred_observation_per_date(imported_package):
    dates = InternalMembershipObservation.objects.filter(is_preferred_for_date=True).values_list(
        "observation_date", flat=True
    )
    assert len(dates) == len(set(dates))


def test_comparison_is_preferred_when_no_direct_reading_exists(tmp_path):
    snapshots = [row for row in default_snapshots() if row["snapshot_id"] != SNAP_A_DIRECT]
    package = build_package(
        tmp_path / "package.zip",
        snapshots=snapshots,
        movements=[],
        removal_reasons=[],
    )

    import_history_package(package, dry_run=False)
    fallback = InternalMembershipObservation.objects.get(external_snapshot_id=SNAP_B_COMPARISON)

    assert fallback.is_preferred_for_date is True


def test_conflicted_metric_marks_the_observation_but_keeps_it(imported_package):
    """The conflict is on total_members for 2024-01-10."""
    observation = InternalMembershipObservation.objects.get(external_snapshot_id=SNAP_A_DIRECT)

    assert observation.quality_status == QualityStatus.CONFLICTED
    assert observation.is_preferred_for_date is True
    # The disputed number is still stored. Only the chart withholds it.
    assert observation.total_members == 3200


def test_paid_over_total_is_stored_as_review_required(tmp_path):
    snapshots = default_snapshots()
    snapshots[1]["paid_members"] = "9999"

    import_history_package(
        build_package(tmp_path / "package.zip", snapshots=snapshots), dry_run=False
    )
    observation = InternalMembershipObservation.objects.get(observation_date=date(2025, 1, 15))

    assert observation.quality_status == QualityStatus.REVIEW_REQUIRED
    assert observation.paid_members == 9999
    assert "paid_exceeds_total" in observation.warning_codes


def test_collection_over_100_is_accepted_when_consistent(imported_package):
    """525 000 of a 500 000 budget really is 105 %. It is not an error."""
    observation = InternalMembershipObservation.objects.get(observation_date=date(2025, 1, 15))

    assert observation.membership_fee_collection_pct_reported == 105
    assert observation.quality_status == QualityStatus.VERIFIED
    assert "collection_pct_mismatch" not in observation.warning_codes


def test_inconsistent_collection_percentage_is_flagged(tmp_path):
    snapshots = default_snapshots()
    snapshots[1]["membership_fee_collection_pct"] = "42.00"

    import_history_package(
        build_package(tmp_path / "package.zip", snapshots=snapshots), dry_run=False
    )
    observation = InternalMembershipObservation.objects.get(observation_date=date(2025, 1, 15))

    assert observation.quality_status == QualityStatus.REVIEW_REQUIRED
    assert "collection_pct_mismatch" in observation.warning_codes


# --------------------------------------------------------------------------
# Monthly values
# --------------------------------------------------------------------------


def test_conflict_month_has_no_value_and_is_not_zero(imported_package):
    value = MembershipMonthlyNewMemberValue.objects.get(calendar_year=2024, calendar_month=3)

    assert value.value_status == MonthlyValueStatus.CONFLICT
    assert value.new_members is None
    assert value.new_members != 0


def test_explicit_zero_month_is_preserved_as_a_value(imported_package):
    value = MembershipMonthlyNewMemberValue.objects.get(calendar_year=2024, calendar_month=2)

    assert value.new_members == 0
    assert value.value_status == MonthlyValueStatus.VERIFIED


def test_unreported_month_has_no_row_at_all(imported_package):
    assert not MembershipMonthlyNewMemberValue.objects.filter(
        calendar_year=2024, calendar_month=7
    ).exists()


def test_provisional_month_is_retained_and_labelled(imported_package):
    value = MembershipMonthlyNewMemberValue.objects.get(calendar_year=2025, calendar_month=1)

    assert value.value_status == MonthlyValueStatus.PROVISIONAL_CURRENT_MONTH
    assert value.new_members == 9


# --------------------------------------------------------------------------
# Provenance, artifacts and audit
# --------------------------------------------------------------------------


def test_the_package_file_is_never_stored(imported_package):
    artifact = SourceArtifact.objects.get(sha256=imported_package.package_sha256)

    assert not artifact.file
    assert artifact.external_reference.startswith("package:membership-history:")
    assert artifact.is_external


def test_children_attach_to_the_direct_observation(imported_package):
    direct = InternalMembershipObservation.objects.get(external_snapshot_id=SNAP_A_DIRECT)

    assert direct.size_movements.count() == 4
    assert direct.removal_reasons.count() == 2
    comparison = InternalMembershipObservation.objects.get(external_snapshot_id=SNAP_B_COMPARISON)
    assert comparison.size_movements.count() == 0


def test_error_warnings_are_kept_not_dropped(imported_package):
    assert MembershipDataIssue.objects.filter(severity="error").count() == 1


def test_conflict_stores_document_ids_and_no_paths(imported_package):
    conflict = MembershipMetricConflict.objects.get()

    assert SOURCE_A in conflict.source_document_ids
    assert ".docx" not in str(conflict.source_document_ids)


def test_audit_records_counts_and_no_source_content(imported_package):
    event = AuditEvent.objects.get(action=AuditAction.MEMBERSHIP_HISTORY_IMPORTED)
    summary = event.change_summary

    assert summary["counts"]["observations"] == 3
    assert summary["package_sha256"] == imported_package.package_sha256
    assert "raw_reference" not in str(summary)
    assert ".docx" not in str(summary)


def test_json_output_carries_counts_only(imported_package):
    payload = imported_package.as_json()

    assert payload["counts"]["snapshots"] == 3
    assert payload["package_sha256"] == imported_package.package_sha256
    assert ".docx" not in str(payload)


# --------------------------------------------------------------------------
# Separation from the public directory count
# --------------------------------------------------------------------------


def test_public_membership_model_is_untouched_by_the_import(imported_package):
    assert MembershipCountObservation.objects.count() == 0


def test_the_two_sources_are_distinct(imported_package, internal_source):
    assert internal_source.slug == settings.MEMBERSHIP_INTERNAL_SOURCE_SLUG
    assert internal_source.slug != settings.KODA_MEMBERS_SOURCE_SLUG
    assert InternalMembershipObservation.objects.exclude(source=internal_source).count() == 0
