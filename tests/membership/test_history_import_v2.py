"""Importing schema 2.0, and refusing to write a second history over the first.

These need PostgreSQL and therefore run in CI only. Everything is synthetic.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.membership.history_import import (
    MembershipHistoryImportError,
    import_history_package,
)
from apps.membership.models import (
    InternalMembershipObservation,
    MembershipDecisionBatch,
    MembershipDecisionBatchReason,
    MembershipDecisionBatchSizeMovement,
    MembershipMonthlyNewMemberValue,
    MembershipNewMemberPeriod,
    MembershipNewMemberSizeDistribution,
    QualityStatus,
    SizeBand,
)

from .package_factory import (
    BATCH_SUSPENSION,
    BATCH_TERMINATION,
    PERIOD_SUMMER,
    build_package,
    default_decision_batches,
    default_new_member_sizes,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def v2_package(tmp_path):
    return build_package(tmp_path / "v2.zip", schema_version="2.0")


@pytest.fixture
def imported_v2(v2_package):
    return import_history_package(v2_package, dry_run=False)


# --------------------------------------------------------------------------
# The new tables arrive
# --------------------------------------------------------------------------


def test_a_two_dot_zero_import_writes_every_new_table(imported_v2):
    assert MembershipDecisionBatch.objects.count() == 2
    assert MembershipDecisionBatchSizeMovement.objects.count() == 4
    assert MembershipDecisionBatchReason.objects.count() == 3
    assert MembershipNewMemberPeriod.objects.count() == 1
    assert MembershipNewMemberSizeDistribution.objects.count() == 3


def test_a_dry_run_writes_no_batch(v2_package):
    import_history_package(v2_package, dry_run=True)

    assert MembershipDecisionBatch.objects.count() == 0
    assert MembershipNewMemberPeriod.objects.count() == 0


def test_the_two_dates_survive_the_import(imported_v2):
    """The appendix's evidence date and the board's signing date are separate."""
    batch = MembershipDecisionBatch.objects.get(external_batch_id=BATCH_TERMINATION)

    assert batch.as_of_date == date(2024, 1, 4)
    assert batch.decision_date == date(2024, 1, 11)


def test_a_batch_is_not_attached_to_any_observation(imported_v2):
    """A batch count is not a year-to-date figure and owns no observation."""
    batch = MembershipDecisionBatch.objects.get(external_batch_id=BATCH_TERMINATION)

    assert not hasattr(batch, "observation")
    assert not [f for f in batch._meta.get_fields() if f.name == "observation"]


def test_termination_and_suspension_stay_separate(imported_v2):
    termination = MembershipDecisionBatch.objects.get(external_batch_id=BATCH_TERMINATION)
    suspension = MembershipDecisionBatch.objects.get(external_batch_id=BATCH_SUSPENSION)

    assert termination.member_count == 6
    assert suspension.member_count == 4
    assert termination.batch_kind != suspension.batch_kind


def test_the_new_size_bands_reach_the_database(imported_v2):
    bands = set(
        MembershipDecisionBatchSizeMovement.objects.filter(
            batch__external_batch_id=BATCH_TERMINATION
        ).values_list("size_band_key", flat=True)
    )

    assert SizeBand.GROUP_COMPANY in bands
    assert SizeBand.UNKNOWN in bands


def test_a_corroborating_document_is_linked_when_the_package_names_one(imported_v2):
    termination = MembershipDecisionBatch.objects.get(external_batch_id=BATCH_TERMINATION)
    suspension = MembershipDecisionBatch.objects.get(external_batch_id=BATCH_SUSPENSION)

    assert termination.corroborating_document is not None
    assert suspension.corroborating_document is None


def test_a_batch_reason_holds_no_raw_text(imported_v2):
    """The reason free text can name another company; no field may hold it."""
    names = {f.name for f in MembershipDecisionBatchReason._meta.get_fields()}

    assert not [n for n in names if "raw" in n or "label" in n]


def test_a_multi_month_span_is_not_written_into_the_monthly_table(imported_v2):
    period = MembershipNewMemberPeriod.objects.get(external_period_id=PERIOD_SUMMER)

    assert period.period_start == date(2024, 6, 1)
    assert period.period_end == date(2024, 7, 31)
    assert period.new_members == 9
    assert not MembershipMonthlyNewMemberValue.objects.filter(
        calendar_year=2024, calendar_month__in=(6, 7)
    ).exists()


def test_a_size_distribution_finds_its_month_or_its_period(imported_v2):
    rows = MembershipNewMemberSizeDistribution.objects.all()

    for row in rows:
        assert (row.monthly_value is None) != (row.period is None)

    monthly = rows.filter(monthly_value__isnull=False)
    assert monthly.count() == 2
    assert monthly.first().monthly_value.calendar_year == 2024

    period_rows = rows.filter(period__isnull=False)
    assert period_rows.count() == 1
    assert period_rows.first().period.external_period_id == PERIOD_SUMMER


def test_an_imported_batch_refuses_every_change(imported_v2):
    from apps.membership.models import InternalObservationImmutable

    batch = MembershipDecisionBatch.objects.get(external_batch_id=BATCH_TERMINATION)
    batch.member_count = 99
    with pytest.raises(InternalObservationImmutable):
        batch.save()


# --------------------------------------------------------------------------
# Backward compatibility and idempotency
# --------------------------------------------------------------------------


def test_a_one_dot_zero_package_still_imports_and_writes_no_batch(tmp_path):
    result = import_history_package(
        build_package(tmp_path / "v1.zip", schema_version="1.0"), dry_run=False
    )

    assert result.unchanged is False
    assert InternalMembershipObservation.objects.count() == 3
    assert MembershipDecisionBatch.objects.count() == 0
    assert MembershipNewMemberPeriod.objects.count() == 0


def test_repeating_the_identical_import_changes_nothing(v2_package):
    first = import_history_package(v2_package, dry_run=False)
    before = MembershipDecisionBatch.objects.count()

    second = import_history_package(v2_package, dry_run=False)

    assert first.unchanged is False
    assert second.unchanged is True
    assert MembershipDecisionBatch.objects.count() == before
    assert MembershipNewMemberSizeDistribution.objects.count() == 3


# --------------------------------------------------------------------------
# The second-history guard
# --------------------------------------------------------------------------


def test_a_different_package_is_refused_over_an_existing_history(tmp_path, imported_v2):
    """Without this the rebuild would double the history instead of replacing it."""
    other = build_package(
        tmp_path / "other.zip",
        schema_version="2.0",
        decision_batches=[
            {**default_decision_batches()[0], "member_count": "7"},
            default_decision_batches()[1],
        ],
    )

    with pytest.raises(MembershipHistoryImportError) as error:
        import_history_package(other, dry_run=False)

    assert "supersede" in str(error.value).lower()


def test_the_refusal_leaves_the_existing_history_untouched(tmp_path, imported_v2):
    before_observations = InternalMembershipObservation.objects.count()
    before_batches = MembershipDecisionBatch.objects.count()
    other = build_package(
        tmp_path / "other.zip",
        schema_version="2.0",
        new_member_sizes=default_new_member_sizes()[:2],
    )

    with pytest.raises(MembershipHistoryImportError):
        import_history_package(other, dry_run=False)

    assert InternalMembershipObservation.objects.count() == before_observations
    assert MembershipDecisionBatch.objects.count() == before_batches
    assert not InternalMembershipObservation.objects.filter(
        quality_status=QualityStatus.SUPERSEDED
    ).exists()


def test_a_dry_run_is_never_blocked_by_an_existing_history(tmp_path, imported_v2):
    other = build_package(
        tmp_path / "other.zip",
        schema_version="2.0",
        new_member_sizes=default_new_member_sizes()[:2],
    )

    result = import_history_package(other, dry_run=True)

    assert result.dry_run is True


def test_superseding_keeps_every_old_row_and_its_values(tmp_path, imported_v2):
    old = list(InternalMembershipObservation.objects.values_list("id", "total_members"))
    other = build_package(
        tmp_path / "other.zip",
        schema_version="2.0",
        new_member_sizes=default_new_member_sizes()[:2],
    )

    result = import_history_package(other, dry_run=False, supersede_previous=True)

    assert result.counts["superseded_observations"] == len(old)
    for pk, total in old:
        row = InternalMembershipObservation.objects.get(pk=pk)
        # Nothing was deleted and no reported value was rewritten.
        assert row.total_members == total
        assert row.quality_status == QualityStatus.SUPERSEDED
        assert row.is_preferred_for_date is False

    # The replacement is present and preferred.
    assert (
        InternalMembershipObservation.objects.exclude(
            quality_status=QualityStatus.SUPERSEDED
        ).count()
        == 3
    )


def test_superseding_does_not_delete_the_old_batches(tmp_path, imported_v2):
    before = MembershipDecisionBatch.objects.count()
    other = build_package(
        tmp_path / "other.zip",
        schema_version="2.0",
        new_member_sizes=default_new_member_sizes()[:2],
    )

    import_history_package(other, dry_run=False, supersede_previous=True)

    assert MembershipDecisionBatch.objects.count() == before * 2
