"""Estonian typography folding, shared by every matcher in this project.

Two matchers now compare Estonian text from Koda.ee — the legal-work matchers
and the event matcher — and they must agree about what "the same string" means.
A curly quote and a straight one, an en dash and a hyphen, a non-breaking space
and a space: if one matcher folded these and the other did not, two identical
phrases would compare as different in one place and the same in the other, and
nothing in either module would say why.

So the rules live here once. What builds *on top* of them is deliberately not
shared: stop words, boilerplate phrases and generic-token lists are specific to
a vocabulary, and a legal instrument and a training seminar do not have the same
scaffolding around their subject.

Diacritics are **kept**. Stripping them would merge `ohutus` (safety) and
`õhutus` (incitement), which mean different things.
"""

from __future__ import annotations

import re
import unicodedata

#: Estonian opens a quotation low („) and closes it high (“), and both forms
#: appear on the same Koda.ee page. Folded to ASCII equivalents so the same
#: phrase typed two ways is one string.
#:
#: The three space characters are written as escapes on purpose. As literals
#: they are indistinguishable from an ordinary space on screen, in a diff and in
#: a review — and a tool that normalises them away silently collapses three keys
#: into one, which removes the folding without removing the line that claims to
#: do it.
PUNCTUATION_MAP = {
    "„": '"', "“": '"', "”": '"', "«": '"', "»": '"',
    "‘": "'", "’": "'", "′": "'",
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    "…": " ",
    # Written as codepoints because a space character is indistinguishable
    # from an ordinary one on screen, in a diff and in review.
    chr(0x00A0): " ",  # no-break space
    chr(0x202F): " ",  # narrow no-break space
    chr(0x2009): " ",  # thin space
}  # fmt: skip

PUNCTUATION_TABLE = str.maketrans(PUNCTUATION_MAP)

#: A token is a run of letters and digits, plus the two characters Estonian
#: writing uses *inside* a single term: the hyphen of "teadus- ja
#: arendustegevus" and the full stop of an ordinal. Everything else separates.
TOKEN_PATTERN = re.compile(r"[^\W_]+(?:[-.][^\W_]+)*", re.UNICODE)


def fold(value: str) -> str:
    """Unicode-normalise, fold case, and collapse whitespace and punctuation.

    NFC first, so a precomposed `ä` and a decomposed one are the same string
    before anything else looks at them.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFC", value)
    text = text.translate(PUNCTUATION_TABLE)
    text = text.casefold()
    return " ".join(text.split())


def character_ngrams(tokens, *, size: int = 4) -> frozenset[str]:
    """Character n-grams over already-tokenised, space-joined text.

    This is what makes Estonian inflection survive comparison without a
    morphological analyser this repository would then have to keep correct:
    `pakendiseadus` and `pakendiseaduse` share every 4-gram but the last, while
    sharing not one whole token.

    Takes tokens rather than raw text because *which* tokens survive is the
    caller's decision — the two vocabularies strip different scaffolding — while
    the n-gram arithmetic is the same either way.
    """
    joined = " ".join(tokens)
    if len(joined) < size:
        return frozenset({joined} if joined else set())
    return frozenset(joined[index : index + size] for index in range(len(joined) - size + 1))
