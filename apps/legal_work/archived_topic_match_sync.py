"""Publish one archive matcher run as an immutable, verified snapshot.

Like the current matcher's publication, nothing is downloaded here and no file
exists, so there is no source, no artifact and no import run. Unlike it, the run
identity has **four** parts rather than three: the legal snapshot, the archive
snapshot, the matcher version — and the exact current-topic match snapshot it
deferred to.

That fourth part is what makes the fallback correct. The archive only considers
records the current matcher did not match, so "which records were considered"
is a function of that run. When the current matcher runs again, this snapshot's
population is stale even if nothing else changed, and pinning the dependency in
the unique constraint is what turns that from a silent wrong answer into a new
run.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from django.db import transaction

from apps.audit.models import AuditAction
from apps.audit.services import record_event

from .archived_topic_matching import ARCHIVE_MATCHER_VERSION, match_archive
from .consultation import consultation_eligible_items
from .models import (
    ArchivedTopicSnapshot,
    CurrentTopicItem,
    LegalArchivedTopicMatch,
    LegalArchivedTopicMatchSnapshot,
    LegalCurrentTopicMatchSnapshot,
    LegalWorkItem,
    LegalWorkSnapshot,
    MatchDecision,
    SyncResult,
)
from .sync import EXIT_FAILED, EXIT_OK

logger = logging.getLogger("dashkoda.legal_work.archived_topic_match")

LOCK_NAME = "dashkoda.legal_work.match_archived_topics"


class ArchiveMatchRunError(RuntimeError):
    """The archive match run could not complete. Messages are safe to log."""


@dataclass
class ArchiveMatchReport:
    """Aggregates only. Never a topic, a candidate title or a URL."""

    result: str
    detail: str = ""
    dry_run: bool = False
    snapshot_id: int | None = None
    considered_items: int = 0
    matched: int = 0
    ambiguous: int = 0
    unmatched: int = 0
    archive_matcher_version: str = ARCHIVE_MATCHER_VERSION

    @property
    def exit_code(self) -> int:
        return EXIT_FAILED if self.result == SyncResult.FAILED else EXIT_OK

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "detail": self.detail,
            "dry_run": self.dry_run,
            "snapshot_id": self.snapshot_id,
            "considered_items": self.considered_items,
            "matched": self.matched,
            "ambiguous": self.ambiguous,
            "unmatched": self.unmatched,
            "archive_matcher_version": self.archive_matcher_version,
        }


def run_archive_matching(*, dry_run: bool = False, actor=None) -> ArchiveMatchReport:
    correlation_id = uuid.uuid4()
    try:
        return _run(dry_run=dry_run, actor=actor, correlation_id=correlation_id)
    except ArchiveMatchRunError as error:
        return _fail(str(error), correlation_id)
    except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
        return _fail(f"{type(error).__name__}: {error}".replace("\n", " "), correlation_id)


def _run(*, dry_run, actor, correlation_id) -> ArchiveMatchReport:
    legal_snapshot = LegalWorkSnapshot.objects.filter(is_current=True).first()
    if legal_snapshot is None:
        raise ArchiveMatchRunError("Kehtivat õigusloome hetkeseisu ei ole.")

    current_match = LegalCurrentTopicMatchSnapshot.objects.filter(is_current=True).first()
    if current_match is None:
        raise ArchiveMatchRunError(
            "Kehtivat hetkel käsil sobitamist ei ole; arhiiv on varuallikas."
        )
    if current_match.legal_snapshot_id != legal_snapshot.pk:
        raise ArchiveMatchRunError(
            "Hetkel käsil sobitamine käib teise õigusloome hetkeseisu kohta."
        )

    archive_snapshot = ArchivedTopicSnapshot.objects.filter(is_current=True).first()
    if archive_snapshot is None:
        raise ArchiveMatchRunError("Kehtivat arhiivi hetkeseisu ei ole.")

    existing = LegalArchivedTopicMatchSnapshot.objects.filter(
        legal_snapshot=legal_snapshot,
        archived_topic_snapshot=archive_snapshot,
        current_topic_match_snapshot=current_match,
        matcher_version=ARCHIVE_MATCHER_VERSION,
    ).first()
    if existing is not None:
        return _unchanged(existing, dry_run=dry_run, correlation_id=correlation_id)

    # The fallback population: consultation-eligible, and not already answered
    # by the current listing.
    already_matched = set(
        current_match.matches.filter(decision=MatchDecision.MATCHED).values_list(
            "legal_item_id", flat=True
        )
    )
    legal_items = list(
        consultation_eligible_items(LegalWorkItem.objects.filter(snapshot=legal_snapshot))
        .exclude(pk__in=already_matched)
        .order_by("pk")
    )

    # A consultation still on the current listing belongs to the current
    # matcher. Excluding those addresses stops the archive re-judging the same
    # page under looser rules and producing a second, contradictory verdict.
    excluded_urls = frozenset(
        CurrentTopicItem.objects.filter(
            snapshot=current_match.current_topic_snapshot_id
        ).values_list("canonical_url", flat=True)
    )

    archive_items = list(archive_snapshot.items.all())
    outcomes = match_archive(legal_items, archive_items, excluded_urls=excluded_urls)

    counts = {
        MatchDecision.MATCHED: 0,
        MatchDecision.AMBIGUOUS: 0,
        MatchDecision.UNMATCHED: 0,
    }
    for outcome in outcomes:
        counts[outcome.decision] += 1

    if dry_run:
        return ArchiveMatchReport(
            result=SyncResult.IMPORTED,
            detail="Kuivkäivitus: arhiivi sobitamine arvutati, midagi ei avaldatud.",
            dry_run=True,
            considered_items=len(legal_items),
            matched=counts[MatchDecision.MATCHED],
            ambiguous=counts[MatchDecision.AMBIGUOUS],
            unmatched=counts[MatchDecision.UNMATCHED],
        )

    _verify(
        outcomes,
        legal_item_ids={item.pk for item in legal_items},
        candidate_ids={item.pk for item in archive_items},
        matchable_ids={item.pk for item in archive_items if item.is_matchable},
        already_matched=already_matched,
    )

    with transaction.atomic():
        snapshot = LegalArchivedTopicMatchSnapshot(
            legal_snapshot=legal_snapshot,
            archived_topic_snapshot=archive_snapshot,
            current_topic_match_snapshot=current_match,
            matcher_version=ARCHIVE_MATCHER_VERSION,
            considered_item_count=len(legal_items),
            matched_count=counts[MatchDecision.MATCHED],
            ambiguous_count=counts[MatchDecision.AMBIGUOUS],
            unmatched_count=counts[MatchDecision.UNMATCHED],
            is_current=False,
        )
        snapshot.save()
        LegalArchivedTopicMatch.objects.bulk_create(
            [
                LegalArchivedTopicMatch(
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
            action=AuditAction.ARCHIVED_TOPIC_MATCH_GENERATED,
            obj=snapshot,
            actor=actor,
            correlation_id=correlation_id,
            change_summary={
                "snapshot_id": snapshot.pk,
                "legal_snapshot_id": legal_snapshot.pk,
                "archived_topic_snapshot_id": archive_snapshot.pk,
                "current_topic_match_snapshot_id": current_match.pk,
                "matcher_version": ARCHIVE_MATCHER_VERSION,
                "considered_item_count": snapshot.considered_item_count,
                "matched_count": snapshot.matched_count,
                "ambiguous_count": snapshot.ambiguous_count,
                "unmatched_count": snapshot.unmatched_count,
            },
        )

    logger.info(
        "archived_topic_match considered=%s matched=%s ambiguous=%s unmatched=%s",
        snapshot.considered_item_count,
        snapshot.matched_count,
        snapshot.ambiguous_count,
        snapshot.unmatched_count,
    )
    return ArchiveMatchReport(
        result=SyncResult.IMPORTED,
        detail="Uus arhiivi sobitamise hetkeseis avaldatud.",
        snapshot_id=snapshot.pk,
        considered_items=snapshot.considered_item_count,
        matched=snapshot.matched_count,
        ambiguous=snapshot.ambiguous_count,
        unmatched=snapshot.unmatched_count,
    )


def _verify(outcomes, *, legal_item_ids, candidate_ids, matchable_ids, already_matched) -> None:
    seen: set[int] = set()
    for outcome in outcomes:
        if outcome.legal_item_id not in legal_item_ids:
            raise ArchiveMatchRunError("Arhiivi tulemus viitab võõrale õigusloome kirjele.")
        if outcome.legal_item_id in already_matched:
            raise ArchiveMatchRunError("Arhiiv vaatas kirjet, mille hetkel käsil juba sidus.")
        if outcome.legal_item_id in seen:
            raise ArchiveMatchRunError("Arhiivi sobitamine andis ühele kirjele mitu otsust.")
        seen.add(outcome.legal_item_id)

        if outcome.best_candidate_id is not None:
            if outcome.best_candidate_id not in candidate_ids:
                raise ArchiveMatchRunError("Arhiivi tulemus viitab võõrale arhiivikirjele.")
            if outcome.best_candidate_id not in matchable_ids:
                raise ArchiveMatchRunError("Arhiivi tulemus viitab lugemata arhiivikirjele.")
        if outcome.decision == MatchDecision.MATCHED and outcome.best_candidate_id is None:
            raise ArchiveMatchRunError("Seotud otsusel puudub kandidaat.")
        if outcome.decision not in MatchDecision.values:
            raise ArchiveMatchRunError("Arhiivi sobitamine andis tundmatu otsuse.")
        for value in (outcome.score, outcome.runner_up_score, outcome.score_margin):
            if value < 0 or value > 100:
                raise ArchiveMatchRunError("Arhiivi skoor on väljaspool lubatud vahemikku.")
        if outcome.runner_up_score > outcome.score:
            raise ArchiveMatchRunError("Teise koha skoor on parimast kõrgem.")
        if outcome.score_margin != outcome.score - outcome.runner_up_score:
            raise ArchiveMatchRunError("Skooride vahe ei vasta skooridele.")

    missing = legal_item_ids - seen
    if missing:
        raise ArchiveMatchRunError(f"{len(missing)} vaadatud kirjet jäi otsuseta.")


def _verify_written(snapshot, *, expected: int) -> None:
    written = snapshot.matches.count()
    if written != expected:
        raise ArchiveMatchRunError("Kirjutatud otsuste arv ei vasta arvutatule.")
    declared = snapshot.matched_count + snapshot.ambiguous_count + snapshot.unmatched_count
    if declared != snapshot.considered_item_count or declared != written:
        raise ArchiveMatchRunError("Hetkeseisu deklareeritud arvud ei vasta ridadele.")


def _publish_current(snapshot) -> None:
    retired = (
        LegalArchivedTopicMatchSnapshot.objects.select_for_update()
        .filter(is_current=True)
        .exclude(pk=snapshot.pk)
    )
    for previous in retired:
        previous.is_current = False
        previous.save(update_fields=["is_current"])
    snapshot.is_current = True
    snapshot.save(update_fields=["is_current"])
    if LegalArchivedTopicMatchSnapshot.objects.filter(is_current=True).count() != 1:
        raise ArchiveMatchRunError("Kehtivaid arhiivi sobitamisi on rohkem kui üks.")


def _unchanged(snapshot, *, dry_run, correlation_id) -> ArchiveMatchReport:
    if not dry_run:
        record_event(
            action=AuditAction.ARCHIVED_TOPIC_MATCH_UNCHANGED,
            obj=snapshot,
            correlation_id=correlation_id,
            change_summary={
                "snapshot_id": snapshot.pk,
                "matcher_version": snapshot.matcher_version,
                "considered_item_count": snapshot.considered_item_count,
            },
        )
    return ArchiveMatchReport(
        result=SyncResult.UNCHANGED,
        detail="Samad sisendid ja sama arhiivisobitaja versioon; midagi ei arvutatud uuesti.",
        dry_run=dry_run,
        snapshot_id=snapshot.pk,
        considered_items=snapshot.considered_item_count,
        matched=snapshot.matched_count,
        ambiguous=snapshot.ambiguous_count,
        unmatched=snapshot.unmatched_count,
        archive_matcher_version=snapshot.matcher_version,
    )


def _fail(message: str, correlation_id) -> ArchiveMatchReport:
    record_event(
        action=AuditAction.ARCHIVED_TOPIC_MATCH_FAILED,
        object_type="legal_work.archive_match_run",
        object_id=str(correlation_id),
        correlation_id=correlation_id,
        change_summary={
            "matcher_version": ARCHIVE_MATCHER_VERSION,
            "detail": message[:300],
        },
    )
    logger.warning("archived_topic_match failed: %s", message)
    return ArchiveMatchReport(result=SyncResult.FAILED, detail=message)
