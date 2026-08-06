"""Matching legal records against the **archived** consultation catalogue.

A separate matcher from the current one, with its own corpus, its own weights,
its own thresholds and its own version. That separation is the point of this
module, so it is worth saying why rather than leaving it to be rediscovered.

**The base rate is an order of magnitude harsher.** The current matcher chooses
among seven live consultations; this one chooses among every consultation the
Chamber has been asked about inside the hydration window — currently about a
hundred and seventy, and growing every time the window widens. The chance that
some entry *happens* to share vocabulary with a legal record rises with the size
of the field, so the same score means much less here.

**The corpus is different, so rarity is different.** `pakendiseadus` appears in
one of seven current consultations and is therefore strong evidence there. In an
archive spanning a year of packaging legislation it may appear in five, and
weighting it identically would manufacture confident nonsense. The idf table
below is computed over the **archive** entries only; the two matchers never
share a corpus.

**Nothing here is calibrated by copying.** The current matcher's 62/38/12 was
tuned against a seven-document field and is not evidence about this one. The
thresholds below start deliberately higher, and the margin requirement is
stricter, because the cost of being wrong is identical — a lawyer sent to the
wrong consultation — while the opportunity to be wrong is far greater.

What *is* shared is the transparent scoring machinery: the same normaliser, the
same n-gram and rarity primitives, the same evidence vocabulary. Those are
mechanics, not calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .current_topic_matching import (
    EVIDENCE_ACRONYM_MATCH,
    EVIDENCE_BLOCKED_CANDIDATES,
    EVIDENCE_DEADLINE_CONFLICT,
    EVIDENCE_GENERIC_ONLY,
    EVIDENCE_IDENTIFIER_CONFLICT,
    EVIDENCE_IDENTIFIER_MATCH,
    EVIDENCE_IMPOSSIBLE_CHRONOLOGY,
    EVIDENCE_NARROW_MARGIN,
    EVIDENCE_NGRAM_STRONG,
    EVIDENCE_NO_CANDIDATES,
    EVIDENCE_NO_PLAUSIBLE,
    EVIDENCE_ORGANIZATION_CONFLICT,
    EVIDENCE_ORGANIZATION_MATCH,
    EVIDENCE_RARITY_STRONG,
    EVIDENCE_UNIQUE_TOKEN,
    IMPOSSIBLE_LEAD_DAYS,
    NGRAM_SIZE,
    CandidateProfile,
    MatchOutcome,
    ScoredCandidate,
    build_legal_profile,
    containment,
    deadline_signal,
    dice_similarity,
    inverse_document_frequency,
    organization_key,
    organization_signal,
    unique_token_score,
)
from .models import MatchDecision
from .text_normalisation import (
    GENERIC_TOKENS,
    NORMALISER_VERSION,
    acronyms,
    character_ngrams,
    identifiers,
    significant_tokens,
    tokenize,
)

# Its own version string, never the current matcher's. An archive snapshot's
# scores must never be comparable-by-accident with a current one's.
ARCHIVE_MATCHER_VERSION = f"archive-1.0-norm{NORMALISER_VERSION}"

# ---------------------------------------------------------------------------
# Weights. Independently named, and deliberately not the current matcher's.
#
# The shift is towards evidence that survives a large field. Rarity coverage
# over the archive corpus and uncommon-token overlap both get *more* weight than
# in the current matcher, because they are the signals that discriminate when
# there are two hundred candidates rather than seven. The organisation signal
# gets more too: across a decade of consultations, agreeing on the ministry
# genuinely narrows the field.
# ---------------------------------------------------------------------------

ARCHIVE_WEIGHT_CHARACTER_NGRAM = 0.26
ARCHIVE_WEIGHT_RARITY_COVERAGE = 0.32
ARCHIVE_WEIGHT_UNIQUE_TOKEN = 0.24
ARCHIVE_WEIGHT_DEADLINE = 0.08
ARCHIVE_WEIGHT_ORGANIZATION = 0.10

ARCHIVE_ALWAYS_APPLICABLE_WEIGHT = (
    ARCHIVE_WEIGHT_CHARACTER_NGRAM + ARCHIVE_WEIGHT_RARITY_COVERAGE + ARCHIVE_WEIGHT_UNIQUE_TOKEN
)

# Generic legal vocabulary keeps less of its weight here than in the current
# matcher. Over a large corpus "seaduse muutmise eelnõu" is nearly every entry.
ARCHIVE_GENERIC_TOKEN_DAMPING = 0.15

# ---------------------------------------------------------------------------
# Thresholds. Higher and stricter than the current matcher's, on purpose.
# ---------------------------------------------------------------------------

ARCHIVE_AUTO_MATCH_SCORE = Decimal("72.00")
ARCHIVE_PLAUSIBLE_SCORE = Decimal("48.00")
ARCHIVE_MINIMUM_MARGIN = Decimal("18.00")


EVIDENCE_INDEX_ONLY = "index-only-candidate"
EVIDENCE_ALSO_CURRENT = "present-in-current-catalogue"
EVIDENCE_NO_RARE_OVERLAP = "no-uncommon-token-overlap"

ARCHIVE_BLOCKING_EVIDENCE = frozenset(
    {
        EVIDENCE_DEADLINE_CONFLICT,
        EVIDENCE_IMPOSSIBLE_CHRONOLOGY,
        EVIDENCE_ORGANIZATION_CONFLICT,
        EVIDENCE_GENERIC_ONLY,
        EVIDENCE_IDENTIFIER_CONFLICT,
        EVIDENCE_NO_RARE_OVERLAP,
    }
)

_ZERO = Decimal("0.00")


@dataclass
class ArchiveScoredCandidate(ScoredCandidate):
    @property
    def is_blocked(self) -> bool:
        return any(code in ARCHIVE_BLOCKING_EVIDENCE for code in self.evidence)


def build_archive_candidate_profiles(items) -> list[CandidateProfile]:
    """Normalise hydrated archive entries. Index-only rows are not candidates.

    An unhydrated row has an editorial headline and nothing else — no body, no
    date, no organisation — so it cannot be scored honestly and is excluded
    before the corpus is even built. Including it would also poison the idf
    table with documents that are one sentence long.
    """
    profiles = []
    for item in items:
        if not item.is_matchable:
            continue
        concise = f"{item.detail_title or item.title} {item.listing_summary}"
        full = f"{item.detail_title or item.title} {item.listing_summary} {item.body_text}"
        profiles.append(
            CandidateProfile(
                item_id=item.pk,
                content_key=item.content_key,
                published_date=item.published_date,
                feedback_deadline=item.feedback_deadline,
                organization_key=organization_key(item.named_organization),
                ngrams=character_ngrams(concise, size=NGRAM_SIZE),
                ngrams_full=character_ngrams(full, size=NGRAM_SIZE),
                tokens=frozenset(tokenize(full)),
                significant=significant_tokens(full),
                acronyms=acronyms(full),
                identifiers=identifiers(full),
            )
        )
    return profiles


def archive_rarity_coverage(query_tokens, candidate_tokens, idf) -> float:
    """Rarity coverage with the archive's own damping.

    Written out rather than reusing the current matcher's, because the damping
    constant is precisely the thing that must differ between the two corpora.
    """
    if not query_tokens:
        return 0.0
    default = max(idf.values(), default=1.0)
    total = covered = 0.0
    for token in set(query_tokens):
        weight = idf.get(token, default)
        if token in GENERIC_TOKENS:
            weight *= ARCHIVE_GENERIC_TOKEN_DAMPING
        total += weight
        if token in candidate_tokens:
            covered += weight
    return covered / total if total else 0.0


def score_archive_pair(legal, candidate, *, idf, owners) -> ArchiveScoredCandidate:
    """Score one legal record against one hydrated archive entry."""
    evidence: list[str] = []

    ngram = max(
        dice_similarity(legal.ngrams, candidate.ngrams),
        containment(legal.ngrams_significant, candidate.ngrams_full),
    )
    if ngram >= 0.5:
        evidence.append(EVIDENCE_NGRAM_STRONG)

    rarity = archive_rarity_coverage(legal.tokens, candidate.tokens, idf)
    if rarity >= 0.5:
        evidence.append(EVIDENCE_RARITY_STRONG)

    unique, unique_hits = unique_token_score(legal, candidate, owners)
    if unique_hits:
        evidence.append(EVIDENCE_UNIQUE_TOKEN)

    deadline, deadline_code = deadline_signal(legal, candidate)
    if deadline_code:
        evidence.append(deadline_code)
    deadline_applies = legal.deadline_date is not None and candidate.feedback_deadline is not None

    organization, organization_conflicts = organization_signal(legal, candidate)
    if organization > 0:
        evidence.append(EVIDENCE_ORGANIZATION_MATCH)
    organization_applies = bool(legal.organization_key and candidate.organization_key)

    weighted = (
        ARCHIVE_WEIGHT_CHARACTER_NGRAM * ngram
        + ARCHIVE_WEIGHT_RARITY_COVERAGE * rarity
        + ARCHIVE_WEIGHT_UNIQUE_TOKEN * unique
    )
    applicable = ARCHIVE_ALWAYS_APPLICABLE_WEIGHT
    if deadline_applies:
        weighted += ARCHIVE_WEIGHT_DEADLINE * deadline
        applicable += ARCHIVE_WEIGHT_DEADLINE
    if organization_applies:
        weighted += ARCHIVE_WEIGHT_ORGANIZATION * organization
        applicable += ARCHIVE_WEIGHT_ORGANIZATION

    score = Decimal(str(round((weighted / applicable) * 100, 4))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    shared_identifiers = legal.identifiers & candidate.identifiers
    if shared_identifiers:
        evidence.append(EVIDENCE_IDENTIFIER_MATCH)
    elif legal.identifiers and candidate.identifiers:
        # Blocking here, unlike in the current matcher. Two explicitly numbered
        # instruments that disagree are two instruments, and over a decade of
        # archive there are plenty of near-identical titles to confuse.
        evidence.append(EVIDENCE_IDENTIFIER_CONFLICT)
    if legal.acronyms & candidate.acronyms:
        evidence.append(EVIDENCE_ACRONYM_MATCH)

    # Contradictions.
    if (
        legal.received_date is not None
        and candidate.published_date is not None
        and (legal.received_date - candidate.published_date).days > IMPOSSIBLE_LEAD_DAYS
    ):
        evidence.append(EVIDENCE_IMPOSSIBLE_CHRONOLOGY)

    shared_significant = legal.significant & candidate.significant
    if organization_conflicts and not shared_significant:
        evidence.append(EVIDENCE_ORGANIZATION_CONFLICT)

    if legal.tokens and not shared_significant:
        shared = set(legal.tokens) & candidate.tokens
        if shared and shared <= GENERIC_TOKENS:
            evidence.append(EVIDENCE_GENERIC_ONLY)

    # The archive-specific floor: a link needs at least one genuinely
    # discriminating word in common. Across two hundred consultations, agreeing
    # only on ordinary legal vocabulary is not evidence of anything.
    #
    # Deliberately *not* keyed on archive-wide uniqueness. A token owned by
    # exactly one entry is a strong positive signal and is weighted as one
    # above — but requiring it here would block every pair the moment the
    # archive holds two consultations about the same law, which it routinely
    # does, since a law amended twice produces two entries sharing every noun.
    if not shared_significant and not shared_identifiers:
        evidence.append(EVIDENCE_NO_RARE_OVERLAP)

    return ArchiveScoredCandidate(profile=candidate, score=score, evidence=evidence)


def decide_archive(legal, scored) -> MatchOutcome:
    """One decision, under the archive's own thresholds."""
    if not scored:
        return MatchOutcome(
            legal_item_id=legal.item_id,
            best_candidate_id=None,
            decision=MatchDecision.UNMATCHED,
            score=_ZERO,
            runner_up_score=_ZERO,
            score_margin=_ZERO,
            candidate_count=0,
            evidence_codes=[EVIDENCE_NO_CANDIDATES],
        )

    ranked = sorted(scored, key=lambda entry: (-entry.score, entry.profile.content_key))
    plausible = [
        entry for entry in ranked if entry.score >= ARCHIVE_PLAUSIBLE_SCORE and not entry.is_blocked
    ]
    blocked_count = sum(1 for entry in ranked if entry.is_blocked)

    field_ = plausible or ranked
    best = field_[0]
    runner_up = field_[1].score if len(field_) > 1 else _ZERO
    margin = best.score - runner_up

    evidence = list(best.evidence)
    if blocked_count:
        evidence.append(EVIDENCE_BLOCKED_CANDIDATES)

    if not plausible:
        evidence.append(EVIDENCE_NO_PLAUSIBLE)
        decision = MatchDecision.UNMATCHED
    elif best.score >= ARCHIVE_AUTO_MATCH_SCORE and margin >= ARCHIVE_MINIMUM_MARGIN:
        decision = MatchDecision.MATCHED
    else:
        if best.score >= ARCHIVE_AUTO_MATCH_SCORE:
            evidence.append(EVIDENCE_NARROW_MARGIN)
        decision = MatchDecision.AMBIGUOUS

    return MatchOutcome(
        legal_item_id=legal.item_id,
        best_candidate_id=best.profile.item_id,
        decision=decision,
        score=best.score,
        runner_up_score=runner_up,
        score_margin=margin,
        candidate_count=len(plausible),
        evidence_codes=sorted(set(evidence)),
    )


def match_archive(legal_items, archive_items, *, excluded_urls=frozenset()):
    """One decision for every considered record, over the archive corpus alone.

    `excluded_urls` are the canonical addresses in the exact current-topic
    snapshot. A consultation that is still on the current listing belongs to the
    current matcher; letting the archive re-judge the same URL under different
    thresholds is how one page ends up with two contradictory verdicts.
    """
    usable = [
        item
        for item in archive_items
        if item.is_matchable and item.canonical_url not in excluded_urls
    ]
    candidates = build_archive_candidate_profiles(usable)
    idf = inverse_document_frequency(candidates)

    owners: dict[str, int] = {}
    for candidate in candidates:
        for token in candidate.significant:
            owners[token] = owners.get(token, 0) + 1

    outcomes = []
    for item in legal_items:
        legal = build_legal_profile(item)
        scored = [
            score_archive_pair(legal, candidate, idf=idf, owners=owners) for candidate in candidates
        ]
        outcomes.append(decide_archive(legal, scored))
    return outcomes


__all__ = [
    "ARCHIVE_AUTO_MATCH_SCORE",
    "ARCHIVE_MATCHER_VERSION",
    "ARCHIVE_MINIMUM_MARGIN",
    "ARCHIVE_PLAUSIBLE_SCORE",
    "EVIDENCE_ALSO_CURRENT",
    "EVIDENCE_INDEX_ONLY",
    "EVIDENCE_NO_RARE_OVERLAP",
    "match_archive",
]
