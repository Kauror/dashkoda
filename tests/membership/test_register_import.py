"""The member-register importer: what it reads, and what it refuses.

Every identity below is invented. The register deliberately *keeps* rows, so
the privacy assertions here are the mirror image of the composition importer's:
that the columns it must not model are still absent after an import, and that
nothing outside the stored subset reaches the database.

The parsing tests need no PostgreSQL; the ones that import are `django_db` and
run in CI.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.membership.register_import import (
    RegisterImportError,
    decode,
    import_member_register,
    read_register,
)

SNAPSHOT = dt.date(2026, 8, 15)

HEADERS = [
    "Ettevõte",
    "Vorm",
    "Number",
    "Staatus",
    "Aadress",
    "Indeks",
    "Linn",
    "Maakond",
    "Riik",
    "Üld e-post",
    "www",
    "Faks",
    "Telefon",
    "Asutatud",
    "Töötajate arv",
    "Töötaja vahemik",
    "Firma juht",
    "Email",
    "Registrikood",
    "KM nr.",
    "Arve e-mail",
    "Kommentaar",
    "Algus kp.",
    "Nace kood",
    "Nace kirjeldus",
    "Nacecomment",
]

# Distinctive enough that a substring search for one cannot match a vocabulary
# term, a label or a checksum by accident.
FAKE_ROWS = [
    {
        "Ettevõte": "Kuutõrvaja Masinaehitus",
        "Vorm": "OÜ",
        "Number": "4101",
        "Staatus": "Koja liige",
        "Aadress": "Väljamõeldud puiestee 14",
        "Indeks": "10101",
        "Linn": "TALLINN",
        "Maakond": "HARJUMAA",
        "Riik": "EESTI",
        "Üld e-post": "kontakt@kuutorvaja-example.invalid",
        "www": "www.kuutorvaja-example.invalid",
        "Telefon": "3 725 000 111",
        "Töötajate arv": "12",
        "Firma juht": "Mihkel Väljamõeldud",
        "Email": "mihkel@kuutorvaja-example.invalid",
        "Registrikood": "99900001",
        "KM nr.": "EE100000001",
        "Arve e-mail": "arved@kuutorvaja-example.invalid",
        "Kommentaar": "Salajane märkus Kuutõrvaja kohta",
        "Algus kp.": "01.03.2015",
        "Nace kood": "46491",
        "Nace kirjeldus": "Muu majapidamistarvete hulgimüük",
    },
    {
        "Ettevõte": "Vesipapi Logistika",
        "Vorm": "AS",
        "Number": "4102",
        "Staatus": "Peatatud liige",
        "Aadress": "Olematu tänav 3",
        "Linn": "TARTU LINN",
        "Maakond": "TARTUMAA",
        "Riik": "EESTI",
        "www": "https://vesipapi-example.invalid",
        "Töötajate arv": "260",
        "Firma juht": "Kadri Olematu",
        "Email": "kadri@vesipapi-example.invalid",
        "Registrikood": "99900002",
        "Kommentaar": "",
        "Algus kp.": "01.05.2026",
        "Nace kood": "49411",
        "Nace kirjeldus": "Kaubavedu maanteel",
    },
    {
        "Ettevõte": "Pilvelõhkuja Tarkvara",
        "Vorm": "OÜ",
        "Number": "4103",
        "Staatus": "Toetaja liige",
        "Linn": "PÄRNU LINN",
        "Maakond": "PÄRNUMAA",
        "Riik": "EESTI",
        "Töötajate arv": "0",
        "Firma juht": "Toomas Puuduv",
        "Email": "toomas@pilvelohkuja-example.invalid",
        "Registrikood": "99900003",
        "Kommentaar": "Ei soovi uudiskirja",
        "Algus kp.": "09.09.1999",
        "Nace kood": "62011",
        "Nace kirjeldus": "Programmeerimine",
    },
]

#: Every value the register must not store, in one list. These are the columns
#: `models/register.py` deliberately has no field for.
FORBIDDEN_VALUES = [
    value
    for row in FAKE_ROWS
    for key, value in row.items()
    if key
    in {
        "Aadress",
        "Indeks",
        "Üld e-post",
        "Telefon",
        "Faks",
        "Firma juht",
        "Email",
        "KM nr.",
        "Arve e-mail",
        "Kommentaar",
    }
    and value
]


def write_export(path, rows=None, *, encoding="utf-16", delimiter="\t", headers=None):
    """A roster-shaped export on disk, in the CRM's own dialect by default."""
    headers = headers if headers is not None else HEADERS
    rows = FAKE_ROWS if rows is None else rows
    lines = [delimiter.join(headers)]
    for row in rows:
        lines.append(delimiter.join(str(row.get(name, "")) for name in headers))
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode(encoding))
    return path


# ---------------------------------------------------------------------------
# Decoding and parsing — no database
# ---------------------------------------------------------------------------


def test_utf16_export_is_decoded_by_its_byte_order_mark():
    """The CRM writes UTF-16 and calls the file .csv. The BOM decides."""
    raw = "Ettevõte\tStaatus\r\n".encode("utf-16")
    assert decode(raw).startswith("Ettevõte")


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8-sig", "utf-8"])
def test_every_supported_encoding_reads_the_same_rows(tmp_path, encoding):
    reading = read_register(
        write_export(tmp_path / f"roster-{encoding}.csv", encoding=encoding),
        snapshot_date=SNAPSHOT,
    )
    assert [row.name for row in reading.rows] == [
        "Kuutõrvaja Masinaehitus",
        "Vesipapi Logistika",
        "Pilvelõhkuja Tarkvara",
    ]


@pytest.mark.parametrize("delimiter", ["\t", ";", ","])
def test_the_delimiter_is_detected_from_the_header(tmp_path, delimiter):
    reading = read_register(
        write_export(tmp_path / "roster.csv", delimiter=delimiter), snapshot_date=SNAPSHOT
    )
    assert reading.rows_read == 3


def test_columns_are_parsed_into_their_types(tmp_path):
    reading = read_register(write_export(tmp_path / "roster.csv"), snapshot_date=SNAPSHOT)
    first = reading.rows[0]
    assert first.registry_code == "99900001"
    assert first.employees == 12
    assert first.membership_start == dt.date(2015, 3, 1)
    assert first.status_key == "regular"
    assert first.status_label == "Koja liige"
    assert first.county == "HARJUMAA"
    assert first.nace_code == "46491"


def test_a_reported_zero_headcount_is_not_a_missing_one(tmp_path):
    """`0` employees is a real value and must stay distinguishable from blank.

    The importer's own rule elsewhere in this app: a missing value is never
    zero, and an explicitly reported zero is never blank.
    """
    reading = read_register(write_export(tmp_path / "roster.csv"), snapshot_date=SNAPSHOT)
    assert reading.rows[2].employees == 0

    blank = [{**FAKE_ROWS[2], "Töötajate arv": ""}]
    reading = read_register(write_export(tmp_path / "blank.csv", blank), snapshot_date=SNAPSHOT)
    assert reading.rows[0].employees is None


def test_a_membership_start_after_the_snapshot_is_reported_not_dropped(tmp_path):
    rows = [*FAKE_ROWS, {**FAKE_ROWS[0], "Registrikood": "99900009", "Algus kp.": "01.12.2026"}]
    reading = read_register(write_export(tmp_path / "roster.csv", rows), snapshot_date=SNAPSHOT)
    assert {"code": "membership_start_after_snapshot", "rows": 1} in reading.diagnostics()
    # And the row is still imported: a date past the snapshot is a fact about
    # the export, not a reason to lose a member.
    assert len(reading.rows) == 4


def test_an_unreadable_start_date_is_counted_and_left_empty(tmp_path):
    rows = [{**FAKE_ROWS[0], "Algus kp.": "eile"}]
    reading = read_register(write_export(tmp_path / "roster.csv", rows), snapshot_date=SNAPSHOT)
    assert reading.rows[0].membership_start is None
    assert {"code": "unreadable_membership_start", "rows": 1} in reading.diagnostics()


def test_a_duplicate_registry_code_keeps_the_first_row_and_is_reported(tmp_path):
    rows = [*FAKE_ROWS, {**FAKE_ROWS[0], "Ettevõte": "Kuutõrvaja Teine Rida"}]
    reading = read_register(write_export(tmp_path / "roster.csv", rows), snapshot_date=SNAPSHOT)
    assert len(reading.rows) == 3
    assert reading.rows_read == 4
    assert {"code": "duplicate_registry_code", "rows": 1} in reading.diagnostics()


def test_rows_without_a_registry_code_are_kept_with_none(tmp_path):
    """PostgreSQL treats NULLs as distinct, so two codeless rows both survive.

    They are listed and excluded from the comparison, which is the honest
    outcome: a member with no code cannot be looked for in the directory.
    """
    rows = [
        {**FAKE_ROWS[0], "Registrikood": ""},
        {**FAKE_ROWS[1], "Registrikood": ""},
    ]
    reading = read_register(write_export(tmp_path / "roster.csv", rows), snapshot_date=SNAPSHOT)
    assert [row.registry_code for row in reading.rows] == [None, None]
    assert {"code": "missing_registry_code", "rows": 2} in reading.diagnostics()


def test_an_unknown_status_is_reported_rather_than_folded_into_a_neighbour(tmp_path):
    rows = [{**FAKE_ROWS[0], "Staatus": "Auliige"}]
    reading = read_register(write_export(tmp_path / "roster.csv", rows), snapshot_date=SNAPSHOT)
    assert reading.rows[0].status_key == "unknown"
    # The source's own wording survives, so the page shows what it said.
    assert reading.rows[0].status_label == "Auliige"
    assert {"code": "unmapped_status", "rows": 1} in reading.diagnostics()


def test_a_file_missing_a_required_column_is_refused_by_name(tmp_path):
    headers = [name for name in HEADERS if name != "Registrikood"]
    path = write_export(tmp_path / "roster.csv", headers=headers)
    with pytest.raises(RegisterImportError, match="Registrikood"):
        read_register(path, snapshot_date=SNAPSHOT)


def test_a_file_with_no_data_rows_is_refused(tmp_path):
    path = write_export(tmp_path / "roster.csv", rows=[])
    with pytest.raises(RegisterImportError, match="ühtegi andmerida"):
        read_register(path, snapshot_date=SNAPSHOT)


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(RegisterImportError, match="ei leitud"):
        read_register(tmp_path / "absent.csv", snapshot_date=SNAPSHOT)


def test_nothing_outside_the_stored_subset_is_parsed(tmp_path):
    """The address, contacts, director and comment never reach a row.

    `RegisterRow` has no field for them, so this asserts the guarantee from the
    outside: every forbidden value is absent from the whole parsed reading.
    """
    reading = read_register(write_export(tmp_path / "roster.csv"), snapshot_date=SNAPSHOT)
    rendered = repr(reading.rows)
    for value in FORBIDDEN_VALUES:
        assert value not in rendered


# ---------------------------------------------------------------------------
# Importing — PostgreSQL
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_dry_run_writes_no_rows(tmp_path):
    from apps.membership.models import MemberRegisterEntry, MemberRegisterSnapshot

    result = import_member_register(
        write_export(tmp_path / "roster.csv"), snapshot_date=SNAPSHOT, dry_run=True
    )
    assert result.dry_run
    assert result.rows_read == 3
    assert not MemberRegisterSnapshot.objects.exists()
    assert not MemberRegisterEntry.objects.exists()


@pytest.mark.django_db
def test_an_import_stores_the_rows_and_nothing_else(tmp_path):
    from apps.membership.models import MemberRegisterEntry, MemberRegisterSnapshot

    result = import_member_register(
        write_export(tmp_path / "roster.csv"), snapshot_date=SNAPSHOT, dry_run=False
    )
    assert result.rows_written == 3

    snapshot = MemberRegisterSnapshot.objects.get(is_current=True)
    assert snapshot.snapshot_date == SNAPSHOT
    assert snapshot.source_row_count == 3

    stored = repr(list(MemberRegisterEntry.objects.values()))
    for value in FORBIDDEN_VALUES:
        assert value not in stored


@pytest.mark.django_db
def test_the_dry_run_then_import_sequence_works_on_the_same_file(tmp_path):
    """The documented sequence must not be refused on its own first half.

    A dry run registers the artifact for provenance; the live import of the
    identical file then has to reuse it rather than register the bytes twice.
    This exact defect reached production once already.
    """
    path = write_export(tmp_path / "roster.csv")
    import_member_register(path, snapshot_date=SNAPSHOT, dry_run=True)
    result = import_member_register(path, snapshot_date=SNAPSHOT, dry_run=False)
    assert not result.unchanged
    assert result.rows_written == 3


@pytest.mark.django_db
def test_reimporting_the_same_file_changes_nothing(tmp_path):
    from apps.membership.models import MemberRegisterSnapshot

    path = write_export(tmp_path / "roster.csv")
    import_member_register(path, snapshot_date=SNAPSHOT, dry_run=False)
    again = import_member_register(path, snapshot_date=SNAPSHOT, dry_run=False)
    assert again.unchanged
    assert MemberRegisterSnapshot.objects.count() == 1


@pytest.mark.django_db
def test_a_second_export_needs_supersede_and_then_retires_the_first(tmp_path):
    from apps.membership.models import MemberRegisterSnapshot

    import_member_register(
        write_export(tmp_path / "first.csv"), snapshot_date=SNAPSHOT, dry_run=False
    )
    newer = write_export(
        tmp_path / "second.csv", [*FAKE_ROWS, {**FAKE_ROWS[0], "Registrikood": "99900004"}]
    )

    with pytest.raises(RegisterImportError, match="supersede-previous"):
        import_member_register(newer, snapshot_date=SNAPSHOT, dry_run=False)

    result = import_member_register(
        newer, snapshot_date=SNAPSHOT, dry_run=False, supersede_previous=True
    )
    assert result.superseded == 1

    # Nothing is deleted: the retired reading keeps its rows and gains a pointer
    # to what replaced it.
    assert MemberRegisterSnapshot.objects.count() == 2
    retired = MemberRegisterSnapshot.objects.get(is_current=False)
    assert retired.superseded_by_id == MemberRegisterSnapshot.objects.get(is_current=True).pk
    assert retired.entries.count() == 3


@pytest.mark.django_db
def test_a_published_entry_cannot_be_edited(tmp_path):
    from apps.membership.models import MemberRegisterEntry, RegisterImmutable

    import_member_register(
        write_export(tmp_path / "roster.csv"), snapshot_date=SNAPSHOT, dry_run=False
    )
    entry = MemberRegisterEntry.objects.first()
    entry.name = "Ümbernimetatud"
    with pytest.raises(RegisterImmutable):
        entry.save()
