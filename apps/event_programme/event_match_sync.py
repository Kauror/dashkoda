"""Running the event matcher and publishing what it decided.

The population is every item in the **current** programme snapshot; the
candidates are every public event page discovered so far. The programme snapshot
is pinned by foreign key and the page set by high-water mark, so any stored
decision names the exact inputs that produced it — see `event_match_models` for
why the two are pinned differently.

Publication is all-or-nothing inside one transaction: the new snapshot's rows
are written, then it becomes current and the previous one steps down. A reader
therefore moves between whole consistent runs and never sees half a matcher.

Nothing here mutates an `EventProgrammeItem`. There is no manual path into any
of it and no way for an operator to name an event, a page or a URL.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Max

from apps.audit.services import record_event
from apps.event_programme.audit_actions import EventProgrammeAudit
from apps.events.public_models import PublicEventResource

from .event_match_models import EventPublicMatch, EventPublicMatchSnapshot
from .event_matching import MATCHER_VERSION, Candidate, MatchDecision, match_event
from .models import EventProgrammeItem, EventProgrammeSnapshot

logger = logging.getLogger("dashkoda.event_programme.match")

LOCK_NAME = "dashkoda.event_programme.match_public_event_links"
LOCKED_MESSAGE = "Sündmuste viidete sobitamine juba käib."


class EventMatchError(RuntimeError):
    """Matching could not run at all."""


@dataclass
class MatchReport:
    """Counts and flags from one run. Never a title, a URL or a slug."""

    considered: int = 0
    matched: int = 0
    ambiguous: int = 0
    unmatched: int = 0
    resource_high_water: int = 0
    resource_count: int = 0
    snapshot_id: int | None = None
    evidence_counts: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "considered": self.considered,
            "matched": self.matched,
            "ambiguous": self.ambiguous,
            "unmatched": self.unmatched,
            "resource_high_water": self.resource_high_water,
            "resource_count": self.resource_count,
            "snapshot_id": self.snapshot_id,
            "matcher_version": MATCHER_VERSION,
            "evidence_counts": dict(sorted(self.evidence_counts.items())),
        }


def _pages_by_date(high_water: int) -> dict[dt.date, list[Candidate]]:
    """Every page at or below the high-water mark, grouped by its date.

    Bounded by the mark rather than by "everything now", so a discovery run
    finishing mid-match cannot change what this run was scored against.
    """
    grouped: dict[dt.date, list[Candidate]] = {}
    rows = PublicEventResource.objects.filter(id__lte=high_water).values_list(
        "id", "canonical_url", "title", "starts_on"
    )
    for resource_id, url, title, starts_on in rows.iterator(chunk_size=2000):
        grouped.setdefault(starts_on, []).append(
            Candidate(resource_id=resource_id, canonical_url=url, title=title, starts_on=starts_on)
        )
    return grouped


def run_event_matching(*, dry_run: bool = False, actor=None) -> MatchReport:
    """Match the current programme against the discovered public pages."""
    correlation_id = uuid.uuid4()
    programme = EventProgrammeSnapshot.objects.filter(is_current=True).first()
    if programme is None:
        raise EventMatchError("Sündmuste programmi kehtiv hetkeseis puudub.")

    high_water = PublicEventResource.objects.aggregate(top=Max("id"))["top"] or 0
    report = MatchReport(
        resource_high_water=high_water,
        resource_count=PublicEventResource.objects.filter(id__lte=high_water).count(),
    )
    pages = _pages_by_date(high_water)

    items = EventProgrammeItem.objects.filter(snapshot=programme).values_list(
        "event_id", "event_name", "start_date"
    )

    decisions = []
    for event_id, name, start_date in items.iterator(chunk_size=2000):
        decision = match_event(
            event_id=event_id, name=name, starts_on=start_date, pages_by_date=pages
        )
        decisions.append(decision)
        report.considered += 1
        if decision.decision == MatchDecision.MATCHED:
            report.matched += 1
        elif decision.decision == MatchDecision.AMBIGUOUS:
            report.ambiguous += 1
        else:
            report.unmatched += 1
        for code in decision.evidence_codes:
            report.evidence_counts[code] = report.evidence_counts.get(code, 0) + 1

    if dry_run:
        logger.info("event match dry run: %s", report.as_dict())
        return report

    with transaction.atomic():
        snapshot = EventPublicMatchSnapshot.objects.create(
            programme_snapshot=programme,
            resource_high_water=high_water,
            matcher_version=MATCHER_VERSION,
            considered_count=report.considered,
            matched_count=report.matched,
            ambiguous_count=report.ambiguous,
            unmatched_count=report.unmatched,
        )
        EventPublicMatch.objects.bulk_create(
            [
                EventPublicMatch(
                    snapshot=snapshot,
                    event_id=decision.event_id,
                    resource_id=decision.resource_id,
                    decision=decision.decision,
                    score=round(decision.score, 4),
                    runner_up_score=round(decision.runner_up_score, 4),
                    score_margin=decision.score_margin,
                    evidence_codes=list(decision.evidence_codes),
                )
                for decision in decisions
            ],
            batch_size=1000,
        )
        # Current last, so a reader never sees a snapshot without its rows.
        EventPublicMatchSnapshot.objects.filter(is_current=True).update(is_current=False)
        snapshot.is_current = True
        snapshot.save(update_fields=["is_current"])
        report.snapshot_id = snapshot.pk

    record_event(
        action=EventProgrammeAudit.EVENT_PUBLIC_LINKS_MATCHED,
        obj=snapshot,
        actor=actor,
        correlation_id=correlation_id,
        change_summary=report.as_dict(),
    )
    logger.info("event match published: %s", report.as_dict())
    return report
