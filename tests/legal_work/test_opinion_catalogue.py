"""Building and publishing the catalogue.

The properties under test here are the ones that make a 759-document backfill
safe to run in slices: a partial build never becomes current, work is never
repeated, one bad document never stops the rest, and a failure leaves the last
good catalogue exactly where it was.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.legal_work.models import SnapshotImmutable
from apps.legal_work.opinion_catalogue_sync import (
    RESULT_IMPORTED,
    RESULT_PARTIAL,
    RESULT_UNCHANGED,
    synchronize_opinion_documents,
)
from apps.legal_work.opinion_filenames import FILENAME_NORMALISER_VERSION
from apps.legal_work.opinion_models import (
    CatalogueBuildState,
    OpinionCatalogueEntry,
    OpinionCatalogueFeedState,
    OpinionCatalogueSnapshot,
    OpinionDocumentBlob,
    OpinionDocumentExtraction,
)
from apps.legal_work.opinion_pdf import ExtractionStatus, ValidationStatus
from apps.legal_work.opinion_storage import blob_path, store_root
from apps.sources.models import SourceArtifact

from .opinion_factory import build_zip, make_encrypted_pdf, make_pdf, opinion_pdf

pytestmark = pytest.mark.django_db


def bootstrap(source, entries):
    build_zip(entries, path=source / "Opinions.zip")


def letters(count: int) -> dict[str, bytes]:
    return {
        f"Opinions/2026-01-{n + 1:02d} - Rahandusministeerium - Arvamus nr {n}.pdf": opinion_pdf(
            our_date=f"{n + 1:02d}.01.2026",
            our_reference=f"4/{n}",
            subject=f"Arvamus number {n} eelnou kohta",
        )
        for n in range(count)
    }


# -- a complete build -------------------------------------------------------


def test_a_complete_build_publishes_one_current_snapshot(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(3))

    report = synchronize_opinion_documents()

    assert report.result == RESULT_IMPORTED
    snapshot = OpinionCatalogueSnapshot.objects.get(is_current=True)
    assert snapshot.entry_count == 3
    assert snapshot.valid_count == 3
    assert snapshot.extracted_count == 3
    assert OpinionCatalogueEntry.objects.filter(snapshot=snapshot).count() == 3


def test_every_document_is_stored_under_its_own_digest(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(2))

    synchronize_opinion_documents()

    for blob in OpinionDocumentBlob.objects.all():
        assert blob_path(blob.sha256).exists()
        assert blob.storage_key.startswith("blobs/")


def test_a_repeated_build_reports_unchanged(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(2))
    synchronize_opinion_documents()

    report = synchronize_opinion_documents()

    assert report.result == RESULT_UNCHANGED
    assert OpinionCatalogueSnapshot.objects.filter(is_current=True).count() == 1


def test_a_stale_normaliser_version_republishes_instead_of_failing_for_ever(
    opinion_roots, opinion_source
):
    """The production deadlock this guards against.

    Raising `FILENAME_NORMALISER_VERSION` invalidates the unchanged fast path on
    purpose — dates and recipients are parsed out of filenames, so a new reader
    changes the catalogue from identical bytes. The run then reaches `_publish`
    with a checksum whose artifact already exists.

    Registering it again raised `ArtifactRejected`, and the failure was
    self-perpetuating: publishing is what writes the new version stamp, so every
    later run took the same path and failed the same way. It failed daily from
    2026-08-09 until this was fixed, and no rerun could ever clear it.
    """
    source, _ = opinion_roots
    bootstrap(source, letters(2))
    synchronize_opinion_documents()
    published = OpinionCatalogueSnapshot.objects.get(is_current=True)
    # Rewrite history the way the real database holds it: a snapshot published
    # before the field existed carries an empty stamp. `update` rather than
    # `save` because a published snapshot is immutable through the model.
    OpinionCatalogueSnapshot.objects.filter(pk=published.pk).update(filename_normaliser_version="")
    before = SourceArtifact.objects.count()

    report = synchronize_opinion_documents()

    assert report.result == RESULT_IMPORTED, "a stale stamp must republish, not fail"
    assert SourceArtifact.objects.count() == before
    current = OpinionCatalogueSnapshot.objects.get(is_current=True)
    assert current.filename_normaliser_version == FILENAME_NORMALISER_VERSION, (
        "publishing is what clears the condition, so it must actually happen"
    )
    # And the next run settles back to the fast path rather than churning.
    assert synchronize_opinion_documents().result == RESULT_UNCHANGED


def test_a_changed_inbox_publishes_a_new_snapshot(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(2))
    synchronize_opinion_documents()
    first = OpinionCatalogueSnapshot.objects.get(is_current=True)

    bootstrap(source, letters(3))
    synchronize_opinion_documents()

    assert OpinionCatalogueSnapshot.objects.filter(is_current=True).count() == 1
    second = OpinionCatalogueSnapshot.objects.get(is_current=True)
    assert second.pk != first.pk
    assert second.entry_count == 3


def test_unchanged_documents_are_not_processed_again(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(2))
    synchronize_opinion_documents()

    bootstrap(source, letters(3))
    report = synchronize_opinion_documents()

    # Two blobs already existed; only the third is new work.
    assert report.unique_blobs == 1


def test_the_same_bytes_under_two_names_are_one_blob(opinion_roots, opinion_source):
    source, _ = opinion_roots
    payload = opinion_pdf()
    bootstrap(source, {"Opinions/a.pdf": payload, "Opinions/b.pdf": payload})

    synchronize_opinion_documents()

    assert OpinionDocumentBlob.objects.count() == 1


# -- bounded and resumable --------------------------------------------------


def test_a_bounded_run_publishes_nothing_and_reports_partial(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(5))

    report = synchronize_opinion_documents(max_documents=2)

    assert report.result == RESULT_PARTIAL
    assert report.processed_entries == 2
    assert report.pending_entries == 3
    assert not OpinionCatalogueSnapshot.objects.filter(is_current=True).exists()


def test_repeated_bounded_runs_accumulate_and_then_publish(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(5))

    first = synchronize_opinion_documents(max_documents=2)
    second = synchronize_opinion_documents(max_documents=2)
    third = synchronize_opinion_documents(max_documents=2)

    assert [first.result, second.result, third.result] == [
        RESULT_PARTIAL,
        RESULT_PARTIAL,
        RESULT_IMPORTED,
    ]
    assert OpinionCatalogueSnapshot.objects.get(is_current=True).entry_count == 5


def test_a_partial_build_leaves_the_previous_catalogue_current(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(2))
    synchronize_opinion_documents()
    published = OpinionCatalogueSnapshot.objects.get(is_current=True)

    bootstrap(source, letters(6))
    synchronize_opinion_documents(max_documents=1)

    assert OpinionCatalogueSnapshot.objects.get(is_current=True).pk == published.pk


def test_the_feed_state_shows_progress_during_a_build(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(5))

    synchronize_opinion_documents(max_documents=2)

    state = OpinionCatalogueFeedState.objects.get()
    assert state.build_state == CatalogueBuildState.BUILDING
    assert state.manifest_entry_count == 5
    assert state.processed_entry_count == 2
    assert state.pending_entry_count == 3


# -- dry runs ---------------------------------------------------------------


def test_a_dry_run_publishes_nothing(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(2))

    report = synchronize_opinion_documents(dry_run=True)

    assert report.dry_run is True
    assert not OpinionCatalogueSnapshot.objects.exists()


def test_a_dry_run_reports_what_a_live_run_would_do(opinion_roots, opinion_source):
    """A dry run exists to answer "what would happen?".

    Reporting `unchanged` while 34 documents sat unprocessed told an operator
    there was nothing to do — the opposite of the truth, and the one question
    the flag is for.
    """
    source, _ = opinion_roots
    bootstrap(source, letters(5))

    report = synchronize_opinion_documents(dry_run=True, max_documents=2)

    assert report.result == RESULT_PARTIAL
    assert report.pending_entries == 5
    assert report.dry_run is True


def test_a_dry_run_over_a_published_catalogue_still_reports_unchanged(
    opinion_roots, opinion_source
):
    source, _ = opinion_roots
    bootstrap(source, letters(2))
    synchronize_opinion_documents()

    assert synchronize_opinion_documents(dry_run=True).result == RESULT_UNCHANGED


def test_a_dry_run_claims_no_blob_work(opinion_roots, opinion_source):
    """It stores nothing, so it has neither created nor reused a blob."""
    source, _ = opinion_roots
    bootstrap(source, letters(3))

    report = synchronize_opinion_documents(dry_run=True)

    assert report.unique_blobs == 0
    assert report.reused_blobs == 0


def test_a_dry_run_writes_no_managed_blob(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(2))

    synchronize_opinion_documents(dry_run=True)

    assert OpinionDocumentBlob.objects.count() == 0
    blobs = store_root() / "blobs"
    assert not blobs.exists() or not any(blobs.rglob("*.pdf"))


# -- one bad document does not stop the rest --------------------------------


def test_an_invalid_document_is_catalogued_and_quarantined(opinion_roots, opinion_source):
    source, _ = opinion_roots
    entries = letters(2)
    entries["Opinions/2026-02-01 - Ministeerium - Katkine.pdf"] = make_pdf(broken=True)
    bootstrap(source, entries)

    report = synchronize_opinion_documents()

    assert report.result == RESULT_IMPORTED
    snapshot = OpinionCatalogueSnapshot.objects.get(is_current=True)
    assert snapshot.entry_count == 3
    assert snapshot.valid_count == 2
    assert snapshot.quarantined_count == 1


def test_a_quarantined_document_is_never_matchable(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, {"Opinions/2026-02-01 - X - Krupt.pdf": make_encrypted_pdf()})

    synchronize_opinion_documents()

    entry = OpinionCatalogueEntry.objects.get()
    assert entry.is_matchable is False
    assert entry.extraction_id is None
    assert entry.blob.validation_status == ValidationStatus.QUARANTINED_ENCRYPTED


def test_quarantined_bytes_are_kept_outside_the_servable_blob_area(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, {"Opinions/2026-02-01 - X - Krupt.pdf": make_encrypted_pdf()})

    synchronize_opinion_documents()

    blob = OpinionDocumentBlob.objects.get()
    assert blob.storage_key.startswith("quarantine/")
    assert not blob_path(blob.sha256).exists()


def test_a_document_needing_ocr_is_recorded_and_excluded(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, {"Opinions/2026-02-01 - X - Pilt.pdf": make_pdf([[]])})

    synchronize_opinion_documents()

    snapshot = OpinionCatalogueSnapshot.objects.get(is_current=True)
    assert snapshot.needs_ocr_count == 1
    entry = OpinionCatalogueEntry.objects.get()
    assert entry.is_matchable is False
    assert entry.extraction.status == ExtractionStatus.NEEDS_OCR


# -- failure containment ----------------------------------------------------


def test_a_source_failure_leaves_the_last_good_catalogue(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(2))
    synchronize_opinion_documents()
    published = OpinionCatalogueSnapshot.objects.get(is_current=True)

    (source / "Opinions.zip").write_bytes(b"this is not a zip at all")
    report = synchronize_opinion_documents()

    assert report.result == "failed"
    assert OpinionCatalogueSnapshot.objects.get(is_current=True).pk == published.pk


def test_a_failure_summary_carries_no_filename_or_path(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(1))
    synchronize_opinion_documents()
    (source / "Opinions.zip").write_bytes(b"broken")

    synchronize_opinion_documents()

    summary = OpinionCatalogueFeedState.objects.get().last_error_summary
    assert str(source) not in summary
    assert ".pdf" not in summary


def test_a_source_file_disappearing_never_removes_its_blob(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(2))
    synchronize_opinion_documents()
    digests = list(OpinionDocumentBlob.objects.values_list("sha256", flat=True))

    bootstrap(source, letters(1))
    synchronize_opinion_documents()

    for digest in digests:
        assert blob_path(digest).exists()
    assert OpinionDocumentBlob.objects.count() == 2


# -- immutability -----------------------------------------------------------


def test_a_stored_blob_cannot_be_changed(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(1))
    synchronize_opinion_documents()

    blob = OpinionDocumentBlob.objects.first()
    blob.page_count = 99
    with pytest.raises(SnapshotImmutable):
        blob.save()


def test_a_recorded_extraction_cannot_be_changed(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(1))
    synchronize_opinion_documents()

    extraction = OpinionDocumentExtraction.objects.first()
    extraction.text = "rewritten"
    with pytest.raises(SnapshotImmutable):
        extraction.save()


def test_a_catalogue_entry_cannot_be_changed(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(1))
    synchronize_opinion_documents()

    entry = OpinionCatalogueEntry.objects.first()
    entry.classification = "annex"
    with pytest.raises(SnapshotImmutable):
        entry.save()


def test_a_published_snapshot_may_only_change_its_current_flag(opinion_roots, opinion_source):
    source, _ = opinion_roots
    bootstrap(source, letters(1))
    synchronize_opinion_documents()

    snapshot = OpinionCatalogueSnapshot.objects.get(is_current=True)
    snapshot.entry_count = 99
    with pytest.raises(SnapshotImmutable):
        snapshot.save()

    snapshot.refresh_from_db()
    snapshot.is_current = False
    snapshot.save(update_fields=["is_current"])  # permitted


def test_only_one_snapshot_is_ever_current(opinion_roots, opinion_source):
    source, _ = opinion_roots
    for count in (1, 2, 3):
        bootstrap(source, letters(count))
        synchronize_opinion_documents()

    assert OpinionCatalogueSnapshot.objects.filter(is_current=True).count() == 1
    assert OpinionCatalogueSnapshot.objects.count() == 3


# -- the command ------------------------------------------------------------


def test_the_command_emits_aggregate_only_json(opinion_roots, opinion_source, capsys):
    source, _ = opinion_roots
    bootstrap(source, letters(2))

    call_command("sync_legal_opinion_documents", "--json")

    import json

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["result"] == RESULT_IMPORTED
    assert payload["manifest_entries"] == 2
    # A prefix, never the whole checksum.
    assert len(payload["source_manifest_checksum"]) == 12
    serialised = json.dumps(payload)
    assert ".pdf" not in serialised
    assert "Rahandusministeerium" not in serialised
    assert str(source) not in serialised


def test_the_command_has_no_path_or_url_option():
    from apps.legal_work.management.commands.sync_legal_opinion_documents import Command

    parser = Command().create_parser("manage.py", "sync_legal_opinion_documents")
    options = {action.dest for action in parser._actions}

    assert "path" not in options
    assert "url" not in options
    assert "source" not in options
    assert {"dry_run", "full", "max_documents", "as_json"} <= options


def test_the_command_fails_with_exit_code_one(opinion_roots, opinion_source):
    source, _ = opinion_roots
    (source / "Opinions.zip").write_bytes(b"not a zip")
    (source / "stray.pdf").write_bytes(b"not a pdf either")

    with pytest.raises(SystemExit) as raised:
        call_command("sync_legal_opinion_documents", "--json")

    assert raised.value.code == 1


def test_the_verification_command_reports_a_healthy_store(opinion_roots, opinion_source, capsys):
    source, _ = opinion_roots
    bootstrap(source, letters(2))
    synchronize_opinion_documents()

    call_command("verify_legal_opinion_store", "--json")

    import json

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["result"] == "ok"
    assert payload["blobs_checked"] == 2
    assert payload["blobs_intact"] == 2
    assert payload["blobs_missing"] == 0


def test_the_verification_command_notices_a_missing_blob(opinion_roots, opinion_source, capsys):
    source, _ = opinion_roots
    bootstrap(source, letters(1))
    synchronize_opinion_documents()
    blob_path(OpinionDocumentBlob.objects.get().sha256).unlink()

    with pytest.raises(SystemExit) as raised:
        call_command("verify_legal_opinion_store", "--json")

    assert raised.value.code == 1
    payload = __import__("json").loads(capsys.readouterr().out.strip())
    assert payload["blobs_missing"] == 1
