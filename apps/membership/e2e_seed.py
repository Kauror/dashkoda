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
