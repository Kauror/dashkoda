"""Deciding which opinion document answered which legal record.

A separate matcher from either consultation matcher, with its own weights,
thresholds and rarity corpus. Sharing them would be wrong in both directions: a
consultation page is an editorial invitation written by Koda.ee, while an
opinion letter is formal correspondence written by the Chamber and carries
structured evidence — an outgoing date, an outgoing number, an addressee, a
subject line — that no consultation page has.

**Dates carry this matcher.** That is the deliberate difference from the
consultation matchers, where dates are a supporting signal. Here the workbook
records when an opinion was sent and the document records when it was written,
so agreement between them is close to decisive and disagreement beyond a
plausible window is disqualifying.

The tolerance is measured, not guessed. Across the 759-document bootstrap
catalogue, **369 letters carry exactly their filename's date and 269 carry
that date plus one day** — the filename records drafting and the letter's own
`Meie <date>` records sending, usually the next working day. A matcher treating
a one-day gap as a contradiction would reject a third of the catalogue. So
agreement is generous (±3 days at full credit) and only a gap of months is
allowed to block, with an explicit exemption for the classifications that
legitimately arrive later.

No record, filename or document is special-cased anywhere in this module.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from decimal import Decimal

from .opinion_classification import NEVER_PRIMARY, DocumentClassification
from .opinion_pdf import EXTRACTOR_VERSION
from .text_normalisation import (
    NORMALISER_VERSION,
    acronyms,
    character_ngrams,
    fold,
    identifiers,
    significant_tokens,
)

MATCHER_VERSION = f"opinion-1.1-norm{NORMALISER_VERSION}-extract{EXTRACTOR_VERSION}"

# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

# Full credit. Covers the measured one-day drafting/sending drift with room to
# spare, and a weekend either side of it.
DATE_EXACT_DAYS = 3
# Credit decays to nothing across this window. A letter sent a fortnight after
# the workbook's date is still plausibly the same business.
DATE_DECAY_DAYS = 30
# Neither credit nor contradiction. The Chamber does revisit matters.
DATE_NEUTRAL_DAYS = 90
# Beyond this a document is a different piece of business — unless its own
# classification says it is a later addition to an earlier one.
DATE_BLOCK_DAYS = 90
LATE_DOCUMENT_CLASSES = frozenset(
    {
        DocumentClassification.SUPPLEMENTARY_OPINION,
        DocumentClassification.FOLLOW_UP,
    }
)

# --------------------------------------------------------------------------
# Weights — renormalised over the signals that actually apply
# --------------------------------------------------------------------------

WEIGHTS: dict[str, float] = {
    "date": 0.34,
    "subject": 0.26,
    "instrument": 0.18,
    "rarity": 0.12,
    "recipient": 0.10,
}

THRESHOLD_MATCH = Decimal("70.00")
THRESHOLD_AMBIGUOUS = Decimal("45.00")
THRESHOLD_PLAUSIBLE = Decimal("20.00")
MIN_MARGIN = Decimal("12.00")

GENERIC_TOKEN_DAMPING = 0.2

EVIDENCE_DATE_EXACT = "date-exact"
EVIDENCE_DATE_NEAR = "date-near"
EVIDENCE_SUBJECT_STRONG = "subject-strong"
EVIDENCE_INSTRUMENT = "instrument-match"
EVIDENCE_IDENTIFIER = "identifier-match"
EVIDENCE_ACRONYM = "acronym-match"
EVIDENCE_RECIPIENT = "recipient-match"
EVIDENCE_RARITY = "rarity-strong"
EVIDENCE_BODY = "body-confirms"

CONTRADICTION_IDENTIFIER = "identifier-conflict"
CONTRADICTION_DATE = "date-impossible"
CONTRADICTION_BEFORE_REQUEST = "document-precedes-request"
CONTRADICTION_GENERIC_ONLY = "generic-vocabulary-only"
CONTRADICTION_NOT_PRIMARY = "classification-cannot-lead"
CONTRADICTION_UNREADABLE = "document-not-readable"
# Another matter claimed the same letter with materially better date agreement.
CONTRADICTION_COMPETING_CLAIM = "competing-primary-claim"


@dataclass(frozen=True)
class Candidate:
    """One catalogue entry, reduced to what the matcher weighs."""

    entry_id: int
    classification: str
    filename_date: dt.date | None
    detected_date: dt.date | None
    filename_subject: str
    detected_subject: str
    recipient: str
    text: str
    first_page_text: str
    is_readable: bool


@dataclass
class Scored:
    candidate: Candidate
    score: Decimal
    evidence: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    primary_bars: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """Cannot be used at all — not as a primary, not as a companion."""
        return bool(self.contradictions)

    @property
    def can_be_primary(self) -> bool:
        """Whether this document may *lead* a legal topic.

        Deliberately separate from `blocked`. An annex cannot be the document a
        topic links to, but it is exactly the kind of thing that belongs
        *alongside* one — so it still has to be scored, or grouping could never
        find it. Collapsing the two is how annexes silently disappear.
        """
        return not self.blocked and not self.primary_bars


def date_agreement(sent: dt.date, candidate: Candidate) -> tuple[float, int | None, str | None]:
    """How well the document's dates agree with the workbook's sent date.

    Uses whichever of the two document dates is closer. They routinely differ by
    a day, and insisting on one of them would be a coin toss about which the
    Chamber happened to record.
    """
    gaps = [
        abs((value - sent).days)
        for value in (candidate.filename_date, candidate.detected_date)
        if value is not None
    ]
    if not gaps:
        return 0.0, None, None

    gap = min(gaps)
    if gap <= DATE_EXACT_DAYS:
        return 1.0, gap, EVIDENCE_DATE_EXACT
    if gap <= DATE_DECAY_DAYS:
        span = DATE_DECAY_DAYS - DATE_EXACT_DAYS
        return 1.0 - (gap - DATE_EXACT_DAYS) / span, gap, EVIDENCE_DATE_NEAR
    return 0.0, gap, None


def _dice(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def _containment(needle: frozenset[str], haystack: frozenset[str]) -> float:
    if not needle:
        return 0.0
    return len(needle & haystack) / len(needle)


def score_candidate(
    *,
    topic: str,
    sent_date: dt.date,
    received_date: dt.date | None,
    recipient: str,
    candidate: Candidate,
    rarity: dict[str, float],
) -> Scored:
    """Weigh one document against one legal record."""
    result = Scored(candidate=candidate, score=Decimal("0.00"))

    if not candidate.is_readable:
        result.contradictions.append(CONTRADICTION_UNREADABLE)
        return result
    if candidate.classification in NEVER_PRIMARY:
        # Recorded as a bar on leading, not as a block on being considered:
        # this document may still be grouped with whichever letter does lead.
        result.primary_bars.append(CONTRADICTION_NOT_PRIMARY)

    # -- dates ------------------------------------------------------------
    date_credit, gap, date_evidence = date_agreement(sent_date, candidate)
    if date_evidence:
        result.evidence.append(date_evidence)

    if gap is not None and gap > DATE_BLOCK_DAYS:
        if candidate.classification not in LATE_DOCUMENT_CLASSES:
            result.contradictions.append(CONTRADICTION_DATE)
            return result

    earliest = min(
        (d for d in (candidate.filename_date, candidate.detected_date) if d is not None),
        default=None,
    )
    if received_date and earliest and earliest < received_date:
        # An opinion cannot predate the request it answers.
        result.contradictions.append(CONTRADICTION_BEFORE_REQUEST)
        return result

    # -- subject ------------------------------------------------------------
    topic_tokens = significant_tokens(topic)
    subject_text = f"{candidate.filename_subject} {candidate.detected_subject}"
    subject_tokens = significant_tokens(subject_text)

    token_overlap = _dice(topic_tokens, subject_tokens)
    ngram_overlap = _dice(character_ngrams(fold(topic)), character_ngrams(fold(subject_text)))
    body_containment = _containment(topic_tokens, significant_tokens(candidate.text))

    subject_credit = max(token_overlap, ngram_overlap)
    if body_containment > 0.6:
        subject_credit = max(subject_credit, body_containment * 0.9)
        result.evidence.append(EVIDENCE_BODY)
    if subject_credit > 0.55:
        result.evidence.append(EVIDENCE_SUBJECT_STRONG)

    # -- the instrument itself ---------------------------------------------
    topic_ids = identifiers(topic)
    candidate_ids = identifiers(f"{subject_text} {candidate.first_page_text}")
    instrument_credit = 0.0
    if topic_ids and candidate_ids:
        if topic_ids & candidate_ids:
            instrument_credit = 1.0
            result.evidence.append(EVIDENCE_IDENTIFIER)
        else:
            result.contradictions.append(CONTRADICTION_IDENTIFIER)
            return result

    topic_acronyms = acronyms(topic)
    if topic_acronyms and topic_acronyms & acronyms(subject_text + " " + candidate.first_page_text):
        instrument_credit = max(instrument_credit, 0.8)
        result.evidence.append(EVIDENCE_ACRONYM)

    # -- rarity -------------------------------------------------------------
    shared = topic_tokens & subject_tokens
    rarity_credit = 0.0
    if shared:
        weight = sum(rarity.get(token, 1.0) for token in shared)
        total = sum(rarity.get(token, 1.0) for token in topic_tokens) or 1.0
        rarity_credit = min(weight / total, 1.0)
        if rarity_credit > 0.5:
            result.evidence.append(EVIDENCE_RARITY)

    # -- recipient ----------------------------------------------------------
    recipient_credit = 0.0
    if recipient and candidate.recipient:
        left, right = fold(recipient), fold(candidate.recipient)
        if left and right and (left in right or right in left):
            recipient_credit = 1.0
            result.evidence.append(EVIDENCE_RECIPIENT)
    # A recipient mismatch alone is never blocking: the addressee of a letter
    # and the institution that owns the draft are routinely different bodies.

    # -- nothing but generic words -----------------------------------------
    if not shared and instrument_credit == 0.0 and subject_credit < 0.35:
        result.contradictions.append(CONTRADICTION_GENERIC_ONLY)
        return result

    # -- renormalise over applicable signals --------------------------------
    parts = {
        "date": (date_credit, bool(candidate.filename_date or candidate.detected_date)),
        "subject": (subject_credit, True),
        "instrument": (instrument_credit, bool(topic_ids or topic_acronyms)),
        "rarity": (rarity_credit, True),
        "recipient": (recipient_credit, bool(recipient and candidate.recipient)),
    }
    applicable = sum(WEIGHTS[name] for name, (_, ok) in parts.items() if ok) or 1.0
    total = sum(WEIGHTS[name] * value for name, (value, ok) in parts.items() if ok)

    result.score = Decimal(f"{100 * total / applicable:.2f}")
    return result


def build_rarity(corpus: list[str]) -> dict[str, float]:
    """Inverse document frequency over the opinion catalogue alone.

    Never shared with a consultation corpus: a word that is rare among a decade
    of consultations is unremarkable among the Chamber's own letters, which are
    all about legislation and all written by the same office.
    """
    if not corpus:
        return {}
    counts: dict[str, int] = {}
    for document in corpus:
        for token in significant_tokens(document):
            counts[token] = counts.get(token, 0) + 1
    size = len(corpus)
    return {
        token: math.log(size / count) / math.log(size) if size > 1 else 1.0
        for token, count in counts.items()
    }
