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


# Board decisions live under their own focus now: the page is five views
# behind one URL and `fookus` names which one is drawn. A test that asked for
# the bare path would be asserting about the overview, which does not draw
# them — deliberately, because a batch is one decision's own list and is not
# addable to anything the overview shows.
MOVEMENT_VIEW = "/liikmeskond/?fookus=liikumine"


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


def test_the_page_no_longer_renders_the_decision_section(viewer_client, batch):
    """`Juhatuse otsused` — the section, the picker, both decision-scoped
    charts — left this focus on 2026-08-17. `get_decision_batches`,
    `decision_batch_reasons_chart` and `decision_batch_sizes_chart` are still
    tested above, directly; nothing on the page composes them now."""
    response = viewer_client.get(MOVEMENT_VIEW)

    assert response.status_code == 200
    body = response.content.decode()
    assert "section-decisions" not in body
    assert "Juhatuse otsused" not in body
    assert "aasta algusest kogunenud arv" not in body
    # The URL parameter that used to choose a decision is inert now, not an
    # error — the same rule a stale bookmark gets everywhere on this page.
    response = viewer_client.get(f"{MOVEMENT_VIEW}&otsus=2026-08-12")
    assert response.status_code == 200
    assert "section-decisions" not in response.content.decode()


def _second_decision(internal_source, batch):
    """A second, older decision so the control has something to choose between.

    It carries both distributions, because every batch in production does: they
    are counted from the appendix's own member rows, so a batch without them
    does not occur. A childless fixture made this decision render no charts at
    all, and the picker then appeared not to work.
    """
    row = MembershipDecisionBatch.objects.create(
        source=internal_source,
        import_run=batch.import_run,
        external_batch_id="batch_seed_2",
        batch_kind=DecisionBatchKind.TERMINATION,
        as_of_date=date(2025, 3, 10),
        decision_date=date(2025, 3, 17),
        decision_reference="otsus nr 2",
        member_count=4,
        quality_status=QualityStatus.VERIFIED,
    )
    MembershipDecisionBatchSizeMovement.objects.create(
        batch=row, size_band_key=SizeBand.EMPLOYEES_1_4, member_count=4
    )
    MembershipDecisionBatchReason.objects.create(
        batch=row, reason_key=BatchDepartureReasonKey.FINANCIAL, member_count=4
    )
    return row


def test_the_page_is_unchanged_by_a_second_decision_existing(viewer_client, internal_source, batch):
    """The picker, the per-decision draw and the fallback-on-unknown rule all
    left with the section. What's left to check is that a second decision
    existing does not somehow bring any of that back."""
    _second_decision(internal_source, batch)

    for url in (
        MOVEMENT_VIEW,
        f"{MOVEMENT_VIEW}&otsus=2025-03-10",
        f"{MOVEMENT_VIEW}&otsus=1999-01-01",
    ):
        response = viewer_client.get(url)
        assert response.status_code == 200
        body = response.content.decode()
        assert "section-decisions" not in body
        assert "otsus=" not in body
