"""Report opinion source coverage, split the way the questions actually differ.

One "linked %" figure would blend four different questions, so this reports
them apart:

- **private source coverage** — how many sent matters a private document
  answers;
- **public exact recovery** — how many the public corpus alone recovered;
- **combined document coverage** — how many any exact document answers;
- **automatic link coverage** — how many carry an automatic link right now.

An article-only confirmation is counted separately from all of them, because
a public page confirming a position is not a document, and "no known
document" is never evidence that no opinion was sent.

Read-only: one pass over the current match snapshot's decisions.
"""

import datetime as dt

from django.core.management.base import BaseCommand

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.legal_work.models import MatchDecision
from apps.legal_work.opinion_eligibility import opinion_eligible_q
from apps.legal_work.opinion_match_models import (
    LegalOpinionDocumentRelation,
    LegalOpinionMatchSnapshot,
    LegalOpinionPageRelation,
)


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Report sent-matter opinion coverage by source: private, public, "
        "combined, automatic links, and article-only confirmations."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--from-date",
            type=dt.date.fromisoformat,
            default=dt.date(2025, 1, 1),
            metavar="YYYY-MM-DD",
            help="Count sent matters from this sent date on.",
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        boundary = options["from_date"]

        snapshot = LegalOpinionMatchSnapshot.objects.filter(is_current=True).first()
        if snapshot is None:
            self.emit(
                as_json,
                {"result": "skipped", "detail": "Sobitamist ei ole veel arvutatud."},
                "Sobitamist ei ole veel arvutatud.",
                style=self.style.WARNING,
            )
            return

        decisions = (
            snapshot.decisions.filter(legal_item__sent_date__gte=boundary)
            .filter(opinion_eligible_q("legal_item__"))
            .select_related("legal_item")
        )
        primaries = {
            relation.decision_id: relation
            for relation in LegalOpinionDocumentRelation.objects.filter(
                decision__snapshot=snapshot, is_primary=True
            )
        }
        page_confirmed = set(
            LegalOpinionPageRelation.objects.filter(decision__snapshot=snapshot).values_list(
                "decision_id", flat=True
            )
        )

        totals = {
            "sent_matters": 0,
            "private_document": 0,
            "public_exact_document": 0,
            "both_sources": 0,
            "public_only_document": 0,
            "article_only": 0,
            "no_known_document": 0,
            "matched": 0,
            "ambiguous": 0,
            "unmatched": 0,
        }
        for decision in decisions:
            totals["sent_matters"] += 1
            if decision.decision == MatchDecision.MATCHED:
                totals["matched"] += 1
            elif decision.decision == MatchDecision.AMBIGUOUS:
                totals["ambiguous"] += 1
            else:
                totals["unmatched"] += 1

            relation = primaries.get(decision.pk)
            if relation is not None:
                has_private = relation.entry_id is not None
                has_public = relation.public_document_id is not None
                if has_private:
                    totals["private_document"] += 1
                if has_public:
                    totals["public_exact_document"] += 1
                if has_private and has_public:
                    totals["both_sources"] += 1
                if has_public and not has_private:
                    totals["public_only_document"] += 1
            elif decision.pk in page_confirmed:
                totals["article_only"] += 1
            else:
                totals["no_known_document"] += 1

        sent = totals["sent_matters"] or 1
        coverage = {
            "private_source_coverage_pct": round(100 * totals["private_document"] / sent, 1),
            "public_exact_recovery_pct": round(100 * totals["public_only_document"] / sent, 1),
            "combined_document_coverage_pct": round(
                100 * (totals["private_document"] + totals["public_only_document"]) / sent,
                1,
            ),
            "automatic_link_coverage_pct": round(100 * totals["matched"] / sent, 1),
        }
        payload = {
            "result": "ok",
            "from_date": boundary.isoformat(),
            "match_snapshot_id": snapshot.pk,
            "matcher_version": snapshot.matcher_version,
            **totals,
            **coverage,
        }
        message = (
            f"{totals['sent_matters']} saadetud asja alates {boundary.isoformat()}: "
            f"privaatne dokument {totals['private_document']}, "
            f"ainult avalik {totals['public_only_document']}, "
            f"mõlemad {totals['both_sources']}, "
            f"ainult artikkel {totals['article_only']}, "
            f"dokumendita {totals['no_known_document']}."
        )
        self.emit(as_json, payload, message, style=self.style.SUCCESS)
