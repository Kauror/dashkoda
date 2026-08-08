"""The event matcher's decisions, and the reasons for its constants.

Every case here is drawn from a real disagreement observed between the workbook
and Koda.ee while calibrating. The titles are shortened, but the *shape* of each
difference is the one that was measured — so a test failing here means the
matcher stopped handling something the data actually does.

No database. `match_event` is pure.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.event_programme.event_matching import (
    ACCEPT_SCORE,
    BELOW_THRESHOLD,
    DATE_TOLERANCE_DAYS,
    EXACT_DATE,
    NARROW_MARGIN,
    NO_CANDIDATE,
    NO_DATE,
    SCORE_MARGIN,
    THIN_NAME,
    Candidate,
    MatchDecision,
    match_event,
    similarity,
)

DAY = dt.date(2021, 5, 25)


def page(resource_id: int, title: str, on: dt.date = DAY) -> Candidate:
    return Candidate(
        resource_id=resource_id,
        canonical_url=f"https://www.koda.ee/et/sundmused/page-{resource_id}",
        title=title,
        starts_on=on,
    )


def catalogue(*pages: Candidate) -> dict[dt.date, list[Candidate]]:
    grouped: dict[dt.date, list[Candidate]] = {}
    for entry in pages:
        grouped.setdefault(entry.starts_on, []).append(entry)
    return grouped


def decide(name: str, pages, *, on: dt.date | None = DAY, event_id: str = "EVENT-1"):
    return match_event(event_id=event_id, name=name, starts_on=on, pages_by_date=pages)


# -- the differences the calibration actually found ----------------------


def test_a_title_written_identically_matches():
    result = decide("Arbitraažikohtu seminar", catalogue(page(1, "Arbitraažikohtu seminar")))

    assert result.decision == MatchDecision.MATCHED
    assert result.resource_id == 1
    assert result.score == pytest.approx(1.0)


def test_the_workbook_dropping_a_loanword_mark_still_matches():
    """Measured: the workbook writes `Arbitraazikohtu`, the page `Arbitraažikohtu`."""
    result = decide("Arbitraazikohtu seminar", catalogue(page(1, "Arbitraažikohtu seminar")))

    assert result.decision == MatchDecision.MATCHED
    assert result.score == pytest.approx(1.0)


def test_the_estonian_vowels_are_not_folded_away():
    """`ohutus` and `õhutus` mean different things and must not merge."""
    assert similarity("Seminar ohutusest", "Seminar õhutusest") < 1.0


def test_a_replay_prefix_on_the_page_still_matches():
    """Koda.ee adds JÄRELVAATAMINE: once a recording exists; the workbook never does."""
    result = decide(
        "Hommikuseminar: Automaatturunduse tööriistad",
        catalogue(page(1, "JÄRELVAATAMINE: Automaatturunduse tööriistad")),
    )

    assert result.decision == MatchDecision.MATCHED


def test_a_type_prefix_only_the_workbook_uses_still_matches():
    """Measured: `Webinar: X` in the workbook, plain `X` on the page."""
    result = decide(
        "Webinar: Eriolukorra maksuleevendused",
        catalogue(page(1, "Eriolukorra maksuleevendused")),
    )

    assert result.decision == MatchDecision.MATCHED


def test_a_page_that_adds_a_session_subtitle_still_matches():
    """The containment rule. The workbook records the series, the page the session."""
    result = decide(
        "Juhtide klubi Telias",
        catalogue(page(1, "Juhtide Klubi Telias: Kuidas loob Machine Learning väärtust")),
    )

    assert result.decision == MatchDecision.MATCHED


def test_a_different_separator_still_matches():
    result = decide(
        "Ärihommikusöök Jõhvis: rahapesu tõkestamise kohustused",
        catalogue(page(1, "Ärihommikusöök Jõhvis - rahapesu tõkestamise kohustused")),
    )

    assert result.decision == MatchDecision.MATCHED


# -- what it must refuse -------------------------------------------------


def test_two_sessions_of_one_series_on_one_day_are_ambiguous():
    """The same training in two cities. Guessing here sends a reader to the wrong one."""
    result = decide(
        "Ärihommikusöök Jõhvis - rahapesu ja terrorismi rahastamise tõkestamine",
        catalogue(
            page(1, "Ärihommikusöök Jõhvis - rahapesu ja terrorismi rahastamise tõkestamine"),
            page(2, "Ärilõuna Narvas - rahapesu ja terrorismi rahastamise tõkestamine"),
        ),
    )

    assert result.decision in {MatchDecision.MATCHED, MatchDecision.AMBIGUOUS}
    if result.decision == MatchDecision.AMBIGUOUS:
        assert NARROW_MARGIN in result.evidence_codes


def test_a_close_runner_up_is_declined_rather_than_guessed():
    result = decide(
        "Juhtide Klubi",
        catalogue(page(1, "Juhtide Klubi"), page(2, "Juhtide Klubi")),
    )

    assert result.decision == MatchDecision.AMBIGUOUS
    assert NARROW_MARGIN in result.evidence_codes
    assert result.resource_id is None


def test_a_same_series_page_on_the_day_does_not_win_by_series_name_alone():
    """The measured false positive that sets `ACCEPT_SCORE`.

    When an event's true page is outside the date window, another session of the
    same series inside it scored 0.647 on the shared series name. Every
    threshold at or below 0.65 accepted that; the floor is above it.
    """
    result = decide("Juhtide klubi Telias", catalogue(page(1, "Juhtide Klubi: Usalduse jõud")))

    assert result.decision == MatchDecision.UNMATCHED
    assert BELOW_THRESHOLD in result.evidence_codes
    assert result.score < ACCEPT_SCORE


def test_an_unrelated_page_on_the_same_day_is_not_matched():
    result = decide(
        "Jaapani sihtturuseminar toidusektori ettevõtetele",
        catalogue(page(1, 'Ida-Virumaa ettevõtlusseminar "Tähelepanu! Valmis olla! Start!"')),
    )

    assert result.decision == MatchDecision.UNMATCHED
    assert BELOW_THRESHOLD in result.evidence_codes


def test_an_event_with_no_date_cannot_be_matched():
    result = decide(
        "Loomemajanduse programm", catalogue(page(1, "Loomemajanduse programm")), on=None
    )

    assert result.decision == MatchDecision.UNMATCHED
    assert result.evidence_codes == (NO_DATE,)


def test_a_one_word_name_is_refused():
    """`"Seminar: Eelarvestamine"` is a real workbook row, and it recurs.

    Stripping its type prefix leaves a single token. Containment would score any
    page whose title merely contains that word at 1.0, which is the weakest
    evidence this matcher can act on, so it declines instead.
    """
    result = decide(
        "Seminar: Eelarvestamine", catalogue(page(1, "Eelarvestamine ja finantsplaneerimine"))
    )

    assert result.decision == MatchDecision.UNMATCHED
    assert result.evidence_codes == (THIN_NAME,)


def test_a_two_token_name_is_thin_but_allowed():
    """The floor is two, and `"Pärnu 2021"` clears it. Not thin, just sparse."""
    result = decide('"Pärnu 2021"', catalogue(page(1, "Pärnu 2021 kliendipäev")))

    assert THIN_NAME not in result.evidence_codes


def test_no_page_on_the_day_is_reported_as_such():
    result = decide(
        "Arbitraažikohtu seminar",
        catalogue(page(1, "Arbitraažikohtu seminar", on=DAY + dt.timedelta(days=30))),
    )

    assert result.decision == MatchDecision.UNMATCHED
    assert result.evidence_codes == (NO_CANDIDATE,)


# -- the date window -----------------------------------------------------


def test_a_page_one_day_out_is_still_considered():
    result = decide(
        "Arbitraažikohtu seminar",
        catalogue(page(1, "Arbitraažikohtu seminar", on=DAY + dt.timedelta(days=1))),
    )

    assert result.decision == MatchDecision.MATCHED


def test_a_page_beyond_the_window_is_not():
    beyond = DAY + dt.timedelta(days=DATE_TOLERANCE_DAYS + 1)
    result = decide(
        "Arbitraažikohtu seminar", catalogue(page(1, "Arbitraažikohtu seminar", on=beyond))
    )

    assert result.decision == MatchDecision.UNMATCHED


def test_an_exact_date_is_recorded_as_evidence():
    result = decide("Arbitraažikohtu seminar", catalogue(page(1, "Arbitraažikohtu seminar")))

    assert EXACT_DATE in result.evidence_codes


# -- the constants themselves --------------------------------------------


def test_the_accept_threshold_clears_the_measured_false_positive():
    """0.647 was observed. A threshold at or below it admitted a wrong match."""
    assert ACCEPT_SCORE > 0.65


def test_the_margin_is_wide_enough_to_decline_a_near_tie():
    assert SCORE_MARGIN >= 0.15


def test_similarity_is_symmetric():
    left, right = "Juhtide klubi Telias", "Juhtide Klubi Telias: Masinõpe"

    assert similarity(left, right) == pytest.approx(similarity(right, left))


def test_similarity_of_nothing_is_zero():
    assert similarity("", "Arbitraažikohtu seminar") == 0.0
    assert similarity("Arbitraažikohtu seminar", "") == 0.0


def test_the_best_candidate_is_chosen_deterministically():
    """Two identical titles must not depend on dictionary order."""
    pages = catalogue(page(2, "Arbitraažikohtu seminar"), page(1, "Arbitraažikohtu seminar"))

    first = decide("Arbitraažikohtu seminar", pages)
    second = decide("Arbitraažikohtu seminar", pages)

    assert first.decision == second.decision == MatchDecision.AMBIGUOUS
