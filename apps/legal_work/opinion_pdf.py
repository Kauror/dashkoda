"""Validating and reading Chamber opinion PDFs, without ever running one.

A PDF is a container format that can carry JavaScript, an action that launches a
program, an action that opens a URL, and whole embedded files. None of that is
needed to read an opinion letter, so this module treats every one of them as a
reason to quarantine rather than something to handle. Nothing here executes,
follows or fetches anything a document asks for; the document is bytes, and the
only questions asked of it are structural.

`pypdf` does the parsing. It was chosen over the alternatives because the
application image has no PDF tooling at all — no Poppler, no library — so
something had to be added, and pypdf is pure Python (no system packages, no
Dockerfile change), permissively licensed, and covers all five things needed:
structure validation, page count, encryption detection, text extraction and
object-model inspection for active content. PyMuPDF is a large AGPL C extension;
pdfminer.six cannot inspect actions; Poppler would mean image changes and
parsing subprocess output.

Extraction is versioned. `EXTRACTOR_VERSION` is recorded on every result and
changing it invalidates stored extractions, which is what lets the text layer
improve without any risk of a stale mixture.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from django.conf import settings
from django.db import models
from pypdf import PdfReader
from pypdf.errors import PdfReadError

EXTRACTOR_NAME = "pypdf"
EXTRACTOR_VERSION = "1.0"

PDF_SIGNATURES = (b"%PDF-1.", b"%PDF-2.")

# Long, distinctive names used only as a backstop for documents whose object
# model could not be inspected. Deliberately **not** the primary rule and
# deliberately not `/JS`: measured against the 759-document bootstrap catalogue,
# a raw byte scan for `/JS` matches four documents and `/AA` two more, every one
# of them a coincidental byte sequence inside a Flate-compressed object stream
# rather than a real name. Quarantining on that would have destroyed six valid
# opinion letters. The parsed object model is authoritative; these tokens are
# long enough that a stream collision is implausible, and the corpus contains
# none of them.
BACKSTOP_TOKENS = (b"/JavaScript", b"/Launch", b"/EmbeddedFile", b"/RichMedia")

# Action types that make an annotation or a page do something when opened.
EXECUTABLE_ACTIONS = frozenset({"/JavaScript", "/Launch", "/ImportData", "/SubmitForm", "/GoToR"})

HYPHEN_BREAK = re.compile(r"(\w)[-‐‑]\n(\w)")
MULTI_NEWLINE = re.compile(r"\n{3,}")
TRAILING_SPACE = re.compile(r"[ \t]+\n")


class ValidationStatus(models.TextChoices):
    """Whether a document may be stored and read, and why not when it may not.

    `TextChoices` rather than a plain enum so the one definition serves both the
    validator and the admin column, with no second list to drift.
    """

    VALID = "valid", "Korras"
    QUARANTINED_ENCRYPTED = "quarantined_encrypted", "Karantiin: krüpteeritud"
    QUARANTINED_ACTIVE_CONTENT = "quarantined_active_content", "Karantiin: aktiivne sisu"
    QUARANTINED_INVALID = "quarantined_invalid", "Karantiin: vigane fail"
    QUARANTINED_TOO_LARGE = "quarantined_too_large", "Karantiin: liiga suur"
    QUARANTINED_TOO_MANY_PAGES = "quarantined_too_many_pages", "Karantiin: liiga palju lehti"


class ExtractionStatus(models.TextChoices):
    EXTRACTED = "extracted", "Loetud"
    NEEDS_OCR = "needs_ocr", "Vajab OCR-i"
    FAILED = "failed", "Ebaõnnestus"


WARN_NO_TEXT = "extraction_no_text"
WARN_SPARSE_TEXT = "extraction_sparse_text"
WARN_REPLACEMENT_HEAVY = "extraction_replacement_characters"
WARN_TRUNCATED = "extraction_text_truncated"
WARN_HAS_URI = "document_contains_link"
WARN_OPEN_ACTION = "document_contains_open_action"


@dataclass(frozen=True)
class ValidationResult:
    status: ValidationStatus
    byte_size: int
    page_count: int = 0
    is_encrypted: bool = False
    has_active_content: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return self.status == ValidationStatus.VALID


def validate_pdf(payload: bytes) -> ValidationResult:
    """Decide whether these bytes are a document DashKoda will keep and read."""
    size = len(payload)
    if size > settings.LEGAL_OPINION_MAX_PDF_BYTES:
        return ValidationResult(status=ValidationStatus.QUARANTINED_TOO_LARGE, byte_size=size)

    if not any(payload.startswith(signature) for signature in PDF_SIGNATURES):
        return ValidationResult(status=ValidationStatus.QUARANTINED_INVALID, byte_size=size)

    warnings: list[str] = []
    try:
        reader = PdfReader(_reader_source(payload), strict=False)
    except PdfReadError, ValueError, OSError, RecursionError:
        return ValidationResult(status=ValidationStatus.QUARANTINED_INVALID, byte_size=size)

    # An encrypted document is refused rather than opened with a guessed empty
    # password: DashKoda has no key management, and a document it cannot read is
    # a document it must not claim to have read.
    if getattr(reader, "is_encrypted", False):
        return ValidationResult(
            status=ValidationStatus.QUARANTINED_ENCRYPTED,
            byte_size=size,
            is_encrypted=True,
        )

    try:
        page_count = len(reader.pages)
    except PdfReadError, ValueError, OSError, RecursionError:
        return ValidationResult(status=ValidationStatus.QUARANTINED_INVALID, byte_size=size)

    if page_count <= 0:
        return ValidationResult(status=ValidationStatus.QUARANTINED_INVALID, byte_size=size)
    if page_count > settings.LEGAL_OPINION_MAX_PAGES:
        return ValidationResult(
            status=ValidationStatus.QUARANTINED_TOO_MANY_PAGES,
            byte_size=size,
            page_count=page_count,
        )

    active, structural_warnings = _inspect_active_content(reader, payload)
    warnings.extend(structural_warnings)
    if active:
        return ValidationResult(
            status=ValidationStatus.QUARANTINED_ACTIVE_CONTENT,
            byte_size=size,
            page_count=page_count,
            has_active_content=True,
            warnings=tuple(warnings),
        )

    return ValidationResult(
        status=ValidationStatus.VALID,
        byte_size=size,
        page_count=page_count,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class ExtractionResult:
    status: ExtractionStatus
    text: str = ""
    first_page_text: str = ""
    page_count: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    extractor_name: str = EXTRACTOR_NAME
    extractor_version: str = EXTRACTOR_VERSION

    @property
    def is_usable(self) -> bool:
        return self.status == ExtractionStatus.EXTRACTED


def extract_text(payload: bytes) -> ExtractionResult:
    """Read a validated document's text. Deterministic, and never OCR.

    The first page is kept separately because an opinion letter's header — date,
    addressee, subject line, outgoing number — is the densest evidence in the
    document and matching should not have to find it inside forty thousand
    characters of body.
    """
    try:
        reader = PdfReader(_reader_source(payload), strict=False)
        page_count = len(reader.pages)
    except PdfReadError, ValueError, OSError, RecursionError:
        return ExtractionResult(status=ExtractionStatus.FAILED)

    warnings: list[str] = []
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except PdfReadError, ValueError, KeyError, TypeError, RecursionError:
            # One unreadable page does not fail the document; the rest of the
            # letter is still evidence.
            pages.append("")

    first_page = normalise_document_text(pages[0]) if pages else ""
    whole = normalise_document_text("\n".join(pages))

    if not whole.strip():
        return ExtractionResult(
            status=ExtractionStatus.NEEDS_OCR,
            page_count=page_count,
            warnings=(WARN_NO_TEXT,),
        )

    replacement_ratio = whole.count("�") / max(len(whole), 1)
    if replacement_ratio > settings.LEGAL_OPINION_MAX_REPLACEMENT_RATIO:
        return ExtractionResult(
            status=ExtractionStatus.NEEDS_OCR,
            page_count=page_count,
            warnings=(WARN_REPLACEMENT_HEAVY,),
        )

    if len(whole) / max(page_count, 1) < settings.LEGAL_OPINION_MIN_CHARS_PER_PAGE:
        return ExtractionResult(
            status=ExtractionStatus.NEEDS_OCR,
            page_count=page_count,
            warnings=(WARN_SPARSE_TEXT,),
        )

    if len(whole) > settings.LEGAL_OPINION_TEXT_MAX_LENGTH:
        whole = whole[: settings.LEGAL_OPINION_TEXT_MAX_LENGTH]
        warnings.append(WARN_TRUNCATED)

    return ExtractionResult(
        status=ExtractionStatus.EXTRACTED,
        text=whole,
        first_page_text=first_page[: settings.LEGAL_OPINION_FIRST_PAGE_MAX_LENGTH],
        page_count=page_count,
        warnings=tuple(warnings),
    )


def normalise_document_text(raw: str) -> str:
    """Make extracted text comparable without rewriting what it says.

    Only layout artefacts are touched: the encoding form, line-break hyphenation,
    trailing spaces and runs of blank lines. No word is substituted and no
    sentence is reordered — this text is legal correspondence and later becomes
    matching evidence, so it has to stay the document's own words.
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFC", raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ").replace("​", "")
    text = HYPHEN_BREAK.sub(r"\1\2", text)
    text = TRAILING_SPACE.sub("\n", text)
    text = MULTI_NEWLINE.sub("\n\n", text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(lines).strip()


def _reader_source(payload: bytes):
    import io

    return io.BytesIO(payload)


def _inspect_active_content(reader: PdfReader, payload: bytes) -> tuple[bool, list[str]]:
    """Look for anything executable. Never follows, opens or runs what it finds.

    The parsed object model decides. pypdf resolves object streams while
    parsing, so a name hidden inside one is still visible here — which is why
    this does not need, and must not use, a raw scan for short names.
    """
    warnings: list[str] = []
    inspected = False

    try:
        root = reader.trailer.get("/Root", {})
        if hasattr(root, "get"):
            inspected = True
            names = root.get("/Names", {})
            if hasattr(names, "get"):
                if names.get("/JavaScript") is not None:
                    return True, warnings
                if names.get("/EmbeddedFiles") is not None:
                    return True, warnings

            if _is_executable_action(root.get("/OpenAction")):
                return True, warnings
            if root.get("/OpenAction") is not None:
                # Often only a zoom instruction. Recorded, not rejected.
                warnings.append(WARN_OPEN_ACTION)

            if _is_executable_action(root.get("/AA")):
                return True, warnings

        for page in reader.pages:
            inspected = True
            if _is_executable_action(page.get("/AA")):
                return True, warnings
            for annotation in page.get("/Annots", []) or []:
                try:
                    resolved = annotation.get_object()
                except PdfReadError, ValueError, KeyError, AttributeError, OSError:
                    continue
                if not hasattr(resolved, "get"):
                    continue
                if _is_executable_action(resolved.get("/A")):
                    return True, warnings
                if _is_executable_action(resolved.get("/AA")):
                    return True, warnings
                subtype = resolved.get("/Subtype")
                if str(subtype) in {"/FileAttachment", "/Movie", "/Screen", "/RichMedia"}:
                    return True, warnings
                action = resolved.get("/A")
                if hasattr(action, "get") and action.get("/URI") is not None:
                    warnings.append(WARN_HAS_URI)
    except PdfReadError, ValueError, KeyError, TypeError, OSError, RecursionError:
        # Structure that cannot be walked is structure that cannot be cleared.
        inspected = False

    if not inspected and any(token in payload for token in BACKSTOP_TOKENS):
        return True, warnings

    return False, sorted(set(warnings))


def _is_executable_action(action) -> bool:
    """True when a PDF action object would make the viewer *do* something."""
    if action is None:
        return False
    try:
        resolved = action.get_object() if hasattr(action, "get_object") else action
    except PdfReadError, ValueError, KeyError, AttributeError, OSError:
        return True  # unreadable action: refuse rather than assume it is inert
    if not hasattr(resolved, "get"):
        return False
    if str(resolved.get("/S")) in EXECUTABLE_ACTIONS:
        return True
    if resolved.get("/JS") is not None:
        return True
    nested = resolved.get("/Next")
    if nested is not None:
        return _is_executable_action(nested)
    return False
