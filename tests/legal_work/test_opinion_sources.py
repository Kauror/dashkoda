"""Reading the opinion inbox: what is accepted, what is refused, and what is ignored.

The distinction that runs through this file is between **refusing a container**
and **ignoring an entry**. A ZIP that tries to escape its own directory is
hostile and the whole archive is refused; a stray readme inside an otherwise
sound archive is merely not a document, and refusing the handover over it would
be a denial of service by typo.
"""

from __future__ import annotations

import pytest

from apps.legal_work.opinion_sources import (
    BootstrapZipProvider,
    DirectoryProvider,
    SourceRejected,
    collect_manifest,
    manifest_checksum,
)

from .opinion_factory import build_hostile_zip, build_zip, make_pdf, opinion_pdf


def write_bootstrap(source, entries: dict[str, bytes]) -> None:
    build_zip(entries, path=source / "Opinions.zip")


# -- the bootstrap archive --------------------------------------------------


def test_a_sound_archive_yields_every_pdf(opinion_roots):
    source, _ = opinion_roots
    write_bootstrap(source, {"Opinions/a.pdf": make_pdf(), "Opinions/b.pdf": opinion_pdf()})

    entries = BootstrapZipProvider().manifest()

    assert [entry.filename for entry in entries] == ["a.pdf", "b.pdf"]
    assert all(len(entry.sha256) == 64 for entry in entries)


def test_a_directory_placeholder_is_not_a_document(opinion_roots):
    source, _ = opinion_roots
    write_bootstrap(source, {"Opinions/": b"", "Opinions/a.pdf": make_pdf()})

    assert len(BootstrapZipProvider().manifest()) == 1


def test_a_non_pdf_entry_is_ignored_rather_than_refused(opinion_roots):
    """A stray readme must not make the whole handover unreadable."""
    source, _ = opinion_roots
    write_bootstrap(source, {"Opinions/readme.txt": b"hello", "Opinions/a.pdf": make_pdf()})

    entries = BootstrapZipProvider().manifest()

    assert [entry.filename for entry in entries] == ["a.pdf"]


@pytest.mark.parametrize(
    "kind",
    ["traversal", "absolute", "drive", "symlink", "nested", "duplicate"],
)
def test_a_hostile_archive_is_refused(opinion_roots, kind):
    source, _ = opinion_roots
    build_hostile_zip(kind, path=source / "Opinions.zip")

    with pytest.raises(SourceRejected):
        BootstrapZipProvider().manifest()


def test_an_implausible_decompression_ratio_is_refused(opinion_roots, settings):
    source, _ = opinion_roots
    settings.LEGAL_OPINION_MAX_ZIP_RATIO = 10.0
    build_hostile_zip("bomb", path=source / "Opinions.zip")

    with pytest.raises(SourceRejected):
        BootstrapZipProvider().manifest()


def test_too_many_entries_is_refused(opinion_roots, settings):
    source, _ = opinion_roots
    settings.LEGAL_OPINION_MAX_SOURCE_ENTRIES = 2
    write_bootstrap(source, {f"Opinions/{n}.pdf": make_pdf() for n in range(4)})

    with pytest.raises(SourceRejected):
        BootstrapZipProvider().manifest()


def test_an_oversized_entry_is_refused(opinion_roots, settings):
    source, _ = opinion_roots
    settings.LEGAL_OPINION_MAX_PDF_BYTES = 100
    write_bootstrap(source, {"Opinions/a.pdf": make_pdf()})

    with pytest.raises(SourceRejected):
        BootstrapZipProvider().manifest()


def test_the_archive_is_never_unpacked_into_the_source_directory(opinion_roots):
    """The inbox must be left exactly as the Chamber handed it over."""
    source, _ = opinion_roots
    write_bootstrap(source, {"Opinions/a.pdf": make_pdf(), "Opinions/b.pdf": make_pdf()})
    before = sorted(p.name for p in source.iterdir())

    BootstrapZipProvider().manifest()

    assert sorted(p.name for p in source.iterdir()) == before == ["Opinions.zip"]


def test_reading_an_entry_that_is_not_there_returns_nothing(opinion_roots):
    source, _ = opinion_roots
    write_bootstrap(source, {"Opinions/a.pdf": make_pdf()})

    assert BootstrapZipProvider().read("Opinions/absent.pdf") is None


def test_a_traversing_key_cannot_be_read(opinion_roots):
    source, _ = opinion_roots
    write_bootstrap(source, {"Opinions/a.pdf": make_pdf()})

    with pytest.raises(SourceRejected):
        BootstrapZipProvider().read("../../etc/passwd")


# -- the recurring directory ------------------------------------------------


def test_the_directory_provider_reads_nested_year_folders(opinion_roots):
    source, _ = opinion_roots
    (source / "2025").mkdir()
    (source / "2026").mkdir()
    (source / "2025" / "a.pdf").write_bytes(make_pdf())
    (source / "2026" / "b.pdf").write_bytes(opinion_pdf())

    entries = DirectoryProvider().manifest()

    assert {entry.key for entry in entries} == {"2025/a.pdf", "2026/b.pdf"}


def test_the_directory_order_is_deterministic(opinion_roots):
    source, _ = opinion_roots
    for name in ("c.pdf", "a.pdf", "b.pdf"):
        (source / name).write_bytes(make_pdf([[name]]))

    first = [entry.key for entry in DirectoryProvider().manifest()]
    second = [entry.key for entry in DirectoryProvider().manifest()]

    assert first == second == ["a.pdf", "b.pdf", "c.pdf"]


def test_a_temporary_filename_is_ignored(opinion_roots):
    source, _ = opinion_roots
    (source / "real.pdf").write_bytes(make_pdf())
    (source / "~$draft.pdf").write_bytes(make_pdf())
    (source / "half.pdf.part").write_bytes(b"not finished")

    assert [entry.key for entry in DirectoryProvider().manifest()] == ["real.pdf"]


def test_a_file_still_being_copied_is_ignored(opinion_roots, settings):
    """A half-written PDF hashed now would be stored as complete and wrong."""
    source, _ = opinion_roots
    settings.LEGAL_OPINION_MIN_STABLE_AGE_SECONDS = 3600
    (source / "arriving.pdf").write_bytes(make_pdf())

    assert DirectoryProvider().manifest() == []


def test_a_non_pdf_in_the_directory_is_ignored(opinion_roots):
    source, _ = opinion_roots
    (source / "notes.txt").write_bytes(b"hello")
    (source / "a.pdf").write_bytes(make_pdf())

    assert [entry.key for entry in DirectoryProvider().manifest()] == ["a.pdf"]


def test_a_key_cannot_escape_the_source_root(opinion_roots):
    source, _ = opinion_roots
    (source / "a.pdf").write_bytes(make_pdf())

    with pytest.raises(SourceRejected):
        DirectoryProvider().read("../../../etc/passwd")


def test_reading_a_directory_key_that_is_absent_returns_nothing(opinion_roots):
    opinion_roots  # noqa: B018 - fixture applies the settings

    assert DirectoryProvider().read("absent.pdf") is None


# -- the manifest -----------------------------------------------------------


def test_an_unchanged_inbox_produces_an_identical_checksum(opinion_roots):
    source, _ = opinion_roots
    write_bootstrap(source, {"Opinions/a.pdf": make_pdf(), "Opinions/b.pdf": opinion_pdf()})

    _, first = collect_manifest()
    _, second = collect_manifest()

    assert first == second


def test_a_changed_inbox_produces_a_different_checksum(opinion_roots):
    source, _ = opinion_roots
    write_bootstrap(source, {"Opinions/a.pdf": make_pdf()})
    _, before = collect_manifest()

    write_bootstrap(source, {"Opinions/a.pdf": make_pdf(), "Opinions/b.pdf": opinion_pdf()})
    _, after = collect_manifest()

    assert before != after


def test_the_checksum_does_not_depend_on_entry_order(opinion_roots):
    source, _ = opinion_roots
    write_bootstrap(
        source, {"Opinions/a.pdf": make_pdf([["a"]]), "Opinions/b.pdf": make_pdf([["b"]])}
    )
    forward = collect_manifest()[0]

    reversed_manifest = list(reversed(forward))

    assert manifest_checksum(forward) == manifest_checksum(reversed_manifest)


def test_the_same_bytes_in_two_places_are_one_document(opinion_roots):
    """A file present both loose and inside the handover is not two documents."""
    source, _ = opinion_roots
    payload = opinion_pdf()
    write_bootstrap(source, {"Opinions/letter.pdf": payload})
    (source / "letter.pdf").write_bytes(payload)

    entries, _ = collect_manifest()

    assert len(entries) == 1


def test_an_empty_inbox_is_an_empty_manifest(opinion_roots):
    opinion_roots  # noqa: B018 - fixture applies the settings

    entries, checksum = collect_manifest()

    assert entries == []
    assert len(checksum) == 64
