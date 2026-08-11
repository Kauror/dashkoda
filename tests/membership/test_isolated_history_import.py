"""The upgrade path, against a database shaped like the one in production.

The rebuild is not imported into an empty database. It is imported over a
history that already holds the 2026-07-31 extraction, so the questions that
matter are whether anything already there is lost, whether the replacement
arrives whole, and whether repeating the import is safe.

The shape is seeded synthetically at production scale — 148 documents, 296
observations, 234 monthly values, 2960 size movements, 435 removal reasons —
rather than restored from a backup. No production row, figure or path is
present or needed: what is under test is the behaviour of the upgrade, and that
depends on the shape rather than on the values.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.membership.bootstrap import ensure_internal_membership_source
from apps.membership.history_import import (
    MembershipHistoryImportError,
    import_history_package,
)
from apps.membership.models import (
    InternalMembershipObservation,
    InternalSourceKind,
    MembershipDecisionBatch,
    MembershipHistoricalSourceDocument,
    MembershipMonthlyNewMemberValue,
    MembershipRemovalReason,
    MembershipSizeMovement,
    MonthlyValueStatus,
    QualityStatus,
    RemovalReasonKey,
    SizeBand,
)
from apps.sources.models import ImportRun, ImportStatus

from .package_factory import build_package

pytestmark = pytest.mark.django_db

# The counts read from production on 2026-08-11, read-only.
PRODUCTION_SHAPE = {
    "source_documents": 148,
    "observations": 296,
    "monthly_values": 234,
    "size_movements": 2960,
    "removal_reasons": 435,
}


@pytest.fixture
def production_shaped_history():
    """Seed a history with the same shape production carries."""
    source = ensure_internal_membership_source()
    run = ImportRun.objects.create(
        source=source,
        importer_name="seed",
        schema_version="1.0",
        status=ImportStatus.SUCCEEDED,
        dry_run=False,
    )

    documents = MembershipHistoricalSourceDocument.objects.bulk_create(
        [
            MembershipHistoricalSourceDocument(
                source=source,
                import_run=run,
                external_source_id=f"seed_doc_{i:04d}",
                filename=f"seed-{i:04d}.docx",
                extension=".docx",
                file_sha256=f"{i:064d}",
                observation_date=date(2014 + i % 13, (i % 12) + 1, 5),
                observation_date_precision="day",
            )
            for i in range(PRODUCTION_SHAPE["source_documents"])
        ]
    )

    rows = []
    for i in range(PRODUCTION_SHAPE["observations"]):
        document = documents[i % len(documents)]
        rows.append(
            InternalMembershipObservation(
                source=source,
                import_run=run,
                source_document=document,
                external_snapshot_id=f"seed_snap_{i:04d}",
                observation_date=document.observation_date,
                observation_date_precision="day",
                source_kind=(
                    InternalSourceKind.MERGED_SAME_DOCUMENT
                    if i % 2 == 0
                    else InternalSourceKind.REPORTED_COMPARISON
                ),
                reported_year=document.observation_date.year,
                total_members=3000 + i,
                paid_members=2500 + i,
                new_members_ytd=i,
                removed_members_ytd=i // 2,
                quality_status=QualityStatus.VERIFIED,
                is_preferred_for_date=(i % 2 == 0),
            )
        )
    observations = InternalMembershipObservation.objects.bulk_create(rows)

    MembershipMonthlyNewMemberValue.objects.bulk_create(
        [
            MembershipMonthlyNewMemberValue(
                source=source,
                import_run=run,
                calendar_year=2007 + i // 12,
                calendar_month=(i % 12) + 1,
                new_members=10 + (i % 40),
                value_status=MonthlyValueStatus.VERIFIED,
                source_count=1,
                is_current_for_month=True,
            )
            for i in range(PRODUCTION_SHAPE["monthly_values"])
        ]
    )

    movements = []
    for i, observation in enumerate(observations):
        for direction in ("joined", "removed"):
            for band in list(SizeBand)[:5]:
                movements.append(
                    MembershipSizeMovement(
                        observation=observation,
                        direction=direction,
                        size_band_key=band,
                        member_count=i % 7,
                    )
                )
    MembershipSizeMovement.objects.bulk_create(movements[: PRODUCTION_SHAPE["size_movements"]])

    reasons = []
    for i, observation in enumerate(observations[:145]):
        for key in list(RemovalReasonKey)[:3]:
            reasons.append(
                MembershipRemovalReason(observation=observation, reason_key=key, member_count=i % 5)
            )
    MembershipRemovalReason.objects.bulk_create(reasons[: PRODUCTION_SHAPE["removal_reasons"]])
    return source


def test_the_seed_matches_the_production_shape(production_shaped_history):
    assert MembershipHistoricalSourceDocument.objects.count() == 148
    assert InternalMembershipObservation.objects.count() == 296
    assert MembershipMonthlyNewMemberValue.objects.count() == 234
    assert MembershipSizeMovement.objects.count() == 2960
    assert MembershipRemovalReason.objects.count() == 435


def test_the_rebuild_is_refused_until_superseding_is_asked_for(tmp_path, production_shaped_history):
    """The default must not be to write a second history beside the first."""
    package = build_package(tmp_path / "rebuild.zip", schema_version="2.0")

    with pytest.raises(MembershipHistoryImportError):
        import_history_package(package, dry_run=False)

    assert InternalMembershipObservation.objects.count() == 296
    assert MembershipDecisionBatch.objects.count() == 0


def test_a_dry_run_validates_the_rebuild_without_touching_the_history(
    tmp_path, production_shaped_history
):
    package = build_package(tmp_path / "rebuild.zip", schema_version="2.0")

    result = import_history_package(package, dry_run=True)

    assert result.dry_run is True
    assert InternalMembershipObservation.objects.count() == 296
    assert MembershipDecisionBatch.objects.count() == 0


def test_the_rebuild_supersedes_without_losing_a_single_old_row(
    tmp_path, production_shaped_history
):
    before = {
        pk: (total, paid)
        for pk, total, paid in InternalMembershipObservation.objects.values_list(
            "id", "total_members", "paid_members"
        )
    }
    before_movements = MembershipSizeMovement.objects.count()
    before_reasons = MembershipRemovalReason.objects.count()
    package = build_package(tmp_path / "rebuild.zip", schema_version="2.0")

    result = import_history_package(package, dry_run=False, supersede_previous=True)

    assert result.counts["superseded_observations"] == 296

    # Every old observation still exists, with its reported values intact.
    for pk, (total, paid) in before.items():
        row = InternalMembershipObservation.objects.get(pk=pk)
        assert row.total_members == total
        assert row.paid_members == paid
        assert row.quality_status == QualityStatus.SUPERSEDED
        assert row.is_preferred_for_date is False

    # Their children were never touched.
    assert MembershipSizeMovement.objects.count() >= before_movements
    assert MembershipRemovalReason.objects.count() >= before_reasons

    # And the new evidence arrived.
    assert MembershipDecisionBatch.objects.count() == 2
    assert (
        InternalMembershipObservation.objects.exclude(
            quality_status=QualityStatus.SUPERSEDED
        ).count()
        == 3
    )


def test_repeating_the_rebuild_is_a_no_op(tmp_path, production_shaped_history):
    package = build_package(tmp_path / "rebuild.zip", schema_version="2.0")
    import_history_package(package, dry_run=False, supersede_previous=True)

    after_first = {
        "observations": InternalMembershipObservation.objects.count(),
        "batches": MembershipDecisionBatch.objects.count(),
        "superseded": InternalMembershipObservation.objects.filter(
            quality_status=QualityStatus.SUPERSEDED
        ).count(),
    }

    second = import_history_package(package, dry_run=False, supersede_previous=True)

    assert second.unchanged is True
    assert InternalMembershipObservation.objects.count() == after_first["observations"]
    assert MembershipDecisionBatch.objects.count() == after_first["batches"]
    assert (
        InternalMembershipObservation.objects.filter(
            quality_status=QualityStatus.SUPERSEDED
        ).count()
        == after_first["superseded"]
    )


def test_a_failed_rebuild_leaves_the_old_history_exactly_as_it_was(
    tmp_path, production_shaped_history
):
    """A package that fails partway must not supersede anything."""
    before = InternalMembershipObservation.objects.count()

    def corrupt(payloads):
        payloads["data/decision_batches.csv"] = b"batch_id,wrong_header\n"
        return payloads

    broken = build_package(tmp_path / "broken.zip", schema_version="2.0", mutate_payloads=corrupt)

    with pytest.raises(MembershipHistoryImportError):
        import_history_package(broken, dry_run=False, supersede_previous=True)

    assert InternalMembershipObservation.objects.count() == before
    assert not InternalMembershipObservation.objects.filter(
        quality_status=QualityStatus.SUPERSEDED
    ).exists()
    assert MembershipDecisionBatch.objects.count() == 0
