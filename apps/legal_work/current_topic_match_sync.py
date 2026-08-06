"""Publish one matcher run as an immutable, verified match snapshot.

Nothing is collected here and no file exists, so this run has no source, no
artifact and no import run. Its identity is exactly the three things that
determine its output — the legal snapshot read, the catalogue read and the
matcher version — and that triple is a unique constraint on the snapshot, which
makes "identical inputs report unchanged" a single `exists()` rather than a
checksum over fabricated bytes.

Everything is verified before publication and the whole publication is one
transaction. A run that fails verification writes nothing and leaves the
previous match snapshot current, exactly as a failed collection leaves the
previous catalogue published.

A `matched` decision published here is what makes a topic clickable on the
Õigusloome page. It reaches a viewer only through
:mod:`apps.legal_work.topic_links`, which re-checks that this snapshot is still
the current one for both of its inputs before offering any address.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from django.db import transaction

from apps.audit.models import AuditAction
from apps.audit.services import record_event

from .consultation import consultation_eligible_items
from .current_topic_matching import MATCHER_VERSION, match_all
from .models import (
    CurrentTopicItem,
    CurrentTopicSnapshot,
    LegalCurrentTopicMatch,
    LegalCurrentTopicMatchSnapshot,
    LegalWorkItem,
    LegalWorkSnapshot,
    MatchDecision,
    SyncResult,
)
from .sync import EXIT_FAILED, EXIT_OK

logger = logging.getLogger("dashkoda.legal_work.current_topic_match")

LOCK_NAME = "dashkoda.legal_work.match_current_topics"


class MatchRunError(RuntimeError):
    """The match run could not be completed. Messages are safe to log."""


@dataclass
class MatchOutcomeReport:
    """What one match run produced. Never carries a topic, a title or a URL."""

    result: str
    detail: str = ""
    snapshot_id: int | None = None
    legal_item_count: int = 0
    current_topic_count: int = 0
    matched_count: int = 0
    ambiguous_count: int = 0
    unmatched_count: int = 0
    matcher_version: str = MATCHER_VERSION
    dry_run: bool = False
    warnings: list = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return EXIT_FAILED if self.result == SyncResult.FAILED else EXIT_OK

    def as_dict(self) -> dict:
        """The command's whole JSON contract: aggregates and identifiers only."""
        return {
            "result": self.result,
            "detail": self.detail,
            "dry_run": self.dry_run,
            "snapshot_id": self.snapshot_id,
            "legal_item_count": self.legal_item_count,
            "current_topic_count": self.current_topic_count,
            "matched_count": self.matched_count,
            "ambiguous_count": self.ambiguous_count,
            "unmatched_count": self.unmatched_count,
            "matcher_version": self.matcher_version,
        }


def run_current_topic_matching(*, dry_run: bool = False, actor=None) -> MatchOutcomeReport:
    """Match the current legal snapshot against the current catalogue."""
    correlation_id = uuid.uuid4()
    try:
        return _run(dry_run=dry_run, actor=actor, correlation_id=correlation_id)
    except MatchRunError as error:
        return _fail(str(error), correlation_id)
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _fail(f"{type(error).__name__}: {error}".replace("\n", " "), correlation_id)


def _run(*, dry_run: bool, actor, correlation_id) -> MatchOutcomeReport:
    legal_snapshot = LegalWorkSnapshot.objects.filter(is_current=True).first()
    if legal_snapshot is None:
        raise MatchRunError("Kehtivat õigusloome hetkeseisu ei ole; sobitamist ei tehtud.")

    topic_snapshot = CurrentTopicSnapshot.objects.filter(is_current=True).first()
    if topic_snapshot is None:
        raise MatchRunError("Kehtivat hetkel käsil hetkeseisu ei ole; sobitamist ei tehtud.")

    existing = LegalCurrentTopicMatchSnapshot.objects.filter(
        legal_snapshot=legal_snapshot,
        current_topic_snapshot=topic_snapshot,
        matcher_version=MATCHER_VERSION,
    ).first()
    if existing is not None:
        return _unchanged(existing, dry_run=dry_run, correlation_id=correlation_id)

    # Only consultation-eligible records take part: open, and with no opinion
    # yet sent. The rule is `apps.legal_work.consultation`'s, not this module's,
    # because the archive matcher and the viewer resolver must agree with it
    # exactly or a record could be linked by one path and not another.
    legal_items = list(
        consultation_eligible_items(
            LegalWorkItem.objects.filter(snapshot=legal_snapshot)
        ).order_by("pk")
    )
    candidate_items = list(
        CurrentTopicItem.objects.filter(snapshot=topic_snapshot).order_by("source_order")
    )

    outcomes = match_all(legal_items, candidate_items)

    counts = {
        MatchDecision.MATCHED: 0,
        MatchDecision.AMBIGUOUS: 0,
        MatchDecision.UNMATCHED: 0,
    }
    for outcome in outcomes:
        counts[outcome.decision] += 1

    if dry_run:
        return MatchOutcomeReport(
            result=SyncResult.IMPORTED,
            detail="Kuivkäivitus: sobitamine arvutati, midagi ei avaldatud.",
            dry_run=True,
            matcher_version=MATCHER_VERSION,
            legal_item_count=len(legal_items),
            current_topic_count=len(candidate_items),
            matched_count=counts[MatchDecision.MATCHED],
            ambiguous_count=counts[MatchDecision.AMBIGUOUS],
            unmatched_count=counts[MatchDecision.UNMATCHED],
        )

    legal_item_ids = {item.pk for item in legal_items}
    candidate_ids = {item.pk for item in candidate_items}
    _verify(outcomes, legal_item_ids=legal_item_ids, candidate_ids=candidate_ids)

    with transaction.atomic():
        snapshot = LegalCurrentTopicMatchSnapshot(
            legal_snapshot=legal_snapshot,
            current_topic_snapshot=topic_snapshot,
            matcher_version=MATCHER_VERSION,
            legal_item_count=len(legal_items),
            matched_count=counts[MatchDecision.MATCHED],
            ambiguous_count=counts[MatchDecision.AMBIGUOUS],
            unmatched_count=counts[MatchDecision.UNMATCHED],
            is_current=False,
        )
        snapshot.save()
        LegalCurrentTopicMatch.objects.bulk_create(
            [
                LegalCurrentTopicMatch(
                    snapshot=snapshot,
                    legal_item_id=outcome.legal_item_id,
                    best_candidate_id=outcome.best_candidate_id,
                    decision=outcome.decision,
                    score=outcome.score,
                    runner_up_score=outcome.runner_up_score,
                    score_margin=outcome.score_margin,
                    candidate_count=outcome.candidate_count,
                    evidence_codes=outcome.evidence_codes,
                )
                for outcome in outcomes
            ]
        )
        _verify_written(snapshot, expected=len(outcomes))
        _publish_current(snapshot)
        record_event(
            action=AuditAction.CURRENT_TOPIC_MATCH_GENERATED,
            obj=snapshot,
            actor=actor,
            correlation_id=correlation_id,
            # Identifiers, counts and a version. No topic, no candidate title,
            # no URL and no evidence text.
            change_summary={
                "snapshot_id": snapshot.pk,
                "legal_snapshot_id": legal_snapshot.pk,
                "current_topic_snapshot_id": topic_snapshot.pk,
                "matcher_version": MATCHER_VERSION,
                "legal_item_count": snapshot.legal_item_count,
                "current_topic_count": len(candidate_items),
                "matched_count": snapshot.matched_count,
                "ambiguous_count": snapshot.ambiguous_count,
                "unmatched_count": snapshot.unmatched_count,
            },
        )

    logger.info(
        "current_topic_match generated items=%s matched=%s ambiguous=%s unmatched=%s",
        snapshot.legal_item_count,
        snapshot.matched_count,
        snapshot.ambiguous_count,
        snapshot.unmatched_count,
    )
    return MatchOutcomeReport(
        result=SyncResult.IMPORTED,
        detail="Uus sobitamise hetkeseis avaldatud.",
        snapshot_id=snapshot.pk,
        matcher_version=snapshot.matcher_version,
        legal_item_count=snapshot.legal_item_count,
        current_topic_count=len(candidate_items),
        matched_count=snapshot.matched_count,
        ambiguous_count=snapshot.ambiguous_count,
        unmatched_count=snapshot.unmatched_count,
    )


def _verify(outcomes, *, legal_item_ids: set[int], candidate_ids: set[int]) -> None:
    """Everything that must be true before a single row is written."""
    seen: set[int] = set()
    for outcome in outcomes:
        if outcome.legal_item_id not in legal_item_ids:
            raise MatchRunError("Sobitamise tulemus viitab võõrale õigusloome kirjele.")
        if outcome.legal_item_id in seen:
            raise MatchRunError("Sobitamine andis ühele kirjele mitu otsust.")
        seen.add(outcome.legal_item_id)

        if outcome.best_candidate_id is not None and outcome.best_candidate_id not in candidate_ids:
            raise MatchRunError("Sobitamise tulemus viitab võõrale hetkel käsil teemale.")
        if outcome.decision == MatchDecision.MATCHED and outcome.best_candidate_id is None:
            raise MatchRunError("Seotud otsusel puudub kandidaat.")
        if outcome.decision not in MatchDecision.values:
            raise MatchRunError("Sobitamine andis tundmatu otsuse.")

        for value in (outcome.score, outcome.runner_up_score, outcome.score_margin):
            if value < 0 or value > 100:
                raise MatchRunError("Sobitamise skoor on väljaspool lubatud vahemikku.")
        if outcome.runner_up_score > outcome.score:
            raise MatchRunError("Teise koha skoor on parimast kõrgem.")
        if outcome.score_margin != outcome.score - outcome.runner_up_score:
            raise MatchRunError("Skooride vahe ei vasta skooridele.")

    missing = legal_item_ids - seen
    if missing:
        raise MatchRunError(f"{len(missing)} avatud kirjet jäi otsuseta.")


def _verify_written(snapshot, *, expected: int) -> None:
    """The declared counts must equal what is actually on the table."""
    written = snapshot.matches.count()
    if written != expected:
        raise MatchRunError("Kirjutatud otsuste arv ei vasta arvutatule.")
    declared = snapshot.matched_count + snapshot.ambiguous_count + snapshot.unmatched_count
    if declared != snapshot.legal_item_count or declared != written:
        raise MatchRunError("Hetkeseisu deklareeritud arvud ei vasta ridadele.")


def _publish_current(snapshot) -> None:
    """Make `snapshot` the only current match snapshot."""
    retired = (
        LegalCurrentTopicMatchSnapshot.objects.select_for_update()
        .filter(is_current=True)
        .exclude(pk=snapshot.pk)
    )
    for previous in retired:
        previous.is_current = False
        previous.save(update_fields=["is_current"])
    snapshot.is_current = True
    snapshot.save(update_fields=["is_current"])
    if LegalCurrentTopicMatchSnapshot.objects.filter(is_current=True).count() != 1:
        raise MatchRunError("Kehtivaid sobitamise hetkeseise on rohkem kui üks.")


def _unchanged(snapshot, *, dry_run: bool, correlation_id) -> MatchOutcomeReport:
    if not dry_run:
        record_event(
            action=AuditAction.CURRENT_TOPIC_MATCH_UNCHANGED,
            obj=snapshot,
            correlation_id=correlation_id,
            change_summary={
                "snapshot_id": snapshot.pk,
                "matcher_version": snapshot.matcher_version,
                "legal_item_count": snapshot.legal_item_count,
            },
        )
    return MatchOutcomeReport(
        result=SyncResult.UNCHANGED,
        detail="Samad sisendid ja sama sobitaja versioon; midagi ei arvutatud uuesti.",
        dry_run=dry_run,
        snapshot_id=snapshot.pk,
        legal_item_count=snapshot.legal_item_count,
        current_topic_count=snapshot.current_topic_snapshot.item_count,
        matched_count=snapshot.matched_count,
        ambiguous_count=snapshot.ambiguous_count,
        unmatched_count=snapshot.unmatched_count,
        matcher_version=snapshot.matcher_version,
    )


def _fail(message: str, correlation_id) -> MatchOutcomeReport:
    record_event(
        action=AuditAction.CURRENT_TOPIC_MATCH_FAILED,
        # A failed run has no snapshot to point at, so the trail is keyed by the
        # run itself. The correlation id ties it to whatever else that run wrote.
        object_type="legal_work.match_run",
        object_id=str(correlation_id),
        correlation_id=correlation_id,
        change_summary={"matcher_version": MATCHER_VERSION, "detail": message[:300]},
    )
    logger.warning("current_topic_match failed: %s", message)
    return MatchOutcomeReport(result=SyncResult.FAILED, detail=message)
