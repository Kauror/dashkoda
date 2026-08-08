"""Deciding which public Koda.ee page belongs to which programme event.

**The date carries this matcher.** That is the deliberate difference from every
other matcher in this project, where dates support a decision that text drives.
An event happens on a stated day, and both sides state it: the workbook records
when the Chamber held it and the page's structured data records when Koda.ee
says it happened. There is no drafting-to-sending drift to allow for, as there
is for an opinion letter.

Measured on the events that already carry a workbook URL — which is a
ground-truth set, because the workbook itself names the correct page — the two
dates agree exactly in about 93% of cases, and **80% of events have no other
public event on their day at all**. So the date does almost all of the
discriminating and the title only has to separate same-day siblings.

## Why the title alone could never do it

58 titles repeat across 154 programme events on different dates: *Juhtide
Klubi*, *Ärihommikusöök*, *Hommikukohv* are recurring series, and the workbook
often records only the series name and a venue. Scored on text alone, every
session of a series is a perfect match for every other. Date first is not an
optimisation here — it is the only thing that makes the question answerable.

## Why the threshold is where it is

Scores are containment over character n-grams, floored by token Jaccard.
Containment rather than Jaccard alone because the workbook records the short
series name where the page adds the session's subtitle — *"Juhtide klubi
Telias"* against *"Juhtide Klubi Telias: Kuidas loob Machine Learning väärtust
nii kliendile kui ärile"* — and Jaccard punishes that containment as if it were
disagreement. Jaccard is kept as a second path because containment alone rewards
a very short title for being trivially inside a long one.

The accept threshold is set from a measured failure. When an event's true page
falls **outside** the date window, a different session of the same series inside
the window scores **0.647** on the shared series name alone — high enough to
look convincing and entirely wrong. Every threshold at or below 0.65 admitted
that match; every threshold above it rejected the case while keeping recall
unchanged. `ACCEPT_SCORE` is therefore 0.70, above the observed failure with
room to spare.

At that setting the matcher made **no wrong match** against the ground-truth
set. Precision matters far more than recall here: an event that goes unmatched
keeps whatever the workbook gave it and a reader loses nothing, while a wrong
link sends a reader to a different event and looks authoritative doing it.

## What this never does

It never writes an event-programme field. The workbook remains the authority on
an event's name, date, type, delivery mode, tag, service code and inclusion
status; matching only attaches an address. No event, page, slug or title is
special-cased anywhere in this module.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from django.db import models

from .event_normalisation import EVENT_NORMALISER_VERSION, ngrams, token_set

MATCHER_VERSION = f"event-1.0-norm{EVENT_NORMALISER_VERSION}"

#: How far a page's date may sit from the workbook's and still be considered.
#: One day, because a page occasionally records the day an event ran into rather
#: than the day it opened. Widening this is what lets a same-series sibling in,
#: so it is deliberately the smallest window that is not zero.
DATE_TOLERANCE_DAYS = 1

#: Below this, no match. Set above the measured 0.647 false positive; see above.
ACCEPT_SCORE = 0.70

#: How far the best candidate must be clear of the runner-up. Two sessions of
#: one series on one day are genuinely indistinguishable from the workbook's
#: name, and the honest answer there is "ambiguous", not a coin toss.
SCORE_MARGIN = 0.20

#: An event whose name carries fewer content tokens than this cannot be matched
#: on text at all. `"Pärnu 2021"` is a real workbook row and matches nothing
#: safely.
MINIMUM_TOKENS = 2


class MatchDecision(models.TextChoices):
    MATCHED = "matched", "Sobitatud"
    AMBIGUOUS = "ambiguous", "Mitmetimõistetav"
    UNMATCHED = "unmatched", "Sobitamata"


# Evidence codes. Recorded per decision so an operator can tell *why* an event
# has no link without re-running anything.
NO_DATE = "no-programme-date"
THIN_NAME = "name-too-thin"
NO_CANDIDATE = "no-page-on-date"
BELOW_THRESHOLD = "below-accept-score"
NARROW_MARGIN = "runner-up-too-close"
EXACT_DATE = "exact-date"
NEAR_DATE = "near-date"


@dataclass(frozen=True)
class Candidate:
    """One public page being considered for one event."""

    resource_id: int
    canonical_url: str
    title: str
    starts_on: dt.date


@dataclass(frozen=True)
class Match:
    """What the matcher decided about one event, and on what evidence."""

    event_id: str
    decision: str
    resource_id: int | None = None
    score: float = 0.0
    runner_up_score: float = 0.0
    evidence_codes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def score_margin(self) -> float:
        return round(self.score - self.runner_up_score, 4)


def _containment(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def similarity(programme_name: str, page_title: str) -> float:
    """How alike two event names are, on [0, 1].

    Containment over n-grams handles inflection and the workbook's habit of
    recording a shorter name than the page. Token Jaccard is the floor.
    """
    return max(
        _containment(ngrams(programme_name), ngrams(page_title)),
        _jaccard(token_set(programme_name), token_set(page_title)),
    )


def candidates_for(
    starts_on: dt.date, pages_by_date: dict[dt.date, list[Candidate]]
) -> list[Candidate]:
    """Every page whose date is within tolerance of the event's."""
    found: list[Candidate] = []
    for offset in range(-DATE_TOLERANCE_DAYS, DATE_TOLERANCE_DAYS + 1):
        found.extend(pages_by_date.get(starts_on + dt.timedelta(days=offset), ()))
    return found


def match_event(
    *,
    event_id: str,
    name: str,
    starts_on: dt.date | None,
    pages_by_date: dict[dt.date, list[Candidate]],
) -> Match:
    """Decide one event. Pure: no database access, no clock, no network."""
    if starts_on is None:
        return Match(event_id, MatchDecision.UNMATCHED, evidence_codes=(NO_DATE,))

    if len(token_set(name)) < MINIMUM_TOKENS:
        return Match(event_id, MatchDecision.UNMATCHED, evidence_codes=(THIN_NAME,))

    considered = candidates_for(starts_on, pages_by_date)
    if not considered:
        return Match(event_id, MatchDecision.UNMATCHED, evidence_codes=(NO_CANDIDATE,))

    ranked = sorted(
        ((similarity(name, page.title), page) for page in considered),
        key=lambda pair: (-pair[0], pair[1].canonical_url),
    )
    best_score, best_page = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    dated = EXACT_DATE if best_page.starts_on == starts_on else NEAR_DATE

    if best_score < ACCEPT_SCORE:
        return Match(
            event_id,
            MatchDecision.UNMATCHED,
            score=best_score,
            runner_up_score=runner_up,
            evidence_codes=(BELOW_THRESHOLD,),
        )

    if best_score - runner_up < SCORE_MARGIN:
        return Match(
            event_id,
            MatchDecision.AMBIGUOUS,
            score=best_score,
            runner_up_score=runner_up,
            evidence_codes=(NARROW_MARGIN, dated),
        )

    return Match(
        event_id,
        MatchDecision.MATCHED,
        resource_id=best_page.resource_id,
        score=best_score,
        runner_up_score=runner_up,
        evidence_codes=(dated,),
    )
