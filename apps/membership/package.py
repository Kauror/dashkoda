"""Reading and validating the one-time historical membership package.

This module holds no Django import on purpose. Opening an archive, checking a
manifest and deciding whether a CSV honours its contract are questions about
bytes, not about the database, and keeping them separate means the whole
contract can be exercised without PostgreSQL — which is also how the real
package is validated on a developer machine that has no database.

The archive is treated as hostile until every check has passed:

- no absolute path, no parent-directory segment, no backslash, no symlink;
- a bounded number of members, a bounded size per member, a bounded total
  uncompressed size and a bounded compression ratio;
- `IMPORT_README.md` and `manifest.json` are required;
- every manifest entry is verified by streaming its bytes and comparing the
  server-computed SHA-256 and size;
- a member the manifest does not list is refused rather than ignored, because an
  approved package is exactly the set of files that were approved;
- every CSV must present its exact expected header, in order.

Only then is anything parsed, and only then are foreign references resolved.
`raw_reference` is validated as a required column and deliberately never read:
it holds sentences copied out of the board documents, and this application has
no reason to hold source prose.
"""

from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

# The importer's own contract version. It is what the import key is built from
# together with the package digest, so raising it makes a previously imported
# package importable again under new parsing rules.
PACKAGE_SCHEMA_VERSION = "2.0"

# Manifest schema versions this importer knows how to read. An unknown version
# is refused rather than guessed at.
#
# 2.0 adds the board-decision batch tables and the new-member period tables. It
# is a **major** bump rather than a minor one because a 2.0 package answers
# questions 1.0 could not express at all: what one board decision did, as
# distinct from what a year had done so far. Nothing in 1.0 changed meaning, and
# a 1.0 package is still read exactly as before — the five new files are simply
# absent from it, which is not the same as their being empty.
SUPPORTED_MANIFEST_SCHEMA_VERSIONS = frozenset({"1.0", "2.0"})

README_NAME = "IMPORT_README.md"
MANIFEST_NAME = "manifest.json"

CHUNK_SIZE = 64 * 1024
MAX_COMPRESSION_RATIO = 200

# Separators the package uses inside single CSV cells.
CODE_SEPARATOR = ";"
PATH_SEPARATOR = " | "

REQUIRED_HEADERS: dict[str, tuple[str, ...]] = {
    "data/source_documents.csv": (
        "source_id",
        "relative_path",
        "filename",
        "extension",
        "file_sha256",
        "file_size_bytes",
        "filesystem_modified_at",
        "year_folder",
        "month_folder",
        "candidate_reason",
        "extraction_status",
        "observation_date",
        "observation_date_precision",
        "date_source",
        "date_confidence",
        "document_title",
        "document_year_claim",
        "warning_codes",
        "notes",
    ),
    "data/membership_snapshots.csv": (
        "snapshot_id",
        "source_id",
        "observation_date",
        "observation_date_precision",
        "source_kind",
        "source_column_label",
        "total_members",
        "paid_members",
        "membership_fees_received_eur",
        "membership_fee_budget_eur",
        "membership_fee_collection_pct",
        "new_members_ytd",
        "suspended_members",
        "removed_members_ytd",
        "reported_year",
        "extraction_confidence",
        "warning_codes",
        "raw_reference",
    ),
    "data/monthly_new_members.csv": (
        "calendar_year",
        "calendar_month",
        "new_members",
        "value_status",
        "source_count",
        "source_ids",
        "earliest_source_observation_date",
        "latest_source_observation_date",
        "selected_source_id",
        "warning_codes",
        "conflicting_values",
    ),
    "data/membership_size_movements.csv": (
        "source_id",
        "observation_date",
        "direction",
        "size_band_key",
        "size_band_label_raw",
        "member_count",
        "total_reported",
        "extraction_confidence",
        "warning_codes",
    ),
    "data/membership_removal_reasons.csv": (
        "source_id",
        "observation_date",
        "reason_key",
        "reason_label_raw",
        "member_count",
        "removed_total_reported",
        "extraction_confidence",
        "warning_codes",
    ),
    "data/extraction_warnings.csv": (
        "warning_id",
        "source_id",
        "dataset",
        "record_key",
        "warning_code",
        "severity",
        "message",
        "raw_value",
        "suggested_action",
        "resolved",
        "resolution_note",
    ),
    "data/conflicts.csv": (
        "observation_date",
        "metric",
        "warning_code",
        "distinct_values",
        "values_summary",
        "source_paths",
    ),
    "data/coverage.csv": (
        "year",
        "month",
        "candidate_documents",
        "extracted_documents",
        "observations",
        "warnings",
        "conflicts",
        "missing_document",
    ),
}

# Tables that exist only from schema 2.0 onwards.
REQUIRED_HEADERS.update(
    {
        "data/decision_batches.csv": (
            "batch_id",
            "source_id",
            "batch_kind",
            "as_of_date",
            "as_of_date_precision",
            "decision_date",
            "decision_reference",
            "member_count",
            "corroborating_source_id",
            "quality_status",
            "extraction_confidence",
            "warning_codes",
        ),
        "data/decision_batch_size_movements.csv": (
            "batch_id",
            "size_band_key",
            "member_count",
            "warning_codes",
        ),
        # Deliberately no raw-label column: see MembershipDecisionBatchReason.
        "data/decision_batch_reasons.csv": (
            "batch_id",
            "reason_key",
            "member_count",
            "warning_codes",
        ),
        "data/new_member_periods.csv": (
            "period_id",
            "source_id",
            "period_scope",
            "period_start",
            "period_end",
            "new_members",
            "extraction_confidence",
            "warning_codes",
        ),
        "data/new_member_size_distribution.csv": (
            "period_id",
            "calendar_year",
            "calendar_month",
            "size_band_key",
            "member_count",
            "warning_codes",
        ),
    }
)

V2_ONLY_PATHS: tuple[str, ...] = (
    "data/decision_batches.csv",
    "data/decision_batch_size_movements.csv",
    "data/decision_batch_reasons.csv",
    "data/new_member_periods.csv",
    "data/new_member_size_distribution.csv",
)

V1_PATHS: tuple[str, ...] = tuple(path for path in REQUIRED_HEADERS if path not in V2_ONLY_PATHS)

# Files that must exist, whatever else the manifest lists, per schema version.
# A 1.0 package carrying a 2.0-only file is refused: the manifest declares what
# the package is, and a file the declared version does not know about means the
# two disagree about which contract is in force.
REQUIRED_PATHS_BY_VERSION: dict[str, tuple[str, ...]] = {
    "1.0": (README_NAME, *V1_PATHS),
    "2.0": (README_NAME, *V1_PATHS, *V2_ONLY_PATHS),
}

# Kept for callers that predate versioned paths; always the 1.0 floor.
REQUIRED_PATHS: tuple[str, ...] = REQUIRED_PATHS_BY_VERSION["1.0"]


class PackageContractError(RuntimeError):
    """The package is not the approved contract. Nothing is imported."""


@dataclass(frozen=True)
class PackageLimits:
    """Ceilings applied before any parsing happens."""

    max_package_bytes: int = 25 * 1024 * 1024
    max_uncompressed_bytes: int = 100 * 1024 * 1024
    max_member_bytes: int = 25 * 1024 * 1024
    max_members: int = 64


@dataclass(frozen=True)
class SourceDocumentRow:
    source_id: str
    relative_path: str
    filename: str
    extension: str
    file_sha256: str
    file_size_bytes: int
    filesystem_modified_at: datetime | None
    year_folder: str
    month_folder: str
    candidate_reason: str
    extraction_status: str
    observation_date: date | None
    observation_date_precision: str
    date_source: str
    date_confidence: str
    document_title: str
    document_year_claim: int | None
    warning_codes: list[str]
    notes: str


@dataclass(frozen=True)
class SnapshotRow:
    """One reported observation. `raw_reference` is intentionally not a field."""

    snapshot_id: str
    source_id: str
    observation_date: date
    observation_date_precision: str
    source_kind: str
    source_column_label: str
    total_members: int | None
    paid_members: int | None
    membership_fees_received_eur: Decimal | None
    membership_fee_budget_eur: Decimal | None
    membership_fee_collection_pct: Decimal | None
    new_members_ytd: int | None
    suspended_members: int | None
    removed_members_ytd: int | None
    reported_year: int | None
    extraction_confidence: str
    warning_codes: list[str]


@dataclass(frozen=True)
class MonthlyRow:
    calendar_year: int
    calendar_month: int
    new_members: int | None
    value_status: str
    source_count: int
    source_ids: list[str]
    earliest_source_observation_date: date | None
    latest_source_observation_date: date | None
    selected_source_id: str
    warning_codes: list[str]
    conflicting_values: list


@dataclass(frozen=True)
class MovementRow:
    source_id: str
    observation_date: date
    direction: str
    size_band_key: str
    size_band_label_raw: str
    member_count: int | None
    total_reported: int | None
    extraction_confidence: str
    warning_codes: list[str]


@dataclass(frozen=True)
class RemovalReasonRow:
    source_id: str
    observation_date: date
    reason_key: str
    reason_label_raw: str
    member_count: int | None
    removed_total_reported: int | None
    extraction_confidence: str
    warning_codes: list[str]


@dataclass(frozen=True)
class WarningRow:
    warning_id: str
    source_id: str
    dataset: str
    record_key: str
    warning_code: str
    severity: str
    message: str
    raw_value: str
    suggested_action: str


@dataclass(frozen=True)
class ConflictRow:
    observation_date: date
    metric: str
    warning_code: str
    distinct_values: int
    values_summary: str
    source_ids: list[str]


@dataclass(frozen=True)
class DecisionBatchRow:
    batch_id: str
    source_id: str
    batch_kind: str
    as_of_date: date | None
    as_of_date_precision: str
    decision_date: date | None
    decision_reference: str
    member_count: int | None
    corroborating_source_id: str
    quality_status: str
    extraction_confidence: str
    warning_codes: list


@dataclass(frozen=True)
class DecisionBatchSizeRow:
    batch_id: str
    size_band_key: str
    member_count: int | None
    warning_codes: list


@dataclass(frozen=True)
class DecisionBatchReasonRow:
    batch_id: str
    reason_key: str
    member_count: int | None
    warning_codes: list


@dataclass(frozen=True)
class NewMemberPeriodRow:
    period_id: str
    source_id: str
    period_scope: str
    period_start: date
    period_end: date
    new_members: int | None
    extraction_confidence: str
    warning_codes: list


@dataclass(frozen=True)
class NewMemberSizeRow:
    """Size distribution for either one month or one multi-month period.

    Exactly one parent is named: `period_id`, or the calendar year and month.
    """

    period_id: str
    calendar_year: int | None
    calendar_month: int | None
    size_band_key: str
    member_count: int | None
    warning_codes: list


@dataclass(frozen=True)
class ParsedPackage:
    """Everything the importer needs, already validated and typed."""

    package_sha256: str
    package_size_bytes: int
    manifest_schema_version: str
    source_documents: tuple[SourceDocumentRow, ...] = field(default=())
    snapshots: tuple[SnapshotRow, ...] = field(default=())
    monthly_values: tuple[MonthlyRow, ...] = field(default=())
    movements: tuple[MovementRow, ...] = field(default=())
    removal_reasons: tuple[RemovalReasonRow, ...] = field(default=())
    warnings: tuple[WarningRow, ...] = field(default=())
    conflicts: tuple[ConflictRow, ...] = field(default=())
    coverage_rows: int = 0
    # Schema 2.0 only. Empty for a 1.0 package, which is not the same as a 2.0
    # package that happens to carry no batches.
    decision_batches: tuple[DecisionBatchRow, ...] = field(default=())
    decision_batch_sizes: tuple[DecisionBatchSizeRow, ...] = field(default=())
    decision_batch_reasons: tuple[DecisionBatchReasonRow, ...] = field(default=())
    new_member_periods: tuple[NewMemberPeriodRow, ...] = field(default=())
    new_member_sizes: tuple[NewMemberSizeRow, ...] = field(default=())

    @property
    def row_counts(self) -> dict[str, int]:
        """Aggregate counts only. Never a value, never a label, never a path.

        A 1.0 package reports no key for the 2.0 tables at all. Reporting
        ``decision_batches: 0`` would say the package looked and found none,
        when the truth is that it cannot describe batches — the same
        missing-is-not-zero rule the data itself is held to.
        """
        counts = {
            "source_documents": len(self.source_documents),
            "snapshots": len(self.snapshots),
            "monthly_values": len(self.monthly_values),
            "size_movements": len(self.movements),
            "removal_reasons": len(self.removal_reasons),
            "warnings": len(self.warnings),
            "conflicts": len(self.conflicts),
            "coverage_rows": self.coverage_rows,
        }
        if self.manifest_schema_version != "1.0":
            counts.update(
                {
                    "decision_batches": len(self.decision_batches),
                    "decision_batch_sizes": len(self.decision_batch_sizes),
                    "decision_batch_reasons": len(self.decision_batch_reasons),
                    "new_member_periods": len(self.new_member_periods),
                    "new_member_sizes": len(self.new_member_sizes),
                }
            )
        return counts


# --------------------------------------------------------------------------
# Archive safety
# --------------------------------------------------------------------------


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """Unix mode lives in the top 16 bits of `external_attr`."""
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _safe_relative_name(raw_name: str) -> str | None:
    """Return the member's path relative to the package root, or `None`.

    `None` means "not a file this package contract can contain" — a directory
    entry, or a name that tries to escape. An escape attempt is refused by the
    caller rather than sanitised, because a package that contains one is not the
    approved package.
    """
    if raw_name.endswith("/"):
        return None
    if "\\" in raw_name or raw_name.startswith("/"):
        raise PackageContractError("Pakett sisaldab lubamatut failiteed.")
    parts = PurePosixPath(raw_name).parts
    if any(part in ("..", ".") for part in parts):
        raise PackageContractError("Pakett sisaldab lubamatut failiteed.")
    if len(parts) > 1 and PurePosixPath(raw_name).drive:
        raise PackageContractError("Pakett sisaldab lubamatut failiteed.")
    return "/".join(parts)


def _strip_root_prefix(names: list[str]) -> str:
    """Find the single top-level directory the package is wrapped in, if any.

    The approved package wraps everything in one directory. A package that is
    not wrapped is equally acceptable; a package with two competing roots is
    not, because then "manifest.json" would be ambiguous.
    """
    roots = {name.split("/", 1)[0] for name in names if "/" in name}
    top_level = {name for name in names if "/" not in name}
    if MANIFEST_NAME in top_level:
        return ""
    if len(roots) == 1:
        return f"{next(iter(roots))}/"
    raise PackageContractError("Paketi struktuur ei ole ootuspärane: manifest.json puudub juurest.")


def _digest_and_size(handle) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def file_digest(path: Path) -> tuple[str, int]:
    """Server-computed identity of the package file itself."""
    with path.open("rb") as handle:
        return _digest_and_size(handle)


# --------------------------------------------------------------------------
# Cell parsing
# --------------------------------------------------------------------------


def _text(value: str | None) -> str:
    return (value or "").strip()


def _codes(value: str | None) -> list[str]:
    return [code for code in (_text(value).split(CODE_SEPARATOR)) if code]


def _optional_int(value: str | None, *, column: str) -> int | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as error:
        raise PackageContractError(f"Veerg {column} peab olema täisarv.") from error


def _required_int(value: str | None, *, column: str) -> int:
    parsed = _optional_int(value, column=column)
    if parsed is None:
        raise PackageContractError(f"Veerg {column} on kohustuslik.")
    return parsed


def _optional_decimal(value: str | None, *, column: str) -> Decimal | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as error:
        raise PackageContractError(f"Veerg {column} peab olema arv.") from error


def _optional_date(value: str | None, *, column: str) -> date | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise PackageContractError(f"Veerg {column} peab olema kujul AAAA-KK-PP.") from error


def _required_date(value: str | None, *, column: str) -> date:
    parsed = _optional_date(value, column=column)
    if parsed is None:
        raise PackageContractError(f"Veerg {column} on kohustuslik.")
    return parsed


def _dated_by_precision(value: str | None, precision: str, *, column: str) -> date | None:
    """Read a date whose stated precision may be coarser than a day.

    Sixteen comparison rows in the approved package name only a year, because
    the board document restated a previous year in a column headed `2014`
    rather than on a date. A `DateField` needs one concrete day, so a coarse
    value is anchored to the **end** of the period it describes — the last day
    of the year or month — which is what "the position as of that year" means
    in a board report.

    The anchoring is never a claim of accuracy: `observation_date_precision`
    travels with the row, the admin shows it, and the interface renders a
    year-precision observation as a year. Anchoring to the start instead would
    have placed a year-end figure eleven months before the fact it describes.
    """
    raw = _text(value)
    if not raw:
        return None
    if precision == "year":
        try:
            return date(int(raw), 12, 31)
        except ValueError as error:
            raise PackageContractError(f"Veerg {column} peab olema aastaarv.") from error
    if precision == "month":
        try:
            year_text, month_text = raw.split("-")[:2]
            year, month = int(year_text), int(month_text)
            return date(year, month, calendar.monthrange(year, month)[1])
        except (ValueError, IndexError) as error:
            raise PackageContractError(f"Veerg {column} peab olema kujul AAAA-KK.") from error
    return _optional_date(raw, column=column)


def _required_dated_by_precision(value: str | None, precision: str, *, column: str) -> date:
    parsed = _dated_by_precision(value, precision, column=column)
    if parsed is None:
        raise PackageContractError(f"Veerg {column} on kohustuslik.")
    return parsed


def _optional_datetime(value: str | None, *, column: str) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as error:
        raise PackageContractError(f"Veerg {column} peab olema ISO-8601 ajatempel.") from error


def _json_list(value: str | None, *, column: str) -> list:
    raw = _text(value)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PackageContractError(f"Veerg {column} peab olema JSON.") from error
    return parsed if isinstance(parsed, list) else [parsed]


def _rows(payload: bytes, *, path: str):
    """Decode one CSV member and check its header exactly.

    `utf-8-sig` so a BOM-prefixed export reads identically to one without.
    """
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise PackageContractError(f"Fail ei ole UTF-8: {path}.") from error

    reader = csv.DictReader(io.StringIO(text, newline=""))
    expected = REQUIRED_HEADERS[path]
    actual = tuple(reader.fieldnames or ())
    if actual != expected:
        raise PackageContractError(f"Faili {path} päis ei vasta kokkuleppele.")
    return reader


# --------------------------------------------------------------------------
# Package reading
# --------------------------------------------------------------------------


def _read_members(archive: zipfile.ZipFile, limits: PackageLimits) -> dict[str, bytes]:
    infos = archive.infolist()
    if len(infos) > limits.max_members:
        raise PackageContractError("Pakett sisaldab liiga palju faile.")

    named: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        if _is_symlink(info):
            raise PackageContractError("Pakett sisaldab nimeviidet.")
        name = _safe_relative_name(info.filename)
        if name is None:
            continue
        if info.file_size > limits.max_member_bytes:
            raise PackageContractError("Paketi fail on liiga suur.")
        if info.compress_size > 0 and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise PackageContractError("Paketi fail on kahtlaselt tugevalt pakitud.")
        named[name] = info

    declared_total = sum(info.file_size for info in named.values())
    if declared_total > limits.max_uncompressed_bytes:
        raise PackageContractError("Paketi lahtipakitud maht on liiga suur.")

    prefix = _strip_root_prefix(list(named))

    payloads: dict[str, bytes] = {}
    extracted_total = 0
    for name, info in named.items():
        if prefix and not name.startswith(prefix):
            raise PackageContractError("Pakett sisaldab mitut juurkataloogi.")
        relative = name[len(prefix) :]
        with archive.open(info, "r") as handle:
            # Read one byte past the declared size: a member whose real content
            # is longer than its header claims is a decompression bomb, not a
            # package.
            payload = handle.read(info.file_size + 1)
        if len(payload) != info.file_size:
            raise PackageContractError("Paketi faili tegelik suurus ei vasta deklareeritule.")
        extracted_total += len(payload)
        if extracted_total > limits.max_uncompressed_bytes:
            raise PackageContractError("Paketi lahtipakitud maht on liiga suur.")
        payloads[relative] = payload
    return payloads


def _load_manifest(payloads: dict[str, bytes]) -> tuple[str, dict[str, dict]]:
    if MANIFEST_NAME not in payloads:
        raise PackageContractError("Paketist puudub manifest.json.")
    if README_NAME not in payloads:
        raise PackageContractError("Paketist puudub IMPORT_README.md.")

    try:
        manifest = json.loads(payloads[MANIFEST_NAME].decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PackageContractError("Manifest ei ole loetav JSON.") from error

    version = str(manifest.get("schema_version", "")).strip()
    if version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
        raise PackageContractError(f"Manifesti skeemi versioon ei ole toetatud: {version or '-'}.")

    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise PackageContractError("Manifest ei loetle ühtegi faili.")

    listed: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PackageContractError("Manifesti kirje ei ole objekt.")
        path = _text(entry.get("path"))
        if not path or path in listed:
            raise PackageContractError("Manifesti kirje failitee on puudu või kordub.")
        listed[path] = entry
    return version, listed


def _verify_manifest(payloads: dict[str, bytes], listed: dict[str, dict], version: str) -> None:
    """Check every declared file, then refuse anything undeclared.

    The manifest does not list itself, which is why it is excluded below rather
    than expected.

    What "required" means depends on the declared schema version: a 1.0 package
    has eight data files and a 2.0 package has thirteen. A 1.0 package that
    nevertheless carries a 2.0-only table is refused rather than read leniently,
    because the manifest is what declares which contract is in force and the two
    would then disagree.
    """
    for path, entry in listed.items():
        payload = payloads.get(path)
        if payload is None:
            raise PackageContractError(f"Manifestis loetletud fail puudub: {path}.")
        expected_size = entry.get("size_bytes")
        if not isinstance(expected_size, int) or len(payload) != expected_size:
            raise PackageContractError(f"Faili suurus ei vasta manifestile: {path}.")
        expected_digest = _text(entry.get("sha256")).lower()
        actual_digest = hashlib.sha256(payload).hexdigest()
        if actual_digest != expected_digest:
            raise PackageContractError(f"Faili kontrollsumma ei vasta manifestile: {path}.")

    undeclared = set(payloads) - set(listed) - {MANIFEST_NAME}
    if undeclared:
        raise PackageContractError("Pakett sisaldab manifestis loetlemata faile.")

    required = REQUIRED_PATHS_BY_VERSION[version]
    missing = [path for path in required if path not in payloads]
    if missing:
        raise PackageContractError(f"Paketist puudub kohustuslik fail: {missing[0]}.")

    unexpected = [path for path in payloads if path not in required and path != MANIFEST_NAME]
    if unexpected:
        raise PackageContractError(
            f"Pakett sisaldab skeemi {version} jaoks tundmatut faili: {sorted(unexpected)[0]}."
        )


# --------------------------------------------------------------------------
# Table parsing
# --------------------------------------------------------------------------


def _parse_source_documents(payload: bytes) -> tuple[SourceDocumentRow, ...]:
    path = "data/source_documents.csv"
    rows: list[SourceDocumentRow] = []
    seen: set[str] = set()
    for raw in _rows(payload, path=path):
        source_id = _text(raw["source_id"])
        if not source_id:
            raise PackageContractError("Lähtedokumendi source_id on kohustuslik.")
        if source_id in seen:
            raise PackageContractError(f"Lähtedokumendi source_id kordub: {source_id}.")
        seen.add(source_id)
        precision = _text(raw["observation_date_precision"]) or "day"
        rows.append(
            SourceDocumentRow(
                source_id=source_id,
                relative_path=_text(raw["relative_path"]),
                filename=_text(raw["filename"]),
                extension=_text(raw["extension"]),
                file_sha256=_text(raw["file_sha256"]),
                file_size_bytes=_optional_int(raw["file_size_bytes"], column="file_size_bytes")
                or 0,
                filesystem_modified_at=_optional_datetime(
                    raw["filesystem_modified_at"], column="filesystem_modified_at"
                ),
                year_folder=_text(raw["year_folder"]),
                month_folder=_text(raw["month_folder"]),
                candidate_reason=_text(raw["candidate_reason"]),
                extraction_status=_text(raw["extraction_status"]),
                observation_date=_dated_by_precision(
                    raw["observation_date"], precision, column="observation_date"
                ),
                observation_date_precision=precision,
                date_source=_text(raw["date_source"]),
                date_confidence=_text(raw["date_confidence"]),
                document_title=_text(raw["document_title"]),
                document_year_claim=_optional_int(
                    raw["document_year_claim"], column="document_year_claim"
                ),
                warning_codes=_codes(raw["warning_codes"]),
                notes=_text(raw["notes"]),
            )
        )
    return tuple(rows)


def _parse_snapshots(payload: bytes) -> tuple[SnapshotRow, ...]:
    path = "data/membership_snapshots.csv"
    rows: list[SnapshotRow] = []
    seen: set[str] = set()
    for raw in _rows(payload, path=path):
        snapshot_id = _text(raw["snapshot_id"])
        if not snapshot_id:
            raise PackageContractError("Vaatluse snapshot_id on kohustuslik.")
        if snapshot_id in seen:
            raise PackageContractError(f"Vaatluse snapshot_id kordub: {snapshot_id}.")
        seen.add(snapshot_id)
        precision = _text(raw["observation_date_precision"]) or "day"
        rows.append(
            SnapshotRow(
                snapshot_id=snapshot_id,
                source_id=_text(raw["source_id"]),
                observation_date=_required_dated_by_precision(
                    raw["observation_date"], precision, column="observation_date"
                ),
                observation_date_precision=precision,
                source_kind=_text(raw["source_kind"]),
                source_column_label=_text(raw["source_column_label"]),
                total_members=_optional_int(raw["total_members"], column="total_members"),
                paid_members=_optional_int(raw["paid_members"], column="paid_members"),
                membership_fees_received_eur=_optional_decimal(
                    raw["membership_fees_received_eur"], column="membership_fees_received_eur"
                ),
                membership_fee_budget_eur=_optional_decimal(
                    raw["membership_fee_budget_eur"], column="membership_fee_budget_eur"
                ),
                membership_fee_collection_pct=_optional_decimal(
                    raw["membership_fee_collection_pct"], column="membership_fee_collection_pct"
                ),
                new_members_ytd=_optional_int(raw["new_members_ytd"], column="new_members_ytd"),
                suspended_members=_optional_int(
                    raw["suspended_members"], column="suspended_members"
                ),
                removed_members_ytd=_optional_int(
                    raw["removed_members_ytd"], column="removed_members_ytd"
                ),
                reported_year=_optional_int(raw["reported_year"], column="reported_year"),
                extraction_confidence=_text(raw["extraction_confidence"]) or "medium",
                warning_codes=_codes(raw["warning_codes"]),
                # `raw_reference` is read past deliberately. See the module
                # docstring: the package's identifiers are provenance enough.
            )
        )
    return tuple(rows)


def _parse_monthly(payload: bytes) -> tuple[MonthlyRow, ...]:
    path = "data/monthly_new_members.csv"
    rows: list[MonthlyRow] = []
    seen: set[tuple[int, int]] = set()
    for raw in _rows(payload, path=path):
        year = _required_int(raw["calendar_year"], column="calendar_year")
        month = _required_int(raw["calendar_month"], column="calendar_month")
        if not 1 <= month <= 12:
            raise PackageContractError(f"Kuu peab olema vahemikus 1–12: {month}.")
        if (year, month) in seen:
            raise PackageContractError(f"Kuu kordub: {year}-{month:02d}.")
        seen.add((year, month))
        status = _text(raw["value_status"])
        new_members = _optional_int(raw["new_members"], column="new_members")
        if status == "conflict" and new_members is not None:
            raise PackageContractError(f"Vastuolulisel kuul ei tohi olla väärtust: {year}-{month}.")
        rows.append(
            MonthlyRow(
                calendar_year=year,
                calendar_month=month,
                new_members=new_members,
                value_status=status,
                source_count=_optional_int(raw["source_count"], column="source_count") or 0,
                source_ids=_codes(raw["source_ids"]),
                earliest_source_observation_date=_optional_date(
                    raw["earliest_source_observation_date"],
                    column="earliest_source_observation_date",
                ),
                latest_source_observation_date=_optional_date(
                    raw["latest_source_observation_date"], column="latest_source_observation_date"
                ),
                selected_source_id=_text(raw["selected_source_id"]),
                warning_codes=_codes(raw["warning_codes"]),
                conflicting_values=_json_list(
                    raw["conflicting_values"], column="conflicting_values"
                ),
            )
        )
    return tuple(rows)


def _parse_decision_batches(payload: bytes) -> tuple[DecisionBatchRow, ...]:
    path = "data/decision_batches.csv"
    rows: list[DecisionBatchRow] = []
    seen: set[str] = set()
    for raw in _rows(payload, path=path):
        batch_id = _text(raw["batch_id"])
        if not batch_id:
            raise PackageContractError("Otsuse partii tunnus puudub.")
        if batch_id in seen:
            raise PackageContractError("Otsuse partii tunnus kordub.")
        seen.add(batch_id)
        precision = _text(raw["as_of_date_precision"]) or "day"
        rows.append(
            DecisionBatchRow(
                batch_id=batch_id,
                source_id=_text(raw["source_id"]),
                batch_kind=_text(raw["batch_kind"]),
                as_of_date=_dated_by_precision(raw["as_of_date"], precision, column="as_of_date"),
                as_of_date_precision=precision,
                # The decision date is its own fact and is never derived from
                # the as-of date when the source did not state it.
                decision_date=_optional_date(raw["decision_date"], column="decision_date"),
                decision_reference=_text(raw["decision_reference"]),
                member_count=_optional_int(raw["member_count"], column="member_count"),
                corroborating_source_id=_text(raw["corroborating_source_id"]),
                quality_status=_text(raw["quality_status"]),
                extraction_confidence=_text(raw["extraction_confidence"]),
                warning_codes=_codes(raw["warning_codes"]),
            )
        )
    return tuple(rows)


def _parse_decision_batch_sizes(payload: bytes) -> tuple[DecisionBatchSizeRow, ...]:
    path = "data/decision_batch_size_movements.csv"
    rows: list[DecisionBatchSizeRow] = []
    seen: set[tuple[str, str]] = set()
    for raw in _rows(payload, path=path):
        batch_id = _text(raw["batch_id"])
        band = _text(raw["size_band_key"])
        if (batch_id, band) in seen:
            raise PackageContractError("Otsuse partii suurusklass kordub.")
        seen.add((batch_id, band))
        rows.append(
            DecisionBatchSizeRow(
                batch_id=batch_id,
                size_band_key=band,
                member_count=_optional_int(raw["member_count"], column="member_count"),
                warning_codes=_codes(raw["warning_codes"]),
            )
        )
    return tuple(rows)


def _parse_decision_batch_reasons(payload: bytes) -> tuple[DecisionBatchReasonRow, ...]:
    path = "data/decision_batch_reasons.csv"
    rows: list[DecisionBatchReasonRow] = []
    seen: set[tuple[str, str]] = set()
    for raw in _rows(payload, path=path):
        batch_id = _text(raw["batch_id"])
        reason = _text(raw["reason_key"])
        if (batch_id, reason) in seen:
            raise PackageContractError("Otsuse partii lahkumispõhjus kordub.")
        seen.add((batch_id, reason))
        rows.append(
            DecisionBatchReasonRow(
                batch_id=batch_id,
                reason_key=reason,
                member_count=_optional_int(raw["member_count"], column="member_count"),
                warning_codes=_codes(raw["warning_codes"]),
            )
        )
    return tuple(rows)


def _parse_new_member_periods(payload: bytes) -> tuple[NewMemberPeriodRow, ...]:
    path = "data/new_member_periods.csv"
    rows: list[NewMemberPeriodRow] = []
    seen: set[str] = set()
    for raw in _rows(payload, path=path):
        period_id = _text(raw["period_id"])
        if not period_id:
            raise PackageContractError("Perioodi tunnus puudub.")
        if period_id in seen:
            raise PackageContractError("Perioodi tunnus kordub.")
        seen.add(period_id)
        start = _required_date(raw["period_start"], column="period_start")
        end = _required_date(raw["period_end"], column="period_end")
        if end < start:
            raise PackageContractError("Perioodi lõpp on enne algust.")
        rows.append(
            NewMemberPeriodRow(
                period_id=period_id,
                source_id=_text(raw["source_id"]),
                period_scope=_text(raw["period_scope"]),
                period_start=start,
                period_end=end,
                new_members=_optional_int(raw["new_members"], column="new_members"),
                extraction_confidence=_text(raw["extraction_confidence"]),
                warning_codes=_codes(raw["warning_codes"]),
            )
        )
    return tuple(rows)


def _parse_new_member_sizes(payload: bytes) -> tuple[NewMemberSizeRow, ...]:
    """Parse the size distribution shared by monthly values and periods.

    Exactly one parent must be named. A row naming both, or neither, is a
    contract error rather than something to resolve by preferring one.
    """
    path = "data/new_member_size_distribution.csv"
    rows: list[NewMemberSizeRow] = []
    seen: set[tuple[str, int | None, int | None, str]] = set()
    for raw in _rows(payload, path=path):
        period_id = _text(raw["period_id"])
        year = _optional_int(raw["calendar_year"], column="calendar_year")
        month = _optional_int(raw["calendar_month"], column="calendar_month")
        band = _text(raw["size_band_key"])
        has_period = bool(period_id)
        has_month = year is not None and month is not None
        if has_period == has_month:
            raise PackageContractError(
                "Suurusjaotus peab viitama täpselt ühele vanemale: kas perioodile "
                "või kalendrikuule."
            )
        if has_month and not 1 <= month <= 12:
            raise PackageContractError("Kalendrikuu peab olema vahemikus 1-12.")
        key = (period_id, year, month, band)
        if key in seen:
            raise PackageContractError("Uute liikmete suurusjaotus kordub.")
        seen.add(key)
        rows.append(
            NewMemberSizeRow(
                period_id=period_id,
                calendar_year=year,
                calendar_month=month,
                size_band_key=band,
                member_count=_optional_int(raw["member_count"], column="member_count"),
                warning_codes=_codes(raw["warning_codes"]),
            )
        )
    return tuple(rows)


def _parse_movements(payload: bytes) -> tuple[MovementRow, ...]:
    path = "data/membership_size_movements.csv"
    rows: list[MovementRow] = []
    seen: set[tuple[str, date, str, str]] = set()
    for raw in _rows(payload, path=path):
        source_id = _text(raw["source_id"])
        observation_date = _required_date(raw["observation_date"], column="observation_date")
        direction = _text(raw["direction"])
        band = _text(raw["size_band_key"])
        key = (source_id, observation_date, direction, band)
        if key in seen:
            raise PackageContractError("Suurusklassi liikumine kordub.")
        seen.add(key)
        rows.append(
            MovementRow(
                source_id=source_id,
                observation_date=observation_date,
                direction=direction,
                size_band_key=band,
                size_band_label_raw=_text(raw["size_band_label_raw"]),
                member_count=_optional_int(raw["member_count"], column="member_count"),
                total_reported=_optional_int(raw["total_reported"], column="total_reported"),
                extraction_confidence=_text(raw["extraction_confidence"]) or "medium",
                warning_codes=_codes(raw["warning_codes"]),
            )
        )
    return tuple(rows)


def _parse_removal_reasons(payload: bytes) -> tuple[RemovalReasonRow, ...]:
    path = "data/membership_removal_reasons.csv"
    rows: list[RemovalReasonRow] = []
    seen: set[tuple[str, date, str, str]] = set()
    for raw in _rows(payload, path=path):
        source_id = _text(raw["source_id"])
        observation_date = _required_date(raw["observation_date"], column="observation_date")
        reason_key = _text(raw["reason_key"])
        label = _text(raw["reason_label_raw"])
        key = (source_id, observation_date, reason_key, label)
        if key in seen:
            raise PackageContractError("Lahkumise põhjus kordub.")
        seen.add(key)
        rows.append(
            RemovalReasonRow(
                source_id=source_id,
                observation_date=observation_date,
                reason_key=reason_key,
                reason_label_raw=label,
                member_count=_optional_int(raw["member_count"], column="member_count"),
                removed_total_reported=_optional_int(
                    raw["removed_total_reported"], column="removed_total_reported"
                ),
                extraction_confidence=_text(raw["extraction_confidence"]) or "medium",
                warning_codes=_codes(raw["warning_codes"]),
            )
        )
    return tuple(rows)


def _parse_warnings(payload: bytes) -> tuple[WarningRow, ...]:
    path = "data/extraction_warnings.csv"
    rows: list[WarningRow] = []
    seen: set[str] = set()
    for raw in _rows(payload, path=path):
        warning_id = _text(raw["warning_id"])
        if warning_id and warning_id in seen:
            raise PackageContractError(f"Hoiatuse tunnus kordub: {warning_id}.")
        seen.add(warning_id)
        rows.append(
            WarningRow(
                warning_id=warning_id,
                source_id=_text(raw["source_id"]),
                dataset=_text(raw["dataset"]),
                record_key=_text(raw["record_key"]),
                warning_code=_text(raw["warning_code"]),
                severity=_text(raw["severity"]) or "info",
                message=_text(raw["message"]),
                raw_value=_text(raw["raw_value"]),
                suggested_action=_text(raw["suggested_action"]),
            )
        )
    return tuple(rows)


def _parse_conflicts(payload: bytes, *, path_to_source: dict[str, str]) -> tuple[ConflictRow, ...]:
    """Resolve the conflict rows' document paths into document identifiers.

    The package identifies the disagreeing documents by their original path.
    Those are resolved here and the paths are then dropped, so no filesystem
    path travels further into the application than the source-document table
    that is allowed to hold it.
    """
    path = "data/conflicts.csv"
    rows: list[ConflictRow] = []
    seen: set[tuple[date, str]] = set()
    for raw in _rows(payload, path=path):
        observation_date = _required_date(raw["observation_date"], column="observation_date")
        metric = _text(raw["metric"])
        if (observation_date, metric) in seen:
            raise PackageContractError("Vastuolu kordub sama kuupäeva ja näitaja kohta.")
        seen.add((observation_date, metric))

        source_ids: list[str] = []
        for candidate in _text(raw["source_paths"]).split(PATH_SEPARATOR):
            candidate = candidate.strip()
            if not candidate:
                continue
            resolved = path_to_source.get(candidate)
            if resolved is None:
                raise PackageContractError("Vastuolu viitab tundmatule lähtedokumendile.")
            if resolved not in source_ids:
                source_ids.append(resolved)

        rows.append(
            ConflictRow(
                observation_date=observation_date,
                metric=metric,
                warning_code=_text(raw["warning_code"]),
                distinct_values=_optional_int(raw["distinct_values"], column="distinct_values")
                or 0,
                values_summary=_text(raw["values_summary"]),
                source_ids=source_ids,
            )
        )
    return tuple(rows)


def _count_coverage(payload: bytes) -> int:
    """Coverage is validated but not imported.

    It is a report about how many documents each month had, which is derivable
    from what *is* imported. Storing it would create a second, independently
    drifting answer to the same question.
    """
    return sum(1 for _ in _rows(payload, path="data/coverage.csv"))


def _check_references(parsed: ParsedPackage) -> None:
    """Every cross-table reference must resolve before anything is written."""
    known = {document.source_id for document in parsed.source_documents}

    for snapshot in parsed.snapshots:
        if snapshot.source_id not in known:
            raise PackageContractError("Vaatlus viitab tundmatule lähtedokumendile.")
    for movement in parsed.movements:
        if movement.source_id not in known:
            raise PackageContractError("Suurusklassi liikumine viitab tundmatule lähtedokumendile.")
    for reason in parsed.removal_reasons:
        if reason.source_id not in known:
            raise PackageContractError("Lahkumise põhjus viitab tundmatule lähtedokumendile.")
    for monthly in parsed.monthly_values:
        if monthly.selected_source_id and monthly.selected_source_id not in known:
            raise PackageContractError("Kuu väärtus viitab tundmatule lähtedokumendile.")
        for source_id in monthly.source_ids:
            if source_id not in known:
                raise PackageContractError("Kuu väärtus viitab tundmatule lähtedokumendile.")
    for warning in parsed.warnings:
        if warning.source_id and warning.source_id not in known:
            raise PackageContractError("Hoiatus viitab tundmatule lähtedokumendile.")

    # Schema 2.0 cross-references. Every one must resolve before anything is
    # returned, so a partially wired package cannot reach the importer.
    for batch in parsed.decision_batches:
        if batch.source_id not in known:
            raise PackageContractError("Otsuse partii viitab tundmatule lähtedokumendile.")
        if batch.corroborating_source_id and batch.corroborating_source_id not in known:
            raise PackageContractError("Otsuse kinnitaja viitab tundmatule lähtedokumendile.")
    batch_ids = {batch.batch_id for batch in parsed.decision_batches}
    for size in parsed.decision_batch_sizes:
        if size.batch_id not in batch_ids:
            raise PackageContractError("Suurusjaotus viitab tundmatule otsuse partiile.")
    for reason in parsed.decision_batch_reasons:
        if reason.batch_id not in batch_ids:
            raise PackageContractError("Lahkumispõhjus viitab tundmatule otsuse partiile.")

    for period in parsed.new_member_periods:
        if period.source_id not in known:
            raise PackageContractError("Perioodi kirje viitab tundmatule lähtedokumendile.")
    period_ids = {period.period_id for period in parsed.new_member_periods}
    monthly_keys = {
        (monthly.calendar_year, monthly.calendar_month) for monthly in parsed.monthly_values
    }
    for size in parsed.new_member_sizes:
        if size.period_id:
            if size.period_id not in period_ids:
                raise PackageContractError("Suurusjaotus viitab tundmatule perioodile.")
        elif (size.calendar_year, size.calendar_month) not in monthly_keys:
            raise PackageContractError("Suurusjaotus viitab tundmatule kalendrikuule.")


def read_package(path: Path | str, *, limits: PackageLimits | None = None) -> ParsedPackage:
    """Validate an approved package and return its typed contents.

    Raises :class:`PackageContractError` and writes nothing on any failure. The
    caller is expected to treat that as "the previous data is still correct".
    """
    limits = limits or PackageLimits()
    package_path = Path(path).expanduser()
    if not package_path.is_file():
        raise PackageContractError("Paketifaili ei leitud.")

    package_sha256, package_size = file_digest(package_path)
    if package_size == 0:
        raise PackageContractError("Paketifail on tühi.")
    if package_size > limits.max_package_bytes:
        raise PackageContractError("Paketifail on liiga suur.")

    if not zipfile.is_zipfile(package_path):
        raise PackageContractError("Pakett ei ole ZIP-fail.")

    try:
        with zipfile.ZipFile(package_path) as archive:
            payloads = _read_members(archive, limits)
    except zipfile.BadZipFile as error:
        raise PackageContractError("Pakett on vigane ZIP-fail.") from error

    manifest_version, listed = _load_manifest(payloads)
    _verify_manifest(payloads, listed, manifest_version)

    source_documents = _parse_source_documents(payloads["data/source_documents.csv"])
    path_to_source = {
        document.relative_path: document.source_id
        for document in source_documents
        if document.relative_path
    }

    parsed = ParsedPackage(
        package_sha256=package_sha256,
        package_size_bytes=package_size,
        manifest_schema_version=manifest_version,
        source_documents=source_documents,
        snapshots=_parse_snapshots(payloads["data/membership_snapshots.csv"]),
        monthly_values=_parse_monthly(payloads["data/monthly_new_members.csv"]),
        movements=_parse_movements(payloads["data/membership_size_movements.csv"]),
        removal_reasons=_parse_removal_reasons(payloads["data/membership_removal_reasons.csv"]),
        warnings=_parse_warnings(payloads["data/extraction_warnings.csv"]),
        conflicts=_parse_conflicts(payloads["data/conflicts.csv"], path_to_source=path_to_source),
        coverage_rows=_count_coverage(payloads["data/coverage.csv"]),
        **_parse_v2_tables(payloads, manifest_version),
    )
    _check_references(parsed)
    return parsed


def _parse_v2_tables(payloads: dict[str, bytes], version: str) -> dict:
    """Parse the schema 2.0 tables, or return nothing at all for a 1.0 package.

    A 1.0 package leaves these tuples empty. That is a statement that the
    package cannot speak about decision batches, not a statement that no batches
    exist — which is why the importer must not read an empty tuple here as
    permission to delete anything.
    """
    if version == "1.0":
        return {}
    return {
        "decision_batches": _parse_decision_batches(payloads["data/decision_batches.csv"]),
        "decision_batch_sizes": _parse_decision_batch_sizes(
            payloads["data/decision_batch_size_movements.csv"]
        ),
        "decision_batch_reasons": _parse_decision_batch_reasons(
            payloads["data/decision_batch_reasons.csv"]
        ),
        "new_member_periods": _parse_new_member_periods(payloads["data/new_member_periods.csv"]),
        "new_member_sizes": _parse_new_member_sizes(
            payloads["data/new_member_size_distribution.csv"]
        ),
    }
