"""The typography rules every matcher shares.

This file exists mostly for one failure mode. Three of the punctuation map's
keys are space characters, and a space character is indistinguishable from an
ordinary space on screen, in a diff, and in review. A tool that normalises
Unicode on the way into the file collapses all three into one key — leaving a
line that still *looks* like it folds no-break spaces while doing nothing. That
happened once while this module was being written.

So the keys are asserted by codepoint, not by appearance.
"""

from __future__ import annotations

import pytest

from apps.core.text_folding import PUNCTUATION_MAP, character_ngrams, fold

NO_BREAK_SPACE = chr(0x00A0)
NARROW_NO_BREAK_SPACE = chr(0x202F)
THIN_SPACE = chr(0x2009)


def test_every_punctuation_key_is_distinct():
    """17 rules, 17 keys. A collapsed duplicate silently drops a rule."""
    assert len(PUNCTUATION_MAP) == 17


@pytest.mark.parametrize(
    "space",
    [NO_BREAK_SPACE, NARROW_NO_BREAK_SPACE, THIN_SPACE],
    ids=["no-break", "narrow-no-break", "thin"],
)
def test_each_exotic_space_folds_to_an_ordinary_one(space):
    assert fold(f"teadus{space}ja{space}arendus") == "teadus ja arendus"


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("„Finantsanalüüs“", '"finantsanalüüs"'),
        ("«Finantsanalüüs»", '"finantsanalüüs"'),
        ("ettevõtja’s", "ettevõtja's"),
        ("teadus–ja", "teadus-ja"),
        ("teadus—ja", "teadus-ja"),
        ("teadus‑ja", "teadus-ja"),
    ],
)
def test_the_same_phrase_typed_two_ways_is_one_string(written, expected):
    assert fold(written) == expected


def test_estonian_diacritics_survive():
    """Stripping them would merge two different words."""
    assert fold("ohutus") != fold("õhutus")
    assert fold("ÕHUTUS") == "õhutus"


def test_a_decomposed_letter_matches_its_precomposed_form():
    assert fold("ä") == fold("ä")


def test_ngrams_let_an_inflected_word_match_its_stem():
    """The reason there is no morphological analyser in this repository."""
    stem = character_ngrams(["pakendiseadus"])
    inflected = character_ngrams(["pakendiseaduse"])

    assert stem & inflected
    assert len(stem - inflected) <= 1


def test_a_short_token_still_yields_something_comparable():
    assert character_ngrams(["el"]) == frozenset({"el"})


def test_nothing_yields_nothing():
    assert fold("") == ""
    assert character_ngrams([]) == frozenset()
