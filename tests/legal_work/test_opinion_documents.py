"""Validating, reading and classifying one opinion document.

Three separate questions, deliberately kept separate in the tests as they are in
the code: may these bytes be kept, what do they say, and what kind of document
are they. The middle one is versioned and the last one decides whether a
document can ever become a legal topic's primary resource.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.legal_work.opinion_classification import (
    NEVER_PRIMARY,
    DocumentClassification,
    classify_document,
)
from apps.legal_work.opinion_filenames import (
    WARN_REPAIRED,
    WARN_UNPARSED,
    parse_opinion_filename,
    repair_encoding,
    safe_display_name,
)
from apps.legal_work.opinion_header import (
    WARN_DATE_DISAGREES,
    WARN_RECIPIENT_DISAGREES,
    compare_with_filename,
    parse_document_header,
)
from apps.legal_work.opinion_pdf import (
    ExtractionStatus,
    ValidationStatus,
    extract_text,
    validate_pdf,
)

from .opinion_factory import make_encrypted_pdf, make_pdf, opinion_pdf

# -- validation -------------------------------------------------------------


def test_a_valid_letter_is_accepted():
    result = validate_pdf(opinion_pdf())

    assert result.status == ValidationStatus.VALID
    assert result.page_count == 1
    assert result.is_encrypted is False
    assert result.has_active_content is False


def test_page_count_is_read_from_the_document():
    assert validate_pdf(opinion_pdf(pages=4)).page_count == 4


def test_an_encrypted_document_is_quarantined_rather_than_guessed_open():
    result = validate_pdf(make_encrypted_pdf())

    assert result.status == ValidationStatus.QUARANTINED_ENCRYPTED
    assert result.is_encrypted is True


def test_a_structurally_invalid_document_is_quarantined():
    assert validate_pdf(make_pdf(broken=True)).status == ValidationStatus.QUARANTINED_INVALID


def test_something_that_is_not_a_pdf_is_quarantined():
    assert validate_pdf(b"This is not a PDF").status == ValidationStatus.QUARANTINED_INVALID


def test_an_empty_payload_is_quarantined():
    assert validate_pdf(b"").status == ValidationStatus.QUARANTINED_INVALID


def test_embedded_javascript_is_quarantined():
    result = validate_pdf(make_pdf(with_javascript=True))

    assert result.status == ValidationStatus.QUARANTINED_ACTIVE_CONTENT
    assert result.has_active_content is True


def test_a_launch_action_is_quarantined():
    assert (
        validate_pdf(make_pdf(with_launch_action=True)).status
        == ValidationStatus.QUARANTINED_ACTIVE_CONTENT
    )


def test_an_ordinary_hyperlink_is_recorded_but_never_a_reason_to_reject():
    """Opinion letters cite web pages. That is not active content."""
    result = validate_pdf(make_pdf(with_link_annotation=True))

    assert result.status == ValidationStatus.VALID
    assert "document_contains_link" in result.warnings


def test_a_document_over_the_size_cap_is_quarantined(settings):
    settings.LEGAL_OPINION_MAX_PDF_BYTES = 200

    assert validate_pdf(opinion_pdf()).status == ValidationStatus.QUARANTINED_TOO_LARGE


def test_a_document_over_the_page_cap_is_quarantined(settings):
    settings.LEGAL_OPINION_MAX_PAGES = 2

    assert validate_pdf(opinion_pdf(pages=5)).status == ValidationStatus.QUARANTINED_TOO_MANY_PAGES


def test_a_compressed_stream_that_merely_contains_js_bytes_is_still_valid():
    """The measured false positive that would have destroyed six real letters.

    Six documents in the bootstrap catalogue contain the byte sequence `/JS` or
    `/AA` inside Flate-compressed object streams, with no such name anywhere in
    their object model. The parsed structure decides; a byte scan does not.
    """
    payload = make_pdf([["Arvamus /JS ja /AA tekstina, mitte struktuurina."]])

    assert validate_pdf(payload).status == ValidationStatus.VALID


# -- extraction -------------------------------------------------------------


def test_text_is_extracted_from_a_letter():
    result = extract_text(opinion_pdf())

    assert result.status == ExtractionStatus.EXTRACTED
    assert "Kaubandus" in result.text
    assert result.first_page_text


def test_a_document_with_no_text_needs_ocr_rather_than_failing():
    result = extract_text(make_pdf([[]]))

    assert result.status == ExtractionStatus.NEEDS_OCR
    assert "extraction_no_text" in result.warnings


def test_sparse_text_for_the_page_count_needs_ocr(settings):
    settings.LEGAL_OPINION_MIN_CHARS_PER_PAGE = 10_000

    assert extract_text(opinion_pdf()).status == ExtractionStatus.NEEDS_OCR


def test_extraction_of_an_unreadable_document_fails_cleanly():
    assert extract_text(make_pdf(broken=True)).status == ExtractionStatus.FAILED


def test_stored_text_is_bounded(settings):
    settings.LEGAL_OPINION_TEXT_MAX_LENGTH = 120

    result = extract_text(opinion_pdf(pages=3))

    assert len(result.text) <= 120
    assert "extraction_text_truncated" in result.warnings


def test_extraction_records_which_extractor_read_the_document():
    result = extract_text(opinion_pdf())

    assert result.extractor_name == "pypdf"
    assert result.extractor_version


def test_extraction_is_deterministic():
    payload = opinion_pdf()

    assert extract_text(payload).text == extract_text(payload).text


# -- the letter's own header ------------------------------------------------


def test_the_outgoing_date_and_number_are_read_from_the_header():
    header = parse_document_header(extract_text(opinion_pdf()).first_page_text)

    assert header.date == dt.date(2026, 1, 6)
    assert header.our_reference == "4/1"


def test_the_addressee_is_read_from_the_two_column_block():
    header = parse_document_header(extract_text(opinion_pdf()).first_page_text)

    assert header.recipient == "Rahandusministeerium"


def test_the_incoming_reference_is_read_separately():
    header = parse_document_header(extract_text(opinion_pdf()).first_page_text)

    assert header.their_date == dt.date(2025, 12, 17)
    assert header.their_reference == "1.1-10/4927-5"


def test_the_subject_line_is_read_between_the_block_and_the_salutation():
    header = parse_document_header(extract_text(opinion_pdf()).first_page_text)

    assert "maksukorralduse" in header.subject


def test_an_empty_page_yields_an_empty_header():
    header = parse_document_header("")

    assert header.is_empty
    assert "header_not_found" in header.warnings


def test_a_date_disagreement_is_recorded_rather_than_resolved():
    """Measured on the real catalogue: 269 letters are dated a day after their
    filename, because the name records drafting and the header records sending.
    Neither is wrong, so neither overwrites the other."""
    header = parse_document_header(extract_text(opinion_pdf(our_date="07.01.2026")).first_page_text)

    warnings = compare_with_filename(
        header, filename_date=dt.date(2026, 1, 6), filename_recipient="Rahandusministeerium"
    )

    assert WARN_DATE_DISAGREES in warnings


def test_a_recipient_disagreement_is_recorded():
    header = parse_document_header(extract_text(opinion_pdf()).first_page_text)

    warnings = compare_with_filename(
        header, filename_date=dt.date(2026, 1, 6), filename_recipient="Kliimaministeerium"
    )

    assert WARN_RECIPIENT_DISAGREES in warnings


def test_agreement_produces_no_warning():
    header = parse_document_header(extract_text(opinion_pdf()).first_page_text)

    warnings = compare_with_filename(
        header, filename_date=dt.date(2026, 1, 6), filename_recipient="Rahandusministeerium"
    )

    assert warnings == ()


# -- the filename -----------------------------------------------------------


def test_a_standard_filename_is_parsed():
    parsed = parse_opinion_filename("2026-03-14 - Kliimaministeerium - Arvamus eelnou kohta.pdf")

    assert parsed.date == dt.date(2026, 3, 14)
    assert parsed.recipient == "Kliimaministeerium"
    assert parsed.subject == "Arvamus eelnou kohta"
    assert parsed.is_parsed


def test_a_subject_may_contain_the_separator():
    """43 of the 759 real documents do; splitting on every separator truncates them."""
    parsed = parse_opinion_filename(
        "2026-03-14 - Kliimaministeerium - Liiklusseaduse eelnou - Kooskolastustabel.pdf"
    )

    assert parsed.recipient == "Kliimaministeerium"
    assert parsed.subject == "Liiklusseaduse eelnou - Kooskolastustabel"


def test_a_malformed_filename_is_a_warning_not_a_failure():
    parsed = parse_opinion_filename("mingi suvaline nimi.pdf")

    assert WARN_UNPARSED in parsed.warnings
    assert parsed.display


def test_an_impossible_date_is_a_warning():
    parsed = parse_opinion_filename("2026-13-45 - Ministeerium - Teema.pdf")

    assert parsed.date is None
    assert "filename_date_invalid" in parsed.warnings


def test_partial_double_encoding_is_repaired_generally():
    """One name carries a correct 'õ' and a mojibake 'õ' in different words."""
    damaged = "2023-09-13 - Kliimaministeerium - Liiklusseaduse eelnõu - KooskÃµlastustabel.pdf"

    assert repair_encoding(damaged).endswith("Kooskõlastustabel.pdf")


def test_the_repair_never_touches_a_correctly_encoded_name():
    """Verified against all 759 real filenames: it changes exactly two."""
    good = "2020-01-13 - Siseministeerium - Välismaalaste ja õppetoetuste seadus.pdf"

    assert repair_encoding(good) == good


def test_a_repaired_name_is_flagged():
    parsed = parse_opinion_filename("2023-09-13 - Ministeerium - KooskÃµlastustabel.pdf")

    assert WARN_REPAIRED in parsed.warnings


def test_the_original_filename_is_never_rewritten():
    original = "2026-03-14 - Ministeerium - Teema.pdf"

    assert parse_opinion_filename(original).original == original


def test_a_display_filename_carries_no_path_or_markup_characters():
    display = safe_display_name('2026-01-01 - a/b\\c - <script>"x".pdf')

    for forbidden in ("/", "\\", "<", ">", '"'):
        assert forbidden not in display


def test_a_display_filename_is_bounded():
    assert len(safe_display_name("x" * 400 + ".pdf")) <= 180


# -- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Arvamus maksukorralduse seaduse eelnou kohta", DocumentClassification.OPINION),
        ("Ettepanek riigieelarve kohta", DocumentClassification.OPINION),
        ("Balti kaubanduskodade uhispoordumine", DocumentClassification.JOINT_OPINION),
        ("Taiendav arvamus ELi roheleppe kohta", DocumentClassification.SUPPLEMENTARY_OPINION),
        ("Meeldetuletus seoses riigieelarve poordumisega", DocumentClassification.FOLLOW_UP),
        ("Liiklusseaduse eelnou - Lisa 1", DocumentClassification.ANNEX),
        ("Liiklusseaduse eelnou - Seletuskiri", DocumentClassification.SUPPORTING_DOCUMENT),
        ("Liiklusseaduse eelnou - Kooskolastustabel", DocumentClassification.SUPPORTING_DOCUMENT),
        ("Midagi taiesti muud", DocumentClassification.UNKNOWN),
    ],
)
def test_a_document_is_classified_from_its_words(subject, expected):
    classification, _ = classify_document(filename_subject=subject)

    assert classification == expected


def test_lisaks_is_not_an_annex():
    """`lisa` inflects; `lisaks` means "in addition" and is not a document type."""
    classification, _ = classify_document(filename_subject="Lisaks esitame arvamuse eelnou kohta")

    assert classification == DocumentClassification.OPINION


def test_a_letter_that_merely_discusses_an_explanatory_memorandum_is_still_an_opinion():
    """The measured false positive: reading `seletuskiri` from the page body
    demoted genuine opinions to never-primary, so only the filename decides."""
    classification, _ = classify_document(
        filename_subject="Arvamus liiklusseaduse eelnou kohta",
        first_page_text="Eelnou seletuskiri ei selgita piisavalt kooskolastustabeli sisu.",
    )

    assert classification == DocumentClassification.OPINION


def test_the_never_primary_set_is_exactly_the_three_that_cannot_lead():
    assert NEVER_PRIMARY == {
        DocumentClassification.ANNEX,
        DocumentClassification.SUPPORTING_DOCUMENT,
        DocumentClassification.UNKNOWN,
    }


def test_classification_reports_the_signal_that_decided_it():
    _, signals = classify_document(filename_subject="Liiklusseaduse eelnou - Lisa 1")

    assert signals == ["annex-vocabulary"]
