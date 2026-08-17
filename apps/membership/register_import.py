"""Import the member roster's rows from a CRM export.

The companion to `composition_import.py`, and the deliberate opposite of it:
that importer streams the same file and keeps only counts, this one keeps the
rows the members-list page lists and the directory comparison joins on. Both
read the same export, neither stores the file, and the column boundary in
`models/register.py` is what keeps "keeps rows" from meaning "keeps
everything".

## The export is a UTF-16 TSV that calls itself .csv

The CRM writes UTF-16 with a byte-order mark and separates with tabs while
naming the file `.csv`. Opening it as UTF-8 yields one column of NUL-riddled
text and every heading lookup fails — so the encoding and the delimiter are
both **detected from the bytes**, never assumed and never taken from the
extension. A file whose first line does not decode to the expected headings is
refused by name, not silently parsed into a page full of blanks.

Dates are text here rather than typed cells, which is the one thing the CSV
path makes easier than the xlsx path: `17.12.2025` is unambiguous, whereas the
same value in a spreadsheet arrives as whatever Excel decided it meant. They
are parsed strictly as `dd.mm.yyyy` and an unparseable date is left empty and
counted, never guessed at.

## The snapshot date is an argument, not a filename

Identical rule to the composition import, for the identical reason: the export
states no date of its own, and reading one out of a file name would make a
rename a data edit.

## Idempotency

The import key is the importer name, the schema version and the file's
SHA-256, so re-running the same export writes nothing. A different export is a
new reading and needs `--supersede-previous`, which retires the current
snapshot without deleting it or its rows.

`SCHEMA_VERSION` must be bumped whenever this parser's output shape changes,
because it is part of that key — the identity of a reading has to include
everything that decides what the reading says, or a re-import of the same
bytes under new rules is refused as unchanged. That is the deadlock shape this
repository has hit before.
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from django.db import transaction

from apps.audit.services import record_event
from apps.sources.models import ImportRun, ImportStatus, SourceArtifact
from apps.sources.services import (
    build_import_run,
    calculate_import_key,
    complete_import_run,
    fail_publication,
    register_external_reference,
    start_import_run,
)

from .audit_actions import MembershipAudit
from .bootstrap import ensure_member_register_source
from .composition import STATUS_LABELS, classify_status
from .models import MemberRegisterEntry, MemberRegisterSnapshot

IMPORTER_NAME = "member_register_csv"
#: Bump on any change to what a row becomes. Part of the import key.
SCHEMA_VERSION = "1.0"

ARTIFACT_REFERENCE_PREFIX = "roster:member-register"
ARTIFACT_MIME = "text/csv"

# The export is about 2 MB of UTF-16. Twenty is room for the membership to grow
# several times over and still refuses a file that is not a roster at all.
MAX_SOURCE_BYTES = 20 * 1024 * 1024

COLUMN_NAME = "Ettevõte"
COLUMN_LEGAL_FORM = "Vorm"
COLUMN_NUMBER = "Number"
COLUMN_STATUS = "Staatus"
COLUMN_CITY = "Linn"
COLUMN_COUNTY = "Maakond"
COLUMN_COUNTRY = "Riik"
COLUMN_WEBSITE = "www"
COLUMN_EMPLOYEES = "Töötajate arv"
COLUMN_REGISTRY_CODE = "Registrikood"
COLUMN_START = "Algus kp."
COLUMN_NACE_CODE = "Nace kood"
COLUMN_NACE_LABEL = "Nace kirjeldus"

#: Without these the file is not this roster and is refused rather than
#: imported as a page of blanks.
REQUIRED_COLUMNS: tuple[str, ...] = (
    COLUMN_NAME,
    COLUMN_STATUS,
    COLUMN_REGISTRY_CODE,
)

#: Read, but a missing one is a gap in a row rather than a broken file.
OPTIONAL_COLUMNS: tuple[str, ...] = (
    COLUMN_LEGAL_FORM,
    COLUMN_NUMBER,
    COLUMN_CITY,
    COLUMN_COUNTY,
    COLUMN_COUNTRY,
    COLUMN_WEBSITE,
    COLUMN_EMPLOYEES,
    COLUMN_START,
    COLUMN_NACE_CODE,
    COLUMN_NACE_LABEL,
)

# Everything else in the export — the street address, the postal index, the two
# e-mail columns, the fax and phone numbers, the director's name and personal
# e-mail, the VAT number, the free-text comment and the NACE comment — is
# deliberately absent from both lists, and a column that is not listed is never
# read. `Töötaja vahemik` is absent for the additional reason recorded in
# `composition_import.py`: Excel has coerced two thirds of its values into
# dates, and the integer `Töötajate arv` beside it is complete.

DELIMITERS: tuple[str, ...] = ("\t", ";", ",")


class RegisterImportError(RuntimeError):
    """A refusal that names a column or a count, never a cell value."""


@dataclass(frozen=True)
class RegisterRow:
    """One roster row, reduced to the columns this product stores."""

    name: str
    legal_form: str
    member_number: str
    status_key: str
    status_label: str
    registry_code: str | None
    county: str
    city: str
    country: str
    employees: int | None
    membership_start: date | None
    nace_code: str
    nace_label: str
    website: str


@dataclass(frozen=True)
class RegisterReading:
    """What one export said, plus what was wrong with it."""

    snapshot_date: date
    rows: tuple[RegisterRow, ...]
    rows_read: int
    duplicate_codes: int = 0
    missing_codes: int = 0
    unreadable_starts: int = 0
    future_starts: int = 0
    unknown_statuses: int = 0

    def diagnostics(self) -> list[dict]:
        """Counts and codes only. No cell value is ever named."""
        found = [
            ("duplicate_registry_code", self.duplicate_codes),
            ("missing_registry_code", self.missing_codes),
            ("unreadable_membership_start", self.unreadable_starts),
            ("membership_start_after_snapshot", self.future_starts),
            ("unmapped_status", self.unknown_statuses),
        ]
        return [{"code": code, "rows": count} for code, count in found if count]


@dataclass(frozen=True)
class RegisterImportResult:
    import_run: ImportRun
    dry_run: bool
    unchanged: bool
    source_sha256: str
    snapshot_date: date | None
    rows_read: int = 0
    rows_written: int = 0
    superseded: int = 0
    diagnostics: tuple[dict, ...] = field(default_factory=tuple)

    def as_json(self) -> dict:
        return {
            "importer": IMPORTER_NAME,
            "schema_version": SCHEMA_VERSION,
            "import_run_id": self.import_run.pk if self.import_run else None,
            "dry_run": self.dry_run,
            "unchanged": self.unchanged,
            "source_sha256": self.source_sha256,
            "snapshot_date": self.snapshot_date.isoformat() if self.snapshot_date else None,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "superseded_snapshots": self.superseded,
            "diagnostics": list(self.diagnostics),
        }


def _checksum(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def decode(raw: bytes) -> str:
    """Decode the export by its byte-order mark, not by hope.

    The CRM writes UTF-16 LE with a BOM. Nothing in the file name says so, and
    decoding it as UTF-8 succeeds well enough to produce garbage rather than an
    error — every heading gains interleaved NULs and every column lookup misses.
    So the mark decides, and a file with no mark is treated as UTF-8 with the
    signature form tried first.
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RegisterImportError(
            "Lähtefaili kodeeringut ei õnnestunud tuvastada (oodati UTF-16 või UTF-8)."
        ) from error


def _pick_delimiter(header_line: str) -> str:
    """The separator that actually splits this header into the most columns.

    `csv.Sniffer` guesses from a sample and has been known to answer with a
    character that appears inside a company name. Counting candidates on the
    header line is duller and cannot be surprised by the data.
    """
    best = max(DELIMITERS, key=header_line.count)
    if header_line.count(best) == 0:
        raise RegisterImportError(
            "Lähtefaili esimesel real ei ole eraldajat "
            "(oodati tabulaatorit, semikoolonit või koma)."
        )
    return best


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _digits(value: str | None) -> str:
    return "".join(character for character in (value or "") if character.isdigit())


def _as_int(value: str | None) -> int | None:
    digits = _digits(value)
    if not digits:
        return None
    number = int(digits)
    # A five-figure headcount is not a Chamber member, it is a parse fault.
    return number if number <= 100_000 else None


def _as_date(value: str | None) -> date | None:
    """`dd.mm.yyyy`, or nothing. An unreadable date is never guessed at."""
    text = _clean(value)
    if not text:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def read_register(path: Path | str, *, snapshot_date: date) -> RegisterReading:
    """Parse the export into rows. Refuses a file that is not this roster."""
    path = Path(path)
    if not path.is_file():
        raise RegisterImportError("Lähtefaili ei leitud.")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise RegisterImportError("Lähtefail on lubatust suurem.")

    text = decode(path.read_bytes())
    first_line = text.splitlines()[0] if text else ""
    if not first_line:
        raise RegisterImportError("Lähtefail on tühi.")

    reader = csv.DictReader(io.StringIO(text), delimiter=_pick_delimiter(first_line))
    headings = {_clean(name) for name in (reader.fieldnames or [])}
    missing = [name for name in REQUIRED_COLUMNS if name not in headings]
    if missing:
        # Column names are structure, not content, so naming them is safe and is
        # the only way the refusal is actionable.
        raise RegisterImportError("Lähtefailis puuduvad veerud: " + ", ".join(missing))

    rows: list[RegisterRow] = []
    seen_codes: set[str] = set()
    rows_read = 0
    duplicates = 0
    missing_codes = 0
    unreadable_starts = 0
    future_starts = 0
    unknown_statuses = 0

    for raw_row in reader:
        row = {_clean(key): value for key, value in raw_row.items() if key is not None}
        name = _clean(row.get(COLUMN_NAME))
        if not name and not _clean(row.get(COLUMN_REGISTRY_CODE)):
            # A trailing blank line, not a member.
            continue
        rows_read += 1

        code = _digits(row.get(COLUMN_REGISTRY_CODE)) or None
        if code is None:
            missing_codes += 1
        elif code in seen_codes:
            # One member listed twice is a source quirk, not a reason to refuse
            # the whole export — the same judgement the count collector makes.
            # The first row wins and the repeat is reported.
            duplicates += 1
            continue
        else:
            seen_codes.add(code)

        raw_status = _clean(row.get(COLUMN_STATUS))
        status_key = classify_status(raw_status)
        if status_key not in STATUS_LABELS or status_key == "unknown":
            unknown_statuses += 1

        start = _as_date(row.get(COLUMN_START))
        if start is None and _clean(row.get(COLUMN_START)):
            unreadable_starts += 1
        if start is not None and start > snapshot_date:
            future_starts += 1

        rows.append(
            RegisterRow(
                name=name[:200],
                legal_form=_clean(row.get(COLUMN_LEGAL_FORM))[:16],
                member_number=_clean(row.get(COLUMN_NUMBER))[:16],
                status_key=status_key,
                status_label=raw_status[:32],
                registry_code=code[:16] if code else None,
                county=_clean(row.get(COLUMN_COUNTY))[:64],
                city=_clean(row.get(COLUMN_CITY))[:64],
                country=_clean(row.get(COLUMN_COUNTRY))[:64],
                employees=_as_int(row.get(COLUMN_EMPLOYEES)),
                membership_start=start,
                nace_code=_digits(row.get(COLUMN_NACE_CODE))[:8],
                nace_label=_clean(row.get(COLUMN_NACE_LABEL))[:160],
                website=_clean(row.get(COLUMN_WEBSITE))[:200],
            )
        )

    if not rows:
        raise RegisterImportError("Lähtefailis ei ole ühtegi andmerida.")

    return RegisterReading(
        snapshot_date=snapshot_date,
        rows=tuple(rows),
        rows_read=rows_read,
        duplicate_codes=duplicates,
        missing_codes=missing_codes,
        unreadable_starts=unreadable_starts,
        future_starts=future_starts,
        unknown_statuses=unknown_statuses,
    )


def _ensure_artifact(source, *, sha256: str, size: int, actor, correlation_id) -> SourceArtifact:
    """Register the reading's identity without keeping the file.

    Bytes already registered are reused rather than registered twice: the
    documented sequence is a dry run followed by the live import of the very
    same file, and refusing its own first half is the defect this repository
    has already fixed once. Whether these bytes were ever *published* is a
    different question and stays with the import key.
    """
    existing = SourceArtifact.objects.filter(source=source, sha256=sha256).first()
    if existing is not None:
        return existing
    return register_external_reference(
        source=source,
        external_reference=f"{ARTIFACT_REFERENCE_PREFIX}:{sha256}",
        sha256=sha256,
        size_bytes=size,
        mime_type=ARTIFACT_MIME,
        actor=actor,
        correlation_id=correlation_id,
    )


def _existing_successful_run(import_key: str) -> ImportRun | None:
    return (
        ImportRun.objects.filter(
            import_key=import_key, status=ImportStatus.SUCCEEDED, dry_run=False
        )
        .order_by("-id")
        .first()
    )


def _retire_current(source, *, supersede_previous: bool) -> list[MemberRegisterSnapshot]:
    current = list(
        MemberRegisterSnapshot.objects.select_for_update().filter(source=source, is_current=True)
    )
    if not current:
        return []
    if not supersede_previous:
        raise RegisterImportError(
            "Liikmete nimekiri on juba imporditud. Uue importimiseks kasuta "
            "--supersede-previous, mis märgib varasema asendatuks. Midagi ei kustutata."
        )
    return current


def import_member_register(
    path: Path | str,
    *,
    snapshot_date: date,
    dry_run: bool = True,
    supersede_previous: bool = False,
    actor=None,
    correlation_id: uuid.UUID | None = None,
) -> RegisterImportResult:
    """Read the export and store its rows as one dated snapshot."""
    path = Path(path)
    if not path.is_file():
        raise RegisterImportError("Lähtefaili ei leitud.")

    sha256, size = _checksum(path)
    source = ensure_member_register_source(actor=actor, correlation_id=correlation_id)
    import_key = calculate_import_key(IMPORTER_NAME, SCHEMA_VERSION, sha256)

    already = _existing_successful_run(import_key)
    if already is not None and not dry_run:
        record_event(
            action=MembershipAudit.REGISTER_UNCHANGED,
            obj=already,
            actor=actor,
            correlation_id=already.correlation_id,
            change_summary={
                "source": source.slug,
                "source_sha256": sha256,
                "import_key": import_key,
            },
        )
        return RegisterImportResult(
            import_run=already,
            dry_run=False,
            unchanged=True,
            source_sha256=sha256,
            snapshot_date=snapshot_date,
        )

    reading = read_register(path, snapshot_date=snapshot_date)
    diagnostics = reading.diagnostics()

    artifact = _ensure_artifact(
        source, sha256=sha256, size=size, actor=actor, correlation_id=correlation_id
    )
    run = build_import_run(
        artifact=artifact,
        importer_name=IMPORTER_NAME,
        schema_version=SCHEMA_VERSION,
        dry_run=dry_run,
        initiated_by=actor,
        actor=actor,
        correlation_id=correlation_id,
    )
    start_import_run(run)

    try:
        if dry_run:
            complete_import_run(
                run,
                rows_added=0,
                rows_skipped=reading.rows_read,
                warnings=diagnostics,
                actor=actor,
            )
            return RegisterImportResult(
                import_run=run,
                dry_run=True,
                unchanged=False,
                source_sha256=sha256,
                snapshot_date=snapshot_date,
                rows_read=reading.rows_read,
                diagnostics=tuple(diagnostics),
            )

        with transaction.atomic():
            retiring = _retire_current(source, supersede_previous=supersede_previous)
            # Retire before writing: `one_current_per_source` is a partial unique
            # constraint and fires the moment a second current row exists.
            for previous in retiring:
                previous.is_current = False
                previous.save(update_fields=["is_current"])

            snapshot = MemberRegisterSnapshot.objects.create(
                source=source,
                import_run=run,
                snapshot_date=snapshot_date,
                source_sha256=sha256,
                source_row_count=reading.rows_read,
                is_current=True,
            )
            for previous in retiring:
                previous.superseded_by = snapshot
                previous.save(update_fields=["superseded_by"])

            MemberRegisterEntry.objects.bulk_create(
                [
                    MemberRegisterEntry(
                        snapshot=snapshot,
                        name=row.name,
                        legal_form=row.legal_form,
                        member_number=row.member_number,
                        status_key=row.status_key,
                        status_label=row.status_label,
                        registry_code=row.registry_code,
                        county=row.county,
                        city=row.city,
                        country=row.country,
                        employees=row.employees,
                        membership_start=row.membership_start,
                        nace_code=row.nace_code,
                        nace_label=row.nace_label,
                        website=row.website,
                    )
                    for row in reading.rows
                ],
                batch_size=500,
            )
            written = len(reading.rows)

            complete_import_run(
                run,
                rows_added=written + 1,
                rows_skipped=reading.rows_read - written,
                warnings=diagnostics,
                actor=actor,
            )
            record_event(
                action=MembershipAudit.REGISTER_IMPORTED,
                obj=snapshot,
                actor=actor,
                correlation_id=run.correlation_id,
                change_summary={
                    "source": source.slug,
                    "source_sha256": sha256,
                    "snapshot_date": snapshot_date.isoformat(),
                    "rows_read": reading.rows_read,
                    "rows_written": written,
                    "superseded_snapshots": len(retiring),
                },
            )

        return RegisterImportResult(
            import_run=run,
            dry_run=False,
            unchanged=False,
            source_sha256=sha256,
            snapshot_date=snapshot_date,
            rows_read=reading.rows_read,
            rows_written=written,
            superseded=len(retiring),
            diagnostics=tuple(diagnostics),
        )
    except Exception as error:
        fail_publication(run, errors=[{"code": "register_import_failed"}], actor=actor)
        if isinstance(error, RegisterImportError):
            raise
        raise RegisterImportError("Liikmete nimekirja import ebaõnnestus.") from error
