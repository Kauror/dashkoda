"""Synthetic membership figures for every source, which are never merged.

The public directory count and the internal board-report history count different
things, so they are seeded separately and stay separately labelled. The roster
composition and the member register are two more sources again.

The register seed is the one that writes invented *members* rather than invented
numbers, and the names are visibly synthetic for that reason — a browser suite
runs on every branch and must never carry anything resembling a real company.
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


def seed_register(today: dt.date) -> str:
    """A synthetic roster and a directory that half agrees with it.

    Written straight into the models rather than through the CSV importer: the
    importer's input is a member list, and generating one on disk during a seed
    would put a file of member-shaped rows on every branch's runner. The names
    here are obviously invented, which is the point — this is the one seed that
    publishes rows a page will print by name.

    The two sources deliberately **disagree**: some members are absent from the
    directory, and the directory publishes a code the roster does not have. A
    seed where the two matched perfectly would leave the comparison's whole
    reason for existing untested, and the empty-difference branch is the one
    that renders without it.
    """
    from django.utils import timezone

    from apps.membership.bootstrap import (
        ensure_member_directory_source,
        ensure_member_register_source,
    )
    from apps.membership.models import (
        MemberDirectoryEntry,
        MemberRegisterEntry,
        MemberRegisterSnapshot,
    )
    from apps.membership.register_import import IMPORTER_NAME, SCHEMA_VERSION
    from apps.sources.services import (
        build_import_run,
        complete_import_run,
        register_external_reference,
        start_import_run,
    )

    source = ensure_member_register_source()
    directory_source = ensure_member_directory_source()

    if MemberRegisterSnapshot.objects.filter(source=source, is_current=True).exists():
        return "liikmete nimekiri: juba olemas"

    snapshot_date = today - dt.timedelta(days=2)
    digest = "e2ereg" + "0" * 58

    artifact = register_external_reference(
        source=source,
        external_reference=f"roster:member-register:{digest}",
        sha256=digest,
        size_bytes=2048,
        mime_type="text/csv",
    )
    run = build_import_run(
        artifact=artifact,
        importer_name=IMPORTER_NAME,
        schema_version=SCHEMA_VERSION,
        dry_run=False,
    )
    start_import_run(run)

    # Enough rows to paginate — the pager is invisible on a single page — and a
    # long name, because a squeezed table cell with one long token is this
    # project's recurring horizontal-overflow bug.
    counties = ("HARJUMAA", "TARTUMAA", "PÄRNUMAA", "IDA-VIRUMAA", "SAAREMAA")
    plan = []
    for index in range(1, 61):
        plan.append(
            {
                "name": f"Sünteetiline Näidisettevõte {index:02d}",
                "code": f"1000{index:04d}",
                "county": counties[index % len(counties)],
                "status": "regular" if index % 12 else "suspended",
            }
        )
    plan[0]["name"] = "Sünteetiline Näidisettevõte Pikkanimegakontrollimiseks Väga Pikk Ärinimi 01"

    snapshot = MemberRegisterSnapshot.objects.create(
        source=source,
        import_run=run,
        snapshot_date=snapshot_date,
        source_sha256=digest,
        source_row_count=len(plan),
        is_current=True,
    )
    MemberRegisterEntry.objects.bulk_create(
        [
            MemberRegisterEntry(
                snapshot=snapshot,
                name=row["name"],
                legal_form="OÜ" if index % 5 else "AS",
                member_number=str(2000 + index),
                status_key=row["status"],
                status_label="Koja liige" if row["status"] == "regular" else "Peatatud liige",
                registry_code=row["code"],
                county=row["county"],
                city="TALLINN",
                country="EESTI",
                # One member with no headcount, so the table has to draw the
                # "not reported" dash rather than a zero.
                employees=None if index == 3 else 4 + index,
                membership_start=snapshot_date - dt.timedelta(days=400 + index * 5),
                nace_code="70201",
                nace_label="Äri- ja muu juhtimisalane nõustamine",
                website=f"www.naidis{index:02d}.ee",
            )
            for index, row in enumerate(plan, start=1)
        ],
        batch_size=200,
    )
    complete_import_run(run, rows_added=len(plan) + 1)

    # The directory publishes all but the last three, plus one code the roster
    # has never heard of — so both sides of the comparison have something to
    # list and neither list is empty.
    now = timezone.now()
    published = [row["code"] for row in plan[:-3]] + ["19999999"]
    MemberDirectoryEntry.objects.bulk_create(
        [
            MemberDirectoryEntry(
                source=directory_source,
                registry_code=code,
                profile_path=f"/et/liige/sunteetiline-naidisettevote-{code}",
                first_seen_at=now - dt.timedelta(days=30),
                last_seen_at=now,
                is_published=True,
            )
            for code in published
        ],
        batch_size=200,
    )
    return f"liikmete nimekiri: {len(plan)} kirjet, kataloogis {len(published)}"


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


def seed_composition(today: dt.date) -> str:
    """Synthetic aggregate composition, so the browser suite can see the view.

    Written straight into the composition models rather than through the roster
    importer, because the importer's input is a member list and no member list —
    not even an invented one — belongs in a seed that runs on every branch.
    There is no company name here to be synthetic *about*: the models hold size
    classes, counties, sectors, tenure bands and joining years, and those are
    exactly what is written.

    Before this existed the composition focus was not offered at all in a seeded
    environment, so a green browser suite proved only that the parts worked
    rather than that anything reached them — the same blind spot that once hid
    the decision section on this page.
    """
    from apps.membership.bootstrap import ensure_membership_composition_source
    from apps.membership.composition import (
        MEMBERSHIP_COMPOSITION_MAPPING_VERSION,
        MEMBERSHIP_SECTOR_MAPPING_VERSION,
        Dimension,
        Population,
        category_label,
    )
    from apps.membership.composition_import import IMPORTER_NAME, SCHEMA_VERSION
    from apps.membership.models import (
        MembershipCompositionSnapshot,
        MembershipCompositionValue,
    )
    from apps.sources.services import (
        build_import_run,
        complete_import_run,
        register_external_reference,
        start_import_run,
    )

    snapshot_date = today - dt.timedelta(days=14)
    source = ensure_membership_composition_source()

    if MembershipCompositionSnapshot.objects.filter(source=source, is_current=True).exists():
        return "liikmeskonna koosseis: juba olemas"

    # A fixed synthetic digest. It is not the hash of anything, and it is
    # obviously not one: an invented reading needs an identity, not a claim that
    # some file produced it.
    digest = "e2e" + "0" * 61

    artifact = register_external_reference(
        source=source,
        external_reference=f"roster:membership-composition:{digest}",
        sha256=digest,
        size_bytes=1024,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    run = build_import_run(
        artifact=artifact,
        importer_name=IMPORTER_NAME,
        schema_version=SCHEMA_VERSION,
        dry_run=False,
    )
    start_import_run(run)

    # Counts that add to the same total in every dimension, because a dimension
    # whose parts did not reconcile with its denominator is exactly what the
    # importer refuses and the page should never have to render.
    total = 400
    recent = 40
    plan: dict[str, dict[str, tuple[int, int]]] = {
        Dimension.STATUS: {
            "regular": (380, 38),
            "suspended": (12, 1),
            "supporter": (8, 1),
        },
        Dimension.LEGAL_FORM: {"ou": (300, 32), "as": (86, 7), "mtu": (14, 1)},
        Dimension.EMPLOYEE_SIZE: {
            "employees_0": (6, 1),
            "employees_1_9": (188, 21),
            "employees_10_49": (132, 12),
            "employees_50_249": (56, 5),
            "employees_250_plus": (18, 1),
        },
        Dimension.REGION: {
            "harjumaa": (250, 26),
            "tartumaa": (60, 6),
            "parnumaa": (34, 4),
            "ida-virumaa": (28, 2),
            "saaremaa": (18, 1),
            "unknown": (10, 1),
        },
        Dimension.SECTOR: {
            "G": (120, 10),
            "C": (96, 5),
            "M": (54, 9),
            "F": (40, 4),
            "J": (34, 8),
            "H": (26, 2),
            "N": (18, 1),
            "unknown": (12, 1),
        },
        Dimension.TENURE_BAND: {
            "under_1": (40, 40),
            "years_1_2": (52, 0),
            "years_3_5": (74, 0),
            "years_6_10": (86, 0),
            "years_11_20": (78, 0),
            "years_20_plus": (70, 0),
        },
        Dimension.JOIN_COHORT: {
            str(snapshot_date.year - offset): (count, recent if offset == 0 else 0)
            for offset, count in enumerate((40, 44, 38, 42, 36, 40, 34, 30, 28, 26))
        },
    }
    # The oldest cohorts, folded into one bucket the chart will draw as "enne".
    plan[Dimension.JOIN_COHORT][str(snapshot_date.year - 22)] = (42, 0)

    snapshot = MembershipCompositionSnapshot.objects.create(
        source=source,
        import_run=run,
        snapshot_date=snapshot_date,
        source_sha256=digest,
        source_row_count=total,
        mapping_version=MEMBERSHIP_COMPOSITION_MAPPING_VERSION,
        sector_mapping_version=MEMBERSHIP_SECTOR_MAPPING_VERSION,
        median_tenure_days=3650,
        coverage_pct={dimension: "100.0" for dimension in plan},
        is_current=True,
    )

    rows = []
    for dimension, categories in plan.items():
        for key, (overall, recent_count) in categories.items():
            rows.append(
                MembershipCompositionValue(
                    snapshot=snapshot,
                    population=Population.ALL_CURRENT,
                    dimension=dimension,
                    category_key=key,
                    category_label=category_label(dimension, key),
                    member_count=overall,
                )
            )
            if recent_count:
                rows.append(
                    MembershipCompositionValue(
                        snapshot=snapshot,
                        population=Population.RECENT_JOINERS,
                        dimension=dimension,
                        category_key=key,
                        category_label=category_label(dimension, key),
                        member_count=recent_count,
                    )
                )
    MembershipCompositionValue.objects.bulk_create(rows, batch_size=200)
    complete_import_run(run, rows_added=len(rows) + 1)

    return f"liikmeskonna koosseis: {total} liiget, {recent} hiljuti liitunut"
