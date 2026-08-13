"""Decision-batch evidence, from selector to rendered page.

The rule these tests exist to hold: a batch is what one board decision did, and
it must never be presented as, added to, or drawn beside a year-to-date figure
as though the two were one series.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.membership.charts import (
    decision_batch_reasons_chart,
    decision_batch_sizes_chart,
)
from apps.membership.internal_selectors import (
    get_decision_batches,
    get_monthly_size_distribution,
    get_new_member_periods,
)
from apps.membership.models import (
    BatchDepartureReasonKey,
    DecisionBatchKind,
    MembershipDecisionBatch,
    MembershipDecisionBatchReason,
    MembershipDecisionBatchSizeMovement,
    MembershipNewMemberPeriod,
    MembershipNewMemberSizeDistribution,
    NewMemberPeriodScope,
    QualityStatus,
    SizeBand,
)
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    register_external_reference,
    start_import_run,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def batch(internal_source):
    # An import run carries the artifact it read and must reach `succeeded`
    # through its lifecycle: `artifact` is non-null and two check constraints
    # require `started_at` and `finished_at`. Creating one straight into a
    # terminal state cannot produce a legal row — the same mistake that was
    # already corrected in `test_isolated_history_import`.
    artifact = register_external_reference(
        source=internal_source,
        external_reference="synthetic:decision-batch-presentation",
        original_name="synthetic-batches.zip",
        mime_type="application/zip",
        sha256="c" * 64,
        size_bytes=11,
    )
    run = complete_import_run(
        start_import_run(
            build_import_run(
                artifact=artifact,
                importer_name="seed",
                schema_version="2.0",
                dry_run=False,
            )
        )
    )
    row = MembershipDecisionBatch.objects.create(
        source=internal_source,
        import_run=run,
        external_batch_id="batch_seed_1",
        batch_kind=DecisionBatchKind.TERMINATION,
        as_of_date=date(2026, 8, 12),
        decision_date=date(2026, 8, 19),
        decision_reference="otsus nr 6",
        member_count=15,
        quality_status=QualityStatus.VERIFIED,
    )
    # Deliberately created out of canonical order.
    for band, n in (
        (SizeBand.EMPLOYEES_5_9, 4),
        (SizeBand.EMPLOYEES_1_4, 7),
        (SizeBand.UNKNOWN, 1),
        (SizeBand.EMPLOYEES_10_19, 3),
    ):
        MembershipDecisionBatchSizeMovement.objects.create(
            batch=row, size_band_key=band, member_count=n
        )
    for key, n in (
        (BatchDepartureReasonKey.FINANCIAL, 3),
        (BatchDepartureReasonKey.NO_SERVICE_VALUE, 8),
        (BatchDepartureReasonKey.OTHER, 4),
    ):
        MembershipDecisionBatchReason.objects.create(batch=row, reason_key=key, member_count=n)
    return row


def test_a_batch_is_returned_with_both_of_its_dates(batch):
    (found,) = get_decision_batches()

    assert found.member_count == 15
    assert found.as_of_date == date(2026, 8, 12)
    assert found.decision_date == date(2026, 8, 19)
    assert found.reference == "otsus nr 6"


def test_sizes_come_back_in_canonical_band_order_not_by_count(batch):
    (found,) = get_decision_batches()

    assert [row["band"] for row in found.sizes] == [
        SizeBand.EMPLOYEES_1_4,
        SizeBand.EMPLOYEES_5_9,
        SizeBand.EMPLOYEES_10_19,
        SizeBand.UNKNOWN,
    ]


def test_reasons_come_back_largest_first_with_shares(batch):
    (found,) = get_decision_batches()

    assert [row["count"] for row in found.reasons] == [8, 4, 3]
    assert sum(row["count"] for row in found.reasons) == 15
    assert found.reasons[0]["share_pct"] is not None


def test_the_unknown_band_is_shown_rather_than_dropped(batch):
    (found,) = get_decision_batches()

    assert SizeBand.UNKNOWN in [row["band"] for row in found.sizes]


def test_a_superseded_batch_is_not_returned(batch):
    MembershipDecisionBatch.objects.filter(pk=batch.pk).update(
        quality_status=QualityStatus.SUPERSEDED
    )

    assert get_decision_batches() == ()


def test_no_batch_in_a_window_returns_nothing_not_a_zero(batch):
    found = get_decision_batches(date_from=date(2020, 1, 1), date_to=date(2020, 12, 31))

    assert found == ()


def test_the_charts_name_the_decision_and_both_dates(batch):
    (found,) = get_decision_batches()
    reasons = decision_batch_reasons_chart(found)
    sizes = decision_batch_sizes_chart(found)

    assert "12.08.2026" in reasons.observation_label
    assert "19.08.2026" in reasons.observation_label
    assert "otsus nr 6" in reasons.observation_label
    assert reasons.has_data and sizes.has_data
    assert reasons.payload_id != sizes.payload_id


def test_the_chart_never_calls_a_batch_a_year_to_date_figure(batch):
    (found,) = get_decision_batches()
    text = " ".join(
        [
            decision_batch_reasons_chart(found).summary,
            decision_batch_sizes_chart(found).summary,
        ]
    ).lower()

    assert "aasta algusest" not in text
    assert "otsuses" in text


def test_a_multi_month_period_is_returned_whole(internal_source):
    run = complete_import_run(
        start_import_run(
            build_import_run(
                artifact=register_external_reference(
                    source=internal_source,
                    external_reference="synthetic:new-member-period",
                    original_name="synthetic-period.zip",
                    mime_type="application/zip",
                    sha256="b" * 64,
                    size_bytes=9,
                ),
                importer_name="seed",
                schema_version="2.0",
                dry_run=False,
            )
        )
    )
    period = MembershipNewMemberPeriod.objects.create(
        source=internal_source,
        import_run=run,
        external_period_id="period_seed_1",
        period_scope=NewMemberPeriodScope.MULTI_MONTH,
        period_start=date(2025, 6, 1),
        period_end=date(2025, 7, 31),
        new_members=25,
    )
    MembershipNewMemberSizeDistribution.objects.create(
        period=period, size_band_key=SizeBand.EMPLOYEES_1_4, member_count=25
    )

    (found,) = get_new_member_periods()

    assert found.new_members == 25
    assert found.period_start == date(2025, 6, 1)
    assert found.period_end == date(2025, 7, 31)
    assert found.sizes[0]["count"] == 25


def test_a_month_with_no_distribution_returns_nothing(internal_source):
    assert get_monthly_size_distribution(2026, 8) == ()


def test_the_page_renders_the_decision_section(viewer_client, batch):
    response = viewer_client.get("/liikmeskond/")

    assert response.status_code == 200
    body = response.content.decode()
    assert "section-decisions" in body
    assert "Juhatuse otsused" in body
    # The caveat is on the page, not only in a code comment.
    assert "aasta algusest kogunenud arv" in body


def test_the_chart_bundle_loads_when_only_a_batch_has_data(viewer_client, batch):
    response = viewer_client.get("/liikmeskond/")

    assert "build/charts.js" in response.content.decode()
