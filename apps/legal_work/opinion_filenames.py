"""Reading what a Chamber opinion filename claims, without trusting it.

Nearly every document in the catalogue is named

    YYYY-MM-DD - recipient - subject.pdf

and that is genuinely useful evidence: the date is the day the letter went out
and the recipient is the ministry it went to. It is still only a filename, so
everything parsed here is stored beside — never instead of — what the document
itself says.

Two things the real catalogue forced:

**The subject may contain the separator.** Of 759 bootstrap documents, 716 have
three parts, 42 have four and one has five. Splitting on every `" - "` would
truncate 43 subjects, so the split is bounded: date, recipient, and then all the
rest as the subject.

**Some names are partially double-encoded.** Two documents carry a correct `õ`
in one word and the UTF-8 bytes of `õ` read as latin-1 — `Ã` followed by `µ` —
in another word of the same name. The repair therefore cannot be "decode the
whole string": it decodes only maximal runs that are themselves valid UTF-8 when
taken as latin-1 bytes. Verified against the bootstrap catalogue: it repairs
exactly those two names and changes none of the other 757.

The original filename is always preserved. Nothing here rewrites it.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field

# A lead byte followed by continuation bytes, as they appear once UTF-8 has been
# decoded as latin-1. Matching the run rather than the whole string is what
# keeps a correctly encoded character in the same name from being mangled.
MOJIBAKE_RUN = re.compile("[Â-ô][-¿]+")

FILENAME_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\s*-\s*(?P<rest>.+)$",
    re.DOTALL,
)

SEPARATOR = re.compile(r"\s+-\s+")

# Dashes and quotes that mean the same thing as their ASCII spelling. Collapsing
# them keeps "Arvamus – eelnõu" and "Arvamus - eelnõu" one subject rather than
# two, and it is reversible in the sense that the original name is kept.
DASH_TRANSLATION = {
    ord("‐"): "-",
    ord("‑"): "-",
    ord("‒"): "-",
    ord("–"): "-",
    ord("—"): "-",
    ord("―"): "-",
    ord("−"): "-",
    ord("“"): '"',
    ord("”"): '"',
    ord("„"): '"',
    ord("‘"): "'",
    ord("’"): "'",
    ord(" "): " ",
}

# Characters a display filename may never carry, whatever the source called the
# file. Applied to the *display* name only; the original is stored verbatim in
# its own column and never used to build a path or a header.
UNSAFE_DISPLAY = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')

MAX_DISPLAY_LENGTH = 180

WARN_UNPARSED = "filename_unparsed"
WARN_BAD_DATE = "filename_date_invalid"
WARN_NO_SUBJECT = "filename_subject_missing"
WARN_NO_RECIPIENT = "filename_recipient_missing"
WARN_REPAIRED = "filename_encoding_repaired"


def repair_encoding(name: str) -> str:
    """Decode only the runs that are recoverable UTF-8; leave the rest alone."""

    def decode_run(match: re.Match[str]) -> str:
        run = match.group(0)
        try:
            return run.encode("latin-1").decode("utf-8")
        except UnicodeEncodeError, UnicodeDecodeError:
            return run

    return MOJIBAKE_RUN.sub(decode_run, name)


def normalise_text(value: str) -> str:
    """NFC, sane punctuation, collapsed whitespace. Diacritics are preserved."""
    folded = unicodedata.normalize("NFC", value).translate(DASH_TRANSLATION)
    return " ".join(folded.split())


def safe_display_name(name: str) -> str:
    """A filename fit to show a person and to put in a download header."""
    cleaned = UNSAFE_DISPLAY.sub(" ", normalise_text(name))
    cleaned = " ".join(cleaned.split()).strip(" .")
    if len(cleaned) > MAX_DISPLAY_LENGTH:
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and len(suffix) <= 8:
            keep = MAX_DISPLAY_LENGTH - len(suffix) - 1
            cleaned = f"{stem[:keep].rstrip()}.{suffix}"
        else:
            cleaned = cleaned[:MAX_DISPLAY_LENGTH].rstrip()
    return cleaned or "dokument.pdf"


@dataclass(frozen=True)
class ParsedFilename:
    """What the name claims. Every field may be empty; none of it is trusted."""

    original: str
    display: str
    date: dt.date | None = None
    recipient: str = ""
    subject: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_parsed(self) -> bool:
        return self.date is not None and bool(self.recipient)


def parse_opinion_filename(original: str) -> ParsedFilename:
    """Read date, recipient and subject out of a source filename."""
    warnings: list[str] = []

    repaired = repair_encoding(original)
    if repaired != original:
        warnings.append(WARN_REPAIRED)

    display = safe_display_name(repaired)

    stem = repaired
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    stem = normalise_text(stem)

    match = FILENAME_PATTERN.match(stem)
    if match is None:
        return ParsedFilename(
            original=original,
            display=display,
            warnings=(*warnings, WARN_UNPARSED),
        )

    try:
        parsed_date: dt.date | None = dt.date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
    except ValueError:
        parsed_date = None
        warnings.append(WARN_BAD_DATE)

    # Bounded split: recipient is the first field, the subject keeps every
    # remaining separator it contained.
    pieces = SEPARATOR.split(match.group("rest"), maxsplit=1)
    recipient = pieces[0].strip() if pieces else ""
    subject = pieces[1].strip() if len(pieces) > 1 else ""

    if not recipient:
        warnings.append(WARN_NO_RECIPIENT)
    if not subject:
        warnings.append(WARN_NO_SUBJECT)

    return ParsedFilename(
        original=original,
        display=display,
        date=parsed_date,
        recipient=recipient,
        subject=subject,
        warnings=tuple(warnings),
    )
