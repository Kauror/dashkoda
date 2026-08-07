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

**A second, unrelated family arrived with the full historical archive**: CP437
bytes decoded as CP1252. `ö` is CP437 `0x94`, and `0x94` in CP1252 is `”`, so
`töövaidluse` was stored as `t””vaidluse`. Likewise `ä` (`0x84`) became `„`, and
`ü` (`0x81`) became U+0081 — `0x81` is one of the five undefined CP1252 slots
that fall through to the C1 control character.

That family cannot be repaired by simply substituting characters, because `„`,
`”` and `“` are also **legitimate Estonian quotation marks**, and dozens of
filenames use them correctly. Two conditions separate the two cases:

- a C1 control character is never legitimate text, so it is always repaired;
- a quotation mark is repaired only when its whole run sits **between two
  letters**, which is where a letter was replaced and where a quote cannot be.

Word-initial cases stay ambiguous — `„riühingu` could be a corrupted `äriühingu`
or a quoted `„riühingu` — and are deliberately left alone. Preserving a name we
cannot resolve is better than inventing one.

The repair is verified against an oracle rather than by eye: the 2026 pilot ZIP
and the full archive contain the same PDFs byte for byte under different names,
so the pilot's spelling is evidence for what the archive's should have been. All
six differing pairs repair to the pilot name exactly.

The original filename is always preserved. Nothing here rewrites it.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field

#: Bumped whenever a change here alters the metadata a filename yields. The
#: catalogue stores it beside the extractor version and rebuilds when it moves,
#: because parsed dates, recipients and subjects all come from these names. It
#: is deliberately **not** the extractor version: nothing in this module touches
#: PDF text extraction.
FILENAME_NORMALISER_VERSION = "1.1"

# A lead byte followed by continuation bytes, as they appear once UTF-8 has been
# decoded as latin-1. Matching the run rather than the whole string is what
# keeps a correctly encoded character in the same name from being mangled.
MOJIBAKE_RUN = re.compile("[Â-ô][-¿]+")


def _cp437_repairs() -> dict[str, str]:
    """Characters CP1252 shows for a byte that really meant CP437.

    Derived from the codecs rather than written out, so it cannot drift from
    what the two encodings actually say. A pair is kept only when the corrupt
    form is not itself a letter and the recovered form is one. That excludes
    `ž` — a real Estonian letter whose CP437 slot is the peseta sign — and
    every other pair where "repair" would replace legible text with noise.
    """
    repairs: dict[str, str] = {}
    for byte in range(0x80, 0x100):
        raw = bytes([byte])
        try:
            recovered = raw.decode("cp437")
        except UnicodeDecodeError:  # pragma: no cover - cp437 decodes every byte
            continue
        try:
            corrupt = raw.decode("cp1252")
        except UnicodeDecodeError:
            # One of the five undefined CP1252 slots. Windows and browsers both
            # fall through to the matching C1 control, and so did whatever
            # produced these names.
            corrupt = chr(byte)
        if corrupt == recovered:
            continue
        if unicodedata.category(corrupt).startswith("L"):
            continue
        if not unicodedata.category(recovered).startswith("L"):
            continue
        repairs[corrupt] = recovered
    return repairs


#: Corrupt character -> the letter it should have been.
CP437_REPAIRS = _cp437_repairs()

#: Never legitimate in a filename, so repaired wherever they appear. Everything
#: else in the map is punctuation that Estonian genuinely uses.
CP437_ALWAYS = frozenset(c for c in CP437_REPAIRS if unicodedata.category(c) == "Cc")

CP437_RUN = re.compile("[" + "".join(re.escape(c) for c in CP437_REPAIRS) + "]+")

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


def repair_cp437(name: str) -> str:
    """Repair CP437 letters that were read as CP1252, where that is unambiguous.

    A run is repaired when either test passes:

    - it is a C1 control character, which is never legitimate in a filename;
    - the whole run sits between two letters, which is where a letter was
      replaced and where a quotation mark cannot be.

    Anything else — a quote after a space, before a digit, at either end — is
    left exactly as it came, because `„Kasvuhoonegaaside` is a real quotation
    and `„riühingu` cannot be told apart from one.
    """

    def fix(match: re.Match[str]) -> str:
        run = match.group(0)
        before = name[match.start() - 1] if match.start() else ""
        after = name[match.end()] if match.end() < len(name) else ""
        inside_word = before.isalpha() and after.isalpha()
        return "".join(CP437_REPAIRS[c] if (c in CP437_ALWAYS or inside_word) else c for c in run)

    return CP437_RUN.sub(fix, name)


def repair_filename(name: str) -> str:
    """Every repair this module knows, applied in order. Never mutates input."""
    return repair_cp437(repair_encoding(name))


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

    repaired = repair_filename(original)
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
