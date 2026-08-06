"""The archive matcher: separate corpus, separate thresholds, stricter refusals.

Pure functions over stand-in records, so none of these needs a database. The
publication rules — the four-part run identity, the exclusion of records the
current matcher already answered — live in `test_archived_topic_match_sync.py`.

The wording is synthetic but written to resemble the real corpus, because a
matcher tuned on toy strings tells you nothing about a field of two hundred
consultations where half of them are about amending some law or other.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from apps.legal_work.archived_topic_matching import (
    ARCHIVE_AUTO_MATCH_SCORE,
    ARCHIVE_MATCHER_VERSION,
    ARCHIVE_MINIMUM_MARGIN,
    ARCHIVE_PLAUSIBLE_SCORE,
    EVIDENCE_NO_RARE_OVERLAP,
    match_archive,
)
from apps.legal_work.current_topic_matching import (
    AUTO_MATCH_SCORE,
    EVIDENCE_DEADLINE_CONFLICT,
    EVIDENCE_DEADLINE_EXACT,
    EVIDENCE_GENERIC_ONLY,
    EVIDENCE_IDENTIFIER_CONFLICT,
    EVIDENCE_IMPOSSIBLE_CHRONOLOGY,
    EVIDENCE_NARROW_MARGIN,
    EVIDENCE_NO_CANDIDATES,
    EVIDENCE_ORGANIZATION_MATCH,
    EVIDENCE_UNIQUE_TOKEN,
    MATCHER_VERSION,
    MINIMUM_MARGIN,
    PLAUSIBLE_SCORE,
)
from apps.legal_work.models import DetailStatus, MatchDecision

PUBLISHED = dt.date(2026, 3, 10)
DEADLINE = dt.date(2026, 4, 1)


@dataclass
class Archived:
    """Stands in for an `ArchivedTopicItem` without needing a row."""

    pk: int
    content_key: str
    title: str
    listing_summary: str = ""
    body_text: str = ""
    detail_title: str = ""
    published_date: dt.date | None = PUBLISHED
    feedback_deadline: dt.date | None = DEADLINE
    named_organization: str = "Kliimaministeerium"
    detail_status: str = DetailStatus.HYDRATED
    is_present: bool = True
    canonical_url: str = ""

    def __post_init__(self):
        if not self.canonical_url:
            self.canonical_url = f"https://www.koda.ee/et/meie-moju/hetkel-kasil/{self.content_key}"

    @property
    def is_matchable(self) -> bool:
        return self.detail_status == DetailStatus.HYDRATED and self.is_present


@dataclass
class Record:
    """Stands in for a consultation-eligible `LegalWorkItem`."""

    pk: int
    topic: str
    act_type: str = "Seaduse eelnõu"
    received_date: dt.date | None = PUBLISHED
    deadline_date: dt.date | None = DEADLINE
    recipient: str = "Kliimaministeerium"


def packaging(**overrides) -> Archived:
    defaults = {
        "pk": 1,
        "content_key": "pakend",
        "title": "Mida arvad plaanitavatest pakendiseaduse muudatustest?",
        "listing_summary": "Kliimaministeerium on koostanud eelnõu kaasamüügipakendite kohta.",
        "body_text": (
            "Pakendiseaduse muutmise seaduse eelnõu eesmärk on suurendada "
            "ringmajanduse turgu korduskasutuspakendite kasutamisel."
        ),
    }
    return Archived(**{**defaults, **overrides})


def tourism(**overrides) -> Archived:
    defaults = {
        "pk": 2,
        "content_key": "turism",
        "title": "Mida arvad plaanist täpsustada lühiajalise üüri reegleid?",
        "listing_summary": "Majandusministeerium on koostanud väljatöötamiskavatsuse.",
        "body_text": (
            "Turismiseaduse väljatöötamiskavatsus soovib määratleda, millal on "
            "lühiajaline üüriteenus käsitatav majutusteenusena."
        ),
        "named_organization": "Majandus- ja Kommunikatsiooniministeerium",
    }
    return Archived(**{**defaults, **overrides})


def waste(**overrides) -> Archived:
    defaults = {
        "pk": 3,
        "content_key": "jaatme",
        "title": "Mida arvad plaanitavatest muudatustest jäätmeseaduses?",
        "listing_summary": "Kliimaministeerium on koostanud eelnõu.",
        "body_text": "Jäätmeseaduse muutmise seaduse eelnõu käsitleb jäätmete liigiti kogumist.",
    }
    return Archived(**{**defaults, **overrides})


def only(record, archived, **kwargs):
    outcomes = match_archive([record], archived, **kwargs)
    assert len(outcomes) == 1
    return outcomes[0]


# -- separation from the current matcher ------------------------------------


def test_the_archive_matcher_has_its_own_version():
    assert ARCHIVE_MATCHER_VERSION.startswith("archive-")
    assert ARCHIVE_MATCHER_VERSION != MATCHER_VERSION


def test_the_archive_thresholds_are_stricter_than_the_current_ones():
    """Not copied. A larger field means the same score is weaker evidence."""
    assert ARCHIVE_AUTO_MATCH_SCORE > AUTO_MATCH_SCORE
    assert ARCHIVE_PLAUSIBLE_SCORE > PLAUSIBLE_SCORE
    assert ARCHIVE_MINIMUM_MARGIN > MINIMUM_MARGIN


def test_the_two_matchers_do_not_share_a_corpus():
    """The idf table is built from the archive candidates handed in, and no others."""
    import inspect

    from apps.legal_work import archived_topic_matching

    source = inspect.getsource(archived_topic_matching.match_archive)

    assert "archive_items" in source
    assert "CurrentTopicItem" not in source


def test_the_archive_weights_are_independently_named():
    from apps.legal_work import archived_topic_matching as archive
    from apps.legal_work import current_topic_matching as current

    archive_names = {n for n in dir(archive) if n.startswith("ARCHIVE_WEIGHT_")}
    assert len(archive_names) == 5
    # The current matcher's own constants are untouched by this module.
    assert current.WEIGHT_CHARACTER_NGRAM == 0.30
    assert current.WEIGHT_RARITY_COVERAGE == 0.30


# -- what may and may not be a candidate ------------------------------------


def test_an_index_only_entry_can_never_match():
    """No body, no date, no organisation — nothing to judge honestly."""
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        [packaging(detail_status=DetailStatus.PENDING, body_text="")],
    )

    assert outcome.decision == MatchDecision.UNMATCHED
    assert outcome.best_candidate_id is None
    assert outcome.evidence_codes == [EVIDENCE_NO_CANDIDATES]


def test_a_failed_detail_entry_can_never_match():
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        [packaging(detail_status=DetailStatus.FAILED)],
    )

    assert outcome.decision == MatchDecision.UNMATCHED
    assert outcome.best_candidate_id is None


def test_an_absent_entry_can_never_match():
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        [packaging(is_present=False)],
    )

    assert outcome.decision == MatchDecision.UNMATCHED


def test_a_url_still_on_the_current_listing_is_excluded():
    """Current listing ownership wins; the archive never re-judges the same page."""
    candidate = packaging()
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        [candidate],
        excluded_urls=frozenset({candidate.canonical_url}),
    )

    assert outcome.decision == MatchDecision.UNMATCHED
    assert outcome.best_candidate_id is None


def test_excluding_one_url_leaves_the_others_available():
    right, wrong = packaging(), tourism()
    outcome = only(
        Record(pk=10, topic="Turismiseaduse muutmise väljatöötamiskavatsus", deadline_date=None),
        [right, wrong],
        excluded_urls=frozenset({right.canonical_url}),
    )

    assert outcome.best_candidate_id == wrong.pk


# -- the pairs that should match --------------------------------------------


def test_an_exact_instrument_match_is_matched():
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        [packaging(), tourism(), waste()],
    )

    assert outcome.decision == MatchDecision.MATCHED
    assert outcome.best_candidate_id == 1
    assert outcome.score >= ARCHIVE_AUTO_MATCH_SCORE
    assert outcome.score_margin >= ARCHIVE_MINIMUM_MARGIN


def test_an_editorial_title_with_the_act_named_only_in_the_body_still_matches():
    """The headline asks a question; the instrument is named in the article.

    The record carries the ministry the page names, as the real pair does — the
    workbook's recipient and the page's drafter agree here.
    """
    outcome = only(
        Record(
            pk=10,
            topic="Turismiseaduse muutmise väljatöötamiskavatsus",
            deadline_date=None,
            recipient="Majandus- ja Kommunikatsiooniministeerium",
        ),
        [tourism(), packaging(), waste()],
    )

    assert outcome.decision == MatchDecision.MATCHED
    assert outcome.best_candidate_id == 2


def test_a_matching_deadline_strengthens_the_pair():
    agreeing = only(Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"), [packaging()])
    silent = only(
        Record(pk=11, topic="Pakendiseaduse muutmise seaduse eelnõu", deadline_date=None),
        [packaging()],
    )

    assert EVIDENCE_DEADLINE_EXACT in agreeing.evidence_codes
    assert agreeing.score > silent.score


def test_a_matching_organization_strengthens_the_pair():
    agreeing = only(Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"), [packaging()])
    silent = only(
        Record(pk=11, topic="Pakendiseaduse muutmise seaduse eelnõu", recipient=""), [packaging()]
    )

    assert EVIDENCE_ORGANIZATION_MATCH in agreeing.evidence_codes
    assert agreeing.score > silent.score


# -- refusals ----------------------------------------------------------------


def test_a_conflicting_deadline_blocks():
    outcome = only(
        Record(
            pk=10,
            topic="Pakendiseaduse muutmise seaduse eelnõu",
            deadline_date=dt.date(2026, 11, 1),
        ),
        [packaging()],
    )

    assert EVIDENCE_DEADLINE_CONFLICT in outcome.evidence_codes
    assert outcome.decision == MatchDecision.UNMATCHED


def test_conflicting_identifiers_block_in_the_archive():
    """Stricter than the current matcher, because near-identical titles abound."""
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu 512 SE"),
        [packaging(body_text="Pakendiseaduse eelnõu 998 SE menetlus jätkus.")],
    )

    assert EVIDENCE_IDENTIFIER_CONFLICT in outcome.evidence_codes
    assert outcome.decision == MatchDecision.UNMATCHED


def test_an_impossible_chronology_blocks():
    outcome = only(
        Record(
            pk=10,
            topic="Pakendiseaduse muutmise seaduse eelnõu",
            received_date=dt.date(2027, 6, 1),
            deadline_date=None,
        ),
        [packaging()],
    )

    assert EVIDENCE_IMPOSSIBLE_CHRONOLOGY in outcome.evidence_codes
    assert outcome.decision == MatchDecision.UNMATCHED


def test_sharing_no_uncommon_word_blocks_even_at_a_high_score():
    """The archive's own floor. Over a large corpus, generic agreement is noise."""
    outcome = only(
        Record(
            pk=10,
            topic="Seaduse muutmise seaduse eelnõu",
            recipient="",
            deadline_date=None,
        ),
        [packaging(named_organization="")],
    )

    assert EVIDENCE_NO_RARE_OVERLAP in outcome.evidence_codes
    assert outcome.decision == MatchDecision.UNMATCHED


def test_only_generic_overlap_blocks():
    outcome = only(
        Record(
            pk=10,
            topic="Käibemaksuseaduse muutmise seaduse eelnõu",
            recipient="",
            deadline_date=None,
        ),
        [packaging(named_organization="")],
    )

    assert {EVIDENCE_GENERIC_ONLY, EVIDENCE_NO_RARE_OVERLAP} & set(outcome.evidence_codes)
    assert outcome.decision == MatchDecision.UNMATCHED


def test_an_organization_mismatch_alone_does_not_create_a_match():
    outcome = only(
        Record(
            pk=10,
            topic="Notariaadiseaduse muutmise seaduse eelnõu",
            recipient="Justiitsministeerium",
            deadline_date=None,
        ),
        [packaging()],
    )

    assert outcome.decision == MatchDecision.UNMATCHED


# -- deciding between candidates --------------------------------------------


def test_two_effectively_tied_candidates_are_ambiguous():
    outcome = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        [packaging(), packaging(pk=9, content_key="pakend2")],
    )

    assert outcome.decision == MatchDecision.AMBIGUOUS
    assert EVIDENCE_NARROW_MARGIN in outcome.evidence_codes
    assert outcome.score_margin < ARCHIVE_MINIMUM_MARGIN


def test_a_weak_candidate_is_unmatched():
    outcome = only(
        Record(
            pk=10,
            topic="Notariaadiseaduse muutmise seaduse eelnõu",
            recipient="",
            deadline_date=None,
        ),
        [packaging(named_organization=""), tourism(named_organization="")],
    )

    assert outcome.decision == MatchDecision.UNMATCHED
    assert outcome.score < ARCHIVE_PLAUSIBLE_SCORE


def test_an_empty_archive_leaves_every_record_unmatched():
    outcome = only(Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"), [])

    assert outcome.decision == MatchDecision.UNMATCHED
    assert outcome.evidence_codes == [EVIDENCE_NO_CANDIDATES]


def test_every_considered_record_gets_exactly_one_decision():
    records = [
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        Record(pk=11, topic="Turismiseaduse muutmise väljatöötamiskavatsus"),
        Record(pk=12, topic="Notariaadiseaduse muutmise seaduse eelnõu"),
    ]

    outcomes = match_archive(records, [packaging(), tourism(), waste()])

    assert len(outcomes) == 3
    assert sorted(o.legal_item_id for o in outcomes) == [10, 11, 12]
    assert all(o.decision in MatchDecision.values for o in outcomes)


def test_the_uncommon_token_signal_separates_the_right_pair():
    right = only(
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        [packaging(), tourism(), waste()],
    )
    wrong = only(
        Record(pk=11, topic="Kollektiivlepingu seaduse muutmise seaduse eelnõu"),
        [packaging(), tourism(), waste()],
    )

    assert EVIDENCE_UNIQUE_TOKEN in right.evidence_codes
    assert right.score > wrong.score


# -- determinism -------------------------------------------------------------


def test_the_same_inputs_produce_identical_results_whatever_the_order():
    records = [Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu")]
    candidates = [packaging(), tourism(), waste()]

    first = match_archive(records, candidates)
    second = match_archive(records, list(reversed(candidates)))

    assert [vars(o) for o in first] == [vars(o) for o in second]


def test_scores_stay_on_the_documented_scale():
    records = [
        Record(pk=10, topic="Pakendiseaduse muutmise seaduse eelnõu"),
        Record(pk=11, topic="Notariaadiseaduse muutmise seaduse eelnõu"),
    ]

    for outcome in match_archive(records, [packaging(), tourism(), waste()]):
        assert 0 <= outcome.score <= 100
        assert 0 <= outcome.runner_up_score <= outcome.score
        assert outcome.score_margin == outcome.score - outcome.runner_up_score
        assert outcome.evidence_codes == sorted(set(outcome.evidence_codes))


@pytest.mark.parametrize("bad", [DetailStatus.PENDING, DetailStatus.FAILED])
def test_no_unhydrated_status_is_ever_matchable(bad):
    assert packaging(detail_status=bad).is_matchable is False
