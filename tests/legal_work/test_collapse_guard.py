"""The legal-work feed refuses a workbook that collapses against what is published.

This is the feed the failure actually happened on: a source format change made
every 2025 and 2026 record fail to parse, and the export that reached the
dashboard held only the 2024 rows. Nothing compared it with the day before.
"""

import pytest

from apps.legal_work.importer import LegalWorkImportError, import_artifact
from apps.legal_work.models import LegalWorkItem, LegalWorkSnapshot

from .workbook_factory import synthetic_row

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def guard_at_the_production_floor(settings):
    """The suite disables the guard; these tests are the ones that need it on."""
    settings.FEED_COLLAPSE_MIN_RATIO = 0.5


def _current():
    return LegalWorkSnapshot.objects.filter(is_current=True).first()


def _rows(count: int) -> list:
    # `source_row` has to be distinct as well as `source_nr`: the contract rejects
    # a repeated (source_year, source_row) pair, and every row here shares 2099.
    return [
        synthetic_row(
            record_id=f"OIG-2099-{index + 1:04d}",
            source_nr=index + 1,
            source_row=index + 2,
        )
        for index in range(count)
    ]


def test_a_collapsed_workbook_is_refused_and_the_published_snapshot_survives(
    make_workbook, register_workbook
):
    artifact = register_workbook(make_workbook(rows=_rows(10)))
    import_artifact(artifact, dry_run=False)
    before = _current()
    assert before.total_record_count == 10

    collapsed = register_workbook(make_workbook(rows=_rows(2)))
    with pytest.raises(LegalWorkImportError):
        import_artifact(collapsed, dry_run=False)

    after = _current()
    assert after.pk == before.pk
    assert after.total_record_count == 10
    assert LegalWorkItem.objects.filter(snapshot=after).count() == 10
    assert LegalWorkSnapshot.objects.filter(is_current=True).count() == 1


def test_allow_collapse_publishes_the_smaller_dataset(make_workbook, register_workbook):
    import_artifact(register_workbook(make_workbook(rows=_rows(10))), dry_run=False)

    collapsed = register_workbook(make_workbook(rows=_rows(2)))
    result = import_artifact(collapsed, dry_run=False, allow_collapse=True)

    assert result.snapshot is not None
    assert _current().total_record_count == 2


def test_growth_is_never_refused(make_workbook, register_workbook):
    import_artifact(register_workbook(make_workbook(rows=_rows(2))), dry_run=False)

    grown = register_workbook(make_workbook(rows=_rows(10)))
    import_artifact(grown, dry_run=False)

    assert _current().total_record_count == 10


def test_a_first_import_has_nothing_to_collapse_against(make_workbook, register_workbook):
    result = import_artifact(register_workbook(make_workbook(rows=_rows(1))), dry_run=False)

    assert result.snapshot is not None
    assert _current().total_record_count == 1


def test_a_dry_run_reports_the_refusal_rather_than_passing_quietly(
    make_workbook, register_workbook
):
    import_artifact(register_workbook(make_workbook(rows=_rows(10))), dry_run=False)

    collapsed = register_workbook(make_workbook(rows=_rows(2)))
    with pytest.raises(LegalWorkImportError):
        import_artifact(collapsed, dry_run=True)

    assert _current().total_record_count == 10
