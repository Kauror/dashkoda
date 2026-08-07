"""Building the opinion catalogue: bounded, resumable, and all-or-nothing.

The bootstrap handover is 759 documents and 126 MB uncompressed. Reading,
validating, hashing, copying and extracting all of it in one request, one
transaction or one process would be a long job that fails whole; so the build
does a slice at a time and the next run continues where this one stopped.

The shape of a run:

1. ask every provider what it holds, and hash it — this is the manifest;
2. compare the manifest checksum with what is already published;
3. process up to `--max-documents` entries that have no terminal state yet;
4. when, and only when, every manifest entry has one, publish a snapshot.

Two invariants make that safe. **A partial build never becomes current** — the
snapshot is created in one transaction at the end, so the previous catalogue
stays the answer until a complete one exists. And **work is never repeated**: a
blob is keyed by its digest and an extraction by digest plus extractor version,
so a resumed run skips what is done and a changed manifest pays only for what
actually changed.

A document that cannot be read does not stop the build. It is catalogued with a
quarantine status and warning codes, excluded from matching, and counted.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.core.feed_sync import (
    describe_error,
    fail_feed,
    get_feed_state,
    mark_imported,
    mark_unchanged,
    publish_current,
    start_run,
    touch_checked,
)
from apps.sources.services import complete_import_run, fail_import_run

from .opinion_bootstrap import ensure_opinion_source
from .opinion_classification import classify_document
from .opinion_filenames import parse_opinion_filename
from .opinion_header import compare_with_filename, parse_document_header
from .opinion_models import (
    CatalogueBuildState,
    CatalogueResult,
    OpinionCatalogueEntry,
    OpinionCatalogueFeedState,
    OpinionCatalogueSnapshot,
    OpinionDocumentBlob,
    OpinionDocumentExtraction,
    SourceProvider,
)
from .opinion_pdf import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    ExtractionStatus,
    extract_text,
    validate_pdf,
)
from .opinion_sources import ManifestEntry, SourceRejected, collect_manifest, read_entry
from .opinion_storage import (
    BlobMismatch,
    StorageError,
    digest_bytes,
    ensure_directories,
    quarantine_blob,
    store_blob,
)

logger = logging.getLogger("dashkoda.legal_work.opinion_catalogue_sync")

LOCK_NAME = "legal_opinion_documents"
IMPORTER_NAME = "legal_opinion_catalogue"
SCHEMA_VERSION = "1.0"
ARTIFACT_NAME = "opinion-source-manifest"
# A fixed, non-secret provenance label. Never a path and never a filename.
EXTERNAL_REFERENCE = "chamber-opinion-inbox"

WARN_SOURCE_UNREADABLE = "source_entry_unreadable"

RESULT_IMPORTED = "imported"
RESULT_UNCHANGED = "unchanged"
RESULT_PARTIAL = "partial"
RESULT_FAILED = "failed"


@dataclass
class CatalogueReport:
    """What a run did, in numbers an operator and a JSON consumer can both use.

    Everything here is an aggregate or an identifier. No filename, recipient,
    subject, extracted text, storage path or full digest ever reaches it.
    """

    result: str
    detail: str = ""
    dry_run: bool = False
    snapshot_id: int | None = None
    manifest_entries: int = 0
    processed_entries: int = 0
    pending_entries: int = 0
    valid_entries: int = 0
    quarantined_entries: int = 0
    extracted_entries: int = 0
    needs_ocr_entries: int = 0
    failed_entries: int = 0
    unique_blobs: int = 0
    reused_blobs: int = 0
    source_manifest_checksum: str = ""
    extractor_version: str = EXTRACTOR_VERSION
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "dry_run": self.dry_run,
            "snapshot_id": self.snapshot_id,
            "manifest_entries": self.manifest_entries,
            "processed_entries": self.processed_entries,
            "pending_entries": self.pending_entries,
            "valid_entries": self.valid_entries,
            "quarantined_entries": self.quarantined_entries,
            "extracted_entries": self.extracted_entries,
            "needs_ocr_entries": self.needs_ocr_entries,
            "failed_entries": self.failed_entries,
            "unique_blobs": self.unique_blobs,
            "reused_blobs": self.reused_blobs,
            # A prefix only. The full checksum identifies the exact contents of
            # a private inbox and has no business in a log or a JSON line.
            "source_manifest_checksum": self.source_manifest_checksum[:12],
            "extractor_version": self.extractor_version,
        }


def synchronize_opinion_documents(
    *,
    dry_run: bool = False,
    full: bool = False,
    max_documents: int | None = None,
    actor=None,
) -> CatalogueReport:
    """Run one bounded slice of the catalogue build."""
    correlation_id = uuid.uuid4()
    source = ensure_opinion_source()
    state = get_feed_state(OpinionCatalogueFeedState, source)
    touch_checked(state)

    budget = (
        max_documents if max_documents is not None else settings.LEGAL_OPINION_MAX_DOCUMENTS_PER_RUN
    )
    if budget <= 0:
        budget = settings.LEGAL_OPINION_MAX_DOCUMENTS_PER_RUN

    try:
        manifest, checksum = collect_manifest()
    except (SourceRejected, OSError, ValueError) as error:
        return _fail(state, error, correlation_id)

    report = CatalogueReport(
        result=RESULT_UNCHANGED,
        manifest_entries=len(manifest),
        source_manifest_checksum=checksum,
        dry_run=dry_run,
    )

    if not manifest:
        _mark_unchanged(state, correlation_id, checksum, entries=0)
        report.detail = "Lähtekaustas ei ole dokumente."
        return report

    published = OpinionCatalogueSnapshot.objects.filter(source=source, is_current=True).first()
    if (
        published is not None
        and published.source_manifest_checksum == checksum
        and published.extractor_version == EXTRACTOR_VERSION
        and not full
    ):
        _mark_unchanged(state, correlation_id, checksum, entries=len(manifest))
        report.snapshot_id = published.pk
        report.processed_entries = len(manifest)
        report.valid_entries = published.valid_count
        report.quarantined_entries = published.quarantined_count
        report.extracted_entries = published.extracted_count
        report.needs_ocr_entries = published.needs_ocr_count
        report.failed_entries = published.failed_extraction_count
        report.detail = "Arvamuste kataloog on muutumatu."
        return report

    try:
        ensure_directories()
    except OSError as error:
        return _fail(state, error, correlation_id)

    known_blobs, known_extractions = _load_known(manifest)

    for entry in _outstanding(manifest, known_blobs, known_extractions)[:budget]:
        try:
            created = _process_entry(
                entry,
                known_blobs=known_blobs,
                known_extractions=known_extractions,
                dry_run=dry_run,
                correlation_id=correlation_id,
                actor=actor,
            )
        except (StorageError, BlobMismatch, SourceRejected, OSError) as error:
            return _fail(state, error, correlation_id)
        # A dry run stores nothing, so it has neither created nor reused a blob.
        # Counting its no-ops as reuse made a dry run claim work it had not done.
        if not dry_run:
            if created:
                report.unique_blobs += 1
            else:
                report.reused_blobs += 1

    remaining = _outstanding(manifest, known_blobs, known_extractions)
    report.processed_entries = len(manifest) - len(remaining)
    report.pending_entries = len(remaining)
    _summarise(report, manifest, known_blobs, known_extractions)

    if dry_run:
        _mark_progress(state, checksum, len(manifest), report.processed_entries)
        # Report the outcome a *live* run would reach. Leaving this at the
        # initial `unchanged` told an operator with 34 unprocessed documents
        # that there was nothing to do, which is the opposite of the truth and
        # the one question a dry run exists to answer.
        report.result = RESULT_PARTIAL if remaining else RESULT_IMPORTED
        report.detail = (
            f"Proovikäivitus: {len(manifest)} kirjet, "
            f"{report.processed_entries} valmis, {report.pending_entries} ootel. "
            "Midagi ei avaldatud ega salvestatud."
        )
        return report

    if remaining:
        _mark_progress(state, checksum, len(manifest), report.processed_entries)
        report.result = RESULT_PARTIAL
        report.detail = (
            f"Osaline: {report.processed_entries}/{len(manifest)} valmis, "
            f"{report.pending_entries} ootel. Käivita uuesti."
        )
        return report

    try:
        snapshot = _publish(
            source=source,
            state=state,
            manifest=manifest,
            checksum=checksum,
            known_blobs=known_blobs,
            known_extractions=known_extractions,
            report=report,
            correlation_id=correlation_id,
            actor=actor,
        )
    except Exception as error:  # noqa: BLE001 - a failure must leave the last good catalogue
        return _fail(state, error, correlation_id)

    report.snapshot_id = snapshot.pk
    report.result = RESULT_IMPORTED
    report.detail = f"Arvamuste kataloog avaldatud: {len(manifest)} dokumenti."
    logger.info(
        "opinion_catalogue.sync entries=%s valid=%s quarantined=%s extracted=%s",
        snapshot.entry_count,
        snapshot.valid_count,
        snapshot.quarantined_count,
        snapshot.extracted_count,
    )
    return report


# --------------------------------------------------------------------------
# One document
# --------------------------------------------------------------------------


def _load_known(
    manifest: list[ManifestEntry],
) -> tuple[dict[str, OpinionDocumentBlob], dict[str, OpinionDocumentExtraction]]:
    """Everything earlier runs already established about this manifest."""
    wanted = {entry.sha256 for entry in manifest}
    blobs = {blob.sha256: blob for blob in OpinionDocumentBlob.objects.filter(sha256__in=wanted)}
    extractions = {
        extraction.blob.sha256: extraction
        for extraction in OpinionDocumentExtraction.objects.filter(
            blob__sha256__in=wanted, extractor_version=EXTRACTOR_VERSION
        ).select_related("blob")
    }
    return blobs, extractions


def _outstanding(
    manifest: list[ManifestEntry],
    blobs: dict[str, OpinionDocumentBlob],
    extractions: dict[str, OpinionDocumentExtraction],
) -> list[ManifestEntry]:
    """Manifest entries that have not yet reached a terminal state.

    A quarantined blob *is* terminal: it will never gain an extraction, and
    waiting for one would make the build unable to finish.
    """
    pending = []
    for entry in manifest:
        blob = blobs.get(entry.sha256)
        if blob is None:
            pending.append(entry)
        elif blob.is_valid and entry.sha256 not in extractions:
            pending.append(entry)
    return pending


def _process_entry(
    entry: ManifestEntry,
    *,
    known_blobs: dict[str, OpinionDocumentBlob],
    known_extractions: dict[str, OpinionDocumentExtraction],
    dry_run: bool,
    correlation_id,
    actor,
) -> bool:
    """Validate, store and read one document. Returns whether a blob was created."""
    blob = known_blobs.get(entry.sha256)
    created_blob = False

    if blob is None:
        source_entry = read_entry(entry.provider, entry.key)
        if source_entry is None:
            raise SourceRejected("A manifest entry vanished between listing and reading.")
        if source_entry.sha256 != entry.sha256:
            raise SourceRejected("A source file changed while the catalogue was being built.")

        validation = validate_pdf(source_entry.payload)
        if dry_run:
            # A dry run proves a document can be read without writing a byte
            # into the managed store.
            return False

        if validation.is_valid:
            storage_key = store_blob(source_entry.payload, expected_digest=entry.sha256).key
        else:
            storage_key = quarantine_blob(
                source_entry.payload, digest=entry.sha256, reason=str(validation.status)
            )

        blob = OpinionDocumentBlob.objects.create(
            sha256=entry.sha256,
            storage_key=storage_key,
            byte_size=validation.byte_size,
            page_count=validation.page_count,
            validation_status=validation.status,
            is_encrypted=validation.is_encrypted,
            has_active_content=validation.has_active_content,
            warning_codes=sorted(validation.warnings),
        )
        known_blobs[entry.sha256] = blob
        created_blob = True

        if not validation.is_valid:
            record_event(
                action=AuditAction.OPINION_DOCUMENT_QUARANTINED,
                obj=blob,
                actor=actor,
                correlation_id=correlation_id,
                change_summary={
                    "reason": str(validation.status),
                    "digest_prefix": entry.sha256[:12],
                    "byte_size": validation.byte_size,
                },
            )
            return created_blob

    if not blob.is_valid or entry.sha256 in known_extractions or dry_run:
        return created_blob

    source_entry = read_entry(entry.provider, entry.key)
    if source_entry is None:
        raise SourceRejected("A manifest entry vanished before extraction.")

    result = extract_text(source_entry.payload)
    header = parse_document_header(result.first_page_text)

    known_extractions[entry.sha256] = OpinionDocumentExtraction.objects.create(
        blob=blob,
        extractor_name=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
        status=result.status,
        text=result.text,
        first_page_text=result.first_page_text,
        text_sha256=digest_bytes(result.text.encode("utf-8")) if result.text else "",
        page_count=result.page_count,
        detected_date=header.date,
        detected_recipient=header.recipient,
        detected_subject=header.subject,
        detected_reference=header.our_reference,
        their_reference=header.their_reference,
        our_reference=header.our_reference,
        warning_codes=sorted({*result.warnings, *header.warnings}),
    )
    return created_blob


def _summarise(
    report: CatalogueReport,
    manifest: list[ManifestEntry],
    blobs: dict[str, OpinionDocumentBlob],
    extractions: dict[str, OpinionDocumentExtraction],
) -> None:
    valid = quarantined = extracted = needs_ocr = failed = 0
    for entry in manifest:
        blob = blobs.get(entry.sha256)
        if blob is None:
            continue
        if not blob.is_valid:
            quarantined += 1
            continue
        valid += 1
        extraction = extractions.get(entry.sha256)
        if extraction is None:
            continue
        if extraction.status == ExtractionStatus.EXTRACTED:
            extracted += 1
        elif extraction.status == ExtractionStatus.NEEDS_OCR:
            needs_ocr += 1
        else:
            failed += 1
    report.valid_entries = valid
    report.quarantined_entries = quarantined
    report.extracted_entries = extracted
    report.needs_ocr_entries = needs_ocr
    report.failed_entries = failed


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------


def _publish(
    *,
    source,
    state: OpinionCatalogueFeedState,
    manifest: list[ManifestEntry],
    checksum: str,
    known_blobs: dict[str, OpinionDocumentBlob],
    known_extractions: dict[str, OpinionDocumentExtraction],
    report: CatalogueReport,
    correlation_id,
    actor,
) -> OpinionCatalogueSnapshot:
    """Publish one complete catalogue, atomically.

    The artifact is metadata-only. The documents live in the managed store and
    are never copied into the artifact area, which is served under a different
    policy and is not where private correspondence belongs.
    """
    collection = type(
        "Collection",
        (),
        {"sha256": checksum, "size_bytes": sum(entry.byte_size for entry in manifest)},
    )()
    artifact, run = start_run(
        source,
        collection,
        existing_artifact=None,
        importer_name=IMPORTER_NAME,
        external_reference=EXTERNAL_REFERENCE,
        artifact_name=ARTIFACT_NAME,
        schema_version=SCHEMA_VERSION,
        dry_run=False,
        actor=actor,
        correlation_id=correlation_id,
    )

    try:
        with transaction.atomic():
            snapshot = OpinionCatalogueSnapshot(
                source=source,
                artifact=artifact,
                import_run=run,
                source_manifest_checksum=checksum,
                extractor_version=EXTRACTOR_VERSION,
                observed_at=timezone.now(),
                entry_count=len(manifest),
                valid_count=report.valid_entries,
                quarantined_count=report.quarantined_entries,
                extracted_count=report.extracted_entries,
                needs_ocr_count=report.needs_ocr_entries,
                failed_extraction_count=report.failed_entries,
                is_current=False,
            )
            snapshot.save()

            OpinionCatalogueEntry.objects.bulk_create(
                [_row(snapshot, entry, known_blobs, known_extractions) for entry in manifest],
                batch_size=200,
            )
            written = OpinionCatalogueEntry.objects.filter(snapshot=snapshot).count()
            if written != len(manifest):
                raise RuntimeError("The catalogue did not write every manifest entry.")

            publish_current(snapshot)
            complete_import_run(run, rows_added=len(manifest), actor=actor)
            record_event(
                action=AuditAction.OPINION_CATALOGUE_IMPORTED,
                obj=snapshot,
                actor=actor,
                correlation_id=correlation_id,
                change_summary={
                    "source": source.slug,
                    "snapshot_id": snapshot.pk,
                    "entries": snapshot.entry_count,
                    "valid": snapshot.valid_count,
                    "quarantined": snapshot.quarantined_count,
                    "extracted": snapshot.extracted_count,
                    "needs_ocr": snapshot.needs_ocr_count,
                    "failed_extraction": snapshot.failed_extraction_count,
                    "checksum_prefix": checksum[:12],
                    "extractor_version": EXTRACTOR_VERSION,
                },
            )
    except Exception:
        run.refresh_from_db()
        if not run.is_terminal:
            fail_import_run(run, errors=[{"type": "publication_failed"}], actor=actor)
        raise

    state.build_state = CatalogueBuildState.COMPLETE
    state.building_manifest_checksum = ""
    state.manifest_entry_count = len(manifest)
    state.processed_entry_count = len(manifest)
    state.save(
        update_fields=[
            "build_state",
            "building_manifest_checksum",
            "manifest_entry_count",
            "processed_entry_count",
            "updated_at",
        ]
    )
    mark_imported(state, snapshot, current_field="current_snapshot")
    return snapshot


def _row(
    snapshot: OpinionCatalogueSnapshot,
    entry: ManifestEntry,
    blobs: dict[str, OpinionDocumentBlob],
    extractions: dict[str, OpinionDocumentExtraction],
) -> OpinionCatalogueEntry:
    blob = blobs.get(entry.sha256)
    usable = blob is not None and blob.is_valid
    extraction = extractions.get(entry.sha256) if usable else None
    parsed = parse_opinion_filename(entry.filename)
    first_page = extraction.first_page_text if extraction else ""
    classification, signals = classify_document(
        filename_subject=parsed.subject, first_page_text=first_page
    )

    warnings = set(parsed.warnings)
    if blob is None:
        warnings.add(WARN_SOURCE_UNREADABLE)
    if extraction is not None:
        warnings.update(extraction.warning_codes or [])
        warnings.update(
            compare_with_filename(
                parse_document_header(first_page),
                filename_date=parsed.date,
                filename_recipient=parsed.recipient,
            )
        )

    return OpinionCatalogueEntry(
        snapshot=snapshot,
        source_provider=(
            SourceProvider.BOOTSTRAP_ZIP
            if entry.provider == "bootstrap_zip"
            else SourceProvider.DIRECTORY
        ),
        source_entry_key=entry.key[:400],
        original_filename=entry.filename[:400],
        display_filename=parsed.display[:400],
        filename_date=parsed.date,
        filename_recipient=parsed.recipient[:200],
        filename_subject=parsed.subject[:500],
        classification=classification,
        classification_signals=signals,
        blob=blob,
        extraction=extraction,
        source_order=entry.order,
        warning_codes=sorted(warnings),
    )


# --------------------------------------------------------------------------
# Feed state
# --------------------------------------------------------------------------


def _mark_unchanged(state, correlation_id, checksum: str, *, entries: int) -> None:
    state.build_state = CatalogueBuildState.COMPLETE if entries else CatalogueBuildState.IDLE
    state.building_manifest_checksum = ""
    state.manifest_entry_count = entries
    state.processed_entry_count = entries
    state.save(
        update_fields=[
            "build_state",
            "building_manifest_checksum",
            "manifest_entry_count",
            "processed_entry_count",
            "updated_at",
        ]
    )
    mark_unchanged(
        state,
        correlation_id=correlation_id,
        audit_action=AuditAction.OPINION_CATALOGUE_UNCHANGED,
        change_summary={
            "source": state.source.slug,
            "entries": entries,
            "checksum_prefix": checksum[:12],
            "extractor_version": EXTRACTOR_VERSION,
        },
    )


def _mark_progress(state, checksum: str, total: int, processed: int) -> None:
    state.build_state = CatalogueBuildState.BUILDING
    state.building_manifest_checksum = checksum
    state.manifest_entry_count = total
    state.processed_entry_count = processed
    state.last_result = CatalogueResult.PARTIAL
    state.save(
        update_fields=[
            "build_state",
            "building_manifest_checksum",
            "manifest_entry_count",
            "processed_entry_count",
            "last_result",
            "updated_at",
        ]
    )


def _fail(state, error: Exception, correlation_id) -> CatalogueReport:
    """Record a failure without disturbing the last good catalogue."""
    message = describe_error(error)
    state.build_state = CatalogueBuildState.FAILED
    state.save(update_fields=["build_state", "updated_at"])
    fail_feed(
        state,
        message,
        correlation_id=correlation_id,
        audit_action=AuditAction.OPINION_CATALOGUE_FAILED,
        logger=logger,
    )
    return CatalogueReport(result=RESULT_FAILED, detail=message)
