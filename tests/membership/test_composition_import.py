"""The roster importer: what it counts, and what it must never keep.

The roster is the only personal data this application ever opens, so the tests
that matter most here are the ones proving nothing personal survives the import.
Every identity below is invented.

The workbook-shaped tests run without PostgreSQL; the ones that actually import
are marked `django_db` and run in CI.
"""

from __future__ import annotations

import datetime as dt

import pytest
from openpyxl import Workbook

from apps.membership.composition import Dimension, Population
from apps.membership.composition_import import (
    COLUMN_CORRUPTED_BY_EXCEL,
    CompositionImportError,
    import_composition_snapshot,
    read_roster,
    validate,
)

SNAPSHOT = dt.date(2026, 6, 9)

# Wholly invented identities. Each string is distinctive enough that a substring
# search for it cannot match a vocabulary term, a label or a checksum by
# accident — which is what makes the privacy assertion meaningful.
FAKE_ROWS = [
    {
        "Ettevõte": "Kuutõrvaja Masinaehitus OÜ",
        "Vorm": "OÜ",
        "Staatus": "Koja liige",
        "Aadress": "Väljamõeldud puiestee 14, Tallinn",
        "Linn": "TALLINN",
        "Maakond": "HARJUMAA",
        "Üld e-post": "kontakt@kuutorvaja-example.invalid",
        "Firma juht": "Mihkel Väljamõeldud",
        "Email": "mihkel@kuutorvaja-example.invalid",
        "Registrikood": 99900001,
        "Kommentaar": "Salajane märkus Kuutõrvaja kohta",
        "Töötajate arv": 12,
        "Algus kp.": dt.datetime(2015, 3, 1),
        "Nace kood": 4649,
    },
    {
        "Ettevõte": "Vesipapi Logistika AS",
        "Vorm": "AS",
        "Staatus": "Peatatud liige",
        "Aadress": "Olematu tänav 3, Tartu",
        "Linn": "TARTU LINN",
        "Maakond": "TARTUMAA",
        "Üld e-post": "info@vesipapi-example.invalid",
        "Firma juht": "Kadri Olematu",
        "Email": "kadri@vesipapi-example.invalid",
        "Registrikood": 99900002,
        "Kommentaar": "",
        "Töötajate arv": 260,
        "Algus kp.": dt.datetime(2026, 5, 1),
        "Nace kood": 4941,
    },
    {
        "Ettevõte": "Pilvelõhkuja Tarkvara OÜ",
        "Vorm": "OÜ",
        "Staatus": "Toetaja liige",
        "Aadress": "Puuduv põik 9, Pärnu",
        "Linn": "PÄRNU LINN",
        "Maakond": "PÄRNUMAA",
        "Üld e-post": "hei@pilvelohkuja-example.invalid",
        "Firma juht": "Toomas Puuduv",
        "Email": "toomas@pilvelohkuja-example.invalid",
        "Registrikood": 99900003,
        "Kommentaar": "Ei soovi uudiskirja",
        "Töötajate arv": 0,
        "Algus kp.": dt.datetime(1999, 9, 9),
        "Nace kood": 6201,
    },
]

#: Every value that must not survive the import, in one list.
IDENTIFYING_VALUES = [
    value
    for row in FAKE_ROWS
    for key, value in row.items()
    if key
    in {
        "Ettevõte",
        "Aadress",
        "Üld e-post",
        "Firma juht",
        "Email",
        "Registrikood",
        "Kommentaar",
    }
    and value not in ("", None)
]

COLUMNS = [
    "Ettevõte",
    "Vorm",
    "Staatus",
    "Aadress",
    "Linn",
    "Maakond",
    "Üld e-post",
    "Firma juht",
    "Email",
    "Registrikood",
    "Kommentaar",
    "Töötajate arv",
    COLUMN_CORRUPTED_BY_EXCEL,
    "Algus kp.",
    "Nace kood",
]


def write_roster(path, rows=None, *, columns=None) -> str:
    """A synthetic roster workbook, shaped like the real export."""
    workbook = Workbook()
    sheet = workbook.active
    headings = list(columns if columns is not None else COLUMNS)
    sheet.append(headings)
    for row in rows if rows is not None else FAKE_ROWS:
        sheet.append(
            [
                # The corrupted column is written as a date on purpose: that is
                # what Excel has actually done to it in the real roster, and the
                # importer must not be reading it.
                dt.datetime(2020, 1, 4)
                if heading == COLUMN_CORRUPTED_BY_EXCEL
                else row.get(heading)
                for heading in headings
            ]
        )
    workbook.save(path)
    return str(path)


# ---------------------------------------------------------------------------
# Reading (no database)
# ---------------------------------------------------------------------------


def test_the_roster_is_reduced_to_counts(tmp_path):
    tally = read_roster(write_roster(tmp_path / "roster.xlsx"), snapshot_date=SNAPSHOT)

    assert tally.rows_read == 3
    assert tally.total(Population.ALL_CURRENT) == 3
    statuses = tally.category_counts(Population.ALL_CURRENT, Dimension.STATUS)
    assert statuses == {"regular": 1, "suspended": 1, "supporter": 1}


def test_only_members_inside_the_window_are_recent_joiners(tmp_path):
    tally = read_roster(write_roster(tmp_path / "roster.xlsx"), snapshot_date=SNAPSHOT)

    assert tally.total(Population.RECENT_JOINERS) == 1


def test_the_excel_corrupted_size_column_is_not_read(tmp_path):
    """Two thirds of `Töötaja vahemik` are dates. Reading it would put invented
    size classes on the page; the integer column beside it is what is used."""
    tally = read_roster(write_roster(tmp_path / "roster.xlsx"), snapshot_date=SNAPSHOT)
    sizes = tally.category_counts(Population.ALL_CURRENT, Dimension.EMPLOYEE_SIZE)

    assert sizes == {"employees_10_49": 1, "employees_250_plus": 1, "employees_0": 1}


def test_a_missing_required_column_is_refused_rather_than_read_leniently(tmp_path):
    """Producing a page full of `Teadmata` from a wrong file is worse than
    refusing it, because the page would look like a finding."""
    columns = [column for column in COLUMNS if column != "Maakond"]
    path = write_roster(tmp_path / "roster.xlsx", columns=columns)

    with pytest.raises(CompositionImportError) as error:
        read_roster(path, snapshot_date=SNAPSHOT)

    assert "Maakond" in str(error.value)


def test_an_empty_roster_is_refused(tmp_path):
    path = write_roster(tmp_path / "roster.xlsx", rows=[])

    with pytest.raises(CompositionImportError):
        read_roster(path, snapshot_date=SNAPSHOT)


def test_a_membership_start_after_the_snapshot_is_reported_not_counted(tmp_path):
    future = dict(FAKE_ROWS[0], **{"Algus kp.": dt.datetime(2026, 12, 1)})
    path = write_roster(tmp_path / "roster.xlsx", rows=[future])

    tally = read_roster(path, snapshot_date=SNAPSHOT)
    diagnostics = validate(tally)

    assert any(d["code"] == "membership_start_after_snapshot" for d in diagnostics)
    tenure = tally.category_counts(Population.ALL_CURRENT, Dimension.TENURE_BAND)
    assert tenure == {"unknown": 1}


def test_validation_reports_unclassified_values_without_blocking_the_import(tmp_path):
    odd = dict(FAKE_ROWS[0], **{"Staatus": "Auliige"})
    path = write_roster(tmp_path / "roster.xlsx", rows=[odd])

    diagnostics = validate(read_roster(path, snapshot_date=SNAPSHOT))

    assert any(
        d["code"] == "unclassified_values" and d["dimension"] == Dimension.STATUS
        for d in diagnostics
    )


def test_no_diagnostic_carries_a_cell_value(tmp_path):
    odd = dict(FAKE_ROWS[0], **{"Staatus": "Auliige", "Nace kood": None})
    path = write_roster(tmp_path / "roster.xlsx", rows=[odd])

    diagnostics = validate(read_roster(path, snapshot_date=SNAPSHOT))
    text = repr(diagnostics)

    for value in IDENTIFYING_VALUES:
        assert str(value) not in text


# ---------------------------------------------------------------------------
# Importing (database)
# ---------------------------------------------------------------------------


def stored_text() -> str:
    """Every value in both composition tables, as one searchable string."""
    from apps.membership.models import (
        MembershipCompositionSnapshot,
        MembershipCompositionValue,
    )

    parts: list[str] = []
    for model in (MembershipCompositionSnapshot, MembershipCompositionValue):
        for instance in model.objects.all():
            for field in instance._meta.get_fields():
                if not hasattr(field, "attname"):
                    continue
                parts.append(f"{getattr(instance, field.attname, '')!r}")
    return " ".join(parts)


@pytest.mark.django_db
def test_no_member_identity_is_persisted(tmp_path):
    """The privacy regression test.

    Imports a roster full of invented names, registry codes, addresses,
    contacts and comments, then searches everything the composition models hold
    for any of them. Nothing may match.
    """
    import_composition_snapshot(
        write_roster(tmp_path / "roster.xlsx"), snapshot_date=SNAPSHOT, dry_run=False
    )

    haystack = stored_text()
    assert haystack, "nothing was stored, so the assertion would pass vacuously"

    for value in IDENTIFYING_VALUES:
        assert str(value) not in haystack, f"identifying value reached the database: {value!r}"


@pytest.mark.django_db
def test_no_member_identity_reaches_the_import_run_or_the_audit_trail(tmp_path):
    from apps.audit.models import AuditEvent

    result = import_composition_snapshot(
        write_roster(tmp_path / "roster.xlsx"), snapshot_date=SNAPSHOT, dry_run=False
    )

    run = result.import_run
    run.refresh_from_db()
    text = " ".join(
        [
            repr(run.warnings),
            repr(run.errors),
            repr(run.import_key),
            repr(result.as_json()),
            " ".join(repr(event.change_summary) for event in AuditEvent.objects.all()),
        ]
    )

    for value in IDENTIFYING_VALUES:
        assert str(value) not in text


@pytest.mark.django_db
def test_the_source_file_is_not_stored_only_its_checksum(tmp_path):
    from apps.sources.models import SourceArtifact

    result = import_composition_snapshot(
        write_roster(tmp_path / "roster.xlsx"), snapshot_date=SNAPSHOT, dry_run=False
    )

    artifact = SourceArtifact.objects.get(sha256=result.source_sha256)
    assert not artifact.file
    assert result.source_sha256 in artifact.external_reference


@pytest.mark.django_db
def test_a_dry_run_validates_and_writes_no_snapshot(tmp_path):
    from apps.membership.models import MembershipCompositionSnapshot

    result = import_composition_snapshot(
        write_roster(tmp_path / "roster.xlsx"), snapshot_date=SNAPSHOT, dry_run=True
    )

    assert result.dry_run is True
    assert result.rows_read == 3
    assert not MembershipCompositionSnapshot.objects.exists()


@pytest.mark.django_db
def test_reimporting_the_identical_file_changes_nothing(tmp_path):
    from apps.membership.models import MembershipCompositionValue

    path = write_roster(tmp_path / "roster.xlsx")
    import_composition_snapshot(path, snapshot_date=SNAPSHOT, dry_run=False)
    before = MembershipCompositionValue.objects.count()

    again = import_composition_snapshot(path, snapshot_date=SNAPSHOT, dry_run=False)

    assert again.unchanged is True
    assert MembershipCompositionValue.objects.count() == before


@pytest.mark.django_db
def test_the_documented_dry_run_then_import_sequence_succeeds(tmp_path):
    """A dry run must not block the live import of the very same file.

    The command's own help tells the operator to validate with `--dry-run`
    first. The dry run registers the artifact for provenance; the live import
    of the identical bytes has to reuse that artifact rather than refuse to
    register it a second time — which is exactly how the first production
    roster import failed.
    """
    from apps.membership.models import MembershipCompositionSnapshot
    from apps.sources.models import SourceArtifact

    path = write_roster(tmp_path / "roster.xlsx")
    rehearsal = import_composition_snapshot(path, snapshot_date=SNAPSHOT, dry_run=True)
    result = import_composition_snapshot(path, snapshot_date=SNAPSHOT, dry_run=False)

    assert rehearsal.dry_run is True
    assert result.unchanged is False
    assert result.values_written > 0
    assert MembershipCompositionSnapshot.objects.filter(is_current=True).count() == 1
    # One artifact for one checksum, however many runs consumed it.
    assert SourceArtifact.objects.filter(sha256=result.source_sha256).count() == 1


@pytest.mark.django_db
def test_a_different_roster_will_not_import_over_an_existing_one_by_accident(tmp_path):
    import_composition_snapshot(
        write_roster(tmp_path / "first.xlsx"), snapshot_date=SNAPSHOT, dry_run=False
    )
    revised = write_roster(tmp_path / "second.xlsx", rows=FAKE_ROWS[:2])

    with pytest.raises(CompositionImportError) as error:
        import_composition_snapshot(revised, snapshot_date=SNAPSHOT, dry_run=False)

    assert "supersede" in str(error.value)


@pytest.mark.django_db
def test_a_revision_supersedes_the_previous_snapshot_without_deleting_it(tmp_path):
    from apps.membership.models import MembershipCompositionSnapshot

    first = import_composition_snapshot(
        write_roster(tmp_path / "first.xlsx"), snapshot_date=SNAPSHOT, dry_run=False
    )
    revised = write_roster(tmp_path / "second.xlsx", rows=FAKE_ROWS[:2])

    second = import_composition_snapshot(
        revised, snapshot_date=SNAPSHOT, dry_run=False, supersede_previous=True
    )

    assert second.superseded == 1
    assert MembershipCompositionSnapshot.objects.count() == 2
    retired = MembershipCompositionSnapshot.objects.get(source_sha256=first.source_sha256)
    assert retired.is_current is False
    assert retired.superseded_by_id is not None
    # The retired reading keeps its own numbers.
    assert retired.source_row_count == 3


@pytest.mark.django_db
def test_exactly_one_snapshot_is_current(tmp_path):
    from apps.membership.models import MembershipCompositionSnapshot

    import_composition_snapshot(
        write_roster(tmp_path / "first.xlsx"), snapshot_date=SNAPSHOT, dry_run=False
    )
    import_composition_snapshot(
        write_roster(tmp_path / "second.xlsx", rows=FAKE_ROWS[:2]),
        snapshot_date=SNAPSHOT,
        dry_run=False,
        supersede_previous=True,
    )

    assert MembershipCompositionSnapshot.objects.filter(is_current=True).count() == 1


@pytest.mark.django_db
def test_the_selector_reads_the_current_snapshot_and_its_derived_readouts(tmp_path):
    from apps.membership.composition_selectors import get_current_composition_snapshot

    import_composition_snapshot(
        write_roster(tmp_path / "roster.xlsx"), snapshot_date=SNAPSHOT, dry_run=False
    )

    snapshot = get_current_composition_snapshot()

    assert snapshot is not None
    assert snapshot.snapshot_date == SNAPSHOT
    assert snapshot.row_count == 3
    assert snapshot.recent_joiner_count == 1
    assert snapshot.median_tenure_years is not None
    sizes = snapshot.dimension(Dimension.EMPLOYEE_SIZE)
    assert sizes.total == 3


@pytest.mark.django_db
def test_the_selector_returns_nothing_rather_than_an_empty_snapshot():
    from apps.membership.composition_selectors import get_current_composition_snapshot

    assert get_current_composition_snapshot() is None


@pytest.mark.django_db
def test_a_published_count_cannot_be_edited(tmp_path):
    from apps.membership.models import (
        CompositionSnapshotImmutable,
        MembershipCompositionValue,
    )

    import_composition_snapshot(
        write_roster(tmp_path / "roster.xlsx"), snapshot_date=SNAPSHOT, dry_run=False
    )

    value = MembershipCompositionValue.objects.first()
    value.member_count = 999
    with pytest.raises(CompositionSnapshotImmutable):
        value.save()
