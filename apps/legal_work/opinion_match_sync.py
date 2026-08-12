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

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field, replace
from decimal import Decimal

from django.db import transaction

from apps.audit.services import record_event
from apps.core.feed_sync import describe_error
from apps.legal_work.audit_actions import LegalWorkAudit

from .models import LegalWorkItem, LegalWorkSnapshot, MatchDecision
from .opinion_classification import DocumentClassification
from .opinion_eligibility import opinion_eligible_items
from .opinion_identity import IDENTITY_VERSION, resolve_matter_key
from .opinion_match_models import (
    DocumentRole,
    LegalMatter,
    LegalMatterAlias,
    LegalOpinionDecision,
    LegalOpinionDocumentRelation,
    LegalOpinionMatchSnapshot,
    LegalOpinionPageRelation,
    OpinionResource,
)
from .opinion_matching import (
    CONTRADICTION_COMPETING_CLAIM,
    EVIDENCE_DATE_EXACT,
    EVIDENCE_DATE_NEAR,
    MATCHER_VERSION,
    MIN_MARGIN,
    TEXT_TWIN_WINDOW_DAYS,
    THRESHOLD_AMBIGUOUS,
    THRESHOLD_MATCH,
    Candidate,
    build_rarity,
    date_agreement,
    score_candidate,
    texts_are_same_letter,
)
from .opinion_models import OpinionCatalogueEntry, OpinionCatalogueSnapshot
from .opinion_pdf import ExtractionStatus, ValidationStatus
from .public_opinion_models import PublicOpinionDocument, PublicOpinionSnapshot

logger = logging.getLogger("dashkoda.legal_work.opinion_match_sync")

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
    page_relations: int = 0
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
            "page_relations": self.page_relations,
            "identity_collisions": self.identity_collisions,
            "matcher_version": self.matcher_version,
        }


@dataclass
class _Preview:
    """What a dry run knows about one record: enough to resolve competition.

    Deliberately the same shape `_resolve_competing_primaries` reads off a real
    `LegalOpinionDecision`, so the one function can rank a preview and a live
    row identically.

    `score` has no default on purpose. It used to default to zero, and the one
    construction site never passed it, so every previewed claim tied at zero and
    a date-gap tie demoted both sides instead of picking the stronger one.
    Requiring it makes that omission a `TypeError` rather than a silent
    disagreement with the live run.
    """

    legal_item: object
    decision: str
    score: Decimal
    contradiction_codes: list = field(default_factory=list)


def run_opinion_matching(*, dry_run: bool = False, actor=None) -> MatchReport:
    """Match one legal snapshot against the available opinion sources.

    The private catalogue is required, exactly as before. The public corpus
    joins when one is published and its absence changes nothing: matching a
    deployment that has never crawled Koda.ee is the 1.1 behaviour.
    """
    correlation_id = uuid.uuid4()

    legal_snapshot = LegalWorkSnapshot.objects.filter(is_current=True).first()
    catalogue = OpinionCatalogueSnapshot.objects.filter(is_current=True).first()
    public_corpus = PublicOpinionSnapshot.objects.filter(is_current=True).first()
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
        and published.public_opinion_snapshot_id == (public_corpus.pk if public_corpus else None)
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
            public_corpus=public_corpus,
            dry_run=dry_run,
            correlation_id=correlation_id,
            actor=actor,
        )
    except Exception as error:  # noqa: BLE001 - a failure must keep the last good snapshot
        message = describe_error(error)
        logger.warning("opinion_matching failed: %s", type(error).__name__)
        record_event(
            action=LegalWorkAudit.OPINION_MATCH_FAILED,
            obj=legal_snapshot,
            actor=actor,
            correlation_id=correlation_id,
            change_summary={"error": type(error).__name__},
        )
        return MatchReport(result=RESULT_FAILED, detail=message, dry_run=dry_run)


def _candidates(
    catalogue: OpinionCatalogueSnapshot,
    public_corpus: PublicOpinionSnapshot | None,
) -> list[Candidate]:
    """Every document the matcher may consider, one candidate per letter.

    Private entries build the candidate exactly as 1.1 did. A public document
    then either **joins** an existing candidate or **creates** one, which is
    how a public-only letter enters matching. Where both sources describe the
    same letter, the private description wins every per-field tie: a filename
    a person typed outranks one derived from an upload URL.

    A public document joins a private candidate on either of two identities:

    - **the same bytes** — one blob, trivially the same document;
    - **the same letter re-exported** — Koda.ee routinely publishes the
      letter it sent as a different file, and the rehearsal against
      production data measured what treating those as competitors does:
      twenty-nine letters tied their own re-publication at a margin of zero
      and every one demoted to ambiguous. Equivalence is decided by the
      whole document — near-identical extracted text with a document date in
      the same week (`texts_are_same_letter`) — never by a similar title or
      a near date alone, which keep two files distinct.
    """
    merged: dict[int, Candidate] = {}
    private_by_text: dict[str, int] = {}
    private_by_date: dict[dt.date, list[int]] = {}

    rows = (
        OpinionCatalogueEntry.objects.filter(
            snapshot=catalogue,
            blob__validation_status=ValidationStatus.VALID,
            extraction__status=ExtractionStatus.EXTRACTED,
        )
        .exclude(extraction__isnull=True)
        .select_related("blob", "extraction")
    )
    for row in rows:
        merged[row.blob_id] = Candidate(
            blob_id=row.blob_id,
            entry_id=row.pk,
            extraction_id=row.extraction_id,
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
        if row.extraction.text_sha256:
            private_by_text.setdefault(row.extraction.text_sha256, row.blob_id)
        for value in (row.filename_date, row.extraction.detected_date):
            if value is not None:
                private_by_date.setdefault(value, []).append(row.blob_id)

    if public_corpus is None:
        return list(merged.values())

    public_rows = (
        PublicOpinionDocument.objects.filter(
            snapshot=public_corpus,
            is_present=True,
            blob__validation_status=ValidationStatus.VALID,
            extraction__status=ExtractionStatus.EXTRACTED,
        )
        .exclude(extraction__isnull=True)
        .select_related("blob", "extraction", "page")
    )
    for row in public_rows:
        twin_blob_id = row.blob_id
        if twin_blob_id not in merged and row.extraction.text_sha256:
            twin_blob_id = private_by_text.get(row.extraction.text_sha256, row.blob_id)
        if twin_blob_id not in merged:
            twin_blob_id = _text_twin(row, merged, private_by_date) or row.blob_id
        existing = merged.get(twin_blob_id)
        if existing is not None:
            if existing.public_document_id is None:
                merged[twin_blob_id] = replace(
                    existing,
                    public_document_id=row.pk,
                    page_published_date=row.page.published_date,
                )
            continue
        merged[row.blob_id] = Candidate(
            blob_id=row.blob_id,
            public_document_id=row.pk,
            extraction_id=row.extraction_id,
            classification=row.classification,
            filename_date=row.filename_date,
            detected_date=row.extraction.detected_date,
            filename_subject=row.filename_subject or row.page.title,
            detected_subject=row.extraction.detected_subject,
            recipient=row.filename_recipient or row.extraction.detected_recipient,
            text=row.extraction.text,
            first_page_text=row.extraction.first_page_text,
            is_readable=True,
            page_published_date=row.page.published_date,
        )

    return list(merged.values())


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


def _planned_relations(decisions, relation_plan):
    """Every relation a matched decision would publish, in publication order.

    Yields `(index, primary, [(secondary, role), ...])`.

    Shared by the live path, which turns these into rows, and the dry run, which
    counts them. Two implementations of "which documents does this decision
    link?" would be two chances for a preview to disagree with publication, and
    that disagreement is exactly what a dry run exists to rule out.
    """
    for index, scored_list in relation_plan:
        if decisions[index].decision != MatchDecision.MATCHED or not scored_list:
            continue
        primary, *rest = scored_list
        secondaries = [
            (secondary, SECONDARY_ROLE[secondary.candidate.classification])
            for secondary in rest
            if secondary.candidate.classification in SECONDARY_ROLE
        ]
        yield index, primary, secondaries


def _preview_identity(items) -> tuple[dict[int, str], set[str], int]:
    """Resolve durable identity for a dry run, writing nothing.

    Returns the key each record would resolve to, the keys that cannot carry a
    link, and how many keys collided in this snapshot.

    A key is unusable either because two materially different records in this
    snapshot claim it, or because a previous run already flagged the matter.
    `has_ambiguous_identity` is only ever set and never cleared, so reading the
    stored flag alongside this snapshot's collisions gives the same answer the
    live run would reach after `_ensure_matters`.
    """
    grouped, collisions = resolve_matter_key(items)
    keys_by_item = {row.pk: key for key, rows in grouped.items() for row in rows}
    already_flagged = set(
        LegalMatter.objects.filter(
            matter_key__in=list(grouped), has_ambiguous_identity=True
        ).values_list("matter_key", flat=True)
    )
    return keys_by_item, collisions | already_flagged, len(collisions)


def _generate(
    *, legal_snapshot, catalogue, public_corpus, dry_run, correlation_id, actor
) -> MatchReport:
    items = list(opinion_eligible_items(LegalWorkItem.objects.filter(snapshot=legal_snapshot)))
    candidates = _candidates(catalogue, public_corpus)
    rarity = build_rarity([f"{c.filename_subject} {c.detected_subject}" for c in candidates])
    page_candidates = _page_candidates(public_corpus)

    report = MatchReport(result=RESULT_GENERATED, dry_run=dry_run, considered_records=len(items))

    if dry_run:
        # Score without writing anything, so an operator can see what a live run
        # would decide before it publishes — including the competing-claim
        # resolution, which is part of that decision. Leaving it out made a dry
        # run promise one more match than the live run would produce, which is
        # the one thing a dry run must not do.
        #
        # Two inputs to that decision have to be reproduced exactly, or the
        # preview goes wrong in the same way for a different reason:
        #
        # - the **candidate score**. `_resolve_competing_primaries` breaks a
        #   date-gap tie on score, and a preview that left it at zero made every
        #   tied claim look equal — so both sides were demoted and the dry run
        #   under-promised a match the live run would keep;
        # - the **durable identity**. A live run demotes a match whose matter has
        #   an ambiguous identity, and reports how many keys collided.
        #
        # Both are derived read-only here: `resolve_matter_key` writes nothing,
        # and an existing matter's flag is only ever set, never cleared, so the
        # effective value is what the live run would find.
        keys_by_item, ambiguous_keys, collisions = _preview_identity(items)
        report.identity_collisions = collisions

        preview: list = []
        preview_plan: list = []
        for item in items:
            decision, best, _runner, extra = _decide(item, candidates, rarity)
            if decision == MatchDecision.MATCHED and keys_by_item.get(item.pk) in ambiguous_keys:
                decision = MatchDecision.AMBIGUOUS
            score = best.score if best else Decimal("0.00")
            preview.append(_Preview(item, decision, score=score))
            preview_plan.append((len(preview) - 1, [best, *extra] if best else []))
            _count(report, decision)
        _resolve_competing_primaries(preview, preview_plan, report)

        # Count the links publication would create, through the same planner the
        # live path uses. A preview that reported no relations at all was
        # technically true — a dry run publishes nothing — but useless: the
        # operator wants to know how many resource pages this run would fill in.
        for _index, _primary, secondaries in _planned_relations(preview, preview_plan):
            report.primary_relations += 1
            report.secondary_relations += len(secondaries)
        report.page_relations = sum(
            len(pages) for pages in _planned_page_relations(preview, page_candidates).values()
        )

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
            public_opinion_snapshot=public_corpus,
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

        # One document may legitimately answer several matters — a joint letter
        # covering two bills does exactly that. What is *not* legitimate is two
        # records claiming the same letter as their primary when one claim is
        # materially weaker: the real pilot produced a pair on the same bill
        # where one record was sent the day after the letter and the other
        # fifteen days later, and only the first can be the letter's subject.
        #
        # So a primary claim is kept only by the record whose date agreement is
        # strongest; the rest become ambiguous and render as plain text. This is
        # a general rule about competing claims, not a rule about these records.
        _resolve_competing_primaries(decisions, relation_plan, report)

        LegalOpinionDecision.objects.bulk_create(decisions, batch_size=200)

        relations: list[LegalOpinionDocumentRelation] = []
        for index, primary, secondaries in _planned_relations(decisions, relation_plan):
            decision = decisions[index]
            relations.append(_relation(decision, primary, DocumentRole.PRIMARY, primary=True))
            for secondary, role in secondaries:
                relations.append(_relation(decision, secondary, role, primary=False))
        LegalOpinionDocumentRelation.objects.bulk_create(relations, batch_size=200)

        page_relations: list[LegalOpinionPageRelation] = []
        for index, pages in _planned_page_relations(decisions, page_candidates).items():
            for scored_page in pages:
                page_relations.append(
                    LegalOpinionPageRelation(
                        decision=decisions[index],
                        page_id=scored_page.candidate.page_id,
                        score=scored_page.score,
                        evidence_codes=sorted(scored_page.evidence),
                    )
                )
        LegalOpinionPageRelation.objects.bulk_create(page_relations, batch_size=200)
        report.page_relations = len(page_relations)

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
            action=LegalWorkAudit.OPINION_MATCH_GENERATED,
            obj=snapshot,
            actor=actor,
            correlation_id=correlation_id,
            change_summary={
                "snapshot_id": snapshot.pk,
                "legal_snapshot_id": legal_snapshot.pk,
                "catalogue_snapshot_id": catalogue.pk,
                "public_opinion_snapshot_id": public_corpus.pk if public_corpus else None,
                "considered": len(items),
                "matched": report.matched,
                "ambiguous": report.ambiguous,
                "unmatched": report.unmatched,
                "page_relations": report.page_relations,
                "identity_collisions": collisions,
                "matcher_version": MATCHER_VERSION,
            },
        )

    report.detail = (
        f"Sobitamine avaldatud: {report.matched} seotud, "
        f"{report.ambiguous} ebaselget, {report.unmatched} sidumata."
    )
    return report


def _resolve_competing_primaries(decisions, relation_plan, report) -> None:
    """Demote every weaker claim when one document is claimed by several matters.

    Strength is date agreement first — the signal this matcher is calibrated on —
    then score. A tie leaves both ambiguous rather than picking arbitrarily.
    """
    claims: dict[int, list[int]] = {}
    for index, scored_list in relation_plan:
        if decisions[index].decision != MatchDecision.MATCHED or not scored_list:
            continue
        claims.setdefault(scored_list[0].candidate.blob_id, []).append(index)

    for indexes in claims.values():
        if len(indexes) < 2:
            continue

        def strength(index):
            best = relation_plan[index][1][0]
            gap = date_agreement(decisions[index].legal_item.sent_date, best.candidate)[1]
            return (-(gap if gap is not None else 10**6), decisions[index].score)

        ranked = sorted(indexes, key=strength, reverse=True)
        top, second = strength(ranked[0]), strength(ranked[1])
        losers = ranked[1:] if top != second else ranked

        for index in losers:
            decisions[index].decision = MatchDecision.AMBIGUOUS
            decisions[index].contradiction_codes = sorted(
                {*decisions[index].contradiction_codes, CONTRADICTION_COMPETING_CLAIM}
            )
            relation_plan[index] = (relation_plan[index][0], [])
            report.matched -= 1
            report.ambiguous += 1


def _text_twin(row, merged, private_by_date) -> int | None:
    """The private candidate this public document re-exports, if any.

    Bounded on purpose: only private letters dated within a week of the
    public document are read at all, and the whole-text similarity bar in
    `texts_are_same_letter` decides. The date bucket is what keeps this
    O(few) per document rather than a quadratic sweep of both corpora.
    """
    dates = [d for d in (row.filename_date, row.extraction.detected_date) if d is not None]
    if not dates:
        return None
    seen: set[int] = set()
    best: int | None = None
    for anchor in dates:
        for offset in range(-TEXT_TWIN_WINDOW_DAYS, TEXT_TWIN_WINDOW_DAYS + 1):
            for blob_id in private_by_date.get(anchor + dt.timedelta(days=offset), []):
                if blob_id in seen:
                    continue
                seen.add(blob_id)
                candidate = merged.get(blob_id)
                if candidate is None or candidate.entry_id is None:
                    continue
                if texts_are_same_letter(candidate.text, row.extraction.text):
                    if best is None or blob_id < best:
                        best = blob_id
    return best


def _relation(decision, scored, role, *, primary: bool) -> LegalOpinionDocumentRelation:
    """One relation row: the document identity plus every provenance it has."""
    candidate = scored.candidate
    return LegalOpinionDocumentRelation(
        decision=decision,
        blob_id=candidate.blob_id,
        extraction_id=candidate.extraction_id,
        entry_id=candidate.entry_id,
        public_document_id=candidate.public_document_id,
        role=role,
        is_primary=primary,
        score=scored.score,
        evidence_codes=sorted(scored.evidence),
    )


def _page_candidates(public_corpus) -> list[Candidate]:
    """Article-only public pages, shaped for the shared scorer.

    Only pages with no attachment rows at all qualify: a page whose PDF failed
    to download is document provenance awaiting a retry, not an article-only
    confirmation. The page's title stands where a filename subject would and
    its publication date is the only date evidence — which is exactly why the
    confidence bar below insists on date agreement.
    """
    if public_corpus is None:
        return []
    pages = public_corpus.pages.filter(is_present=True, documents__isnull=True).exclude(
        body_text=""
    )
    return [
        Candidate(
            blob_id=-page.pk,
            page_id=page.pk,
            classification=DocumentClassification.OPINION,
            filename_date=None,
            detected_date=None,
            filename_subject=page.title,
            detected_subject="",
            recipient="",
            text=page.body_text,
            first_page_text=page.body_text[:2000],
            is_readable=True,
            page_published_date=page.published_date,
        )
        for page in pages
    ]


def _planned_page_relations(decisions, page_candidates) -> dict[int, list]:
    """Confident article-only page evidence for records no document answered.

    Held to the *document* match bar plus one extra condition: the page's
    publication date must actually agree with the sent date. An article names
    a bill the way a hundred articles name bills; without date agreement the
    subject overlap alone is exactly the plausible-wrong-link this project
    refuses. A page claimed confidently by two records goes to the one with
    the stronger date agreement, and a tie attaches it to neither.
    """
    if not page_candidates:
        return {}
    rarity = build_rarity([candidate.filename_subject for candidate in page_candidates])

    claims: dict[int, list[tuple[int, object]]] = {}
    for index, decision in enumerate(decisions):
        if decision.decision == MatchDecision.MATCHED:
            continue
        item = decision.legal_item
        scored = [
            score_candidate(
                topic=item.topic,
                sent_date=item.sent_date,
                received_date=item.received_date,
                recipient=item.recipient,
                candidate=candidate,
                rarity=rarity,
            )
            for candidate in page_candidates
        ]
        usable = sorted((s for s in scored if not s.blocked), key=lambda s: s.score, reverse=True)
        if not usable:
            continue
        best = usable[0]
        runner_up = usable[1].score if len(usable) > 1 else Decimal("0.00")
        if best.score < THRESHOLD_MATCH or best.score - runner_up < MIN_MARGIN:
            continue
        if EVIDENCE_DATE_EXACT not in best.evidence and EVIDENCE_DATE_NEAR not in best.evidence:
            continue
        claims.setdefault(best.candidate.page_id, []).append((index, best))

    results: dict[int, list] = {}
    for claimants in claims.values():
        if len(claimants) == 1:
            index, best = claimants[0]
            results.setdefault(index, []).append(best)
            continue

        def strength(pair):
            index, best = pair
            gap = date_agreement(decisions[index].legal_item.sent_date, best.candidate)[1]
            return (-(gap if gap is not None else 10**6), best.score)

        ranked = sorted(claimants, key=strength, reverse=True)
        if strength(ranked[0]) != strength(ranked[1]):
            index, best = ranked[0]
            results.setdefault(index, []).append(best)
    return results


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
    usable = [s for s in scored if not s.blocked]

    # Two populations, deliberately. Only some documents may *lead* a topic, but
    # the ones that may not — annexes, comparison tables — are exactly what
    # belongs beside the one that does, so they are scored and kept for
    # grouping rather than discarded.
    leaders = sorted((s for s in usable if s.can_be_primary), key=lambda s: s.score, reverse=True)
    companions = [s for s in usable if not s.can_be_primary]

    if not leaders:
        return MatchDecision.UNMATCHED, None, None, []

    best = leaders[0]
    runner_up = leaders[1] if len(leaders) > 1 else None

    margin = best.score - (runner_up.score if runner_up else Decimal("0.00"))
    if best.score >= THRESHOLD_MATCH and margin >= MIN_MARGIN:
        return MatchDecision.MATCHED, best, runner_up, _group(best, leaders[1:] + companions)
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
