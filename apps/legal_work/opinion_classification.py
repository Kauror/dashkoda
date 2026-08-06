"""What kind of document this is, decided from words rather than judgement.

The distinction that matters downstream is not editorial: an annex or a
comparison table must never become the document a legal topic links to, however
well its text matches. Phase 2 enforces that, and this is where the label it
enforces comes from.

Every signal is a literal Estonian word or phrase, matched against the filename
subject and the first page. There is no model, no embedding and no learned
threshold — a classification can be read off the document by a person, which is
the only way "why is this an annex?" has an answer.

Estonian is agglutinative, so the vocabulary is matched on stems with a bounded
suffix rather than on whole words: `lisa` must also catch `lisas` and `lisade`
while never catching `lisaks` ("in addition"), which is why the exclusions are
as explicit as the inclusions.

`UNKNOWN` is a real answer. A document nobody can classify from its own words is
labelled unknown and simply never becomes a primary resource.
"""

from __future__ import annotations

import re
import unicodedata

from django.db import models


class DocumentClassification(models.TextChoices):
    OPINION = "opinion", "Arvamus"
    JOINT_OPINION = "joint_opinion", "Ühisarvamus"
    SUPPLEMENTARY_OPINION = "supplementary_opinion", "Täiendav arvamus"
    FOLLOW_UP = "follow_up", "Järelkiri"
    ANNEX = "annex", "Lisa"
    SUPPORTING_DOCUMENT = "supporting_document", "Tugidokument"
    UNKNOWN = "unknown", "Määramata"


# Classifications that may never be the primary document of a legal topic,
# whatever they score. Phase 2 reads this rather than restating the rule.
NEVER_PRIMARY = frozenset(
    {
        DocumentClassification.ANNEX,
        DocumentClassification.SUPPORTING_DOCUMENT,
        DocumentClassification.UNKNOWN,
    }
)

# How much of the first page is read. An opinion letter names itself in its
# heading; text this far down is body, and body is where quotations of *other*
# document types live.
HEADER_WINDOW = 1200


def _stem(pattern: str) -> re.Pattern[str]:
    """A stem plus up to three letters of Estonian inflection, on a word boundary."""
    return re.compile(rf"\b{pattern}\w{{0,3}}\b")


# Ordered most specific first: a joint supplementary opinion is joint, and a
# comparison table that mentions "arvamus" is still a table.
SUPPORTING_PATTERNS = (
    _stem("kooskõlastustabel"),
    _stem("kooskolastustabel"),
    _stem("seletuskiri"),
    _stem("märkuste tabel"),
    _stem("markuste tabel"),
    re.compile(r"\bvastuste\s+tabel\w{0,3}\b"),
)

ANNEX_PATTERNS = (
    # `lisa` as its own word, and the numbered forms a filename uses.
    re.compile(r"\blisa\s*\d+\b"),
    re.compile(r"\blisa\b(?!ks)"),
    re.compile(r"\blisad\w{0,3}\b"),
    _stem("manus"),
)

JOINT_PATTERNS = (
    _stem("ühispöördumine"),
    _stem("uhispoordumine"),
    re.compile(r"\bühine\s+arvamus\w{0,3}\b"),
    re.compile(r"\buhine\s+arvamus\w{0,3}\b"),
    re.compile(r"\bühis\w*\s*arvamus\w{0,3}\b"),
    re.compile(r"\bühiskiri\w{0,3}\b"),
)

SUPPLEMENTARY_PATTERNS = (
    re.compile(r"\btäiendav\w{0,3}\s+arvamus\w{0,3}\b"),
    re.compile(r"\btaiendav\w{0,3}\s+arvamus\w{0,3}\b"),
    re.compile(r"\btäpsustav\w{0,3}\s+arvamus\w{0,3}\b"),
    re.compile(r"\blisaarvamus\w{0,3}\b"),
)

FOLLOW_UP_PATTERNS = (
    _stem("järelkiri"),
    _stem("jarelkiri"),
    re.compile(r"\bkorduv\w{0,3}\s+arvamus\w{0,3}\b"),
    re.compile(r"\bvastuskiri\w{0,3}\b"),
    re.compile(r"\bmeeldetuletus\w{0,3}\b"),
)

OPINION_PATTERNS = (
    _stem("arvamus"),
    re.compile(r"\bettepanek\w{0,3}\b"),
    re.compile(r"\bseisukoht\w{0,3}\b"),
    re.compile(r"\bpöördumine\w{0,3}\b"),
    re.compile(r"\bpoordumine\w{0,3}\b"),
)


def normalise_for_classification(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value or "").casefold().split())


def classify_document(*, filename_subject: str, first_page_text: str = "") -> tuple[str, list[str]]:
    """Return the classification and the signal names that produced it.

    The filename subject is weighed first and alone where it is decisive: it is
    what a person typed to say what the document *is*, whereas the first page is
    what the document *says*, and a letter routinely quotes the names of other
    document types.
    """
    subject = normalise_for_classification(filename_subject)
    header = normalise_for_classification(first_page_text)[:HEADER_WINDOW]

    signals: list[str] = []

    def hit(patterns, haystack: str) -> bool:
        return any(pattern.search(haystack) for pattern in patterns)

    # 1 and 2. Supporting material and annexes — the two labels that permanently
    # bar a document from being a legal topic's primary resource.
    #
    # Both are decided from the **filename only**. The Chamber's naming
    # convention states them there ("... - Seletuskiri", "... - Lisa 1"), and
    # reading them from the first page instead was measured against the real
    # catalogue to demote genuine opinion letters: an opinion routinely discusses
    # the draft's explanatory memorandum or comparison table by name, and a
    # letter that *mentions* a `seletuskiri` is not one. Since the cost of a
    # false positive here is a link that can never appear, the looser signal is
    # not worth the two extra documents it would have caught.
    if hit(SUPPORTING_PATTERNS, subject):
        signals.append("supporting-vocabulary")
        return DocumentClassification.SUPPORTING_DOCUMENT, signals

    if hit(ANNEX_PATTERNS, subject):
        signals.append("annex-vocabulary")
        return DocumentClassification.ANNEX, signals

    # 3. Joint, then supplementary, then follow-up — each more specific than
    # the plain opinion they would otherwise fall into.
    for patterns, label, signal in (
        (JOINT_PATTERNS, DocumentClassification.JOINT_OPINION, "joint-vocabulary"),
        (
            SUPPLEMENTARY_PATTERNS,
            DocumentClassification.SUPPLEMENTARY_OPINION,
            "supplementary-vocabulary",
        ),
        (FOLLOW_UP_PATTERNS, DocumentClassification.FOLLOW_UP, "follow-up-vocabulary"),
    ):
        if hit(patterns, subject):
            signals.append(signal)
            return label, signals
        if hit(patterns, header):
            signals.append(f"{signal}-header")
            return label, signals

    # 4. An ordinary opinion.
    if hit(OPINION_PATTERNS, subject):
        signals.append("opinion-vocabulary")
        return DocumentClassification.OPINION, signals
    if hit(OPINION_PATTERNS, header):
        signals.append("opinion-vocabulary-header")
        return DocumentClassification.OPINION, signals

    return DocumentClassification.UNKNOWN, signals
