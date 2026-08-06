"""Running the opinion matcher and publishing what it decided.

The population is every opinion-eligible record in the **current** legal
snapshot, and the candidates are every usable entry in the **current** opinion
catalogue. Both are pinned on the published match snapshot, so a decision can
always name the exact two inputs that produced it and a later import makes the
staleness visible rather than silent.

Durable identity is established here rather than by the matcher, because it is a
property of the legal record and not of any document: every eligible record is
resolved to a `LegalMatter` first, colliding keys are flagged and excluded, and
only then is matching attempted. That ordering is what stops an ambiguous
identity from ever reaching a resource page.

Nothing in this module mutates a `LegalWorkItem`, and there is no manual path
into any of it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.core.feed_sync import describe_error

from .models import LegalWorkItem, LegalWorkSnapshot, MatchDecision
from .opinion_classification import NEVER_PRIMARY, DocumentClassification
from .opinion_eligibility import opinion_eligible_items
from .opinion_identity import IDENTITY_VERSION, resolve_matter_key
from .opinion_match_models import (
    DocumentRole,
    LegalMatter,
    LegalMatterAlias,
    LegalOpinionDecision,
    LegalOpinionDocumentRelation,
    LegalOpinionMatchSnapshot,
    OpinionResource,
)
from .opinion_matching import (
    MATCHER_VERSION,
    MIN_MARGIN,
    THRESHOLD_AMBIGUOUS,
    THRESHOLD_MATCH,
    Candidate,
    build_rarity,
    score_candidate,
)
from .opinion_models import OpinionCatalogueEntry, OpinionCatalogueSnapshot
from .opinion_pdf import ExtractionStatus, ValidationStatus

logger = logging.getLogger(__name__)

LOCK_NAME = "legal_opinion_matching"

RESULT_GENERATED = "generated"
RESULT_UNCHANGED = "unchanged"
RESULT_FAILED = "failed"
RESULT_SKIPPED = "skipped"

# Roles a secondary document may take, keyed by what it was classified as.
SECONDARY_ROLE = {
    DocumentClassification.JOINT_OPINION: DocumentRole.JOINT,
    DocumentClassification.SUPPLEMENTARY_OPINION: DocumentRole.SUPPLEMENTARY,
    DocumentClassification.FOLLOW_UP: DocumentRole.FOLLOW_UP,
    DocumentClassification.ANNEX: DocumentRole.ANNEX,
    DocumentClassification.SUPPORTING_DOCUMENT: DocumentRole.SUPPORTING,
}

# How close a secondary document must sit to the primary to be grouped with it.
GROUPING_DAYS = 10


@dataclass
class MatchReport:
    """Aggregates only. No topic, filename, recipient, subject or path."""

    result: str
    detail: str = ""
    dry_run: bool = False
    snapshot_id: int | None = None
    considered_records: int = 0
    matched: int = 0
    ambiguous: int = 0
    unmatched: int = 0
    primary_relations: int = 0
    secondary_relations: int = 0
    identity_collisions: int = 0
    matcher_version: str = MATCHER_VERSION

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "dry_run": self.dry_run,
            "snapshot_id": self.snapshot_id,
            "considered_records": self.considered_records,
            "matched": self.matched,
            "ambiguous": self.ambiguous,
            "unmatched": self.unmatched,
            "primary_relations": self.primary_relations,
            "secondary_relations": self.secondary_relations,
            "identity_collisions": self.identity_collisions,
            "matcher_version": self.matcher_version,
        }


def run_opinion_matching(*, dry_run: bool = False, actor=None) -> MatchReport:
    """Match one legal snapshot against one opinion catalogue."""
    correlation_id = uuid.uuid4()

    legal_snapshot = LegalWorkSnapshot.objects.filter(is_current=True).first()
    catalogue = OpinionCatalogueSnapshot.objects.filter(is_current=True).first()
    if legal_snapshot is None or catalogue is None:
        return MatchReport(
            result=RESULT_SKIPPED,
            detail="Puudub kehtiv õigusloome hetkeseis või arvamuste kataloog.",
            dry_run=dry_run,
        )

    published = LegalOpinionMatchSnapshot.objects.filter(is_current=True).first()
    if (
        published is not None
        and published.legal_snapshot_id == legal_snapshot.pk
        and published.opinion_catalogue_snapshot_id == catalogue.pk
        and published.matcher_version == MATCHER_VERSION
    ):
        return MatchReport(
            result=RESULT_UNCHANGED,
            detail="Sobitamine on juba arvutatud samadelt sisenditelt.",
            dry_run=dry_run,
            snapshot_id=published.pk,
            considered_records=published.considered_item_count,
            matched=published.matched_count,
            ambiguous=published.ambiguous_count,
            unmatched=published.unmatched_count,
        )

    try:
        return _generate(
            legal_snapshot=legal_snapshot,
            catalogue=catalogue,
            dry_run=dry_run,
            correlation_id=correlation_id,
            actor=actor,
        )
    except Exception as error:  # noqa: BLE001 - a failure must keep the last good snapshot
        message = describe_error(error)
        logger.warning("opinion_matching failed: %s", type(error).__name__)
        record_event(
            action=AuditAction.OPINION_MATCH_FAILED,
            obj=legal_snapshot,
            actor=actor,
            correlation_id=correlation_id,
            change_summary={"error": type(error).__name__},
        )
        return MatchReport(result=RESULT_FAILED, detail=message, dry_run=dry_run)


def _candidates(catalogue: OpinionCatalogueSnapshot) -> list[Candidate]:
    """Every entry the matcher may consider, reduced to what it weighs."""
    rows = (
        OpinionCatalogueEntry.objects.filter(
            snapshot=catalogue,
            blob__validation_status=ValidationStatus.VALID,
            extraction__status=ExtractionStatus.EXTRACTED,
        )
        .exclude(extraction__isnull=True)
        .select_related("blob", "extraction")
    )
    return [
        Candidate(
            entry_id=row.pk,
            classification=row.classification,
            filename_date=row.filename_date,
            detected_date=row.extraction.detected_date,
            filename_subject=row.filename_subject,
            detected_subject=row.extraction.detected_subject,
            recipient=row.filename_recipient or row.extraction.detected_recipient,
            text=row.extraction.text,
            first_page_text=row.extraction.first_page_text,
            is_readable=True,
        )
        for row in rows
    ]


def _ensure_matters(items: list[LegalWorkItem], snapshot: LegalWorkSnapshot) -> tuple[dict, int]:
    """Resolve every record to a durable matter, flagging collisions.

    A key claimed by two materially different records in one snapshot is an
    ambiguous identity. Both records keep their matter row — losing the
    provenance would be worse — but the matter is flagged and can never produce
    a link.
    """
    grouped, collisions = resolve_matter_key(items)
    resolved: dict[int, LegalMatter] = {}

    for key, rows in grouped.items():
        first = rows[0]
        matter, created = LegalMatter.objects.get_or_create(
            matter_key=key,
            defaults={
                "identity_version": IDENTITY_VERSION,
                "last_known_topic": (first.topic or "")[:500],
                "received_date": first.received_date,
            },
        )
        colliding = key in collisions
        updates = []
        if not created and matter.last_known_topic != (first.topic or "")[:500]:
            matter.last_known_topic = (first.topic or "")[:500]
            updates.append("last_known_topic")
        if colliding and not matter.has_ambiguous_identity:
            matter.has_ambiguous_identity = True
            updates.append("has_ambiguous_identity")
        if updates:
            matter.save(update_fields=updates)

        for row in rows:
            resolved[row.pk] = matter
            LegalMatterAlias.objects.get_or_create(
                matter=matter,
                snapshot=snapshot,
                defaults={
                    "record_id": row.record_id,
                    "source_year": row.source_year,
                    "source_nr": row.source_nr,
                    "source_row": row.source_row,
                },
            )
        if not colliding:
            OpinionResource.objects.get_or_create(matter=matter)

    return resolved, len(collisions)


def _generate(*, legal_snapshot, catalogue, dry_run, correlation_id, actor) -> MatchReport:
    items = list(opinion_eligible_items(LegalWorkItem.objects.filter(snapshot=legal_snapshot)))
    candidates = _candidates(catalogue)
    rarity = build_rarity([f"{c.filename_subject} {c.detected_subject}" for c in candidates])

    report = MatchReport(result=RESULT_GENERATED, dry_run=dry_run, considered_records=len(items))

    if dry_run:
        # Score without writing anything, so an operator can see what a live run
        # would decide before it publishes.
        for item in items:
            decision, _best, _runner, _relations = _decide(item, candidates, rarity)
            _count(report, decision)
        report.detail = (
            f"Proovikäivitus: {len(items)} kirjet, {report.matched} seotud, "
            f"{report.ambiguous} ebaselget, {report.unmatched} sidumata. "
            "Midagi ei avaldatud."
        )
        return report

    with transaction.atomic():
        resolved, collisions = _ensure_matters(items, legal_snapshot)
        report.identity_collisions = collisions

        snapshot = LegalOpinionMatchSnapshot(
            legal_snapshot=legal_snapshot,
            opinion_catalogue_snapshot=catalogue,
            matcher_version=MATCHER_VERSION,
            considered_item_count=len(items),
            is_current=False,
        )
        snapshot.save()

        decisions: list[LegalOpinionDecision] = []
        relation_plan: list[tuple[int, list]] = []

        for item in items:
            matter = resolved[item.pk]
            decision_value, best, runner_up, extra = _decide(item, candidates, rarity)

            # An ambiguous identity can never carry a link, whatever the
            # documents say.
            if matter.has_ambiguous_identity and decision_value == MatchDecision.MATCHED:
                decision_value = MatchDecision.AMBIGUOUS

            score = best.score if best else Decimal("0.00")
            runner = runner_up.score if runner_up else Decimal("0.00")
            row = LegalOpinionDecision(
                snapshot=snapshot,
                legal_item=item,
                matter=matter,
                decision=decision_value,
                score=score,
                runner_up_score=runner,
                score_margin=score - runner,
                candidate_count=len(extra) + (1 if best else 0),
                evidence_codes=sorted(best.evidence) if best else [],
                contradiction_codes=sorted(best.contradictions) if best else [],
            )
            decisions.append(row)
            relation_plan.append((len(decisions) - 1, [best, *extra] if best else []))
            _count(report, decision_value)

        LegalOpinionDecision.objects.bulk_create(decisions, batch_size=200)

        relations: list[LegalOpinionDocumentRelation] = []
        for index, scored_list in relation_plan:
            decision = decisions[index]
            if decision.decision != MatchDecision.MATCHED or not scored_list:
                continue
            primary = scored_list[0]
            relations.append(
                LegalOpinionDocumentRelation(
                    decision=decision,
                    entry_id=primary.candidate.entry_id,
                    role=DocumentRole.PRIMARY,
                    is_primary=True,
                    score=primary.score,
                    evidence_codes=sorted(primary.evidence),
                )
            )
            for secondary in scored_list[1:]:
                role = SECONDARY_ROLE.get(secondary.candidate.classification)
                if role is None:
                    continue
                relations.append(
                    LegalOpinionDocumentRelation(
                        decision=decision,
                        entry_id=secondary.candidate.entry_id,
                        role=role,
                        is_primary=False,
                        score=secondary.score,
                        evidence_codes=sorted(secondary.evidence),
                    )
                )
        LegalOpinionDocumentRelation.objects.bulk_create(relations, batch_size=200)

        snapshot_updates = LegalOpinionMatchSnapshot.objects.filter(pk=snapshot.pk)
        snapshot_updates.update(
            matched_count=report.matched,
            ambiguous_count=report.ambiguous,
            unmatched_count=report.unmatched,
        )
        LegalOpinionMatchSnapshot.objects.filter(is_current=True).update(is_current=False)
        snapshot.is_current = True
        snapshot.save(update_fields=["is_current"])

        report.primary_relations = sum(1 for r in relations if r.is_primary)
        report.secondary_relations = len(relations) - report.primary_relations
        report.snapshot_id = snapshot.pk

        record_event(
            action=AuditAction.OPINION_MATCH_GENERATED,
            obj=snapshot,
            actor=actor,
            correlation_id=correlation_id,
            change_summary={
                "snapshot_id": snapshot.pk,
                "legal_snapshot_id": legal_snapshot.pk,
                "catalogue_snapshot_id": catalogue.pk,
                "considered": len(items),
                "matched": report.matched,
                "ambiguous": report.ambiguous,
                "unmatched": report.unmatched,
                "identity_collisions": collisions,
                "matcher_version": MATCHER_VERSION,
            },
        )

    report.detail = (
        f"Sobitamine avaldatud: {report.matched} seotud, "
        f"{report.ambiguous} ebaselget, {report.unmatched} sidumata."
    )
    return report


def _decide(item, candidates, rarity):
    """Score every candidate for one record and pick a decision.

    Returns the decision, the best scored candidate, the runner-up, and the
    documents that should be grouped with the winner.
    """
    scored = [
        score_candidate(
            topic=item.topic,
            sent_date=item.sent_date,
            received_date=item.received_date,
            recipient=item.recipient,
            candidate=candidate,
            rarity=rarity,
        )
        for candidate in candidates
    ]
    viable = [s for s in scored if not s.blocked]
    viable.sort(key=lambda s: s.score, reverse=True)

    if not viable:
        return MatchDecision.UNMATCHED, None, None, []

    best = viable[0]
    runner_up = viable[1] if len(viable) > 1 else None

    if best.candidate.classification in NEVER_PRIMARY:
        return MatchDecision.UNMATCHED, best, runner_up, []

    margin = best.score - (runner_up.score if runner_up else Decimal("0.00"))
    if best.score >= THRESHOLD_MATCH and margin >= MIN_MARGIN:
        return MatchDecision.MATCHED, best, runner_up, _group(best, viable[1:])
    if best.score >= THRESHOLD_AMBIGUOUS:
        return MatchDecision.AMBIGUOUS, best, runner_up, []
    return MatchDecision.UNMATCHED, best, runner_up, []


def _group(primary, others):
    """Documents that belong with the winner rather than competing with it.

    Grouping is deliberately narrow: near the same date *and* sharing the
    subject evidence. A shared ministry and a shared week is how unrelated
    business ends up attached to the wrong letter.
    """
    grouped = []
    primary_date = primary.candidate.filename_date or primary.candidate.detected_date
    for other in others:
        role = SECONDARY_ROLE.get(other.candidate.classification)
        if role is None:
            continue
        other_date = other.candidate.filename_date or other.candidate.detected_date
        if primary_date and other_date and abs((other_date - primary_date).days) > GROUPING_DAYS:
            continue
        if other.score < THRESHOLD_AMBIGUOUS:
            continue
        grouped.append(other)
    return grouped


def _count(report: MatchReport, decision: str) -> None:
    if decision == MatchDecision.MATCHED:
        report.matched += 1
    elif decision == MatchDecision.AMBIGUOUS:
        report.ambiguous += 1
    else:
        report.unmatched += 1
