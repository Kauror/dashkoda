"""Deterministic, versioned normalisation of Estonian legal and editorial text.

Two vocabularies meet in the matcher and they do not speak the same way. The
workbook names an instrument — *"pakendiseaduse muutmise seaduse eelnõu"* — and
the public page asks a question about it — *"Mida arvad plaanitavatest
pakendiseaduse muudatustest?"*. What the two share is the uncommon noun in the
middle, and everything here exists to make that noun comparable while the
editorial scaffolding around it stops competing for attention.

Nothing here stems, lemmatises or calls a service. Estonian inflection is
handled downstream by character n-grams, which is why ``pakendiseadus`` and
``pakendiseaduse`` compare well without a morphological analyser this
repository would then have to keep correct.

Everything is versioned. :data:`NORMALISER_VERSION` is folded into the matcher
version, so changing a rule below is visibly a different matcher and produces a
new match snapshot rather than silently altering the meaning of an old score.
"""

from __future__ import annotations

import re
import unicodedata

NORMALISER_VERSION = "1.0"

# Estonian typography, folded to ASCII equivalents so a curly quote and a
# straight one cannot make two identical phrases look different. Estonian opens
# a quotation low („) and closes it high (“), and both forms appear on the same
# Koda.ee page.
_PUNCTUATION_MAP = {
    "„": '"', "“": '"', "”": '"', "«": '"', "»": '"',
    "‘": "'", "’": "'", "′": "'",
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    " ": " ", " ": " ", " ": " ",
    "…": " ",
}  # fmt: skip

_PUNCTUATION_TABLE = str.maketrans(_PUNCTUATION_MAP)

# A token is a run of letters, digits and the two characters Estonian legal
# writing uses inside a single term: the hyphen of "teadus- ja arendustegevus"
# and the full stop of an ordinal. Everything else separates.
_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:[-.][^\W_]+)*", re.UNICODE)

# Editorial scaffolding. These are the phrases the Chamber wraps around every
# consultation — the invitation, not the subject — and they appear on nearly
# every page, so they carry no information about *which* topic a page is about.
#
# Removed as whole phrases before tokenisation, so "eelnõu kohta" disappears
# while "eelnõu" standing on its own does not: the word is a genuine legal noun
# and only the stock phrase is boilerplate.
DOWN_WEIGHTED_PHRASES: tuple[str, ...] = (
    "mida arvad",
    "anna teada",
    "anna meile teada",
    "anna meile hiljemalt",
    "anna hiljemalt",
    "jaga oma motteid",
    "jaga motteid",
    "jaga mõtteid",
    "jaga oma mõtteid",
    "kas toetad",
    "plaanitavad muudatused",
    "plaanitavatest muudatustest",
    "eelnou kohta",
    "eelnõu kohta",
    "vasta veebikusitlusele",
    "vasta veebiküsitlusele",
    "anna oma tagasiside",
    "enda poolne tagasiside",
)

# Single tokens too common in this corpus to discriminate between two topics.
# Deliberately short: an over-eager stop list removes the very nouns that decide
# a match, so a word earns its place here only by appearing on essentially every
# page. Legal names, identifiers, numbers and acronyms are never listed.
STOP_TOKENS: frozenset[str] = frozenset(
    {
        # Grammatical words.
        "ja",
        "ning",
        "või",
        "voi",
        "kui",
        "kas",
        "on",
        "ei",
        "ka",
        "et",
        "see",
        "selle",
        "seda",
        "need",
        "nende",
        "oma",
        "mis",
        "mida",
        "millega",
        "kes",
        "keda",
        "kelle",
        "kus",
        "millal",
        "mille",
        "kuidas",
        "juures",
        "ka",
        "veel",
        "nii",
        "siis",
        "kuid",
        "aga",
        "ainult",
        "üle",
        "ule",
        "alla",
        "kohta",
        "puhul",
        "jaoks",
        "poolt",
        "vastu",
        "kaudu",
        "koos",
        "pärast",
        "parast",
        "enne",
        "vahel",
        "ajal",
        "tuleks",
        "peab",
        "saab",
        "tuleb",
        "võib",
        "voib",
        "olema",
        "olla",
        "teatud",
        "muu",
        "muud",
        "hulgas",
        "sh",
        "nt",
        # The consultation's own vocabulary, present on every page of both
        # sources and therefore useless for telling two of them apart.
        "arvad",
        "arvamus",
        "arvamust",
        "teada",
        "anna",
        "meile",
        "hiljemalt",
        "jaga",
        "motteid",
        "mõtteid",
        "tagasiside",
        "tagasisidet",
        "kommentaar",
        "plaanitav",
        "plaanitavad",
        "plaanitavate",
        "plaanitavatest",
        "muudatus",
        "muudatused",
        "muudatuste",
        "muudatustest",
        "muudatusi",
        "soovib",
        "koostanud",
        "valminud",
        "kehtestab",
        "algatanud",
    }
)

# Tokens that are common enough to be poor evidence on their own but too
# meaningful to discard: a match resting only on these is not a match, which is
# what the matcher's `generic-overlap-only` contradiction checks.
GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "seadus",
        "seaduse",
        "seadust",
        "seaduses",
        "seadusega",
        "eelnou",
        "eelnõu",
        "eelnoud",
        "eelnõud",
        "maarus",
        "määrus",
        "maaruse",
        "määruse",
        "maarust",
        "määrust",
        "muutmise",
        "muutmine",
        "muutmiseks",
        "ministeerium",
        "ministeeriumi",
        "ministeeriumis",
        "komisjon",
        "komisjoni",
        "euroopa",
        "liidu",
        "eesti",
        "riigi",
        "ettevotja",
        "ettevõtja",
        "ettevotjate",
        "ettevõtjate",
        "kord",
        "korra",
        "korras",
        "nouded",
        "nõuded",
        "nouete",
        "nõuete",
        "valjatootamiskavatsus",
        "väljatöötamiskavatsus",
        "direktiiv",
        "direktiivi",
    }
)

# Uppercase runs of this length or more are treated as acronyms and preserved
# through case folding as a separate signal (FATCA, OECD, CARF, KMS, TSD).
MIN_ACRONYM_LENGTH = 3
MAX_ACRONYM_LENGTH = 12

_ACRONYM_PATTERN = re.compile(rf"\b[A-ZÄÖÜÕŠŽ]{{{MIN_ACRONYM_LENGTH},{MAX_ACRONYM_LENGTH}}}\b")

# Formal instrument identifiers. Narrow on purpose: `123 SE` and `45 OE` are
# Riigikogu proceeding numbers and a Riigi Teataja reference is a stable act
# identifier. A bare number is **not** an identifier — the pages write "eelnõu
# punktid 1, 2 ja 4", which are paragraph references, and treating those as
# identifiers would manufacture contradictions out of ordinary prose.
_IDENTIFIER_PATTERNS = (
    re.compile(r"\b(\d{1,4})\s*(SE|OE|UA)\b"),
    re.compile(r"\bRT\s*([IV]+),?\s*(\d{2}\.\d{2}\.\d{4}),?\s*(\d+)\b"),
)


def fold(value: str) -> str:
    """Unicode-normalise, fold case and collapse whitespace and punctuation.

    NFC first, so a precomposed ``ä`` and a decomposed one are the same string
    before anything else looks at them. Estonian diacritics are **kept**:
    stripping them would merge ``ohutus`` and ``õhutus``, which mean different
    things.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFC", value)
    text = text.translate(_PUNCTUATION_TABLE)
    text = text.casefold()
    return " ".join(text.split())


def strip_boilerplate(value: str) -> str:
    """Remove the stock editorial phrases, leaving the subject behind."""
    text = value
    for phrase in DOWN_WEIGHTED_PHRASES:
        folded_phrase = fold(phrase)
        if folded_phrase:
            text = text.replace(folded_phrase, " ")
    return " ".join(text.split())


def tokenize(value: str) -> tuple[str, ...]:
    """Content tokens of `value`, in order, with stop words removed."""
    folded = strip_boilerplate(fold(value))
    return tuple(
        token
        for token in _TOKEN_PATTERN.findall(folded)
        if token not in STOP_TOKENS and len(token) > 1
    )


def significant_tokens(value: str) -> frozenset[str]:
    """Tokens that could carry a match: content tokens minus the generic ones."""
    return frozenset(tokenize(value)) - GENERIC_TOKENS


def acronyms(value: str) -> frozenset[str]:
    """Uppercase acronyms, read before case folding destroys them."""
    if not value:
        return frozenset()
    text = unicodedata.normalize("NFC", value)
    return frozenset(_ACRONYM_PATTERN.findall(text))


def identifiers(value: str) -> frozenset[str]:
    """Formal instrument identifiers stated in `value`.

    Returns an empty set for the overwhelming majority of real pages, which is
    correct: these documents mostly do not carry a proceeding number, and an
    identifier invented from a paragraph reference would be worse than none.
    """
    if not value:
        return frozenset()
    text = unicodedata.normalize("NFC", value)
    found: set[str] = set()
    for pattern in _IDENTIFIER_PATTERNS:
        for match in pattern.findall(text):
            parts = match if isinstance(match, tuple) else (match,)
            found.add(" ".join(part.strip() for part in parts if part).upper())
    return frozenset(found)


def character_ngrams(value: str, *, size: int = 4) -> frozenset[str]:
    """Character n-grams over the folded, token-joined text.

    This is what makes Estonian inflection survive comparison:
    ``pakendiseadus`` and ``pakendiseaduse`` share every 4-gram but the last,
    while sharing not one whole token.
    """
    joined = " ".join(tokenize(value))
    if len(joined) < size:
        return frozenset({joined} if joined else set())
    return frozenset(joined[index : index + size] for index in range(len(joined) - size + 1))
