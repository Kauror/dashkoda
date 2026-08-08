"""What the opinion matcher will and will not conclude.

The calibration under test is measured rather than chosen. Across the 759
documents in the Chamber's handover, 369 letters carry exactly their filename's
date and **269 carry that date plus one day** — the filename records drafting
and the letter's own `Meie <date>` records sending, usually the next working
day. A matcher treating a one-day gap as a contradiction would reject a third of
the catalogue, so agreement is generous and only a gap of months can block.

No test here special-cases a record, a filename or a document.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.legal_work.opinion_classification import DocumentClassification
from apps.legal_work.opinion_matching import (
    CONTRADICTION_BEFORE_REQUEST,
    CONTRADICTION_DATE,
    CONTRADICTION_GENERIC_ONLY,
    CONTRADICTION_IDENTIFIER,
    CONTRADICTION_NOT_PRIMARY,
    CONTRADICTION_UNREADABLE,
    DATE_BLOCK_DAYS,
    EVIDENCE_DATE_EXACT,
    EVIDENCE_RECIPIENT,
    MATCHER_VERSION,
    Candidate,
    build_rarity,
    date_agreement,
    score_candidate,
)

SENT = dt.date(2026, 3, 10)
RECEIVED = dt.date(2026, 2, 1)
TOPIC = "Maksukorralduse seaduse muutmise seaduse eelnõu 130 SE"


def candidate(
    *,
    subject="Arvamus maksukorralduse seaduse muutmise eelnõu 130 SE kohta",
    filename_date=SENT,
    detected_date=None,
    recipient="Rahandusministeerium",
    classification=DocumentClassification.OPINION,
    text=None,
    readable=True,
) -> Candidate:
    return Candidate(
        blob_id=1,
        entry_id=1,
        classification=classification,
        filename_date=filename_date,
        detected_date=detected_date if detected_date is not None else filename_date,
        filename_subject=subject,
        detected_subject=subject,
        recipient=recipient,
        text=text if text is not None else f"Kaubanduskoda esitab arvamuse. {subject}",
        first_page_text=subject,
        is_readable=readable,
    )


def score(
    c: Candidate, *, topic=TOPIC, sent=SENT, received=RECEIVED, recipient="Rahandusministeerium"
):
    return score_candidate(
        topic=topic,
        sent_date=sent,
        received_date=received,
        recipient=recipient,
        candidate=c,
        rarity=build_rarity([c.filename_subject, "muu teema", "kolmas teema"]),
    )


# -- the version ------------------------------------------------------------


def test_the_matcher_names_its_normaliser_and_extractor():
    """A stored decision must be able to say what produced it.

    1.2 is the dual-source candidate universe; the weights and thresholds are
    1.1's, unchanged, and the tests below still assert them.
    """
    assert MATCHER_VERSION.startswith("opinion-1.2-norm")
    assert "-extract" in MATCHER_VERSION


def test_the_matcher_version_is_not_a_consultation_version():
    from apps.legal_work.archived_topic_matching import ARCHIVE_MATCHER_VERSION
    from apps.legal_work.current_topic_matching import MATCHER_VERSION as CURRENT

    assert MATCHER_VERSION not in {CURRENT, ARCHIVE_MATCHER_VERSION}


# -- dates ------------------------------------------------------------------


@pytest.mark.parametrize("offset", [0, 1, -1, 2, 3, -3])
def test_a_small_date_gap_is_full_agreement(offset):
    """369 letters agree exactly and 269 are one day later. Both are normal."""
    credit, gap, evidence = date_agreement(
        SENT, candidate(filename_date=SENT + dt.timedelta(days=offset))
    )

    assert credit == 1.0
    assert gap == abs(offset)
    assert evidence == EVIDENCE_DATE_EXACT


def test_the_one_day_drift_is_never_a_contradiction():
    result = score(candidate(filename_date=SENT, detected_date=SENT + dt.timedelta(days=1)))

    assert not result.blocked
    assert EVIDENCE_DATE_EXACT in result.evidence


def test_the_closer_of_the_two_document_dates_is_used():
    """Insisting on one of them would be a coin toss about which was recorded."""
    credit, gap, _ = date_agreement(
        SENT,
        candidate(filename_date=SENT + dt.timedelta(days=40), detected_date=SENT),
    )

    assert credit == 1.0
    assert gap == 0


@pytest.mark.parametrize("days", [7, 14, 29])
def test_a_moderate_gap_decays_rather_than_blocking(days):
    result = score(candidate(filename_date=SENT + dt.timedelta(days=days)))

    assert not result.blocked


def test_a_gap_of_months_blocks():
    result = score(candidate(filename_date=SENT + dt.timedelta(days=DATE_BLOCK_DAYS + 1)))

    assert CONTRADICTION_DATE in result.contradictions


def test_a_late_supplementary_opinion_is_allowed_its_gap():
    """A supplement legitimately arrives after the letter it supplements."""
    result = score(
        candidate(
            filename_date=SENT + dt.timedelta(days=DATE_BLOCK_DAYS + 30),
            classification=DocumentClassification.SUPPLEMENTARY_OPINION,
        )
    )

    assert CONTRADICTION_DATE not in result.contradictions


def test_a_document_cannot_predate_the_request_it_answers():
    result = score(candidate(filename_date=RECEIVED - dt.timedelta(days=5)))

    assert CONTRADICTION_BEFORE_REQUEST in result.contradictions


def test_a_document_with_no_date_at_all_is_not_blocked_on_chronology():
    result = score(candidate(filename_date=None, detected_date=None))

    assert CONTRADICTION_DATE not in result.contradictions


# -- the instrument ---------------------------------------------------------


def test_a_conflicting_identifier_blocks():
    result = score(candidate(subject="Arvamus liiklusseaduse eelnõu 999 SE kohta"))

    assert CONTRADICTION_IDENTIFIER in result.contradictions


def test_only_generic_vocabulary_is_refused():
    """A date and a ministry are not evidence of the same business."""
    result = score(
        candidate(
            subject="Arvamus eelnõu kohta",
            text="Kaubanduskoda esitab arvamuse.",
        ),
        topic="Täiesti muu valdkonna seadus",
    )

    assert CONTRADICTION_GENERIC_ONLY in result.contradictions


def test_a_date_alone_does_not_match():
    result = score(candidate(subject="Midagi muud", text="Midagi muud"), topic="Sootuks teine asi")

    assert result.blocked or result.score < Decimal("45.00")


# -- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    "classification",
    [
        DocumentClassification.ANNEX,
        DocumentClassification.SUPPORTING_DOCUMENT,
        DocumentClassification.UNKNOWN,
    ],
)
def test_a_document_that_cannot_lead_is_barred_from_being_primary(classification):
    result = score(candidate(classification=classification))

    assert CONTRADICTION_NOT_PRIMARY in result.primary_bars
    assert result.can_be_primary is False


@pytest.mark.parametrize(
    "classification",
    [DocumentClassification.ANNEX, DocumentClassification.SUPPORTING_DOCUMENT],
)
def test_a_document_that_cannot_lead_is_still_usable_as_a_companion(classification):
    """The bar on leading is not a block on being considered.

    Collapsing the two meant an annex was discarded before grouping ever saw
    it, so an annex could never be attached to the letter it belongs with —
    which is the one thing the product asks annexes to do.
    """
    result = score(candidate(classification=classification))

    assert result.blocked is False
    assert result.score > Decimal("0.00")


def test_a_genuinely_unusable_document_is_blocked_not_merely_barred():
    result = score(candidate(readable=False))

    assert result.blocked is True
    assert result.can_be_primary is False


def test_a_joint_opinion_may_be_primary():
    result = score(candidate(classification=DocumentClassification.JOINT_OPINION))

    assert CONTRADICTION_NOT_PRIMARY not in result.contradictions


# -- readability ------------------------------------------------------------


def test_an_unreadable_document_is_blocked():
    result = score(candidate(readable=False))

    assert CONTRADICTION_UNREADABLE in result.contradictions


# -- the recipient ----------------------------------------------------------


def test_a_matching_recipient_is_positive_evidence():
    assert EVIDENCE_RECIPIENT in score(candidate()).evidence


def test_a_recipient_mismatch_alone_does_not_block():
    """The addressee of a letter and the body that owns the draft differ often."""
    result = score(candidate(recipient="Kliimaministeerium"))

    assert not result.blocked
    assert EVIDENCE_RECIPIENT not in result.evidence


def test_a_strong_instrument_match_survives_a_recipient_mismatch():
    result = score(candidate(recipient="Kliimaministeerium"))

    assert result.score > Decimal("40.00")


# -- scoring shape ----------------------------------------------------------


def test_a_score_never_leaves_the_scale():
    result = score(candidate())

    assert Decimal("0.00") <= result.score <= Decimal("100.00")


def test_a_blocked_candidate_scores_nothing():
    assert score(candidate(readable=False)).score == Decimal("0.00")


def test_scoring_is_deterministic():
    assert score(candidate()).score == score(candidate()).score


def test_a_strong_candidate_outscores_a_weak_one():
    strong = score(candidate())
    weak = score(candidate(subject="Arvamus mingi muu teema kohta", recipient="Kliimaministeerium"))

    assert strong.score > weak.score
