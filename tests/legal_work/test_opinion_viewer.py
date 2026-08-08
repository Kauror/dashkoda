"""Eligibility, link precedence, the resource page and the protected PDF route.

The rule this file exists to hold down is that **a sent record never shows a
consultation and an open record never shows an opinion**. Those two populations
are disjoint by construction, and the tests assert the construction rather than
trusting it.

Everything private is checked from the other direction too: what the endpoint
refuses, and what never appears in the markup.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import pytest
from django.urls import reverse

from apps.legal_work.consultation import CONSULTATION_ELIGIBLE
from apps.legal_work.models import (
    LegalWorkItem,
    LegalWorkSnapshot,
    MatchDecision,
    SentStatus,
)
from apps.legal_work.opinion_eligibility import (
    OPINION_ELIGIBLE,
    is_opinion_eligible,
    opinion_eligible_items,
)
from apps.legal_work.opinion_match_models import (
    LegalMatter,
    LegalOpinionDecision,
    LegalOpinionDocumentRelation,
    LegalOpinionMatchSnapshot,
    OpinionResource,
)
from apps.legal_work.opinion_match_sync import MatchReport, run_opinion_matching
from apps.legal_work.opinion_matching import THRESHOLD_MATCH
from apps.legal_work.opinion_models import OpinionCatalogueEntry, OpinionDocumentBlob
from apps.legal_work.opinion_pdf import ValidationStatus
from apps.legal_work.topic_links import resolve_opinion_links

from .opinion_match_factory import letter, publish_catalogue

pytestmark = pytest.mark.django_db

SENT = dt.date(2026, 3, 10)


@dataclass
class _CandidateStub:
    """Only what the competing-claim tie-break reads off a candidate.

    Since 1.2 a claim is keyed by the blob — bytes are the document — so the
    stub carries `blob_id` where it used to carry `entry_id`.
    """

    blob_id: int
    filename_date: dt.date
    detected_date: dt.date | None = None
    page_published_date: dt.date | None = None


@dataclass
class _ScoredStub:
    """A scored candidate, reduced to the tie-break's view of one."""

    blob_id: int
    candidate_date: dt.date

    @property
    def candidate(self) -> _CandidateStub:
        return _CandidateStub(blob_id=self.blob_id, filename_date=self.candidate_date)


@dataclass
class _ItemStub:
    """A legal record, reduced to the date the tie-break compares."""

    sent_date: dt.date


# -- eligibility ------------------------------------------------------------


class TestEligibility:
    def test_sent_with_a_date_is_eligible(self, imported_snapshot):
        item = LegalWorkItem.objects.filter(snapshot=imported_snapshot).first()
        LegalWorkItem.objects.filter(pk=item.pk).update(sent_status=SentStatus.SENT, sent_date=SENT)
        item.refresh_from_db()

        assert is_opinion_eligible(item)

    def test_the_schema_itself_forbids_sent_without_a_date(self, imported_snapshot):
        """The second half of the rule is defence over a database guarantee.

        `legalworkitem_sent_date_matches_status` already makes `sent` and a
        present `sent_date` equivalent, so the state cannot be written at all.
        The eligibility expression restates it anyway, because a read path
        should not depend on remembering which constraints exist.
        """
        from django.db.utils import IntegrityError

        item = LegalWorkItem.objects.filter(snapshot=imported_snapshot).first()

        with pytest.raises(IntegrityError):
            LegalWorkItem.objects.filter(pk=item.pk).update(
                sent_status=SentStatus.SENT, sent_date=None
            )

    def test_the_rule_excludes_a_dateless_sent_record_if_one_ever_existed(self):
        """Checked against an unsaved row, since the schema refuses to store one."""
        assert not is_opinion_eligible(LegalWorkItem(sent_status=SentStatus.SENT, sent_date=None))

    @pytest.mark.parametrize(
        "status", [SentStatus.PENDING, SentStatus.NOT_SENT, SentStatus.INVALID]
    )
    def test_an_unsent_status_is_excluded(self, imported_snapshot, status):
        item = LegalWorkItem.objects.filter(snapshot=imported_snapshot).first()
        # The same constraint requires an unsent row to carry no date.
        LegalWorkItem.objects.filter(pk=item.pk).update(sent_status=status, sent_date=None)
        item.refresh_from_db()

        assert not is_opinion_eligible(item)

    def test_the_two_eligibility_rules_can_never_both_apply(self, imported_snapshot):
        """The whole precedence rests on these populations being disjoint."""
        both = (
            LegalWorkItem.objects.filter(snapshot=imported_snapshot)
            .filter(CONSULTATION_ELIGIBLE)
            .filter(OPINION_ELIGIBLE)
        )

        assert both.count() == 0

    def test_the_queryset_helper_agrees_with_the_row_check(self, imported_snapshot):
        LegalWorkItem.objects.filter(snapshot=imported_snapshot).update(
            sent_status=SentStatus.SENT, sent_date=SENT
        )
        items = LegalWorkItem.objects.filter(snapshot=imported_snapshot)

        assert opinion_eligible_items(items).count() == sum(
            1 for i in items if is_opinion_eligible(i)
        )


# -- a matched world --------------------------------------------------------


@pytest.fixture
def matched_world(opinion_roots, opinion_source, imported_snapshot):
    """One sent record, one letter that answers it, matched by the real matcher."""
    source, _ = opinion_roots
    item = LegalWorkItem.objects.filter(snapshot=imported_snapshot).first()
    topic = "Maksukorralduse seaduse muutmise seaduse eelnõu 130 SE"
    LegalWorkItem.objects.filter(pk=item.pk).update(
        topic=topic,
        sent_status=SentStatus.SENT,
        sent_date=SENT,
        received_date=dt.date(2026, 2, 1),
        recipient="Rahandusministeerium",
    )
    # Every other record is deliberately not eligible.
    LegalWorkItem.objects.filter(snapshot=imported_snapshot).exclude(pk=item.pk).update(
        sent_status=SentStatus.PENDING, sent_date=None
    )

    publish_catalogue(
        source,
        [
            letter(
                date=SENT,
                document_date=SENT + dt.timedelta(days=1),
                subject="Arvamus maksukorralduse seaduse muutmise eelnõu 130 SE kohta",
            ),
            letter(
                date=dt.date(2026, 1, 5),
                subject="Arvamus hoopis teise valdkonna seaduse kohta",
                reference="4/9",
            ),
        ],
    )
    report = run_opinion_matching()
    item.refresh_from_db()
    return item, report


class TestMatching:
    def test_the_matcher_publishes_one_current_snapshot(self, matched_world):
        _item, report = matched_world

        assert report.result == "generated"
        assert LegalOpinionMatchSnapshot.objects.filter(is_current=True).count() == 1

    def test_counts_add_up_to_the_considered_population(self, matched_world):
        _item, report = matched_world

        assert report.matched + report.ambiguous + report.unmatched == report.considered_records

    def test_one_decision_exists_per_considered_record(self, matched_world):
        _item, report = matched_world
        snapshot = LegalOpinionMatchSnapshot.objects.get(is_current=True)

        assert (
            LegalOpinionDecision.objects.filter(snapshot=snapshot).count()
            == report.considered_records
        )

    def test_a_repeat_run_is_unchanged(self, matched_world):
        assert run_opinion_matching().result == "unchanged"

    def test_a_dry_run_publishes_nothing(self, opinion_roots, opinion_source, imported_snapshot):
        source, _ = opinion_roots
        LegalWorkItem.objects.filter(snapshot=imported_snapshot).update(
            sent_status=SentStatus.SENT, sent_date=SENT
        )
        publish_catalogue(source, [letter(date=SENT)])

        report = run_opinion_matching(dry_run=True)

        assert report.dry_run is True
        assert not LegalOpinionMatchSnapshot.objects.exists()

    def test_a_dry_run_predicts_the_same_counts_a_live_run_produces(
        self, opinion_roots, opinion_source, imported_snapshot
    ):
        """A dry run exists to say what a live run would do.

        The competing-claim resolution is part of that decision, and leaving it
        out of the preview made a dry run promise one more match than the live
        run delivered — on the real pilot, 29 against 28.
        """
        source, _ = opinion_roots
        items = list(LegalWorkItem.objects.filter(snapshot=imported_snapshot))
        topic = "Maksukorralduse seaduse muutmise seaduse eelnõu 130 SE"
        # Two records on the same instrument, sent a fortnight apart, with one
        # letter between them: exactly the shape that produced the competing
        # claim in the real catalogue.
        for offset, item in enumerate(items[:2]):
            LegalWorkItem.objects.filter(pk=item.pk).update(
                topic=f"{topic} ({offset})",
                sent_status=SentStatus.SENT,
                sent_date=SENT + dt.timedelta(days=offset * 14),
                received_date=dt.date(2026, 2, 1),
                recipient="Rahandusministeerium",
            )
        LegalWorkItem.objects.filter(snapshot=imported_snapshot).exclude(
            pk__in=[i.pk for i in items[:2]]
        ).update(sent_status=SentStatus.PENDING, sent_date=None)
        publish_catalogue(
            source,
            [letter(date=SENT, subject=f"Arvamus {topic} kohta")],
        )

        preview = run_opinion_matching(dry_run=True)
        live = run_opinion_matching()

        assert (preview.matched, preview.ambiguous, preview.unmatched) == (
            live.matched,
            live.ambiguous,
            live.unmatched,
        )

    def test_a_dry_run_breaks_a_date_tie_on_score_exactly_as_a_live_run_does(
        self, opinion_roots, opinion_source, imported_snapshot
    ):
        """Equal date gaps, unequal scores, one letter: the exact regression.

        `_resolve_competing_primaries` ranks a claim by date agreement first and
        by score second. The preview object never carried a score, so both
        claims tied at zero; the tie-break reads a tie as "neither is stronger"
        and demotes **both**. A live run, whose rows do carry the score, kept the
        stronger one. So the dry run reported one match fewer than the live run
        published — the opposite direction to the case already covered above,
        and invisible to it because those two records differ on date.

        Here they cannot: two records on the same instrument, sent two days
        either side of the letter, agree with it equally well. Only the
        recipient separates them, and therefore only the score.
        """
        source, _ = opinion_roots
        items = list(LegalWorkItem.objects.filter(snapshot=imported_snapshot))
        topic = "Maksukorralduse seaduse muutmise seaduse eelnõu 130 SE"
        stronger, weaker = items[0], items[1]

        # Identical topics, different received dates: two distinct durable
        # matters competing for one letter, rather than one colliding key.
        LegalWorkItem.objects.filter(pk=stronger.pk).update(
            topic=topic,
            sent_status=SentStatus.SENT,
            sent_date=SENT - dt.timedelta(days=2),
            received_date=dt.date(2026, 2, 1),
            recipient="Rahandusministeerium",
        )
        LegalWorkItem.objects.filter(pk=weaker.pk).update(
            topic=topic,
            sent_status=SentStatus.SENT,
            sent_date=SENT + dt.timedelta(days=2),
            received_date=dt.date(2026, 2, 2),
            recipient="Justiitsministeerium",
        )
        LegalWorkItem.objects.filter(snapshot=imported_snapshot).exclude(
            pk__in=[stronger.pk, weaker.pk]
        ).update(sent_status=SentStatus.PENDING, sent_date=None)

        publish_catalogue(
            source,
            [
                letter(
                    date=SENT,
                    recipient="Rahandusministeerium",
                    subject=f"Arvamus {topic} kohta",
                )
            ],
        )

        preview = run_opinion_matching(dry_run=True)
        live = run_opinion_matching()

        decisions = {row.legal_item_id: row for row in LegalOpinionDecision.objects.all()}
        strong, weak = decisions[stronger.pk], decisions[weaker.pk]

        # The scenario has to be the one described, or the test proves nothing.
        assert strong.score > weak.score, (
            "the two records must score differently for the tie-break to be "
            f"reached; got {strong.score} and {weak.score}"
        )
        assert min(strong.score, weak.score) >= THRESHOLD_MATCH, (
            "both records must independently reach a match, or they never "
            f"compete; got {strong.score} and {weak.score}"
        )

        # What the live run actually did.
        assert (live.matched, live.ambiguous) == (1, 1)
        assert strong.decision == MatchDecision.MATCHED
        assert weak.decision == MatchDecision.AMBIGUOUS

        # ...and what the dry run promised it would do.
        assert (preview.matched, preview.ambiguous, preview.unmatched) == (
            live.matched,
            live.ambiguous,
            live.unmatched,
        )
        assert preview.primary_relations == live.primary_relations == 1
        assert preview.secondary_relations == live.secondary_relations

    def test_the_tie_break_keeps_the_higher_scoring_claim(self):
        """The winner itself, at the level the dry run resolves it.

        `_resolve_competing_primaries` is the one function both paths use, and a
        preview now reaches it carrying the same score a published row would.
        """
        from decimal import Decimal

        from apps.legal_work.opinion_match_sync import _Preview, _resolve_competing_primaries

        report = MatchReport(result="generated", matched=2)
        scored = _ScoredStub(blob_id=7, candidate_date=SENT)
        previews = [
            _Preview(_ItemStub(SENT - dt.timedelta(days=2)), MatchDecision.MATCHED, Decimal("91")),
            _Preview(_ItemStub(SENT + dt.timedelta(days=2)), MatchDecision.MATCHED, Decimal("81")),
        ]

        _resolve_competing_primaries(previews, [(0, [scored]), (1, [scored])], report)

        assert previews[0].decision == MatchDecision.MATCHED
        assert previews[1].decision == MatchDecision.AMBIGUOUS
        assert (report.matched, report.ambiguous) == (1, 1)

    def test_a_dry_run_reports_the_identity_collisions_a_live_run_records(
        self, opinion_roots, opinion_source, imported_snapshot
    ):
        """A colliding key demotes a match, so a preview must see it too.

        The live run flags the matter in `_ensure_matters` and refuses to link
        it. The dry run writes nothing, so it derives the same answer from
        `resolve_matter_key` instead of ignoring the rule.
        """
        source, _ = opinion_roots
        items = list(LegalWorkItem.objects.filter(snapshot=imported_snapshot))
        topic = "Maksukorralduse seaduse muutmise seaduse eelnõu 130 SE"

        # Same topic *and* same received date: one key, two records, so the
        # matter is ambiguous and can never carry a link.
        for item in items[:2]:
            LegalWorkItem.objects.filter(pk=item.pk).update(
                topic=topic,
                sent_status=SentStatus.SENT,
                sent_date=SENT,
                received_date=dt.date(2026, 2, 1),
                recipient="Rahandusministeerium",
            )
        LegalWorkItem.objects.filter(snapshot=imported_snapshot).exclude(
            pk__in=[i.pk for i in items[:2]]
        ).update(sent_status=SentStatus.PENDING, sent_date=None)

        publish_catalogue(source, [letter(date=SENT, subject=f"Arvamus {topic} kohta")])

        preview = run_opinion_matching(dry_run=True)
        live = run_opinion_matching()

        assert live.identity_collisions == 1
        assert preview.identity_collisions == live.identity_collisions
        assert (preview.matched, preview.ambiguous, preview.unmatched) == (
            live.matched,
            live.ambiguous,
            live.unmatched,
        )
        assert preview.matched == 0, "an ambiguous identity can never carry a link"

    def test_no_legal_row_is_mutated(self, matched_world):
        item, _report = matched_world
        before = LegalWorkItem.objects.get(pk=item.pk)

        run_opinion_matching()

        after = LegalWorkItem.objects.get(pk=item.pk)
        assert (before.topic, before.sent_date, before.sent_status) == (
            after.topic,
            after.sent_date,
            after.sent_status,
        )

    def test_a_durable_matter_and_resource_exist(self, matched_world):
        item, _report = matched_world
        decision = LegalOpinionDecision.objects.get(legal_item=item)

        assert LegalMatter.objects.filter(pk=decision.matter_id).exists()
        assert OpinionResource.objects.filter(matter=decision.matter).exists()

    def test_at_most_one_primary_relation_per_decision(self, matched_world):
        for decision in LegalOpinionDecision.objects.all():
            primaries = decision.relations.filter(is_primary=True).count()
            assert primaries <= 1


# -- link precedence --------------------------------------------------------


class TestPrecedence:
    def test_a_sent_matched_record_resolves_to_its_resource(self, matched_world):
        item, report = matched_world
        if report.matched == 0:
            pytest.skip("matcher was conservative; precedence covered by the tests below")

        links = resolve_opinion_links([item.pk])

        assert item.pk in links
        assert links[item.pk].startswith("/oigusloome/arvamused/")

    def test_an_open_record_never_resolves_an_opinion(self, matched_world):
        item, _report = matched_world
        LegalWorkItem.objects.filter(pk=item.pk).update(
            sent_status=SentStatus.PENDING, sent_date=None
        )

        assert resolve_opinion_links([item.pk]) == {}

    def test_an_ambiguous_decision_never_resolves(self, matched_world):
        item, _report = matched_world
        LegalOpinionDecision.objects.filter(legal_item=item).update(
            decision=MatchDecision.AMBIGUOUS
        )

        assert resolve_opinion_links([item.pk]) == {}

    def test_an_unmatched_decision_never_resolves(self, matched_world):
        item, _report = matched_world
        LegalOpinionDecision.objects.filter(legal_item=item).update(
            decision=MatchDecision.UNMATCHED
        )

        assert resolve_opinion_links([item.pk]) == {}

    def test_a_retired_match_snapshot_never_resolves(self, matched_world):
        item, _report = matched_world
        LegalOpinionMatchSnapshot.objects.filter(is_current=True).update(is_current=False)

        assert resolve_opinion_links([item.pk]) == {}

    def test_an_ambiguous_identity_never_resolves(self, matched_world):
        item, _report = matched_world
        LegalMatter.objects.all().update(has_ambiguous_identity=True)

        assert resolve_opinion_links([item.pk]) == {}

    def test_a_quarantined_document_never_resolves(self, matched_world):
        item, _report = matched_world
        OpinionDocumentBlob.objects.all().update(
            validation_status=ValidationStatus.QUARANTINED_INVALID
        )

        assert resolve_opinion_links([item.pk]) == {}

    def test_an_empty_request_asks_nothing(self, django_assert_num_queries):
        with django_assert_num_queries(0):
            assert resolve_opinion_links([]) == {}

    def test_resolution_costs_two_queries_however_many_records(
        self, matched_world, django_assert_num_queries
    ):
        """Two since the public corpus: matched documents, then article-only
        confirmations for whatever those left. Never one per record."""
        every_id = list(LegalWorkItem.objects.values_list("pk", flat=True))
        assert len(every_id) > 1

        with django_assert_num_queries(2):
            resolve_opinion_links(every_id)


# -- the protected PDF endpoint --------------------------------------------


@pytest.fixture
def served_document(matched_world):
    relation = LegalOpinionDocumentRelation.objects.filter(is_primary=True).first()
    if relation is None:
        pytest.skip("no primary relation to serve")
    return relation


class TestDocumentEndpoint:
    def test_an_anonymous_request_is_refused(self, client, served_document):
        url = reverse("opinion-document", args=[served_document.entry.blob.public_id])

        response = client.get(url)

        assert response.status_code in (302, 403, 404)
        assert b"%PDF" not in response.content

    def test_an_authorised_request_receives_the_document(
        self, client, authenticate_viewer, served_document
    ):
        authenticate_viewer(client)
        url = reverse("opinion-document", args=[served_document.entry.blob.public_id])

        response = client.get(url)

        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert b"".join(response.streaming_content).startswith(b"%PDF")

    def test_the_response_carries_safe_headers(self, client, authenticate_viewer, served_document):
        authenticate_viewer(client)
        url = reverse("opinion-document", args=[served_document.entry.blob.public_id])

        response = client.get(url)

        assert response["X-Content-Type-Options"] == "nosniff"
        assert "no-store" in response["Cache-Control"]
        assert "private" in response["Cache-Control"]

    def test_no_server_path_appears_in_any_header(
        self, client, authenticate_viewer, served_document
    ):
        from apps.legal_work.opinion_storage import store_root

        authenticate_viewer(client)
        url = reverse("opinion-document", args=[served_document.entry.blob.public_id])

        response = client.get(url)

        joined = " ".join(f"{k}: {v}" for k, v in response.items())
        assert str(store_root()) not in joined
        assert served_document.entry.blob.sha256 not in joined
        assert served_document.entry.blob.storage_key not in joined

    def test_an_unknown_identifier_is_a_controlled_404(self, client, authenticate_viewer):
        import uuid

        authenticate_viewer(client)
        url = reverse("opinion-document", args=[uuid.uuid4()])

        assert client.get(url).status_code == 404

    def test_a_quarantined_document_is_refused(self, client, authenticate_viewer, served_document):
        authenticate_viewer(client)
        OpinionDocumentBlob.objects.all().update(
            validation_status=ValidationStatus.QUARANTINED_ACTIVE_CONTENT
        )
        url = reverse("opinion-document", args=[served_document.entry.blob.public_id])

        assert client.get(url).status_code == 404

    def test_a_document_of_a_retired_snapshot_is_refused(
        self, client, authenticate_viewer, served_document
    ):
        authenticate_viewer(client)
        LegalOpinionMatchSnapshot.objects.filter(is_current=True).update(is_current=False)
        url = reverse("opinion-document", args=[served_document.entry.blob.public_id])

        assert client.get(url).status_code == 404

    def test_a_missing_blob_file_is_refused_rather_than_erroring(
        self, client, authenticate_viewer, served_document
    ):
        from apps.legal_work.opinion_storage import blob_path

        authenticate_viewer(client)
        blob_path(served_document.entry.blob.sha256).unlink()
        url = reverse("opinion-document", args=[served_document.entry.blob.public_id])

        assert client.get(url).status_code == 404

    def test_the_url_cannot_carry_a_path(self):
        """The route accepts a UUID converter only, so traversal never parses."""
        from django.urls import NoReverseMatch

        with pytest.raises(NoReverseMatch):
            reverse("opinion-document", args=["../../etc/passwd"])

    def test_the_disposition_filename_is_sanitised(
        self, client, authenticate_viewer, served_document
    ):
        authenticate_viewer(client)
        OpinionCatalogueEntry.objects.filter(pk=served_document.entry_id).update(
            display_filename='../../evil"name.pdf'
        )
        url = reverse("opinion-document", args=[served_document.entry.blob.public_id])

        disposition = client.get(url)["Content-Disposition"]

        assert ".." not in disposition
        assert "/" not in disposition.split("filename=")[1].split(";")[0]

    def test_serving_makes_no_outbound_request(
        self, client, authenticate_viewer, served_document, monkeypatch
    ):
        import requests

        def forbidden(*args, **kwargs):
            raise AssertionError("serving a document attempted an outbound request")

        monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
        monkeypatch.setattr("apps.core.public_http.fetch", forbidden)

        authenticate_viewer(client)
        url = reverse("opinion-document", args=[served_document.entry.blob.public_id])

        assert client.get(url).status_code == 200


# -- the resource page ------------------------------------------------------


class TestResourcePage:
    def _resource(self, item):
        decision = LegalOpinionDecision.objects.get(legal_item=item)
        return OpinionResource.objects.get(matter=decision.matter)

    def test_an_anonymous_request_is_refused(self, client, matched_world):
        item, _ = matched_world
        url = reverse("opinion-resource", args=[self._resource(item).public_id])

        assert client.get(url).status_code in (302, 403, 404)

    def test_an_authorised_request_renders(self, client, authenticate_viewer, matched_world):
        item, _ = matched_world
        authenticate_viewer(client)
        url = reverse("opinion-resource", args=[self._resource(item).public_id])

        assert client.get(url).status_code == 200

    def test_no_matching_internals_reach_the_page(self, client, authenticate_viewer, matched_world):
        item, _ = matched_world
        authenticate_viewer(client)
        url = reverse("opinion-resource", args=[self._resource(item).public_id])

        body = client.get(url).content.decode()

        for leaked in ("matcher_version", "opinion-1.0", "date-exact", "runner_up", "score_margin"):
            assert leaked not in body

    def test_it_is_styled_with_the_shared_design_tokens(
        self, client, authenticate_viewer, matched_world
    ):
        """No raw palette utilities on the one page that used to carry them.

        This page was built with literal `slate-*` and `amber-*` classes, so its
        surfaces, borders and warning colour were pinned to palette values
        instead of following the theme like every other page. The markup and the
        layout are unchanged; only the vocabulary is.
        """
        item, _ = matched_world
        authenticate_viewer(client)
        url = reverse("opinion-resource", args=[self._resource(item).public_id])

        body = client.get(url).content.decode()

        for raw in ("slate-", "amber-", "bg-white"):
            assert raw not in body, f"{raw} is a palette value, not a design token"
        for token in ("dk-section", "dk-card", "text-text"):
            assert token in body

    def test_no_palette_literal_survives_in_the_template_at_all(self):
        """Covers the branches a single request does not render.

        The historical note only appears when the matter has left the current
        workbook, so a rendered-body check cannot see it. Reading the template
        covers every branch, including that one.
        """
        from pathlib import Path

        import apps.legal_work as legal_work

        source = (
            Path(legal_work.__file__).parent / "templates" / "legal_work" / "opinion_resource.html"
        ).read_text(encoding="utf-8")

        # The comment explains what was replaced, so only class attributes count.
        markup = re.sub(r"{% comment %}.*?{% endcomment %}", "", source, flags=re.S)

        for raw in ("slate-", "amber-", "bg-white"):
            assert raw not in markup, f"{raw} is a palette value, not a design token"
        assert "dk-callout-warning" in markup, "the historical note is still a warning"

    def test_no_storage_detail_reaches_the_page(self, client, authenticate_viewer, matched_world):
        from apps.legal_work.opinion_storage import store_root

        item, _ = matched_world
        authenticate_viewer(client)
        url = reverse("opinion-resource", args=[self._resource(item).public_id])

        body = client.get(url).content.decode()

        assert str(store_root()) not in body
        for blob in OpinionDocumentBlob.objects.all():
            assert blob.sha256 not in body
            assert blob.storage_key not in body

    def test_an_unknown_resource_is_a_controlled_404(self, client, authenticate_viewer):
        import uuid

        authenticate_viewer(client)
        url = reverse("opinion-resource", args=[uuid.uuid4()])

        assert client.get(url).status_code == 404

    def test_a_matter_with_an_ambiguous_identity_is_refused(
        self, client, authenticate_viewer, matched_world
    ):
        item, _ = matched_world
        authenticate_viewer(client)
        url = reverse("opinion-resource", args=[self._resource(item).public_id])
        LegalMatter.objects.all().update(has_ambiguous_identity=True)

        assert client.get(url).status_code == 404

    def test_the_address_survives_a_new_legal_snapshot(
        self, client, authenticate_viewer, matched_world
    ):
        """A published address must keep working after tomorrow's import."""
        item, _ = matched_world
        resource = self._resource(item)
        authenticate_viewer(client)
        url = reverse("opinion-resource", args=[resource.public_id])

        LegalWorkSnapshot.objects.filter(is_current=True).update(is_current=False)

        response = client.get(url)
        assert response.status_code == 200
        assert str(resource.public_id) in url

    def test_rendering_makes_no_outbound_request(
        self, client, authenticate_viewer, matched_world, monkeypatch
    ):
        import requests

        def forbidden(*args, **kwargs):
            raise AssertionError("a render attempted an outbound request")

        monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
        monkeypatch.setattr("apps.core.public_http.fetch", forbidden)

        item, _ = matched_world
        authenticate_viewer(client)
        url = reverse("opinion-resource", args=[self._resource(item).public_id])

        assert client.get(url).status_code == 200
