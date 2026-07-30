"""Deciding what an internal observation may be used for.

One place, one set of rules. The historical importer, the manual form and the
selectors all ask this module rather than each re-deriving "is this number safe
to draw", so a template never contains a quality condition and the policy can be
read end to end.

Three principles run through everything here:

1. **Evidence is never discarded to make a chart tidy.** A conflicted or
   impossible value stays in the database with the provenance that explains it.
   What changes is whether a selector will draw it.
2. **Omission is per metric, not per observation.** If two board reports
   disagree about the fee budget on one date, the member count from that date is
   still perfectly good and is still shown.
3. **Missing is not zero.** A withheld metric produces no point. It never
   produces a zero, and it never invites the chart to interpolate across the
   gap.

The functions take plain values rather than model instances so that the same
rules apply to a row that has only just been parsed out of the import package
and to one that has been in PostgreSQL for a year.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .models import ExtractionConfidence, InternalSourceKind, QualityStatus

# Metric names as the import package and the conflict table spell them, mapped
# to the observation fields they withhold. A conflict names a metric; this is
# what that metric means to the model.
METRIC_FIELDS: dict[str, str] = {
    "total_members": "total_members",
    "paid_members": "paid_members",
    "new_members_ytd": "new_members_ytd",
    "suspended_members": "suspended_members",
    "removed_members_ytd": "removed_members_ytd",
    "membership_fees_received_eur": "membership_fees_received_eur",
    "membership_fee_budget_eur": "membership_fee_budget_eur",
    "membership_fee_collection_pct": "membership_fee_collection_pct_reported",
}

# How far a reported collection percentage may sit from the one implied by the
# reported amounts before it is treated as an inconsistency rather than as
# rounding. Board reports state one decimal place, so half a point is generous
# without being meaningless.
COLLECTION_TOLERANCE_PCT = Decimal("0.5")

# Precedence between kinds of evidence for the same date. Lower sorts first.
#
# A manually verified correction outranks everything. A document's own current
# figures outrank the same document's comparison column, because a comparison
# column is a later report restating an earlier year second-hand.
SOURCE_KIND_RANK: dict[str, int] = {
    InternalSourceKind.MANUAL: 0,
    InternalSourceKind.MERGED_SAME_DOCUMENT: 1,
    InternalSourceKind.REPORTED_COMPARISON: 2,
}

CONFIDENCE_RANK: dict[str, int] = {
    ExtractionConfidence.MANUAL_VERIFIED: 0,
    ExtractionConfidence.HIGH: 1,
    ExtractionConfidence.MEDIUM: 2,
    ExtractionConfidence.LOW: 3,
}

QUALITY_RANK: dict[str, int] = {
    QualityStatus.VERIFIED: 0,
    QualityStatus.PROVISIONAL: 1,
    QualityStatus.REVIEW_REQUIRED: 2,
    QualityStatus.CONFLICTED: 3,
    QualityStatus.SUPERSEDED: 4,
}

_UNKNOWN_RANK = 99


@dataclass(frozen=True)
class MetricFacts:
    """The reported numbers of one observation, whatever produced them."""

    total_members: int | None = None
    paid_members: int | None = None
    membership_fees_received_eur: Decimal | None = None
    membership_fee_budget_eur: Decimal | None = None
    membership_fee_collection_pct_reported: Decimal | None = None
    new_members_ytd: int | None = None
    suspended_members: int | None = None
    removed_members_ytd: int | None = None


@dataclass(frozen=True)
class Assessment:
    """What may be stored, and what a default chart may draw."""

    quality_status: str
    withheld_metrics: frozenset[str]
    warning_codes: tuple[str, ...]

    def allows(self, metric_field: str) -> bool:
        return metric_field not in self.withheld_metrics


def computed_collection_pct(
    received: Decimal | None,
    budget: Decimal | None,
) -> Decimal | None:
    """The collection percentage the reported amounts actually imply.

    `None` when it cannot be computed, which includes a zero budget: dividing by
    it would invent a figure rather than derive one.
    """
    if received is None or budget is None or budget == 0:
        return None
    try:
        return (Decimal(received) / Decimal(budget) * 100).quantize(Decimal("0.01"))
    except InvalidOperation, ZeroDivisionError:
        return None


def collection_is_consistent(
    reported: Decimal | None,
    received: Decimal | None,
    budget: Decimal | None,
    *,
    tolerance: Decimal = COLLECTION_TOLERANCE_PCT,
) -> bool | None:
    """Whether a reported percentage agrees with the reported amounts.

    `None` means "cannot be checked", which is not the same as disagreeing and
    must not be treated as a fault.
    """
    computed = computed_collection_pct(received, budget)
    if reported is None or computed is None:
        return None
    return abs(Decimal(reported) - computed) <= tolerance


def impossible_metrics(facts: MetricFacts) -> frozenset[str]:
    """Metrics whose reported value cannot be true as stated.

    Fifteen rows in the approved historical package report more paying members
    than members. Both numbers are kept — the board reported them — but neither
    may be drawn, because whichever one is wrong, the pair is not a fact.

    A collection percentage above 100 is deliberately **not** here. Revenue can
    exceed a budget, and sixty real rows do. It is only questionable when it
    disagrees with the amounts reported beside it, which
    :func:`collection_is_consistent` decides.
    """
    if (
        facts.total_members is not None
        and facts.paid_members is not None
        and facts.paid_members > facts.total_members
    ):
        return frozenset({"total_members", "paid_members"})
    return frozenset()


def assess(
    facts: MetricFacts,
    *,
    conflicted_metrics: frozenset[str] | set[str] = frozenset(),
    is_provisional: bool = False,
    extra_warning_codes: tuple[str, ...] = (),
) -> Assessment:
    """Classify one observation deterministically.

    The same inputs always produce the same status and the same withheld set, so
    re-importing an unchanged package cannot quietly reclassify anything.

    `conflicted_metrics` holds package metric names; they are translated to model
    field names here so callers never have to know both spellings.
    """
    withheld: set[str] = set()
    codes: list[str] = list(extra_warning_codes)

    impossible = impossible_metrics(facts)
    if impossible:
        withheld |= impossible
        codes.append("paid_exceeds_total")

    consistent = collection_is_consistent(
        facts.membership_fee_collection_pct_reported,
        facts.membership_fees_received_eur,
        facts.membership_fee_budget_eur,
    )
    if consistent is False:
        # The reported percentage and the reported amounts cannot both be right,
        # so the percentage is withheld. The amounts themselves are untouched:
        # they are separately reported facts and stay chartable.
        withheld.add("membership_fee_collection_pct_reported")
        codes.append("collection_pct_mismatch")

    for metric in conflicted_metrics:
        field = METRIC_FIELDS.get(metric)
        if field is not None:
            withheld.add(field)

    if conflicted_metrics:
        status = QualityStatus.CONFLICTED
    elif impossible or consistent is False:
        status = QualityStatus.REVIEW_REQUIRED
    elif is_provisional:
        status = QualityStatus.PROVISIONAL
    else:
        status = QualityStatus.VERIFIED

    return Assessment(
        quality_status=status,
        withheld_metrics=frozenset(withheld),
        # Sorted and de-duplicated so an unchanged re-import produces a
        # byte-identical stored value.
        warning_codes=tuple(sorted(set(codes))),
    )


@dataclass(frozen=True)
class PreferenceCandidate:
    """One competing piece of evidence for a single date."""

    key: object
    source_kind: str
    extraction_confidence: str
    quality_status: str
    tie_breaker: object = 0

    @property
    def sort_key(self) -> tuple:
        """Deterministic precedence, best first.

        Kind first, because a manual correction must beat a high-confidence
        extraction and a direct reading must beat a comparison column even when
        the comparison was extracted more confidently. Confidence and quality
        then separate evidence of the same kind, and the tie-breaker — the row
        identifier — makes the order total, so two runs never disagree.
        """
        return (
            SOURCE_KIND_RANK.get(self.source_kind, _UNKNOWN_RANK),
            QUALITY_RANK.get(self.quality_status, _UNKNOWN_RANK),
            CONFIDENCE_RANK.get(self.extraction_confidence, _UNKNOWN_RANK),
            self.tie_breaker,
        )


def choose_preferred(candidates: list[PreferenceCandidate]) -> PreferenceCandidate | None:
    """Pick the one observation a chart should read for a date.

    A conflicted observation can still be preferred. Being preferred decides
    *which row is read*; :class:`Assessment` decides *which of its metrics are
    drawn*. Refusing to prefer a conflicted row would drop every uncontested
    number it also carries.
    """
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate.sort_key)
