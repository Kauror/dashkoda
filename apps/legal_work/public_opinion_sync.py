"""Collecting the public Koda.ee opinion corpus: accumulating, last-good, bounded.

Two modes over one engine:

**Full** walks both listings across the configured historical window — the
`Meie arvamus` view to its end, the news listing until its dated cards leave
the window — reads every candidate article, and fetches every attachment not
already known. This is the historical backfill, and rerunning it is cheap: a
known attachment URL whose blob exists is never downloaded twice.

**Incremental** reads only the listing edge: the first pages of each listing,
plus a refresh of articles published inside a short overlap window, because
Koda.ee attaches the letter to an article a day or two after publishing it.
Everything else is carried forward from the previous snapshot untouched.

The corpus *accumulates*. Every snapshot carries every previously observed
page and document forward, so a page leaving the listing or answering 404
changes `is_present` on the next snapshot and nothing else. History is never
inferred from absence, and a failed run publishes nothing — the previous
snapshot stays current, exactly like every other feed here.

Failure is asymmetric by design. A listing that cannot be read fails the run:
the edge is the one thing a run must see. A *known* detail page that cannot be
read is carried forward and counted. A *new* detail page that cannot be read
fails the run, because a snapshot claiming to cover the edge while missing an
edge page would be the partial publication this project refuses everywhere.
A new attachment that cannot be fetched or validated is recorded as failed
provenance — Koda.ee demonstrably published something there — and is retried
on the next run because no blob is attached.

Bytes reuse the private pipeline end to end: the same validation, the same
quarantine rules, the same content-addressed store, the same versioned
extraction. A PDF published publicly and filed privately is one blob with two
provenances, which is the whole point of the second source.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from urllib.parse import urljoin

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.core.canonical import canonical_checksum
from apps.core.feed_sync import (
    describe_error,
    fail_feed,
    find_published_artifact,
    get_feed_state,
    mark_imported,
    mark_unchanged,
    publish_current,
    start_run,
    touch_checked,
)
from apps.sources.services import complete_import_run, fail_import_run

from .current_topics import content_key_for
from .opinion_classification import classify_document
from .opinion_filenames import parse_opinion_filename
from .opinion_header import parse_document_header
from .opinion_models import OpinionDocumentBlob, OpinionDocumentExtraction
from .opinion_pdf import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    extract_text,
    validate_pdf,
)
from .opinion_storage import (
    digest_bytes,
    ensure_directories,
    quarantine_blob,
    store_blob,
)
from .public_opinion_bootstrap import ensure_public_opinion_source
from .public_opinion_models import (
    PublicFetchState,
    PublicOpinionDocument,
    PublicOpinionFeedState,
    PublicOpinionPage,
    PublicOpinionSnapshot,
    PublicPageType,
)
from .public_opinions import (
    PublicOpinionCollectionError,
    attachment_filename,
    canonical_article_url,
    fetch_html,
    fetch_pdf,
    is_article_url,
    opinion_evidence,
    parse_meie_arvamus_listing,
    parse_news_detail,
    parse_news_listing,
)

logger = logging.getLogger("dashkoda.legal_work.public_opinion_sync")

LOCK_NAME = "legal_public_opinions"
IMPORTER_NAME = "public_opinion_source"
SCHEMA_VERSION = "1.0"
ARTIFACT_NAME = "public-opinion-corpus"
# A fixed, non-secret provenance label.
EXTERNAL_REFERENCE = "koda-public:opinions"

FAILURE_HTTP = "http_error"
FAILURE_NOT_FOUND = "http_404"
FAILURE_INVALID_PDF = "invalid_pdf"
FAILURE_UNPARSABLE = "unparsable"

RESULT_IMPORTED = "imported"
RESULT_UNCHANGED = "unchanged"
RESULT_FAILED = "failed"


@dataclass
class PublicOpinionReport:
    """Aggregates only. No URL, title, filename or digest ever reaches this."""

    result: str
    detail: str = ""
    dry_run: bool = False
    full: bool = False
    snapshot_id: int | None = None
    listing_pages_fetched: int = 0
    detail_pages_fetched: int = 0
    documents_fetched: int = 0
    page_count: int = 0
    document_count: int = 0
    article_only_page_count: int = 0
    new_pages: int = 0
    carried_pages: int = 0
    failed_pages: int = 0
    new_blobs: int = 0
    known_blobs: int = 0
    invalid_documents: int = 0
    reused_extractions: int = 0
    new_extractions: int = 0

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "dry_run": self.dry_run,
            "full": self.full,
            "snapshot_id": self.snapshot_id,
            "listing_pages_fetched": self.listing_pages_fetched,
            "detail_pages_fetched": self.detail_pages_fetched,
            "documents_fetched": self.documents_fetched,
            "page_count": self.page_count,
            "document_count": self.document_count,
            "article_only_page_count": self.article_only_page_count,
            "new_pages": self.new_pages,
            "carried_pages": self.carried_pages,
            "failed_pages": self.failed_pages,
            "new_blobs": self.new_blobs,
            "known_blobs": self.known_blobs,
            "invalid_documents": self.invalid_documents,
            "reused_extractions": self.reused_extractions,
            "new_extractions": self.new_extractions,
        }


@dataclass
class _PageDraft:
    """One page of the next corpus, before it becomes a row."""

    content_key: str
    canonical_url: str
    page_type: str
    title: str
    listing_summary: str = ""
    body_text: str = ""
    published_date: dt.date | None = None
    opinion_evidence_codes: list[str] = field(default_factory=list)
    fetch_state: str = PublicFetchState.CARRIED
    failure_code: str = ""
    content_hash: str = ""
    first_seen_at: dt.datetime | None = None
    last_fetched_at: dt.datetime | None = None
    is_present: bool = True
    documents: list[_DocumentDraft] = field(default_factory=list)


@dataclass
class _DocumentDraft:
    """One attachment of the next corpus, before it becomes a row."""

    pdf_url: str
    attachment_label: str = ""
    display_filename: str = ""
    filename_date: dt.date | None = None
    filename_recipient: str = ""
    filename_subject: str = ""
    classification: str = ""
    classification_signals: list = field(default_factory=list)
    blob: OpinionDocumentBlob | None = None
    extraction: OpinionDocumentExtraction | None = None
    fetch_state: str = PublicFetchState.CARRIED
    failure_code: str = ""
    first_seen_at: dt.datetime | None = None
    is_present: bool = True
    source_order: int = 0
    warning_codes: list = field(default_factory=list)


def synchronize_public_opinions(
    *,
    dry_run: bool = False,
    full: bool = False,
    actor=None,
    session=None,
) -> PublicOpinionReport:
    """Run one public opinion collection. The caller holds the advisory lock."""
    correlation_id = uuid.uuid4()
    source = ensure_public_opinion_source()
    state = get_feed_state(PublicOpinionFeedState, source)
    touch_checked(state)

    report = PublicOpinionReport(result=RESULT_UNCHANGED, dry_run=dry_run, full=full)
    previous = PublicOpinionSnapshot.objects.filter(source=source, is_current=True).first()

    if not full and not state.backfill_complete:
        report.result = RESULT_FAILED
        report.detail = "Ajalooline kogumine ei ole veel tehtud. Käivita kõigepealt --full."
        return report

    try:
        drafts = _collect(
            previous=previous,
            full=full,
            dry_run=dry_run,
            report=report,
            session=session,
        )
    except PublicOpinionCollectionError as error:
        return _fail(state, error, correlation_id)
    except OSError as error:
        return _fail(state, error, correlation_id)

    checksum, canonical_size = _corpus_checksum(drafts)
    _summarise(report, drafts)

    if previous is not None and previous.corpus_checksum == checksum:
        if not dry_run:
            _mark_unchanged(state, correlation_id, checksum, pages=len(drafts))
        report.snapshot_id = previous.pk
        report.detail = "Avalik arvamuskorpus on muutumatu."
        return report

    if dry_run:
        report.result = RESULT_IMPORTED
        report.detail = (
            f"Proovikäivitus: {report.page_count} lehte, {report.document_count} "
            "dokumenti. Midagi ei avaldatud ega salvestatud."
        )
        return report

    try:
        snapshot = _publish(
            source=source,
            state=state,
            drafts=drafts,
            checksum=checksum,
            canonical_size=canonical_size,
            report=report,
            full=full,
            correlation_id=correlation_id,
            actor=actor,
        )
    except Exception as error:  # noqa: BLE001 - a failure must keep the last good corpus
        return _fail(state, error, correlation_id)

    report.result = RESULT_IMPORTED
    report.snapshot_id = snapshot.pk
    report.detail = (
        f"Avalik arvamuskorpus avaldatud: {report.page_count} lehte, "
        f"{report.document_count} dokumenti."
    )
    logger.info(
        "public_opinions.sync pages=%s documents=%s new_pages=%s new_blobs=%s full=%s",
        report.page_count,
        report.document_count,
        report.new_pages,
        report.new_blobs,
        full,
    )
    return report


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def _collect(*, previous, full, dry_run, report, session) -> list[_PageDraft]:
    """Walk the listings, read what the mode requires, carry the rest forward."""
    known_pages = _load_previous(previous)
    now = timezone.now()

    meie_arvamus_urls, news_cards = _walk_listings(
        known_pages=known_pages, full=full, report=report, session=session
    )

    candidates: dict[str, dict] = {}
    for url in meie_arvamus_urls:
        candidates.setdefault(url, {"summary": "", "listed": True})
    for card in news_cards:
        url = canonical_article_url(card.url)
        entry = candidates.setdefault(url, {"summary": "", "listed": False})
        if card.summary and not entry["summary"]:
            entry["summary"] = card.summary

    # A full walk also re-reads every known page the listings no longer name:
    # that is the one honest way to learn a page is gone, and a 404 there
    # moves `is_present` rather than deleting anything.
    listed_keys = {content_key_for(url) for url in candidates}
    if full:
        for key, page in known_pages.items():
            if key not in listed_keys:
                candidates.setdefault(
                    page.canonical_url, {"summary": page.listing_summary, "listed": False}
                )

    boundary = _refresh_boundary(known_pages)
    from_year = settings.KODA_OPINIONS_FROM_YEAR

    drafts: dict[str, _PageDraft] = {}
    for url, found in candidates.items():
        key = content_key_for(url)
        known = known_pages.get(key)
        must_fetch = known is None or (
            full
            or known.published_date is None
            or (boundary is not None and known.published_date >= boundary)
        )
        if not must_fetch:
            continue

        draft = _read_page(
            url=url,
            key=key,
            known=known,
            listed=found["listed"],
            summary=found["summary"],
            now=now,
            dry_run=dry_run,
            report=report,
            session=session,
        )
        if draft is None:
            continue
        # The historical window is a property of the *page*, decided from its
        # own date: a full crawl walks listings past the boundary because the
        # `Meie arvamus` cards carry no year, but pages outside the window are
        # not activated.
        if (
            known is None
            and draft.published_date is not None
            and draft.published_date.year < from_year
        ):
            continue
        if known is None and not draft.opinion_evidence_codes:
            # Ordinary news: no editorial listing, no position wording, no
            # opinion-shaped attachment. Not part of this corpus.
            continue
        drafts[key] = draft

    # Everything known and not re-read this run is carried forward untouched.
    for key, page in known_pages.items():
        if key not in drafts:
            drafts[key] = _carry_page(page, report=report)

    ordered = sorted(
        drafts.values(),
        key=lambda d: (d.published_date or dt.date.min, d.content_key),
        reverse=True,
    )
    return ordered


def _walk_listings(*, known_pages, full, report, session):
    """Both listing edges, each under its own stop rule.

    The news listing stops on dates — its cards carry full dates. The
    `Meie arvamus` listing cannot: a full walk runs to the view's end, an
    incremental one reads a fixed number of edge pages.
    """
    meie_arvamus_urls: set[str] = set()
    listing_url = settings.KODA_OPINIONS_MEIE_ARVAMUS_URL
    max_pages = (
        settings.KODA_OPINIONS_MAX_LISTING_PAGES
        if full
        else settings.KODA_OPINIONS_INCREMENTAL_LISTING_PAGES
    )
    for page_number in range(max_pages):
        page_url = listing_url if page_number == 0 else f"{listing_url}?page={page_number}"
        _pause()
        cards = parse_meie_arvamus_listing(fetch_html(page_url, session=session))
        report.listing_pages_fetched += 1
        if not cards:
            break
        for card in cards:
            absolute = canonical_article_url(urljoin(listing_url, card.url))
            if is_article_url(absolute):
                meie_arvamus_urls.add(absolute)
    else:
        if full:
            raise PublicOpinionCollectionError(
                "Meie arvamus loend ei lõppenud lubatud lehtede piires."
            )

    news_cards = []
    news_url = settings.KODA_OPINIONS_NEWS_URL
    boundary = _refresh_boundary(known_pages)
    from_year = settings.KODA_OPINIONS_FROM_YEAR
    stop_before = dt.date(from_year, 1, 1)
    for page_number in range(settings.KODA_OPINIONS_MAX_LISTING_PAGES):
        page_url = news_url if page_number == 0 else f"{news_url}?page={page_number}"
        _pause()
        cards = parse_news_listing(fetch_html(page_url, session=session))
        report.listing_pages_fetched += 1
        if not cards:
            break
        dated = [card for card in cards if card.card_date is not None]
        for card in cards:
            absolute = canonical_article_url(urljoin(news_url, card.url))
            if is_article_url(absolute):
                news_cards.append(replace(card, url=absolute))
        if full:
            if dated and max(card.card_date for card in dated) < stop_before:
                break
        else:
            edge = boundary or stop_before
            if dated and max(card.card_date for card in dated) < edge:
                break
    else:
        if full:
            raise PublicOpinionCollectionError(
                "Uudiste loend ei jõudnud ajaloolise piirini lubatud lehtede sees."
            )

    return meie_arvamus_urls, news_cards


def _read_page(*, url, key, known, listed, summary, now, dry_run, report, session):
    """Fetch and normalise one article, resolving its attachments."""
    try:
        _pause()
        html = fetch_html(url, session=session)
    except PublicOpinionCollectionError as error:
        if known is None:
            # A new edge page the run cannot read: the snapshot would be a
            # partial claim about the edge. Refuse the run.
            raise
        report.failed_pages += 1
        draft = _carry_page(known)
        draft.fetch_state = PublicFetchState.FAILED
        draft.failure_code = _failure_code(error)
        if draft.failure_code == FAILURE_NOT_FOUND:
            # The one failure that *is* an answer: the page is gone. Its rows
            # and its documents stay — availability moved, history did not.
            draft.is_present = False
        return draft

    report.detail_pages_fetched += 1
    detail = parse_news_detail(html, base_url=url)
    if not detail.title or not detail.body_text:
        if known is None:
            raise PublicOpinionCollectionError(f"Artikli leht ei ole loetav: {key[:12]}.")
        report.failed_pages += 1
        draft = _carry_page(known)
        draft.fetch_state = PublicFetchState.FAILED
        draft.failure_code = FAILURE_UNPARSABLE
        return draft

    evidence = opinion_evidence(
        listed_in_meie_arvamus=listed,
        title=detail.title,
        body_text=detail.body_text,
        attachments=detail.attachments,
    )
    if known is not None:
        previous_codes = set(known.opinion_evidence_codes or [])
        evidence = sorted(previous_codes | set(evidence))

    draft = _PageDraft(
        content_key=key,
        canonical_url=url,
        page_type=(
            PublicPageType.MEIE_ARVAMUS
            if listed or (known is not None and known.page_type == PublicPageType.MEIE_ARVAMUS)
            else PublicPageType.NEWS
        ),
        title=detail.title,
        listing_summary=summary or (known.listing_summary if known else ""),
        body_text=detail.body_text,
        published_date=detail.published_date
        or (known.published_date if known is not None else None),
        opinion_evidence_codes=list(evidence),
        fetch_state=PublicFetchState.FETCHED,
        content_hash=hashlib.sha256(detail.body_text.encode("utf-8")).hexdigest(),
        first_seen_at=known.first_seen_at if known is not None else now,
        last_fetched_at=now,
        is_present=True,
    )

    known_documents = (
        {document.pdf_url: document for document in known.documents.all()}
        if known is not None
        else {}
    )
    seen_urls: set[str] = set()
    for order, attachment in enumerate(detail.attachments):
        seen_urls.add(attachment.url)
        draft.documents.append(
            _read_document(
                attachment=attachment,
                order=order,
                known=known_documents.get(attachment.url),
                now=now,
                dry_run=dry_run,
                report=report,
                session=session,
            )
        )
    # An attachment that vanished from the page is history, not deletion.
    for pdf_url, document in known_documents.items():
        if pdf_url not in seen_urls:
            carried = _carry_document(document)
            carried.is_present = False
            draft.documents.append(carried)

    return draft


def _read_document(*, attachment, order, known, now, dry_run, report, session):
    """Resolve one attachment to a blob and an extraction, fetching at most once.

    A known URL whose blob exists is never downloaded again: Koda.ee uploads a
    changed letter under a new filename, so the URL is content-stable. A known
    URL whose fetch previously failed is retried, because no blob pins it.
    """
    parsed = parse_opinion_filename(attachment_filename(attachment))
    draft = _DocumentDraft(
        pdf_url=attachment.url,
        attachment_label=attachment.label[:400],
        display_filename=parsed.display[:400],
        filename_date=parsed.date,
        filename_recipient=parsed.recipient[:200],
        filename_subject=parsed.subject[:500],
        first_seen_at=known.first_seen_at if known is not None else now,
        is_present=True,
        source_order=order,
        warning_codes=sorted(parsed.warnings),
    )

    if known is not None and known.blob_id is not None:
        reused = _carry_document(known)
        reused.source_order = order
        reused.is_present = True
        report.known_blobs += 1
        return reused

    try:
        _pause()
        payload = fetch_pdf(attachment.url, session=session)
    except PublicOpinionCollectionError as error:
        draft.fetch_state = PublicFetchState.FAILED
        draft.failure_code = _failure_code(error)
        draft.classification, draft.classification_signals = classify_document(
            filename_subject=parsed.subject, first_page_text=""
        )
        report.invalid_documents += 1
        return draft

    report.documents_fetched += 1
    digest = digest_bytes(payload)
    blob = OpinionDocumentBlob.objects.filter(sha256=digest).first()
    validation = validate_pdf(payload)

    if dry_run:
        draft.fetch_state = PublicFetchState.FETCHED
        if not validation.is_valid:
            draft.failure_code = FAILURE_INVALID_PDF
            report.invalid_documents += 1
        elif blob is not None:
            report.known_blobs += 1
        else:
            report.new_blobs += 1
        draft.classification, draft.classification_signals = classify_document(
            filename_subject=parsed.subject, first_page_text=""
        )
        return draft

    if blob is None:
        ensure_directories()
        if validation.is_valid:
            storage_key = store_blob(payload, expected_digest=digest).key
        else:
            storage_key = quarantine_blob(payload, digest=digest, reason=str(validation.status))
        blob = OpinionDocumentBlob.objects.create(
            sha256=digest,
            storage_key=storage_key,
            byte_size=validation.byte_size,
            page_count=validation.page_count,
            validation_status=validation.status,
            is_encrypted=validation.is_encrypted,
            has_active_content=validation.has_active_content,
            warning_codes=sorted(validation.warnings),
        )
        report.new_blobs += 1
        if not validation.is_valid:
            record_event(
                action=AuditAction.OPINION_DOCUMENT_QUARANTINED,
                obj=blob,
                change_summary={
                    "reason": str(validation.status),
                    "digest_prefix": digest[:12],
                    "byte_size": validation.byte_size,
                    "provenance": "public",
                },
            )
    else:
        report.known_blobs += 1

    draft.blob = blob
    draft.fetch_state = PublicFetchState.FETCHED
    if not blob.is_valid:
        draft.failure_code = FAILURE_INVALID_PDF
        report.invalid_documents += 1
    else:
        draft.extraction = _ensure_extraction(blob, payload, report)

    first_page = draft.extraction.first_page_text if draft.extraction else ""
    draft.classification, draft.classification_signals = classify_document(
        filename_subject=parsed.subject, first_page_text=first_page
    )
    return draft


def _ensure_extraction(blob, payload, report) -> OpinionDocumentExtraction:
    """The same bytes read once per extractor version, whatever the source."""
    existing = OpinionDocumentExtraction.objects.filter(
        blob=blob, extractor_version=EXTRACTOR_VERSION
    ).first()
    if existing is not None:
        report.reused_extractions += 1
        return existing

    result = extract_text(payload)
    header = parse_document_header(result.first_page_text)
    report.new_extractions += 1
    return OpinionDocumentExtraction.objects.create(
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


# --------------------------------------------------------------------------
# Carry-forward
# --------------------------------------------------------------------------


def _load_previous(previous) -> dict[str, PublicOpinionPage]:
    if previous is None:
        return {}
    pages = previous.pages.prefetch_related("documents")
    return {page.content_key: page for page in pages}


def _refresh_boundary(known_pages) -> dt.date | None:
    """Pages published on or after this date are re-read on an incremental run."""
    dates = [
        page.published_date for page in known_pages.values() if page.published_date is not None
    ]
    if not dates:
        return None
    return max(dates) - dt.timedelta(days=settings.KODA_OPINIONS_INCREMENTAL_OVERLAP_DAYS)


def _carry_page(page: PublicOpinionPage, *, report=None) -> _PageDraft:
    """The previous snapshot's answer, restated without having looked."""
    draft = _PageDraft(
        content_key=page.content_key,
        canonical_url=page.canonical_url,
        page_type=page.page_type,
        title=page.title,
        listing_summary=page.listing_summary,
        body_text=page.body_text,
        published_date=page.published_date,
        opinion_evidence_codes=list(page.opinion_evidence_codes or []),
        fetch_state=PublicFetchState.CARRIED,
        failure_code=page.failure_code,
        content_hash=page.content_hash,
        first_seen_at=page.first_seen_at,
        last_fetched_at=page.last_fetched_at,
        is_present=page.is_present,
        documents=[_carry_document(document) for document in page.documents.all()],
    )
    if report is not None:
        report.carried_pages += 1
    return draft


def _carry_document(document: PublicOpinionDocument) -> _DocumentDraft:
    return _DocumentDraft(
        pdf_url=document.pdf_url,
        attachment_label=document.attachment_label,
        display_filename=document.display_filename,
        filename_date=document.filename_date,
        filename_recipient=document.filename_recipient,
        filename_subject=document.filename_subject,
        classification=document.classification,
        classification_signals=list(document.classification_signals or []),
        blob=document.blob,
        extraction=document.extraction,
        fetch_state=PublicFetchState.CARRIED,
        failure_code=document.failure_code,
        first_seen_at=document.first_seen_at,
        is_present=document.is_present,
        source_order=document.source_order,
        warning_codes=list(document.warning_codes or []),
    )


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------


def _corpus_checksum(drafts: list[_PageDraft]) -> tuple[str, int]:
    """The canonical corpus document's identity: digest and byte length.

    Computed over what the corpus *says*, never over any response body, so
    markup churn cannot republish identical data. The size is the canonical
    document's own — which is what the metadata-only artifact records, and
    which is never zero even for an empty corpus.
    """
    canonical = {
        "dataset": "koda-public-opinions",
        "schema_version": SCHEMA_VERSION,
        "pages": [
            {
                "key": draft.content_key,
                "url": draft.canonical_url,
                "type": draft.page_type,
                "title": draft.title,
                "published": draft.published_date,
                "content_hash": draft.content_hash,
                "evidence": sorted(draft.opinion_evidence_codes),
                "present": draft.is_present,
                # Deliberately no fetch state: whether a fact was re-read or
                # carried is run bookkeeping, and identical content re-read
                # must not republish.
                "documents": [
                    {
                        "url": document.pdf_url,
                        "sha256": document.blob.sha256 if document.blob else "",
                        "present": document.is_present,
                    }
                    for document in sorted(draft.documents, key=lambda d: d.pdf_url)
                ],
            }
            for draft in sorted(drafts, key=lambda d: d.content_key)
        ],
    }
    return canonical_checksum(canonical)


def _summarise(report: PublicOpinionReport, drafts: list[_PageDraft]) -> None:
    report.page_count = len(drafts)
    report.document_count = sum(len(draft.documents) for draft in drafts)
    report.article_only_page_count = sum(1 for draft in drafts if not draft.documents)
    report.new_pages = sum(
        1
        for draft in drafts
        if draft.fetch_state == PublicFetchState.FETCHED
        and draft.first_seen_at is not None
        and draft.last_fetched_at == draft.first_seen_at
    )


def _publish(
    *, source, state, drafts, checksum, canonical_size, report, full, correlation_id, actor
) -> PublicOpinionSnapshot:
    """One complete corpus, atomically. The artifact is metadata-only.

    An artifact left behind by a failed publication is reused rather than
    re-registered, exactly like every other collector: the content identity
    already exists under this source, and registering it twice is refused.
    """
    collection = type(
        "Collection",
        (),
        {"sha256": checksum, "size_bytes": canonical_size},
    )()
    existing_artifact, _already_published = find_published_artifact(source, checksum, IMPORTER_NAME)
    artifact, run = start_run(
        source,
        collection,
        existing_artifact=existing_artifact,
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
            snapshot = PublicOpinionSnapshot(
                source=source,
                artifact=artifact,
                import_run=run,
                observed_at=timezone.now(),
                corpus_checksum=checksum,
                page_count=report.page_count,
                document_count=report.document_count,
                article_only_page_count=report.article_only_page_count,
                listing_pages_fetched=report.listing_pages_fetched,
                detail_pages_fetched=report.detail_pages_fetched,
                documents_fetched=report.documents_fetched,
                new_blob_count=report.new_blobs,
                known_blob_count=report.known_blobs,
                invalid_document_count=report.invalid_documents,
                failed_page_count=report.failed_pages,
                is_current=False,
            )
            snapshot.save()

            for draft in drafts:
                page = PublicOpinionPage.objects.create(
                    snapshot=snapshot,
                    content_key=draft.content_key,
                    canonical_url=draft.canonical_url,
                    page_type=draft.page_type,
                    title=draft.title,
                    listing_summary=draft.listing_summary,
                    body_text=draft.body_text,
                    published_date=draft.published_date,
                    opinion_evidence_codes=sorted(draft.opinion_evidence_codes),
                    fetch_state=draft.fetch_state,
                    failure_code=draft.failure_code,
                    content_hash=draft.content_hash,
                    first_seen_at=draft.first_seen_at,
                    last_fetched_at=draft.last_fetched_at,
                    is_present=draft.is_present,
                )
                PublicOpinionDocument.objects.bulk_create(
                    [
                        PublicOpinionDocument(
                            snapshot=snapshot,
                            page=page,
                            pdf_url=document.pdf_url,
                            attachment_label=document.attachment_label,
                            display_filename=document.display_filename,
                            filename_date=document.filename_date,
                            filename_recipient=document.filename_recipient,
                            filename_subject=document.filename_subject,
                            classification=document.classification,
                            classification_signals=document.classification_signals,
                            blob=document.blob,
                            extraction=document.extraction,
                            fetch_state=document.fetch_state,
                            failure_code=document.failure_code,
                            first_seen_at=document.first_seen_at,
                            is_present=document.is_present,
                            source_order=document.source_order,
                            warning_codes=document.warning_codes,
                        )
                        for document in draft.documents
                    ],
                    batch_size=200,
                )

            publish_current(snapshot)
            complete_import_run(run, rows_added=report.page_count, actor=actor)
            if full:
                state.backfill_complete = True
                state.save(update_fields=["backfill_complete", "updated_at"])
            record_event(
                action=AuditAction.PUBLIC_OPINIONS_IMPORTED,
                obj=snapshot,
                actor=actor,
                correlation_id=correlation_id,
                change_summary={
                    "source": source.slug,
                    "snapshot_id": snapshot.pk,
                    "pages": report.page_count,
                    "documents": report.document_count,
                    "new_pages": report.new_pages,
                    "new_blobs": report.new_blobs,
                    "invalid_documents": report.invalid_documents,
                    "checksum_prefix": checksum[:12],
                    "full": full,
                },
            )
    except Exception:
        run.refresh_from_db()
        if not run.is_terminal:
            fail_import_run(run, errors=[{"type": "publication_failed"}], actor=actor)
        raise

    mark_imported(state, snapshot, current_field="current_snapshot")
    return snapshot


def _mark_unchanged(state, correlation_id, checksum: str, *, pages: int) -> None:
    mark_unchanged(
        state,
        correlation_id=correlation_id,
        audit_action=AuditAction.PUBLIC_OPINIONS_UNCHANGED,
        change_summary={
            "source": state.source.slug,
            "pages": pages,
            "checksum_prefix": checksum[:12],
        },
    )


def _fail(state, error: Exception, correlation_id) -> PublicOpinionReport:
    """Record a failure without disturbing the last good corpus."""
    message = describe_error(error)
    fail_feed(
        state,
        message,
        correlation_id=correlation_id,
        audit_action=AuditAction.PUBLIC_OPINIONS_FAILED,
        logger=logger,
    )
    return PublicOpinionReport(result=RESULT_FAILED, detail=message)


def _failure_code(error: Exception) -> str:
    from apps.core.public_http import FetchFailure, PublicFetchError

    cause = error.__cause__
    if isinstance(cause, PublicFetchError):
        if cause.failure == FetchFailure.NOT_FOUND:
            return FAILURE_NOT_FOUND
        return str(cause.failure)
    return FAILURE_HTTP


def _pause() -> None:
    """Polite pacing between consecutive requests to the one allowed host."""
    seconds = settings.KODA_OPINIONS_REQUEST_PAUSE_SECONDS
    if seconds > 0:
        time.sleep(seconds)
