"""Making a workbook event name and a public page title comparable.

A separate normaliser from the legal-work one, with its own version, because
the two vocabularies share nothing but the language. A legal page wraps an
instrument in an invitation — *"Mida arvad plaanitavatest muudatustest?"* — while
an event page names a seminar. The scaffolding to remove is different, and a
shared stop list would remove the wrong words in both directions.

Every rule below was measured against the events that already carry a workbook
URL, which is a ground-truth set: the workbook itself says which page belongs to
which event. Of the pairs available, roughly two thirds have byte-identical
titles and the rest differ in the specific ways handled here. Nothing is
included on the strength of seeming plausible.

## What the measurement found

**`š` and `ž` are dropped, the Estonian vowels never are.** The workbook writes
*Arbitraazikohtu* and *Tsehhi* where the page writes *Arbitraažikohtu* and
*Tšehhi*. Both letters live almost only in loanwords and both are awkward to
type; `õäöü` are ordinary Estonian and appear correctly on both sides. So the
equivalence here is deliberately **narrow** — `š→s` and `ž→z` and nothing else.
Folding all diacritics would merge `ohutus` and `õhutus`, which mean different
things, and the legal-work normaliser is right to refuse it.

**`JÄRELVAATAMINE:` is public-side-only.** Koda.ee prefixes a page with it once
a recording is available, and it replaces whatever type prefix the title had.
The workbook never uses the word. Left in, an event's own page stops matching it
the week after it happens.

**Type prefixes disagree across the boundary.** The workbook writes
*"Webinar: Eriolukorra maksuleevendused"* where the page writes *"Eriolukorra
maksuleevendused"*, and *"Hommikuseminar: X"* where the page says
*"JÄRELVAATAMINE: X"*. So a leading word followed by a colon is dropped from
both sides. This does lose some series identity — *Hommikuseminar* and
*Ärihommikusöök* are real recurring series — which is affordable only because
the date does the discriminating; see the matcher for that reasoning.

## What is deliberately not here

No stemming, no lemmatiser, no service, and no list of event titles. Estonian
inflection is handled by character n-grams, as it is for legal text. No event,
page or slug is ever special-cased.
"""

from __future__ import annotations

import re

from apps.core.text_folding import TOKEN_PATTERN, character_ngrams, fold

#: Folded into the matcher version, so changing any rule here is visibly a
#: different matcher and produces a new snapshot rather than silently altering
#: what an old score meant.
EVENT_NORMALISER_VERSION = "1.0"

#: The two letters the workbook loses. Measured, not assumed: across the
#: ground-truth pairs these are the only characters that differed by a
#: diacritic, and the Estonian vowels never did.
LOANWORD_MARKS = str.maketrans({"š": "s", "ž": "z"})

#: A leading type or state word followed by a colon. The word itself is not
#: matched against a list — any single leading token before a colon goes, so a
#: type the Chamber introduces next year needs no code change.
_LEADING_LABEL = re.compile(r"^\s*[^\W_]{3,}\s*:\s*")

#: Publication state, not subject. Public-side only, and it appears *before* the
#: type prefix, so it is removed first and can itself expose another label.
PUBLICATION_LABELS: frozenset[str] = frozenset({"järelvaatamine", "jarelvaatamine"})

#: Words too common across Chamber events to tell two of them apart. Kept very
#: short on purpose: an over-eager list removes the nouns that decide a match.
#: A word earns its place only by being grammatical rather than descriptive.
STOP_TOKENS: frozenset[str] = frozenset(
    {
        "ja",
        "ning",
        "või",
        "voi",
        "kui",
        "on",
        "ei",
        "the",
        "and",
        "for",
        "in",
        "of",
        "with",
    }
)


def relax_loanword_marks(value: str) -> str:
    """Fold `š`/`ž` only. Estonian vowels are left exactly as written."""
    return value.translate(LOANWORD_MARKS)


def strip_labels(value: str) -> str:
    """Remove publication state and a leading type label.

    Applied repeatedly, because a page can carry both: *"JÄRELVAATAMINE:
    Hommikuseminar: Turundamine TikTokis"* has to lose two before the subject
    is reached. Bounded so a pathological title cannot spin.
    """
    text = value
    for _ in range(3):
        stripped = _LEADING_LABEL.sub("", text, count=1)
        if stripped == text:
            break
        # Only drop the label if something is left. A title that *is* its label
        # keeps it, because an empty string matches everything.
        if not stripped.strip():
            break
        text = stripped
    return text.strip()


def normalise(value: str) -> str:
    """The comparable form of an event name or page title."""
    folded = relax_loanword_marks(fold(value))
    for label in PUBLICATION_LABELS:
        # The label is removed wherever it leads, before the general rule, since
        # it stands in front of the type prefix rather than replacing it.
        if folded.startswith(label):
            folded = folded[len(label) :].lstrip(": ").strip()
    return strip_labels(folded)


def tokens(value: str) -> tuple[str, ...]:
    """Content tokens in order, with grammatical words removed."""
    return tuple(
        token
        for token in TOKEN_PATTERN.findall(normalise(value))
        if token not in STOP_TOKENS and len(token) > 1
    )


def token_set(value: str) -> frozenset[str]:
    return frozenset(tokens(value))


def ngrams(value: str) -> frozenset[str]:
    """Character n-grams, which is how Estonian inflection survives comparison."""
    return character_ngrams(tokens(value))
