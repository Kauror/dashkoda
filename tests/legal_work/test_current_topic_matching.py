"""The deterministic matcher: what it accepts, what it refuses and why.

Pure functions over stand-in records, so these cases stay readable and none of
them needs a database. The publication rules — one decision per open record,
exact snapshot references, last-good behaviour — live in
`test_current_topic_match_sync.py`, which does.

All wording is synthetic. It is written to *resemble* the real vocabularies,
because a matcher tuned on toy strings would tell us nothing about the corpus it
has to work on: the workbook names an instrument, the page asks a question, and
the discriminating noun is the only thing the two share.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from apps.legal_work.current_topic_matching import (
    AUTO_MATCH_SCORE,
    EVIDENCE_DEADLINE_CONFLICT,
    EVIDENCE_DEADLINE_EXACT,
    EVIDENCE_GENERIC_ONLY,
    EVIDENCE_IDENTIFIER_CONFLICT,
    EVIDENCE_IDENTIFIER_MATCH,
    EVIDENCE_IMPOSSIBLE_CHRONOLOGY,
    EVIDENCE_NARROW_MARGIN,
    EVIDENCE_NO_CANDIDATES,
    EVIDENCE_NO_PLAUSIBLE,
    EVIDENCE_ORGANIZATION_CONFLICT,
    EVIDENCE_ORGANIZATION_MATCH,
    EVIDENCE_UNIQUE_TOKEN,
    MATCHER_VERSION,
    PLAUSIBLE_SCORE,
    match_all,
)
from apps.legal_work.models import MatchDecision

PUBLISHED = dt.date(2026, 7, 16)
DEADLINE = dt.date(2026, 8, 17)


@dataclass
class Topic:
    """Stands in for a `CurrentTopicItem` without needing a row."""

    pk: int
    content_key: str
    title: str
    listing_summary: str = ""
    body_text: str = ""
    published_date: dt.date | None = PUBLISHED
    feedback_deadline: dt.date | None = DEADLINE
    named_organization: str = "Kliimaministeerium"


@dataclass
class Record:
    """Stands in for an open `LegalWorkItem`."""

    pk: int
    topic: str
    act_type: str = "Seaduse eelnõu"
    received_date: dt.date | None = PUBLISHED
    deadline_date: dt.date | None = DEADLINE
    recipient: str = "Kliimaministeerium"


def packaging_topic(**overrides) -> Topic:
    defaults = {
        "pk": 1,
        "content_key": "aaa",
        "title": "Mida arvad plaanitavatest pakendiseaduse muudatustest?",
        "listing_summary": (
            "Kliimaministeerium on koostanud eelnõu, millega keelatakse "
            "müügikohtades ühekordselt kasutatavate kaasamüügipakendite tasuta "
            "andmine. Anna hiljemalt 17. augustiks teada."
        ),
        "body_text": (
            "Pakendiseaduse muutmise seaduse eelnõu eesmärk on suurendada "
            "ringmajanduse turgu korduskasutuspakendite kasutamisel."
        ),
    }
    return Topic(**{**defaults, **overrides})


def accessibility_topic(**overrides) -> Topic:
    defaults = {
        "pk": 2,
        "content_key": "bbb",
        "title": "Jaga mõtteid toodete ja teenuste ligipääsetavuse seaduse muudatuste kohta",
        "listing_summary": (
            "Majandus- ja kommunikatsiooniministeerium on koostanud eelnõu, "
            "millega soovib täpsustada toodete ja teenuste ligipääsetavuse "
            "seadust ja laiendada mikroettevõtja mõistet."
        ),
        "body_text": (
            "Toodete ja teenuste ligipääsetavuse seaduse muutmise seaduse eelnõu "
            "selgitab ebaproportsionaalse koormuse erandi kohaldamist."
        ),
        "named_organization": "Majandus- ja Kommunikatsiooniministeerium",
    }
    return Topic(**{**defaults, **overrides})


def only(record, topics) -> object:
    outcomes = match_all([record], topics)
    assert len(outcomes) == 1
    return outcomes[0]


# -- the pairs that should match -------------------------------------------


def test_a_near_identical_topic_and_title_match():
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        [packaging_topic(), accessibility_topic()],
    )

    assert outcome.decision == MatchDecision.MATCHED
    assert outcome.best_candidate_id == 1
    assert outcome.score >= AUTO_MATCH_SCORE


def test_editorial_boilerplate_does_not_prevent_a_match():
    """The page's headline is a question; the record's topic is an act name."""
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        [packaging_topic(title="Mida arvad? Anna teada ja jaga oma mõtteid!")],
    )

    assert outcome.decision == MatchDecision.MATCHED


def test_an_act_named_only_in_the_body_still_matches():
    """The headline avoids the instrument; the article names it in full."""
    outcome = only(
        Record(pk=10, topic="Turismiseaduse muutmise väljatöötamiskavatsus", deadline_date=None),
        [
            Topic(
                pk=3,
                content_key="ccc",
                title="Mida arvad plaanist täpsustada lühiajalise üüri reegleid?",
                listing_summary=(
                    "Majandus- ja kommunikatsiooniministeerium on koostanud väljatöötamiskavatsuse."
                ),
                body_text=(
                    "Turismiseaduse väljatöötamiskavatsus soovib määratleda, millal on "
                    "lühiajaline üüriteenus käsitatav majutusteenusena."
                ),
                named_organization="Majandus- ja Kommunikatsiooniministeerium",
            )
        ],
    )

    assert outcome.decision == MatchDecision.MATCHED


def test_uncommon_legal_terms_outweigh_generic_words():
    """Two records share every generic word; only one shares the rare noun."""
    topics = [packaging_topic(), accessibility_topic()]

    right = only(Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"), topics)
    wrong = only(Record(pk=11, topic="Kollektiivlepingu seaduse muutmise seaduse eelnõu"), topics)

    assert right.score > wrong.score
    assert EVIDENCE_UNIQUE_TOKEN in right.evidence_codes
    assert EVIDENCE_UNIQUE_TOKEN not in wrong.evidence_codes


def test_a_missing_deadline_does_not_cap_the_achievable_score():
    """A record with no deadline is scored out of the signals that apply to it."""
    with_deadline = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"), [packaging_topic()]
    )
    without = only(
        Record(pk=11, topic="Pakendiseaduse muutmise seaduse eelnõu", deadline_date=None),
        [packaging_topic()],
    )

    assert without.decision == MatchDecision.MATCHED
    # Losing the deadline costs confidence, not the ability to reach the bar.
    assert without.score < with_deadline.score
    assert without.score >= AUTO_MATCH_SCORE


# -- signals that add or remove confidence ----------------------------------


def test_a_matching_deadline_raises_confidence():
    topics = [packaging_topic()]
    agreeing = only(Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"), topics)
    silent = only(
        Record(pk=11, topic="Pakendiseaduse muutmise seaduse eelnõu", deadline_date=None), topics
    )

    assert EVIDENCE_DEADLINE_EXACT in agreeing.evidence_codes
    assert agreeing.score > silent.score


def test_a_conflicting_deadline_blocks_the_candidate():
    outcome = only(
        Record(
            pk=10,
            topic="Pakendiseaduse muutmise seaduse eelnõu",
            deadline_date=dt.date(2026, 12, 1),
        ),
        [packaging_topic()],
    )

    assert EVIDENCE_DEADLINE_CONFLICT in outcome.evidence_codes
    assert outcome.decision == MatchDecision.UNMATCHED


def test_a_matching_organization_strengthens_the_pair():
    topics = [packaging_topic()]
    agreeing = only(Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"), topics)
    silent = only(
        Record(pk=11, topic="Pakendiseaduse muutmise seaduse eelnõu", recipient=""), topics
    )

    assert EVIDENCE_ORGANIZATION_MATCH in agreeing.evidence_codes
    assert agreeing.score > silent.score


def test_conflicting_organizations_with_nothing_else_in_common_block_the_pair():
    outcome = only(
        Record(
            pk=10,
            topic="Kollektiivlepingu seaduse muutmise seaduse eelnõu",
            recipient="Sotsiaalministeerium",
            deadline_date=None,
        ),
        [packaging_topic()],
    )

    assert EVIDENCE_ORGANIZATION_CONFLICT in outcome.evidence_codes
    assert outcome.decision == MatchDecision.UNMATCHED


def test_conflicting_organizations_do_not_block_when_the_subject_agrees():
    """The workbook records who the opinion goes to, not always who drafted it."""
    outcome = only(
        Record(
            pk=10,
            topic="Pakendiseaduse muutmise seaduse eelnõu",
            recipient="Rahandusministeerium",
        ),
        [packaging_topic()],
    )

    assert EVIDENCE_ORGANIZATION_CONFLICT not in outcome.evidence_codes
    assert outcome.decision == MatchDecision.MATCHED


def test_only_generic_words_in_common_blocks_the_pair():
    outcome = only(
        Record(
            pk=10,
            topic="Käibemaksuseaduse muutmise seaduse eelnõu",
            recipient="",
            deadline_date=None,
        ),
        [packaging_topic(named_organization="")],
    )

    assert EVIDENCE_GENERIC_ONLY in outcome.evidence_codes
    assert outcome.decision == MatchDecision.UNMATCHED


def test_an_impossible_chronology_blocks_the_pair():
    """The page cannot invite comment months before the draft arrived."""
    outcome = only(
        Record(
            pk=10,
            topic="Pakendiseaduse muutmise seaduse eelnõu",
            received_date=dt.date(2027, 6, 1),
            deadline_date=None,
        ),
        [packaging_topic()],
    )

    assert EVIDENCE_IMPOSSIBLE_CHRONOLOGY in outcome.evidence_codes
    assert outcome.decision == MatchDecision.UNMATCHED


def test_a_shared_proposal_identifier_is_recorded_as_evidence():
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu 512 SE"),
        [packaging_topic(body_text="Pakendiseaduse eelnõu 512 SE menetlus jätkub.")],
    )

    assert EVIDENCE_IDENTIFIER_MATCH in outcome.evidence_codes


def test_conflicting_identifiers_are_recorded_but_do_not_block_on_their_own():
    """`eelnõu punktid 1, 2 ja 4` is prose, so identifiers stay evidence-only."""
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu 512 SE"),
        [packaging_topic(body_text="Pakendiseaduse eelnõu 998 SE menetlus jätkub.")],
    )

    assert EVIDENCE_IDENTIFIER_CONFLICT in outcome.evidence_codes
    assert outcome.decision == MatchDecision.MATCHED


# -- deciding between candidates -------------------------------------------


def test_one_clear_winner_is_matched():
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        [packaging_topic(), accessibility_topic()],
    )

    assert outcome.decision == MatchDecision.MATCHED
    assert outcome.score - outcome.runner_up_score == outcome.score_margin


def test_two_effectively_tied_candidates_are_ambiguous():
    """The same page published twice under two slugs cannot be told apart."""
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        [packaging_topic(), packaging_topic(pk=9, content_key="zzz")],
    )

    assert outcome.decision == MatchDecision.AMBIGUOUS
    assert EVIDENCE_NARROW_MARGIN in outcome.evidence_codes
    assert outcome.candidate_count == 2
    assert outcome.score_margin < 12


def test_a_weak_candidate_is_unmatched():
    outcome = only(
        Record(
            pk=10,
            topic="Notariaadiseaduse muutmise seaduse eelnõu",
            recipient="",
            deadline_date=None,
        ),
        [packaging_topic(named_organization="")],
    )

    assert outcome.decision == MatchDecision.UNMATCHED
    assert outcome.score < PLAUSIBLE_SCORE
    assert EVIDENCE_NO_PLAUSIBLE in outcome.evidence_codes


def test_the_rejected_front_runner_is_still_recorded_for_calibration():
    outcome = only(
        Record(
            pk=10,
            topic="Notariaadiseaduse muutmise seaduse eelnõu",
            recipient="",
            deadline_date=None,
        ),
        [packaging_topic(named_organization="")],
    )

    assert outcome.decision == MatchDecision.UNMATCHED
    assert outcome.best_candidate_id == 1
    assert outcome.candidate_count == 0


def test_an_empty_catalogue_leaves_every_record_unmatched():
    outcome = only(Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"), [])

    assert outcome.decision == MatchDecision.UNMATCHED
    assert outcome.best_candidate_id is None
    assert outcome.evidence_codes == [EVIDENCE_NO_CANDIDATES]
    assert outcome.score == 0


def test_every_record_receives_exactly_one_decision():
    records = [
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        Record(pk=11, topic="Toodete ja teenuste ligipääsetavuse seaduse muutmise seaduse eelnõu"),
        Record(pk=12, topic="Notariaadiseaduse muutmise seaduse eelnõu"),
    ]

    outcomes = match_all(records, [packaging_topic(), accessibility_topic()])

    assert len(outcomes) == 3
    assert sorted(outcome.legal_item_id for outcome in outcomes) == [10, 11, 12]
    assert all(outcome.decision in MatchDecision.values for outcome in outcomes)


# -- determinism ------------------------------------------------------------


def test_the_same_inputs_produce_byte_identical_results():
    records = [Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu")]
    topics = [packaging_topic(), accessibility_topic()]

    first = match_all(records, topics)
    second = match_all(records, list(reversed(topics)))

    assert [vars(outcome) for outcome in first] == [vars(outcome) for outcome in second]


def test_evidence_codes_are_sorted_and_unique():
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"), [packaging_topic()]
    )

    assert outcome.evidence_codes == sorted(set(outcome.evidence_codes))


def test_scores_stay_on_the_documented_scale():
    records = [
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        Record(pk=11, topic="Notariaadiseaduse muutmise seaduse eelnõu"),
    ]

    for outcome in match_all(records, [packaging_topic(), accessibility_topic()]):
        assert 0 <= outcome.score <= 100
        assert 0 <= outcome.runner_up_score <= outcome.score
        assert outcome.score_margin == outcome.score - outcome.runner_up_score


def test_the_matcher_version_names_the_normaliser_it_depends_on():
    from apps.legal_work.text_normalisation import NORMALISER_VERSION

    assert NORMALISER_VERSION in MATCHER_VERSION


# -- the normaliser it depends on ------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "removed"),
    [
        ("Mida arvad pakendiseadusest", "arvad"),
        ("Anna teada pakendiseadusest", "teada"),
        ("Jaga mõtteid pakendiseadusest", "mõtteid"),
        ("Kas toetad pakendiseadust", "toetad"),
        ("Plaanitavad muudatused pakendiseaduses", "plaanitavad"),
        ("Eelnõu kohta pakendiseadusest", "kohta"),
    ],
)
def test_editorial_prompts_are_down_weighted_away(phrase, removed):
    from apps.legal_work.text_normalisation import tokenize

    tokens = tokenize(phrase)

    assert removed not in tokens
    assert any(token.startswith("pakendiseadus") for token in tokens)


def test_legal_names_numbers_and_acronyms_survive_normalisation():
    from apps.legal_work.text_normalisation import acronyms, tokenize

    text = "Pakendiseaduse § 24 muutmine FATCA ja OECD kontekstis, 512 SE"

    tokens = tokenize(text)
    assert "pakendiseaduse" in tokens
    assert "24" in tokens
    assert "512" in tokens
    assert {"FATCA", "OECD"} <= acronyms(text)


def test_estonian_typography_folds_to_one_form():
    from apps.legal_work.text_normalisation import fold

    assert fold("„Pakendiseadus“ – muutmine") == fold('"Pakendiseadus" - muutmine')


def test_diacritics_are_preserved_because_they_change_meaning():
    from apps.legal_work.text_normalisation import tokenize

    assert tokenize("õhutus") != tokenize("ohutus")
