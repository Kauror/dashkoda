"""Builds synthetic historical import packages for the tests.

Everything here is invented. No Chamber document, no real member figure and no
part of the approved package is reproduced — the tests check the *contract*, and
a contract can be checked with made-up numbers.

The builder produces a package that passes every check by default, and exposes
enough seams to break exactly one thing at a time: a wrong digest, a wrong size,
a missing file, an undeclared extra, a traversing member name, a malformed CSV,
a wrong header, a dangling foreign key or a duplicate identifier.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from apps.membership.package import REQUIRED_HEADERS

PACKAGE_ROOT = "dashkoda-membership-history-import-package"

SOURCE_A = "src_aaaa000000000001"
SOURCE_B = "src_bbbb000000000002"
SNAP_A_DIRECT = "snap_aaaa000000000001"
SNAP_B_DIRECT = "snap_bbbb000000000002"
SNAP_B_COMPARISON = "snap_bbbb000000000003"

PATH_A = "Juhatus 2024/Jaanuar/liikmeskond_2024.docx"
PATH_B = "Juhatus 2025/Jaanuar/liikmeskond_2025.docx"


def _row(header: tuple[str, ...], values: dict) -> dict:
    """A full row for `header`, with anything unspecified left blank."""
    return {name: values.get(name, "") for name in header}


def _csv_bytes(path: str, rows: list[dict]) -> bytes:
    header = REQUIRED_HEADERS[path]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(header), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(_row(header, row))
    return buffer.getvalue().encode("utf-8")


def default_source_documents() -> list[dict]:
    return [
        {
            "source_id": SOURCE_A,
            "relative_path": PATH_A,
            "filename": "liikmeskond_2024.docx",
            "extension": ".docx",
            "file_sha256": "a" * 64,
            "file_size_bytes": "1024",
            "filesystem_modified_at": "2024-01-10T09:00:00+00:00",
            "year_folder": "Juhatus 2024",
            "month_folder": "Jaanuar",
            "candidate_reason": "filename contains 'liikmeskond'",
            "extraction_status": "ok",
            "observation_date": "2024-01-10",
            "observation_date_precision": "day",
            "date_source": "document_heading",
            "date_confidence": "high",
            "document_title": "Liikmeskonnast 2024",
            "document_year_claim": "2024",
            "warning_codes": "",
            "notes": "",
        },
        {
            "source_id": SOURCE_B,
            "relative_path": PATH_B,
            "filename": "liikmeskond_2025.docx",
            "extension": ".docx",
            "file_sha256": "b" * 64,
            "file_size_bytes": "2048",
            "filesystem_modified_at": "2025-01-15T09:00:00+00:00",
            "year_folder": "Juhatus 2025",
            "month_folder": "Jaanuar",
            "candidate_reason": "filename contains 'liikmeskond'",
            "extraction_status": "ok",
            "observation_date": "2025-01-15",
            "observation_date_precision": "day",
            "date_source": "document_heading",
            "date_confidence": "high",
            "document_title": "Liikmeskonnast 2025",
            "document_year_claim": "2025",
            "warning_codes": "",
            "notes": "",
        },
    ]


def default_snapshots() -> list[dict]:
    return [
        {
            "snapshot_id": SNAP_A_DIRECT,
            "source_id": SOURCE_A,
            "observation_date": "2024-01-10",
            "observation_date_precision": "day",
            "source_kind": "merged_same_document",
            "source_column_label": "current",
            "total_members": "3200",
            "paid_members": "3000",
            "membership_fees_received_eur": "500000.00",
            "membership_fee_budget_eur": "500000.00",
            "membership_fee_collection_pct": "100.00",
            "new_members_ytd": "40",
            "suspended_members": "5",
            "removed_members_ytd": "20",
            "reported_year": "2024",
            "extraction_confidence": "high",
            "warning_codes": "",
            # Prose the importer must never read.
            "raw_reference": '{"summary_sentences": ["Kojal on hetkel 3200 liiget."]}',
        },
        {
            "snapshot_id": SNAP_B_DIRECT,
            "source_id": SOURCE_B,
            "observation_date": "2025-01-15",
            "observation_date_precision": "day",
            "source_kind": "merged_same_document",
            "source_column_label": "current",
            "total_members": "3300",
            "paid_members": "3100",
            "membership_fees_received_eur": "525000.00",
            "membership_fee_budget_eur": "500000.00",
            "membership_fee_collection_pct": "105.00",
            "new_members_ytd": "45",
            "suspended_members": "6",
            "removed_members_ytd": "25",
            "reported_year": "2025",
            "extraction_confidence": "high",
            "warning_codes": "collection_pct_over_100",
            "raw_reference": "",
        },
        # The same date as the direct 2024 observation, restated a year later.
        # Kept as evidence, never preferred over the direct reading.
        {
            "snapshot_id": SNAP_B_COMPARISON,
            "source_id": SOURCE_B,
            "observation_date": "2024-01-10",
            "observation_date_precision": "day",
            "source_kind": "reported_comparison",
            "source_column_label": "2024 (10.01)",
            "total_members": "3199",
            "paid_members": "",
            "membership_fees_received_eur": "",
            "membership_fee_budget_eur": "",
            "membership_fee_collection_pct": "",
            "new_members_ytd": "",
            "suspended_members": "",
            "removed_members_ytd": "",
            "reported_year": "2024",
            "extraction_confidence": "medium",
            "warning_codes": "",
            "raw_reference": "",
        },
    ]


def default_monthly() -> list[dict]:
    return [
        {
            "calendar_year": "2024",
            "calendar_month": "1",
            "new_members": "12",
            "value_status": "verified",
            "source_count": "1",
            "source_ids": SOURCE_A,
            "earliest_source_observation_date": "2024-01-10",
            "latest_source_observation_date": "2024-01-10",
            "selected_source_id": SOURCE_A,
            "warning_codes": "",
            "conflicting_values": "",
        },
        # An explicitly reported zero. It is a value, and it must survive as one.
        {
            "calendar_year": "2024",
            "calendar_month": "2",
            "new_members": "0",
            "value_status": "verified",
            "source_count": "1",
            "source_ids": SOURCE_A,
            "earliest_source_observation_date": "2024-01-10",
            "latest_source_observation_date": "2024-01-10",
            "selected_source_id": SOURCE_A,
            "warning_codes": "",
            "conflicting_values": "",
        },
        # A conflict: no value at all, and never a zero.
        {
            "calendar_year": "2024",
            "calendar_month": "3",
            "new_members": "",
            "value_status": "conflict",
            "source_count": "2",
            "source_ids": f"{SOURCE_A};{SOURCE_B}",
            "earliest_source_observation_date": "2024-01-10",
            "latest_source_observation_date": "2025-01-15",
            "selected_source_id": "",
            "warning_codes": "monthly_value_conflict",
            "conflicting_values": (
                '[{"value": 4, "source_ids": ["' + SOURCE_A + '"]},'
                ' {"value": 9, "source_ids": ["' + SOURCE_B + '"]}]'
            ),
        },
        {
            "calendar_year": "2025",
            "calendar_month": "1",
            "new_members": "9",
            "value_status": "provisional_current_month",
            "source_count": "1",
            "source_ids": SOURCE_B,
            "earliest_source_observation_date": "2025-01-15",
            "latest_source_observation_date": "2025-01-15",
            "selected_source_id": SOURCE_B,
            "warning_codes": "",
            "conflicting_values": "",
        },
    ]


def default_movements() -> list[dict]:
    rows = []
    for source_id, day, joined, removed in (
        (SOURCE_A, "2024-01-10", ("25", "15"), ("12", "8")),
        (SOURCE_B, "2025-01-15", ("30", "15"), ("15", "10")),
    ):
        for band, join_count, remove_count in (
            ("employees_1_4", joined[0], removed[0]),
            ("supporter", joined[1], removed[1]),
        ):
            rows.append(
                {
                    "source_id": source_id,
                    "observation_date": day,
                    "direction": "joined",
                    "size_band_key": band,
                    "size_band_label_raw": band,
                    "member_count": join_count,
                    "total_reported": "40",
                    "extraction_confidence": "high",
                    "warning_codes": "",
                }
            )
            rows.append(
                {
                    "source_id": source_id,
                    "observation_date": day,
                    "direction": "removed",
                    "size_band_key": band,
                    "size_band_label_raw": band,
                    "member_count": remove_count,
                    "total_reported": "20",
                    "extraction_confidence": "high",
                    "warning_codes": "",
                }
            )
    return rows


def default_removal_reasons() -> list[dict]:
    return [
        {
            "source_id": SOURCE_A,
            "observation_date": "2024-01-10",
            "reason_key": "dissolved_bankrupt_merged_inactive_missing",
            "reason_label_raw": "likvideeritud",
            "member_count": "12",
            "removed_total_reported": "20",
            "extraction_confidence": "high",
            "warning_codes": "",
        },
        {
            "source_id": SOURCE_A,
            "observation_date": "2024-01-10",
            "reason_key": "voluntary_no_service_value",
            "reason_label_raw": "ei näe väärtust",
            "member_count": "8",
            "removed_total_reported": "20",
            "extraction_confidence": "high",
            "warning_codes": "",
        },
    ]


def default_warnings() -> list[dict]:
    return [
        {
            "warning_id": "W00001",
            "source_id": SOURCE_B,
            "dataset": "membership_snapshots",
            "record_key": "2025-01-15",
            "warning_code": "collection_pct_over_100",
            "severity": "info",
            "message": "Laekumise protsent on üle 100.",
            "raw_value": "105.00",
            "suggested_action": "Kontrolli eelarvet.",
            "resolved": "false",
            "resolution_note": "",
        },
        {
            "warning_id": "W00002",
            "source_id": SOURCE_A,
            "dataset": "monthly_new_members",
            "record_key": "2024",
            "warning_code": "monthly_value_conflict",
            "severity": "error",
            "message": "Kaks dokumenti annavad erineva väärtuse.",
            "raw_value": '[{"value": 4}, {"value": 9}]',
            "suggested_action": "Vali õige allikas.",
            "resolved": "false",
            "resolution_note": "",
        },
    ]


def default_conflicts() -> list[dict]:
    return [
        {
            "observation_date": "2024-01-10",
            "metric": "total_members",
            "warning_code": "cross_document_metric_conflict",
            "distinct_values": "2",
            "values_summary": "3200 | 3199",
            "source_paths": f"{PATH_A} | {PATH_B}",
        }
    ]


def default_coverage() -> list[dict]:
    return [
        {
            "year": "2024",
            "month": "1",
            "candidate_documents": "1",
            "extracted_documents": "1",
            "observations": "2",
            "warnings": "1",
            "conflicts": "1",
            "missing_document": "false",
        }
    ]


BATCH_TERMINATION = "batch_aaaa000000000001"
BATCH_SUSPENSION = "batch_aaaa000000000002"
PERIOD_SUMMER = "period_aaaa00000000001"


def default_decision_batches() -> list[dict]:
    """Two batches from one decision: the shape the real appendices have.

    The as-of date and the decision date differ on purpose — the appendix is
    compiled before the board signs — because keeping them apart is the whole
    point of the model.
    """
    return [
        {
            "batch_id": BATCH_TERMINATION,
            "source_id": SOURCE_A,
            "batch_kind": "termination",
            "as_of_date": "2024-01-04",
            "as_of_date_precision": "day",
            "decision_date": "2024-01-11",
            "decision_reference": "otsus nr 1",
            "member_count": "6",
            "corroborating_source_id": SOURCE_B,
            "quality_status": "verified",
            "extraction_confidence": "high",
            "warning_codes": "",
        },
        {
            "batch_id": BATCH_SUSPENSION,
            "source_id": SOURCE_A,
            "batch_kind": "suspension",
            "as_of_date": "2024-01-04",
            "as_of_date_precision": "day",
            "decision_date": "2024-01-11",
            "decision_reference": "otsus nr 1",
            "member_count": "4",
            "corroborating_source_id": "",
            "quality_status": "verified",
            "extraction_confidence": "high",
            "warning_codes": "",
        },
    ]


def default_decision_batch_sizes() -> list[dict]:
    return [
        {
            "batch_id": BATCH_TERMINATION,
            "size_band_key": "employees_1_4",
            "member_count": "4",
            "warning_codes": "",
        },
        {
            "batch_id": BATCH_TERMINATION,
            "size_band_key": "group_company",
            "member_count": "1",
            "warning_codes": "",
        },
        {
            "batch_id": BATCH_TERMINATION,
            "size_band_key": "unknown",
            "member_count": "1",
            "warning_codes": "size_band_explicit_unknown_marker",
        },
        {
            "batch_id": BATCH_SUSPENSION,
            "size_band_key": "employees_5_9",
            "member_count": "4",
            "warning_codes": "",
        },
    ]


def default_decision_batch_reasons() -> list[dict]:
    return [
        {
            "batch_id": BATCH_TERMINATION,
            "reason_key": "financial_difficulty_or_cost_cutting",
            "member_count": "4",
            "warning_codes": "",
        },
        {
            "batch_id": BATCH_TERMINATION,
            "reason_key": "other",
            "member_count": "2",
            "warning_codes": "reason_unmapped",
        },
        {
            "batch_id": BATCH_SUSPENSION,
            "reason_key": "activity_ceased_or_dormant",
            "member_count": "4",
            "warning_codes": "",
        },
    ]


def default_new_member_periods() -> list[dict]:
    """One span the source never broke down into its two months."""
    return [
        {
            "period_id": PERIOD_SUMMER,
            "source_id": SOURCE_A,
            "period_scope": "multi_month_period",
            "period_start": "2024-06-01",
            "period_end": "2024-07-31",
            "new_members": "9",
            "extraction_confidence": "high",
            "warning_codes": "",
        }
    ]


def default_new_member_sizes() -> list[dict]:
    """One distribution against a month, one against the span."""
    return [
        {
            "period_id": "",
            "calendar_year": "2024",
            "calendar_month": "1",
            "size_band_key": "employees_1_4",
            "member_count": "3",
            "warning_codes": "",
        },
        {
            "period_id": "",
            "calendar_year": "2024",
            "calendar_month": "1",
            "size_band_key": "supporter",
            "member_count": "1",
            "warning_codes": "",
        },
        {
            "period_id": PERIOD_SUMMER,
            "calendar_year": "",
            "calendar_month": "",
            "size_band_key": "employees_10_19",
            "member_count": "9",
            "warning_codes": "",
        },
    ]


@dataclass
class PackageBuilder:
    """A package that is valid unless a test deliberately breaks it."""

    source_documents: list[dict] = field(default_factory=default_source_documents)
    snapshots: list[dict] = field(default_factory=default_snapshots)
    monthly: list[dict] = field(default_factory=default_monthly)
    movements: list[dict] = field(default_factory=default_movements)
    removal_reasons: list[dict] = field(default_factory=default_removal_reasons)
    warnings: list[dict] = field(default_factory=default_warnings)
    conflicts: list[dict] = field(default_factory=default_conflicts)
    coverage: list[dict] = field(default_factory=default_coverage)

    # Schema 2.0 tables. Written only when `schema_version` is 2.0, so the same
    # builder produces a faithful 1.0 package for the compatibility tests.
    decision_batches: list[dict] = field(default_factory=default_decision_batches)
    decision_batch_sizes: list[dict] = field(default_factory=default_decision_batch_sizes)
    decision_batch_reasons: list[dict] = field(default_factory=default_decision_batch_reasons)
    new_member_periods: list[dict] = field(default_factory=default_new_member_periods)
    new_member_sizes: list[dict] = field(default_factory=default_new_member_sizes)

    readme: bytes = b"# Sunthetic test package\n"
    schema_version: str = "1.0"
    root_prefix: str = f"{PACKAGE_ROOT}/"

    #: Applied to the finished `{path: bytes}` mapping, so a test can corrupt,
    #: remove or add a member without re-implementing the builder.
    mutate_payloads: object = None
    #: Applied to the manifest dict just before it is written.
    mutate_manifest: object = None
    #: Extra archive members written with a raw, unsanitised name.
    raw_members: dict = field(default_factory=dict)

    def payloads(self) -> dict[str, bytes]:
        payloads = {
            "IMPORT_README.md": self.readme,
            "data/source_documents.csv": _csv_bytes(
                "data/source_documents.csv", self.source_documents
            ),
            "data/membership_snapshots.csv": _csv_bytes(
                "data/membership_snapshots.csv", self.snapshots
            ),
            "data/monthly_new_members.csv": _csv_bytes(
                "data/monthly_new_members.csv", self.monthly
            ),
            "data/membership_size_movements.csv": _csv_bytes(
                "data/membership_size_movements.csv", self.movements
            ),
            "data/membership_removal_reasons.csv": _csv_bytes(
                "data/membership_removal_reasons.csv", self.removal_reasons
            ),
            "data/extraction_warnings.csv": _csv_bytes(
                "data/extraction_warnings.csv", self.warnings
            ),
            "data/conflicts.csv": _csv_bytes("data/conflicts.csv", self.conflicts),
            "data/coverage.csv": _csv_bytes("data/coverage.csv", self.coverage),
        }
        if self.schema_version != "1.0":
            payloads.update(
                {
                    "data/decision_batches.csv": _csv_bytes(
                        "data/decision_batches.csv", self.decision_batches
                    ),
                    "data/decision_batch_size_movements.csv": _csv_bytes(
                        "data/decision_batch_size_movements.csv", self.decision_batch_sizes
                    ),
                    "data/decision_batch_reasons.csv": _csv_bytes(
                        "data/decision_batch_reasons.csv", self.decision_batch_reasons
                    ),
                    "data/new_member_periods.csv": _csv_bytes(
                        "data/new_member_periods.csv", self.new_member_periods
                    ),
                    "data/new_member_size_distribution.csv": _csv_bytes(
                        "data/new_member_size_distribution.csv", self.new_member_sizes
                    ),
                }
            )
        if self.mutate_payloads is not None:
            payloads = self.mutate_payloads(payloads)
        return payloads

    def manifest(self, payloads: dict[str, bytes]) -> dict:
        manifest = {
            "package": PACKAGE_ROOT,
            "schema_version": self.schema_version,
            "files": [
                {
                    "path": path,
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                for path, payload in sorted(payloads.items())
            ],
        }
        if self.mutate_manifest is not None:
            manifest = self.mutate_manifest(manifest)
        return manifest

    def write(self, path: Path) -> Path:
        payloads = self.payloads()
        manifest = self.manifest(payloads)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                f"{self.root_prefix}manifest.json",
                json.dumps(manifest, indent=2).encode("utf-8"),
            )
            for name, payload in payloads.items():
                archive.writestr(f"{self.root_prefix}{name}", payload)
            for raw_name, payload in self.raw_members.items():
                archive.writestr(raw_name, payload)
        return path


def build_package(path: Path, **overrides) -> Path:
    """Write a package to `path`. Keyword arguments override any builder field."""
    return replace(PackageBuilder(), **overrides).write(path)
