"""Deterministic matching of open legal records against the current catalogue.

No model, no embedding, no vector store and no external service. Ordinary code
over the two normalised texts, so every score is reproducible from the inputs
and explainable from the evidence codes stored beside it.

**Why the title is not the primary signal.** The two sides name the same thing
differently and always will. The workbook records the instrument —
*"pakendiseaduse muutmise seaduse eelnõu"* — while Koda.ee publishes the
invitation — *"Mida arvad plaanitavatest pakendiseaduse muudatustest?"*. Scoring
title against title would systematically under-rate true pairs. What is compared
instead is the legal topic against the whole of what a catalogue entry says:
its title, its listing summary and its article text. The formal instrument is
almost always named in the body even when the headline avoids it.

**Why character n-grams carry as much weight as tokens.** Estonian inflects.
``pakendiseadus`` and ``pakendiseaduse`` share no token at all and share every
4-gram but one. A morphological analyser would be the alternative, and it would
be one more thing that has to stay correct in a repository with no other use
for it.

**The base rate is the real risk.** Nine catalogue entries face roughly thirty
open legal records, so most open records genuinely have no match. A matcher
scoring every record against its nearest of nine will find "the best of nine"
every time, and the best of nine is usually wrong. The absolute plausibility
floor is therefore the load-bearing threshold, and a run that reports mostly
``unmatched`` is the correct result rather than a broken one.

A ``matched`` decision from this module becomes a link on the Õigusloome page,
so the conservative side of every threshold is the side that refuses. What no
decision here ever does is change a :class:`LegalWorkItem`: the address is
resolved at read time by :mod:`apps.legal_work.topic_links` and the imported row
stays exactly what the workbook said.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

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

# The scoring contract. Anything that changes what a score *means* — a weight, a
# threshold, a contradiction rule, the normaliser — changes this string, so an
# old snapshot's numbers keep meaning what they meant when they were written and
# a re-run under new rules publishes a new snapshot rather than overwriting one.
# 1.1 changed *which records are considered*, not how any of them is scored:
# a record whose opinion has already been sent is no longer offered a
# consultation link. Every weight and threshold below is byte-for-byte what 1.0
# used, so the two versions' scores are directly comparable — what differs is
# the population. The bump exists because an old snapshot's counts would
# otherwise silently mean something different from a new one's.
MATCHER_VERSION = f"1.1-norm{NORMALISER_VERSION}"

# ---------------------------------------------------------------------------
# Weights. Named constants in this module, deliberately not settings and not
# database rows: a scoring rule that can be edited without a code review is a
# scoring rule nobody can reconstruct six months later. They sum to 1.
# ---------------------------------------------------------------------------

WEIGHT_CHARACTER_NGRAM = 0.30
WEIGHT_RARITY_COVERAGE = 0.30
WEIGHT_UNIQUE_TOKEN = 0.20
WEIGHT_DEADLINE = 0.12
WEIGHT_ORGANIZATION = 0.08

# The deadline and organisation signals only exist when **both** sides state the
# fact. Scoring an inapplicable signal as zero would cap a record that simply
# has no deadline at 88 out of 100 and one with neither at 80, so a threshold
# would silently mean something stricter for the sparser records — precisely the
# ones this feature exists to enrich. The applicable weights are renormalised
# instead, so every record is scored out of a full hundred and one threshold
# means one thing.
ALWAYS_APPLICABLE_WEIGHT = WEIGHT_CHARACTER_NGRAM + WEIGHT_RARITY_COVERAGE + WEIGHT_UNIQUE_TOKEN

# How much of its rarity weight a generic legal word keeps. Over a catalogue
# this small, idf alone leaves "seaduse", "muutmise" and "eelnõu" carrying real
# mass, and a topic made mostly of those words would then cover itself against
# any entry that is also about a law.
GENERIC_TOKEN_DAMPING = 0.25

# ---------------------------------------------------------------------------
# Thresholds, on the documented 0–100 scale.
#
# Chosen from synthetic tests written to resemble the real corpus, where true
# pairs land in the sixties and above and false pairs in the teens. They are
# revisited from what production actually produces, and the read-only admin
# exists to make that inspectable. See docs/legal-current-topic-matching.md.
# ---------------------------------------------------------------------------

AUTO_MATCH_SCORE = Decimal("62.00")
PLAUSIBLE_SCORE = Decimal("38.00")
MINIMUM_MARGIN = Decimal("12.00")

# Contradiction tolerances.
#
# Two explicit deadlines this far apart are two different consultations. A few
# days of slack absorbs the difference between the workbook's own deadline and
# the slightly earlier one the Chamber publishes to leave itself time to write.
DEADLINE_AGREEMENT_DAYS = 3
DEADLINE_CONFLICT_DAYS = 14
# A page cannot invite comment on a draft the Chamber had not yet received. Some
# slack, because the workbook's received date is a clerical entry, not a
# timestamp.
IMPOSSIBLE_LEAD_DAYS = 60
# Publication and receipt within this window is weak supporting evidence.
DATE_PROXIMITY_DAYS = 45

# How many uniquely-owned catalogue tokens a legal topic must hit for the
# uncommon-token signal to saturate. Three rare words in common is already a
# strong statement; more adds no further confidence.
UNIQUE_TOKEN_SATURATION = 3

# n-gram width. Four holds an Estonian stem fragment without matching on
# grammatical noise.
NGRAM_SIZE = 4

# Evidence vocabulary. Short, stable, machine-readable codes; never free text
# and never an explanation of the algorithm's reasoning.
EVIDENCE_NGRAM_STRONG = "ngram-strong"
EVIDENCE_RARITY_STRONG = "rarity-strong"
EVIDENCE_UNIQUE_TOKEN = "unique-token-hit"
EVIDENCE_DEADLINE_EXACT = "deadline-exact"
EVIDENCE_DEADLINE_NEAR = "deadline-near"
EVIDENCE_ORGANIZATION_MATCH = "organization-match"
EVIDENCE_IDENTIFIER_MATCH = "identifier-match"
EVIDENCE_ACRONYM_MATCH = "acronym-match"
EVIDENCE_DATE_PROXIMATE = "date-proximate"

EVIDENCE_DEADLINE_CONFLICT = "deadline-conflict"
EVIDENCE_IMPOSSIBLE_CHRONOLOGY = "impossible-chronology"
EVIDENCE_ORGANIZATION_CONFLICT = "organization-conflict-unsupported"
EVIDENCE_GENERIC_ONLY = "generic-overlap-only"
EVIDENCE_IDENTIFIER_CONFLICT = "identifier-conflict"

EVIDENCE_NARROW_MARGIN = "narrow-margin"
EVIDENCE_NO_CANDIDATES = "no-candidates"
EVIDENCE_NO_PLAUSIBLE = "no-plausible-candidate"
EVIDENCE_BLOCKED_CANDIDATES = "blocked-candidates"

# Contradictions that stop a candidate from being accepted at any score.
BLOCKING_EVIDENCE = frozenset(
    {
        EVIDENCE_DEADLINE_CONFLICT,
        EVIDENCE_IMPOSSIBLE_CHRONOLOGY,
        EVIDENCE_ORGANIZATION_CONFLICT,
        EVIDENCE_GENERIC_ONLY,
    }
)

_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class CandidateProfile:
    """Everything the matcher needs from one catalogue entry, computed once."""

    item_id: int
    content_key: str
    published_date: object
    feedback_deadline: object
    organization_key: str
    ngrams: frozenset[str]
    ngrams_full: frozenset[str]
    tokens: frozenset[str]
    significant: frozenset[str]
    acronyms: frozenset[str]
    identifiers: frozenset[str]


@dataclass(frozen=True)
class LegalProfile:
    """Everything the matcher needs from one open legal record."""

    item_id: int
    received_date: object
    deadline_date: object
    organization_key: str
    ngrams: frozenset[str]
    ngrams_significant: frozenset[str]
    tokens: tuple[str, ...]
    significant: frozenset[str]
    acronyms: frozenset[str]
    identifiers: frozenset[str]


@dataclass
class ScoredCandidate:
    profile: CandidateProfile
    score: Decimal
    evidence: list[str] = field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return any(code in BLOCKING_EVIDENCE for code in self.evidence)


@dataclass(frozen=True)
class MatchOutcome:
    """One decision about one open legal record."""

    legal_item_id: int
    best_candidate_id: int | None
    decision: str
    score: Decimal
    runner_up_score: Decimal
    score_margin: Decimal
    candidate_count: int
    evidence_codes: list[str]


# ---------------------------------------------------------------------------
# Profiles.
# ---------------------------------------------------------------------------


def build_candidate_profiles(items) -> list[CandidateProfile]:
    """Normalise every catalogue entry once, before any pair is compared."""
    profiles = []
    for item in items:
        # n-grams over the concise fields only. Including a six-thousand
        # character body would let two long pages resemble each other simply by
        # both being long.
        concise = f"{item.title} {item.listing_summary}"
        full = f"{item.title} {item.listing_summary} {item.body_text}"
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


def build_legal_profile(item) -> LegalProfile:
    """Normalise one open legal record.

    The topic and the act type are the record's own description of the matter.
    `recipient` is read separately as the organisation: it is who the opinion
    goes to, which is usually but not always who drafted the thing, so it
    informs a signal rather than deciding one.
    """
    text = f"{item.topic} {item.act_type}"
    significant = significant_tokens(text)
    return LegalProfile(
        item_id=item.pk,
        received_date=item.received_date,
        deadline_date=item.deadline_date,
        organization_key=organization_key(item.recipient),
        ngrams=character_ngrams(text, size=NGRAM_SIZE),
        # Containment is measured on the discriminating words alone. Built from
        # the generic ones too, it would report that "…seaduse muutmise seaduse
        # eelnõu" is contained in every article that mentions amending a law,
        # which is all of them.
        ngrams_significant=character_ngrams(" ".join(sorted(significant)), size=NGRAM_SIZE),
        tokens=tokenize(text),
        significant=significant,
        acronyms=acronyms(text),
        identifiers=identifiers(text),
    )


def organization_key(value: str) -> str:
    """A comparable key for an organisation name from either side.

    The workbook writes "Rahandusministeerium" or "Rahandusmin." and the page
    writes "Rahandusministeeriumis". Reducing both to their content tokens and
    dropping the shared word "ministeerium" leaves the part that distinguishes
    one ministry from another.
    """
    tokens = [token for token in tokenize(value or "") if token not in GENERIC_TOKENS]
    if not tokens:
        return ""
    # The distinguishing prefix, capped: "majandus- ja kommunikatsiooni…" and
    # "majandus- ja tööstus…" must not collapse into one key.
    return " ".join(sorted(tokens))[:120]


# ---------------------------------------------------------------------------
# Signals.
# ---------------------------------------------------------------------------


def inverse_document_frequency(profiles) -> dict[str, float]:
    """Rarity of each token across the catalogue.

    The classic BM25 idf, over a deliberately small corpus: the catalogue is
    the only collection where "how unusual is this word *here*" is answerable,
    and it is exactly the question that makes ``krüptovarateenuse`` outweigh
    ``eelnõu``.
    """
    total = len(profiles)
    frequencies: dict[str, int] = {}
    for profile in profiles:
        for token in profile.tokens:
            frequencies[token] = frequencies.get(token, 0) + 1
    return {
        token: math.log(1 + (total - count + 0.5) / (count + 0.5))
        for token, count in frequencies.items()
    }


def dice_similarity(left: frozenset[str], right: frozenset[str]) -> float:
    """Overlap of two sets on a 0–1 scale, tolerant of unequal sizes."""
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def containment(needle: frozenset[str], haystack: frozenset[str]) -> float:
    """How much of `needle` appears in `haystack`, ignoring the haystack's size.

    Dice punishes a long document for being long, which is exactly wrong here:
    "Turismiseaduse muutmise väljatöötamiskavatsus" appears nowhere in its
    page's headline or summary and in full in its body, and dice against a
    six-thousand-character article scores that near zero. Containment asks the
    question that actually matters — is the legal record's own wording present
    on this page at all — and the two are combined by taking the better.
    """
    if not needle or not haystack:
        return 0.0
    return len(needle & haystack) / len(needle)


def rarity_coverage(
    query_tokens: tuple[str, ...],
    candidate_tokens: frozenset[str],
    idf: dict[str, float],
) -> float:
    """How much of the legal record's *information* the candidate accounts for.

    Weighted by rarity, so covering one uncommon noun counts for more than
    covering four grammatical ones, and normalised by the query's own total so
    a long topic is not penalised for having more words to cover.

    Generic legal vocabulary is damped rather than dropped. Over a catalogue of
    nine documents, idf alone does not separate ``seaduse`` from
    ``pakendiseaduse`` nearly enough: a topic reading "X-seaduse muutmise
    seaduse eelnõu" is three quarters scaffolding, and without the damping every
    such record covers most of its own weight against *any* entry that also
    happens to be about a law.
    """
    if not query_tokens:
        return 0.0
    # A query token absent from the whole catalogue is maximally rare; giving it
    # the highest observed weight stops an unknown word from silently counting
    # as free.
    default = max(idf.values(), default=1.0)
    total = 0.0
    covered = 0.0
    for token in set(query_tokens):
        weight = idf.get(token, default)
        if token in GENERIC_TOKENS:
            weight *= GENERIC_TOKEN_DAMPING
        total += weight
        if token in candidate_tokens:
            covered += weight
    return covered / total if total else 0.0


def unique_token_score(
    legal: LegalProfile, candidate: CandidateProfile, owners: dict[str, int]
) -> tuple[float, int]:
    """Hits on tokens only this catalogue entry uses.

    A word that appears in exactly one catalogue entry is that entry's own
    subject. The legal record using it too is the single most direct evidence
    available without a shared identifier.
    """
    hits = sum(1 for token in candidate.significant & legal.significant if owners.get(token) == 1)
    return min(hits, UNIQUE_TOKEN_SATURATION) / UNIQUE_TOKEN_SATURATION, hits


def deadline_signal(legal: LegalProfile, candidate: CandidateProfile) -> tuple[float, str | None]:
    """Agreement between two explicitly stated deadlines.

    Absent on either side is *no evidence* and scores zero — never a penalty.
    Most pages state one and many workbook rows do not, and treating a missing
    fact as a negative one would punish exactly the records this feature exists
    to enrich.
    """
    if legal.deadline_date is None or candidate.feedback_deadline is None:
        return 0.0, None
    gap = abs((candidate.feedback_deadline - legal.deadline_date).days)
    if gap == 0:
        return 1.0, EVIDENCE_DEADLINE_EXACT
    if gap <= DEADLINE_AGREEMENT_DAYS:
        return 0.7, EVIDENCE_DEADLINE_NEAR
    if gap > DEADLINE_CONFLICT_DAYS:
        return 0.0, EVIDENCE_DEADLINE_CONFLICT
    return 0.0, None


def organization_signal(legal: LegalProfile, candidate: CandidateProfile) -> tuple[float, bool]:
    """Whether the two sides name the same body. Returns (score, conflicts)."""
    if not legal.organization_key or not candidate.organization_key:
        return 0.0, False
    if legal.organization_key == candidate.organization_key:
        return 1.0, False
    # Partial agreement: the workbook may abbreviate. One shared distinguishing
    # token is agreement; none at all is a conflict.
    left = set(legal.organization_key.split())
    right = set(candidate.organization_key.split())
    if left & right:
        return 0.6, False
    return 0.0, True


# ---------------------------------------------------------------------------
# Scoring one pair.
# ---------------------------------------------------------------------------


def score_pair(
    legal: LegalProfile,
    candidate: CandidateProfile,
    *,
    idf: dict[str, float],
    owners: dict[str, int],
) -> ScoredCandidate:
    """Score one legal record against one catalogue entry."""
    evidence: list[str] = []

    # The better of two readings: how alike the two headlines are, and how much
    # of the legal record's wording appears anywhere on the page.
    ngram = max(
        dice_similarity(legal.ngrams, candidate.ngrams),
        containment(legal.ngrams_significant, candidate.ngrams_full),
    )
    if ngram >= 0.5:
        evidence.append(EVIDENCE_NGRAM_STRONG)

    rarity = rarity_coverage(legal.tokens, candidate.tokens, idf)
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
        WEIGHT_CHARACTER_NGRAM * ngram
        + WEIGHT_RARITY_COVERAGE * rarity
        + WEIGHT_UNIQUE_TOKEN * unique
    )
    applicable = ALWAYS_APPLICABLE_WEIGHT
    if deadline_applies:
        weighted += WEIGHT_DEADLINE * deadline
        applicable += WEIGHT_DEADLINE
    if organization_applies:
        weighted += WEIGHT_ORGANIZATION * organization
        applicable += WEIGHT_ORGANIZATION

    raw = weighted / applicable
    score = Decimal(str(round(raw * 100, 4))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Evidence that is recorded but never weighted. These fire rarely on the
    # real pages, so weighting them now would tune the matcher on cases it has
    # not yet met; the codes make them measurable in the admin first.
    shared_identifiers = legal.identifiers & candidate.identifiers
    if shared_identifiers:
        evidence.append(EVIDENCE_IDENTIFIER_MATCH)
    elif legal.identifiers and candidate.identifiers:
        evidence.append(EVIDENCE_IDENTIFIER_CONFLICT)
    if legal.acronyms & candidate.acronyms:
        evidence.append(EVIDENCE_ACRONYM_MATCH)

    if (
        legal.received_date is not None
        and candidate.published_date is not None
        and abs((candidate.published_date - legal.received_date).days) <= DATE_PROXIMITY_DAYS
    ):
        evidence.append(EVIDENCE_DATE_PROXIMATE)

    # Contradictions. Each one blocks acceptance outright rather than shaving
    # points off, because a high text score is exactly the situation in which a
    # contradiction matters most.
    if (
        legal.received_date is not None
        and candidate.published_date is not None
        and (legal.received_date - candidate.published_date).days > IMPOSSIBLE_LEAD_DAYS
    ):
        evidence.append(EVIDENCE_IMPOSSIBLE_CHRONOLOGY)

    if organization_conflicts and not (legal.significant & candidate.significant):
        # Different ministries *and* not one uncommon word in common. Either
        # alone is survivable; together there is nothing supporting the pair.
        evidence.append(EVIDENCE_ORGANIZATION_CONFLICT)

    if legal.tokens and not (legal.significant & candidate.significant):
        shared = set(legal.tokens) & candidate.tokens
        if shared and shared <= GENERIC_TOKENS:
            evidence.append(EVIDENCE_GENERIC_ONLY)

    return ScoredCandidate(profile=candidate, score=score, evidence=evidence)


# ---------------------------------------------------------------------------
# Deciding.
# ---------------------------------------------------------------------------


def decide(legal: LegalProfile, scored: list[ScoredCandidate]) -> MatchOutcome:
    """Turn a scored field into exactly one decision.

    Every number on the resulting row describes the same field: the *acceptable*
    candidates when there are any, and the rejected front-runner when there are
    not. Reporting a winner from one ranking and a margin from another would
    make the stored evidence unreadable during calibration, which is the only
    thing these rows are for.
    """
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

    # Deterministic to the last tie: equal scores order by content key, so two
    # runs over identical inputs cannot disagree about which came first.
    ranked = sorted(scored, key=lambda entry: (-entry.score, entry.profile.content_key))
    plausible = [
        entry for entry in ranked if entry.score >= PLAUSIBLE_SCORE and not entry.is_blocked
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
    elif best.score >= AUTO_MATCH_SCORE and margin >= MINIMUM_MARGIN:
        decision = MatchDecision.MATCHED
    else:
        if best.score >= AUTO_MATCH_SCORE:
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
        # Stable order, so two runs produce byte-identical rows.
        evidence_codes=sorted(set(evidence)),
    )


def match_all(legal_items, candidate_items) -> list[MatchOutcome]:
    """One decision for every open legal record, against the whole catalogue."""
    candidates = build_candidate_profiles(candidate_items)
    idf = inverse_document_frequency(candidates)

    owners: dict[str, int] = {}
    for candidate in candidates:
        for token in candidate.significant:
            owners[token] = owners.get(token, 0) + 1

    outcomes = []
    for item in legal_items:
        legal = build_legal_profile(item)
        scored = [score_pair(legal, candidate, idf=idf, owners=owners) for candidate in candidates]
        outcomes.append(decide(legal, scored))
    return outcomes
