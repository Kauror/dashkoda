"""Importing a workbook the caller holds only temporarily.

The metadata-only artifact carries the content identity; the bytes exist for the
duration of one command. What must stay true is that this is the *same* importer:
same parser, same publication rule, same failure containment — and that the
temporary path never reaches PostgreSQL, the audit trail or the diagnostics.
"""

import hashlib

import pytest

from apps.audit.models import AuditEvent
from apps.legal_work.importer import LegalWorkImportError, import_artifact
from apps.legal_work.models import LegalWorkSnapshot
from apps.sources.models import ImportRun, ImportStatus
from apps.sources.services import register_external_reference

from .workbook_factory import REPORTING_DATE, synthetic_row

pytestmark = pytest.mark.django_db

SAFE_REFERENCE = "onedrive-public:oigusloome"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def metadata_artifact(legal_work_source):
    """A metadata-only artifact for the given workbook's bytes."""

    def build(path):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return register_external_reference(
            source=legal_work_source,
            external_reference=SAFE_REFERENCE,
            original_name="dashkoda_oigusloome.xlsx",
            mime_type=XLSX_MIME,
            sha256=digest,
            size_bytes=path.stat().st_size,
        )

    return build


def test_a_metadata_only_artifact_imports_from_a_supplied_path(make_workbook, metadata_artifact):
    path = make_workbook()
    artifact = metadata_artifact(path)

    result = import_artifact(artifact, workbook_path=path, dry_run=False)

    snapshot = result.snapshot
    assert snapshot.is_current is True
    assert snapshot.total_record_count == 3
    assert snapshot.items.count() == 3
    assert snapshot.reporting_date == REPORTING_DATE
    assert snapshot.artifact_id == artifact.pk
    assert not artifact.file


def test_a_metadata_only_artifact_without_a_path_fails_safely(make_workbook, metadata_artifact):
    artifact = metadata_artifact(make_workbook())

    with pytest.raises(LegalWorkImportError, match="Välise viitega"):
        import_artifact(artifact, dry_run=False)

    assert LegalWorkSnapshot.objects.count() == 0


def test_a_file_backed_artifact_still_imports_without_a_path(make_workbook, register_workbook):
    artifact = register_workbook(make_workbook())

    result = import_artifact(artifact, dry_run=False)

    assert result.snapshot.is_current is True
    assert result.rows_added == 3


def test_a_supplied_path_overrides_a_stored_file(make_workbook, register_workbook):
    """One artifact, two byte sequences, and the supplied path is what is read.

    Contrived on purpose: it proves the path argument is honoured rather than
    silently ignored in favour of the stored file.
    """
    artifact = register_workbook(make_workbook())
    other = make_workbook(rows=[synthetic_row(record_id="SYN-ONLY", source_row=2)])

    result = import_artifact(artifact, workbook_path=other, dry_run=False)

    assert result.rows_added == 1


def test_a_missing_supplied_path_is_refused(make_workbook, metadata_artifact, tmp_path):
    artifact = metadata_artifact(make_workbook())

    with pytest.raises(LegalWorkImportError, match="ei leitud"):
        import_artifact(artifact, workbook_path=tmp_path / "absent.xlsx", dry_run=False)

    assert LegalWorkSnapshot.objects.count() == 0


def test_a_supplied_path_that_is_not_a_workbook_is_refused(
    make_workbook, metadata_artifact, tmp_path
):
    artifact = metadata_artifact(make_workbook())
    intruder = tmp_path / "synthetic.bin"
    intruder.write_bytes(b"not a workbook at all")

    with pytest.raises(LegalWorkImportError, match=".xlsx"):
        import_artifact(artifact, workbook_path=intruder, dry_run=False)


def test_a_dry_run_from_a_supplied_path_creates_no_snapshot(make_workbook, metadata_artifact):
    path = make_workbook()
    artifact = metadata_artifact(path)

    result = import_artifact(artifact, workbook_path=path, dry_run=True)

    assert result.snapshot is None
    assert LegalWorkSnapshot.objects.count() == 0
    assert result.import_run.status == ImportStatus.SUCCEEDED
    assert result.import_run.dry_run is True


def test_a_failed_supplied_path_import_preserves_the_previous_snapshot(
    imported_snapshot, make_workbook, metadata_artifact
):
    broken = make_workbook(control_overrides={"total_record_count": 999})
    artifact = metadata_artifact(broken)

    with pytest.raises(LegalWorkImportError):
        import_artifact(artifact, workbook_path=broken, dry_run=False)

    imported_snapshot.refresh_from_db()
    assert imported_snapshot.is_current is True
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1


def test_the_temporary_path_is_never_persisted(make_workbook, metadata_artifact):
    path = make_workbook()
    artifact = metadata_artifact(path)

    result = import_artifact(artifact, workbook_path=path, dry_run=False)

    fragments = (str(path), path.name, str(path.parent))
    run = ImportRun.objects.get(pk=result.import_run.pk)
    haystacks = [
        str(run.warnings),
        str(run.errors),
        artifact.external_reference,
        *[str(event.change_summary) for event in AuditEvent.objects.all()],
    ]
    for haystack in haystacks:
        for fragment in fragments:
            assert fragment not in haystack


def test_failure_diagnostics_from_a_supplied_path_carry_no_content_or_path(
    make_workbook, metadata_artifact
):
    secret_topic = "Sünteetiline salajane pealkiri"
    broken = make_workbook(
        rows=[synthetic_row(record_id="SYN-1", topic=secret_topic, is_open="jah")]
    )
    artifact = metadata_artifact(broken)

    with pytest.raises(LegalWorkImportError):
        import_artifact(artifact, workbook_path=broken, dry_run=False)

    run = ImportRun.objects.get()
    assert secret_topic not in str(run.errors)
    assert str(broken) not in str(run.errors)
