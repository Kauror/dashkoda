"""Repairing CP437 letters that were stored as CP1252 punctuation.

The full historical archive names some documents with `t””vaidluse` where
`töövaidluse` was meant: `ö` is CP437 `0x94`, and `0x94` in CP1252 is `”`. The
same archive also uses `„`, `”` and `“` as genuine Estonian quotation marks in
dozens of other names, so the repair cannot be a substitution table.

What makes it safe is the oracle. The 2026 pilot ZIP and the full archive hold
the same PDFs byte for byte under different names, so the pilot spelling is
evidence — not a guess — for what the archive spelling should have been. The
pairs below are those real names.

Nothing here contains legal vocabulary, a document digest, a filename allowlist
or a spelling dictionary. The rule is positional and applies to any name with
this corruption.
"""

from __future__ import annotations

import pytest

from apps.legal_work.opinion_filenames import (
    CP437_ALWAYS,
    CP437_REPAIRS,
    FILENAME_NORMALISER_VERSION,
    parse_opinion_filename,
    repair_cp437,
    repair_filename,
)

#: (corrupted archive name, correct pilot name). Byte-identical PDFs.
ORACLE_PAIRS = [
    (
        "2026-02-02 - Majandus- ja Kommunikatsiooniministeerium - Arvamuse esitamine "
        "t””vaidluse lahendamise seaduse v„ljat””tamiskavatsuse kohta.pdf",
        "2026-02-02 - Majandus- ja Kommunikatsiooniministeerium - Arvamuse esitamine "
        "töövaidluse lahendamise seaduse väljatöötamiskavatsuse kohta.pdf",
    ),
    (
        "2026-03-23 - Riigikogu sotsiaalkomisjon - Ettepanek \x81marlaua korraldamiseks.pdf",
        "2026-03-23 - Riigikogu sotsiaalkomisjon - Ettepanek ümarlaua korraldamiseks.pdf",
    ),
    (
        "2026-03-30 - Riigikogu keskkonnakomisjon, Kliimaministeeri - "
        "T””stusheite seaduse muudatusettepanek.pdf",
        "2026-03-30 - Riigikogu keskkonnakomisjon, Kliimaministeeri - "
        "Tööstusheite seaduse muudatusettepanek.pdf",
    ),
    (
        "2026-05-28 - Majandus- ja Kommunikatsiooniministeerium - Taastuvenergia tasu "
        "v„hendamise toetuse taotlemise ja andmise tingimused.pdf",
        "2026-05-28 - Majandus- ja Kommunikatsiooniministeerium - Taastuvenergia tasu "
        "vähendamise toetuse taotlemise ja andmise tingimused.pdf",
    ),
]

#: Names that are already correct, or whose punctuation is genuine, and must
#: come back untouched.
MUST_NOT_CHANGE = [
    # ordinary correct Estonian
    "2025-01-15 - Kliimaministeerium - Arvamuse esitamine looduskaitseseaduse eelnõu.pdf",
    # a quoted title: the opening mark follows a space
    "2026-02-01 - Ministeerium - Arvamuse esitamine määruse „Nõuded ohtliku ettevõtte kohta.pdf",
    # a quote pair around a title
    "2026-03-26 - Justiits- ja Digiministeerium - „Hea õigusloome ja normitehnika eeskiri“ "
    "muutmise määruse eelnõu.pdf",
    # a closing mark after a digit
    "2026-04-10 - Ministeerium - Üleriigilise planeeringu “Eesti 2050“ eelnõu.pdf",
    # ž is a real letter, and its CP437 slot is the peseta sign
    "2025-05-22 - Justiits- ja Digiministeerium - 28. režiim.pdf",
    # every Estonian diacritic, correctly encoded
    "2025-06-01 - Ministeerium - šokolaad õun äikene öö üksus Šš Õõ Ää Öö Üü.pdf",
]


class TestTheOracle:
    """Repaired archive names must equal the pilot names exactly."""

    @pytest.mark.parametrize(("corrupt", "correct"), ORACLE_PAIRS)
    def test_it_reconstructs_the_pilot_spelling(self, corrupt, correct):
        assert repair_filename(corrupt) == correct

    def test_the_corrupt_and_correct_forms_really_do_differ(self):
        """Otherwise the test above would pass without repairing anything."""
        for corrupt, correct in ORACLE_PAIRS:
            assert corrupt != correct


class TestWhatMustNotChange:
    @pytest.mark.parametrize("name", MUST_NOT_CHANGE)
    def test_it_is_returned_untouched(self, name):
        assert repair_filename(name) == name

    def test_a_quote_at_a_word_boundary_survives(self):
        """`„` after a space is a quotation mark, not a corrupted `ä`."""
        assert repair_cp437("veebilehel „riühingu poolt") == "veebilehel „riühingu poolt"

    def test_an_ambiguous_word_initial_run_is_preserved_not_guessed(self):
        """It could be a corrupted letter or a real quote. We do not choose."""
        name = "Ettepanek avaldada veebilehel „riühingu summa.pdf"
        assert repair_filename(name) == name

    def test_an_empty_name_is_handled(self):
        assert repair_filename("") == ""


class TestTheRuleIsPositional:
    def test_a_run_between_letters_is_repaired(self):
        assert repair_cp437("t””vaidlus") == "töövaidlus"

    def test_the_same_characters_between_spaces_are_not(self):
        assert repair_cp437("sõna ”” sõna") == "sõna ”” sõna"

    def test_a_control_character_is_repaired_wherever_it_sits(self):
        """U+0081 is never legitimate text, so position does not matter."""
        assert repair_cp437("\x81marlaud") == "ümarlaud"
        assert repair_cp437("b\x81rokraatia") == "bürokraatia"

    def test_mixed_correct_and_corrupt_in_one_name(self):
        """A correct diacritic beside a corrupt one; only the corrupt moves."""
        assert repair_cp437("eelnõu t””vaidlus") == "eelnõu töövaidlus"

    def test_a_digit_boundary_does_not_count_as_a_letter(self):
        assert repair_cp437("2050”") == "2050”"


class TestTheMapIsDerivedNotWritten:
    def test_it_comes_from_the_codecs(self):
        assert CP437_REPAIRS["”"] == "ö"
        assert CP437_REPAIRS["„"] == "ä"
        assert CP437_REPAIRS["\x81"] == "ü"

    def test_a_real_letter_is_never_a_repair_candidate(self):
        """`ž` and every other letter must stay out of the map."""
        for corrupt in CP437_REPAIRS:
            assert not corrupt.isalpha(), corrupt

    def test_every_repair_produces_a_letter(self):
        for recovered in CP437_REPAIRS.values():
            assert recovered.isalpha(), recovered

    def test_the_unconditional_set_is_only_control_characters(self):
        for c in CP437_ALWAYS:
            assert ord(c) < 0xA0, c


class TestTheParsedFields:
    def test_the_original_is_preserved_verbatim(self):
        corrupt = ORACLE_PAIRS[0][0]
        parsed = parse_opinion_filename(corrupt)
        assert parsed.original == corrupt

    def test_the_subject_carries_the_repaired_spelling(self):
        """This is what the matcher weighs."""
        parsed = parse_opinion_filename(ORACLE_PAIRS[0][0])
        assert "töövaidluse" in parsed.subject
        assert "väljatöötamiskavatsuse" in parsed.subject
        assert "”" not in parsed.subject

    def test_the_display_name_is_repaired_too(self):
        parsed = parse_opinion_filename(ORACLE_PAIRS[1][0])
        assert "ümarlaua" in parsed.display
        assert "\x81" not in parsed.display

    def test_the_repair_is_reported_as_a_warning(self):
        parsed = parse_opinion_filename(ORACLE_PAIRS[0][0])
        assert "filename_encoding_repaired" in parsed.warnings

    def test_an_untouched_name_reports_no_repair_warning(self):
        parsed = parse_opinion_filename(MUST_NOT_CHANGE[0])
        assert "filename_encoding_repaired" not in parsed.warnings

    def test_the_date_and_recipient_still_parse(self):
        parsed = parse_opinion_filename(ORACLE_PAIRS[0][0])
        assert parsed.date is not None
        assert parsed.recipient == "Majandus- ja Kommunikatsiooniministeerium"


class TestTheLatinOneFamilyStillWorks:
    """The pre-existing repair must not have been displaced by the new one."""

    def test_utf8_read_as_latin1_is_still_decoded(self):
        assert repair_filename("eelnÃµu") == "eelnõu"

    def test_both_families_in_one_name(self):
        assert repair_filename("eelnÃµu t””vaidlus") == "eelnõu töövaidlus"


class TestTheVersion:
    def test_it_is_tracked_separately_from_the_extractor_version(self):
        """Two different things: how a PDF is read, and how its name is read.

        Text extraction is untouched by this change, so its version must not
        move — otherwise every blob would be re-extracted to fix a filename.
        """
        from apps.legal_work.models import OpinionCatalogueSnapshot
        from apps.legal_work.opinion_pdf import EXTRACTOR_VERSION

        assert FILENAME_NORMALISER_VERSION
        assert EXTRACTOR_VERSION == "1.0", "extraction must not have been bumped"
        fields = {f.name for f in OpinionCatalogueSnapshot._meta.get_fields()}
        assert {"extractor_version", "filename_normaliser_version"} <= fields

    def test_the_catalogue_rebuilds_when_it_moves(self):
        """The guard must name it, or a normaliser change would not republish."""
        import inspect

        from apps.legal_work import opinion_catalogue_sync

        source = inspect.getsource(opinion_catalogue_sync)
        assert "filename_normaliser_version == FILENAME_NORMALISER_VERSION" in source
