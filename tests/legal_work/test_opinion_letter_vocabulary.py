"""The stock opening of an outgoing opinion letter is not evidence.

Every letter the Chamber sends opens by naming the act it performs — "Arvamuse
esitamine <instrument> kohta", sometimes "Arvamuse avaldamine …". `arvamus`,
`arvamust` and `kohta` were already stop words; the genitive `arvamuse` and the
verbal nouns were not, and the genitive is the form that actually occurs.

Measured over the 152-document opinion catalogue, counting a document once
whether the word appears in its filename subject or in the subject parsed from
its own header:

    arvamuse     109 / 152   71.7%
    esitamine     73 / 152   48.0%
    avaldamine    35 / 152   23.0%
    arvamus        9 / 152    5.9%   (already a stop word)
    arvamust       5 / 152    3.3%   (already a stop word)

The cost of leaving them in was not theoretical. A document filed under the
wrong name — real catalogue entries 440 and 441, the same letter twice, the
second carrying a name describing a letter it does not contain — shared exactly
`{arvamuse, esitamine}` with its partner, which is enough to look related and
not enough to be. The names below are those real names.
"""

from __future__ import annotations

import pytest

from apps.legal_work.opinion_matching import MATCHER_VERSION
from apps.legal_work.text_normalisation import (
    NORMALISER_VERSION,
    STOP_TOKENS,
    significant_tokens,
)

#: Real filename subjects. 440 and 441 are byte-identical letters, so 441's name
#: describes correspondence its own text does not contain; 360 and 380 are a
#: genuine proposal and the reminder that re-sent it.
MISFILED_PAIR = (
    'Arvamuse esitamine majandus- ja taristuministri määruse "Nõuded ohtliku '
    "ja suurõnnetuse ohuga e",
    "Arvamuse esitamine töövaidluse lahendamise seaduse väljatöötamiskavatsuse kohta",
)
LEGITIMATE_RESEND_PAIR = (
    "Ettepanek maamaksuseaduse muutmiseks",
    "Meeldetuletus seoses maamaksuseaduse muutmise ettepanekuga - 23 04 2025 "
    "Ettepanek maamaksuseaduse muutmise",
)


@pytest.mark.parametrize(
    "opening",
    [
        "Arvamuse esitamine",
        "Arvamuse avaldamine",
        "Arvamuse esitamise kohta",
    ],
)
def test_the_stock_opening_carries_no_evidence(opening):
    assert significant_tokens(opening) == frozenset()


def test_the_instrument_survives_the_opening():
    """Only the scaffolding goes. The nouns that decide a match stay."""
    subject = "Arvamuse esitamine töövaidluse lahendamise seaduse väljatöötamiskavatsuse kohta"
    assert significant_tokens(subject) == {
        "töövaidluse",
        "lahendamise",
        "väljatöötamiskavatsuse",
    }


def test_a_misfiled_name_no_longer_resembles_its_partner():
    left, right = MISFILED_PAIR
    assert significant_tokens(left) & significant_tokens(right) == frozenset()


def test_a_genuine_resend_still_resembles_its_original():
    """The change must not flatten every pair — only the empty openings."""
    left, right = LEGITIMATE_RESEND_PAIR
    assert "maamaksuseaduse" in significant_tokens(left) & significant_tokens(right)


def test_the_already_stopped_siblings_stay_stopped():
    """Guards the inflections that were correct before this change."""
    assert {"arvamus", "arvamust", "kohta"} <= STOP_TOKENS


def test_the_vocabulary_is_folded_into_the_matcher_version():
    """Editing the vocabulary above must be visible as a different matcher."""
    assert f"norm{NORMALISER_VERSION}" in MATCHER_VERSION
