"""Reading an opinion letter's own header, which is better evidence than its name.

Chamber opinions are Estonian official correspondence and carry a standard
reference block. Extraction flattens its two columns onto single lines, so what
the text actually looks like is:

    Rahandusministeerium              Teie 17.12.2019
                                      nr 1.1-10/4927-5
    Suur-Ameerika 1
    10122 Tallinn                     Meie 06.01.2020 nr 4/1
    Arvamuse esitamine maksualase teabevahetuse seaduse rakendusaktide
    eelnõude kohta
    Lugupeetud Martin Helme

`Meie <date> nr <reference>` is the Chamber's own outgoing date and letter
number — the single most reliable field in the document, and the one Phase 2
leans on hardest, because a filename date is whatever someone typed while the
outgoing number is what the Chamber's own registry issued.

Everything is anchored on those markers rather than on line positions. The
letterhead varies across the catalogue's six years — two different boilerplate
blocks appear, one of them five lines long — so counting lines from the top
would break on the older half of the corpus.

Nothing here corrects the document. When the header disagrees with the filename
both are stored and the disagreement becomes a warning code.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

# `Teie`/`Meie` may be followed by the date on the same line, and the number
# either on that line or the next. Both orders occur in the corpus.
OUR_BLOCK = re.compile(
    r"\bMeie\b[ \t]*(?P<date>\d{1,2}\.\d{1,2}\.\d{4})?[ \t]*"
    r"(?:nr\.?[ \t]*(?P<ref>[\w./\-]+))?",
    re.IGNORECASE,
)
THEIR_BLOCK = re.compile(
    r"\bTeie\b[ \t]*(?P<date>\d{1,2}\.\d{1,2}\.\d{4})?[ \t]*"
    r"(?:nr\.?[ \t]*(?P<ref>[\w./\-]+))?",
    re.IGNORECASE,
)
LOOSE_NUMBER = re.compile(r"^\s*nr\.?[ \t]*(?P<ref>[\w./\-]+)\s*$", re.IGNORECASE)

SALUTATION = re.compile(r"^\s*(lugupeetud|austatud|tere|head\b|hea\b)", re.IGNORECASE)

# Letterhead lines. Matched on content rather than position because the block
# changed shape twice over the catalogue's six years.
BOILERPLATE_MARKERS = (
    "kaubandus-tööstuskoda",
    "kaubandus-toostuskoda",
    "chamber of commerce",
    "toom-kooli",
    "koda@koda.ee",
    "www.koda.ee",
    "enterpriseeurope",
    "registrikood",
    "reg no",
    "tel:",
)

# An addressee line looks like an institution or a postal address, not prose.
RECIPIENT_HINTS = (
    "ministeerium",
    "amet",
    "kohus",
    "komisjon",
    "kantselei",
    "inspektsioon",
    "riigikogu",
    "valitsus",
    "keskus",
    "nõukogu",
    "noukogu",
    "liit",
    "koda",
)

MAX_HEADER_LINES = 40
MAX_SUBJECT_LINES = 6
MAX_SUBJECT_LENGTH = 500
MAX_RECIPIENT_LENGTH = 200
MAX_REFERENCE_LENGTH = 100

WARN_NO_HEADER = "header_not_found"
WARN_NO_DATE = "header_date_missing"
WARN_NO_RECIPIENT = "header_recipient_missing"
WARN_NO_SUBJECT = "header_subject_missing"
WARN_DATE_DISAGREES = "header_date_differs_from_filename"
WARN_RECIPIENT_DISAGREES = "header_recipient_differs_from_filename"


@dataclass(frozen=True)
class DocumentHeader:
    """What the letter says about itself. Every field is optional."""

    date: dt.date | None = None
    recipient: str = ""
    subject: str = ""
    our_reference: str = ""
    their_reference: str = ""
    their_date: dt.date | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not (self.date or self.recipient or self.subject or self.our_reference)


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        day, month, year = (int(part) for part in value.split("."))
        return dt.date(year, month, day)
    except ValueError, TypeError:
        return None


def _is_boilerplate(line: str) -> bool:
    low = line.casefold()
    if any(marker in low for marker in BOILERPLATE_MARKERS):
        return True
    # The long all-caps description of the Chamber. Judged by shape so a change
    # of wording does not reintroduce it.
    letters = [c for c in line if c.isalpha()]
    return len(letters) > 40 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.9


def parse_document_header(first_page_text: str) -> DocumentHeader:
    """Pull the reference block out of a first page."""
    if not first_page_text.strip():
        return DocumentHeader(warnings=(WARN_NO_HEADER,))

    lines = first_page_text.split("\n")[:MAX_HEADER_LINES]
    warnings: list[str] = []

    our_date = their_date = None
    our_reference = their_reference = ""
    our_line_index = -1

    for index, line in enumerate(lines):
        if our_date is None and not our_reference:
            match = OUR_BLOCK.search(line)
            if match and (match.group("date") or match.group("ref")):
                our_date = _parse_date(match.group("date"))
                our_reference = (match.group("ref") or "")[:MAX_REFERENCE_LENGTH]
                our_line_index = index
                # A number wrapped onto the next line.
                if not our_reference and index + 1 < len(lines):
                    loose = LOOSE_NUMBER.match(lines[index + 1])
                    if loose:
                        our_reference = loose.group("ref")[:MAX_REFERENCE_LENGTH]
                        our_line_index = index + 1
        if their_date is None and not their_reference:
            match = THEIR_BLOCK.search(line)
            if match and (match.group("date") or match.group("ref")):
                their_date = _parse_date(match.group("date"))
                their_reference = (match.group("ref") or "")[:MAX_REFERENCE_LENGTH]
                if not their_reference and index + 1 < len(lines):
                    loose = LOOSE_NUMBER.match(lines[index + 1])
                    if loose:
                        their_reference = loose.group("ref")[:MAX_REFERENCE_LENGTH]

    recipient = _find_recipient(lines)
    subject = _find_subject(lines, our_line_index)

    if our_date is None:
        warnings.append(WARN_NO_DATE)
    if not recipient:
        warnings.append(WARN_NO_RECIPIENT)
    if not subject:
        warnings.append(WARN_NO_SUBJECT)

    return DocumentHeader(
        date=our_date,
        recipient=recipient,
        subject=subject,
        our_reference=our_reference,
        their_reference=their_reference,
        their_date=their_date,
        warnings=tuple(warnings),
    )


def _find_recipient(lines: list[str]) -> str:
    """The addressee: the first non-boilerplate line that names an institution.

    The two-column layout puts the addressee and the `Teie` marker on one line,
    so anything from the marker rightwards is dropped.
    """
    for line in lines:
        if not line.strip() or _is_boilerplate(line):
            continue
        candidate = THEIR_BLOCK.split(line)[0]
        candidate = OUR_BLOCK.split(candidate)[0]
        candidate = " ".join(candidate.split()).strip(" ,;:")
        if not candidate:
            continue
        low = candidate.casefold()
        if any(hint in low for hint in RECIPIENT_HINTS):
            return candidate[:MAX_RECIPIENT_LENGTH]
    return ""


def _find_subject(lines: list[str], our_line_index: int) -> str:
    """The subject line: between the reference block and the salutation."""
    if our_line_index < 0:
        start = 0
    else:
        start = our_line_index + 1

    collected: list[str] = []
    for line in lines[start : start + MAX_SUBJECT_LINES + 4]:
        stripped = line.strip()
        if not stripped:
            if collected:
                break
            continue
        if SALUTATION.match(stripped):
            break
        if _is_boilerplate(stripped):
            continue
        # A postal address line below the addressee, not a subject.
        if re.match(r"^\d{4,5}\s+\w+$", stripped):
            continue
        if THEIR_BLOCK.search(stripped) or OUR_BLOCK.search(stripped):
            continue
        collected.append(stripped)
        if len(collected) >= MAX_SUBJECT_LINES:
            break

    subject = " ".join(collected).strip()
    return subject[:MAX_SUBJECT_LENGTH]


def compare_with_filename(
    header: DocumentHeader,
    *,
    filename_date: dt.date | None,
    filename_recipient: str,
) -> tuple[str, ...]:
    """Record where the document and its name disagree. Neither one wins."""
    warnings: list[str] = []
    if header.date and filename_date and header.date != filename_date:
        warnings.append(WARN_DATE_DISAGREES)
    if header.recipient and filename_recipient:
        left = header.recipient.casefold()
        right = filename_recipient.casefold()
        if left not in right and right not in left:
            warnings.append(WARN_RECIPIENT_DISAGREES)
    return tuple(warnings)
