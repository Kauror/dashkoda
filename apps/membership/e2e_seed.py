"""Synthetic membership figures for both sources, which are never merged.

The public directory count and the internal board-report history count different
things, so they are seeded separately and stay separately labelled.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal


def seed_public() -> str:
    from apps.core.canonical import canonical_checksum
    from apps.membership.collector import MembershipCollection
    from apps.membership.sync import synchronize_membership

    def collection(total: int) -> MembershipCollection:
        canonical = {
            "dataset": "koda-public-members",
            "schema_version": "1.0",
            "total_members": total,
        }
        checksum, size = canonical_checksum(canonical)
        return MembershipCollection(
            total_members=total,
            sha256=checksum,
            size_bytes=size,
            canonical=canonical,
            etag="",
            last_modified="",
            duplicate_identities=0,
            rejected_rows=0,
        )

    # Two readings, so the directory count has a predecessor to compare with.
    # Both are four-digit, which is what makes grouped-number formatting visible.
    results = []
    for total in (4187, 4203):
        outcome = synchronize_membership(collector=lambda total=total, **_kwargs: collection(total))
        results.append(outcome.result)
    return f"liikmed (avalik): {results[-1]} (4203)"


def seed_internal(today: dt.date) -> str:
    from apps.membership.manual import ManualReport, publish_manual_report
    from apps.membership.quality import MetricFacts

    # Six readings across roughly a year, so both overview trend lines have
    # enough points to draw. Values move plausibly and are all invented.
    plan = [
        (330, 4050, 3810, "1180000.00", "1300000.00", 210, 41, 12),
        (270, 4090, 3860, "1205500.00", "1300000.00", 260, 38, 18),
        (210, 4120, 3905, "1231000.50", "1300000.00", 300, 35, 24),
        (150, 4150, 3950, "1252750.25", "1310000.00", 340, 0, 31),
        (90, 4176, 3988, "1268400.00", "1310000.00", 372, 29, 37),
        (30, 4203, 4025, "1276101.00", "1310000.00", 401, None, 44),
    ]
    # Monthly arrivals, so the recruitment chart has a subject year and an
    # earlier one to draw behind it.
    #
    # A report may only fill months up to its own observation date — a board
    # report cannot state how many joined in a month that has not happened when
    # it was written — so each report carries its own year up to its own month
    # and the series is built across the six of them.
    #
    # February is an explicit `0` and March is left out entirely. The chart has
    # to keep "nobody joined" apart from "nobody reported", and only a seed
    # carrying both shapes can prove that it does.
    latest_date = today - dt.timedelta(days=plan[-1][0])

    def monthly_for(when: dt.date) -> dict[int, int]:
        if when.year < latest_date.year:
            return {number: 18 + number for number in range(1, when.month + 1)}
        return {
            number: (0 if number == 2 else 20 + number)
            for number in range(1, when.month + 1)
            if number != 3
        }

    # Movements and removal reasons ride on the newest report, which is the one
    # the movement section describes. Every band reports both directions except
    # the largest, which reports only arrivals — a band with one side missing
    # must show no net rather than a net that counts a gap as zero.
    #
    # Neither table is marked complete, and that is the honest flag rather than
    # a way around the cross-checks: a table missing one band's departures is a
    # partial table. `publish_manual_report` only reconciles these sums against
    # the year-to-date figures when the report claims completeness, which is the
    # right rule — a partly filled table is an ordinary thing to have and must
    # not be rejected for failing to add up.
    size_joined = {
        "employees_1_4": 21,
        "employees_20_49": 27,
        "employees_100_249": 14,
        "employees_250_499": 8,
    }
    size_removed = {
        "employees_1_4": 38,
        "employees_20_49": 22,
        "employees_100_249": 11,
    }
    reasons = {
        "dissolved_bankrupt_merged_inactive_missing": 120,
        "voluntary_debt_financial_or_other": 84,
        "voluntary_no_service_value": 31,
    }

    published = 0
    for offset, total, paid, received, budget, new_ytd, suspended, removed in plan:
        when = today - dt.timedelta(days=offset)
        is_latest = offset == plan[-1][0]
        report = ManualReport(
            observation_date=when,
            reported_year=when.year,
            document_title="Sünteetiline juhatuse aruanne",
            source_note="Sünteetiline seeme, mitte tegelik aruanne.",
            monthly_year=when.year,
            monthly_new_members=monthly_for(when),
            joined_by_band=size_joined if is_latest else {},
            removed_by_band=size_removed if is_latest else {},
            size_table_complete=False,
            removal_reasons=reasons if is_latest else {},
            reasons_complete=False,
            facts=MetricFacts(
                total_members=total,
                paid_members=paid,
                membership_fees_received_eur=Decimal(received),
                membership_fee_budget_eur=Decimal(budget),
                # Left unreported on purpose: the page then shows the computed
                # percentage and says which basis it used.
                membership_fee_collection_pct_reported=None,
                new_members_ytd=new_ytd,
                # `0` on one reading and `None` on another, so the interface has
                # to distinguish "nobody was suspended" from "nobody counted".
                suspended_members=suspended,
                removed_members_ytd=removed,
            ),
        )
        publish_manual_report(report)
        published += 1
    return f"liikmeskonna aruanded (sisemine): {published} vaatlust"


def seed_decision_batches(today: dt.date) -> str:
    """Two board-decision batches, so `section-decisions` is actually drawn.

    There is no manual form for a batch — they arrive only through the schema
    2.0 import — so this writes them directly, the same way the import does.

    Without this the section is invisible to the browser suite, which is the
    blind spot that hid the website-traffic section until it was seeded: a
    green run proves the parts work, not that anything reaches them.

    The two dates differ on purpose. The appendix is compiled on one day and the
    board signs on another, and a batch whose label collapsed them would pass a
    test that only ever saw one date.
    """
    from apps.membership.bootstrap import ensure_internal_membership_source
    from apps.membership.models import (
        BatchDepartureReasonKey,
        DecisionBatchKind,
        MembershipDecisionBatch,
        MembershipDecisionBatchReason,
        MembershipDecisionBatchSizeMovement,
        QualityStatus,
        SizeBand,
    )
    from apps.sources.models import SourceArtifact
    from apps.sources.services import (
        build_import_run,
        complete_import_run,
        register_external_reference,
        start_import_run,
    )

    source = ensure_internal_membership_source()

    # The seed is run twice in the same database by design, and a second run
    # must publish nothing new. Batches are immutable once written, so the
    # second pass returns rather than trying to write them again.
    existing = MembershipDecisionBatch.objects.filter(
        source=source, external_batch_id__startswith="seed_batch_"
    ).count()
    if existing:
        return f"juhatuse otsuste partiid: {existing} (juba olemas)"

    # The same bytes under one source are one artifact, and registering a
    # duplicate is refused — which is what a second seed run would otherwise do.
    artifact = SourceArtifact.objects.filter(source=source, sha256="d" * 64).first()
    if artifact is None:
        artifact = register_external_reference(
            source=source,
            external_reference="synthetic:membership-decision-batches",
            original_name="synthetic-batches.zip",
            mime_type="application/zip",
            sha256="d" * 64,
            size_bytes=12,
        )
    # Through the lifecycle rather than straight to a terminal status: two check
    # constraints require `started_at` and `finished_at` on a finished run.
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

    as_of = today - dt.timedelta(days=30)
    decided = today - dt.timedelta(days=23)

    plan = (
        (
            DecisionBatchKind.TERMINATION,
            17,
            # Includes both new bands, so the page has to render a supporter and
            # an unknown size rather than only employee counts.
            {
                SizeBand.EMPLOYEES_1_4: 8,
                SizeBand.EMPLOYEES_5_9: 4,
                SizeBand.EMPLOYEES_20_49: 3,
                SizeBand.GROUP_COMPANY: 1,
                SizeBand.UNKNOWN: 1,
            },
            {
                BatchDepartureReasonKey.FINANCIAL: 7,
                BatchDepartureReasonKey.NO_SERVICE_VALUE: 5,
                BatchDepartureReasonKey.LIQUIDATION: 3,
                BatchDepartureReasonKey.OTHER: 2,
            },
        ),
        (
            DecisionBatchKind.SUSPENSION,
            4,
            {SizeBand.EMPLOYEES_1_4: 3, SizeBand.SUPPORTER: 1},
            {BatchDepartureReasonKey.ACTIVITY_CEASED: 4},
        ),
    )

    seeded = 0
    for kind, count, sizes, reasons in plan:
        batch = MembershipDecisionBatch.objects.create(
            source=source,
            import_run=run,
            external_batch_id=f"seed_batch_{kind}",
            batch_kind=kind,
            as_of_date=as_of,
            decision_date=decided,
            decision_reference="otsus nr 4",
            member_count=count,
            quality_status=QualityStatus.VERIFIED,
        )
        for band, number in sizes.items():
            MembershipDecisionBatchSizeMovement.objects.create(
                batch=batch, size_band_key=band, member_count=number
            )
        for key, number in reasons.items():
            MembershipDecisionBatchReason.objects.create(
                batch=batch, reason_key=key, member_count=number
            )
        seeded += 1
    return f"juhatuse otsuste partiid: {seeded}"
