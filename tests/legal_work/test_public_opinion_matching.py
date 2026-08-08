"""Both sources in front of one matcher, and what a reader then sees.

The required integration behaviours, each as the project brief states them:
identical private/public bytes are one document with two provenances; a
public-only letter links a topic; a private-only world is byte-for-byte the
old behaviour; an article without a PDF is evidence, never a document; a
competing claim is recorded, never silently resolved; and the daily lifecycle
— published publicly first, filed privately later, delisted eventually —
never mints a second resource or loses a provenance.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from apps.legal_work.models import LegalWorkItem, MatchDecision, SentStatus
from apps.legal_work.opinion_match_models import (
    LegalOpinionDecision,
    LegalOpinionDocumentRelation,
    LegalOpinionMatchSnapshot,
    LegalOpinionPageRelation,
    OpinionResource,
)
from apps.legal_work.opinion_match_sync import run_opinion_matching
from apps.legal_work.opinion_models import OpinionDocumentBlob
from apps.legal_work.public_opinion_models import PublicOpinionSnapshot
from apps.legal_work.public_opinion_sync import synchronize_public_opinions
from apps.legal_work.topic_links import resolve_opinion_links

from .opinion_factory import opinion_pdf
from .opinion_match_factory import letter, publish_catalogue
from .public_opinion_factory import simple_public_site

pytestmark = pytest.mark.django_db

SENT = dt.date(2026, 3, 10)
RECEIVED = dt.date(2026, 2, 1)
TOPIC = "Maksukorralduse seaduse muutmise seaduse eelnõu 130 SE"
SUBJECT = "Arvamus maksukorralduse seaduse muutmise eelnou 130 SE kohta"
ARTICLE_TITLE = "Koda esitas arvamuse maksukorralduse seaduse muutmise eelnõu 130 SE kohta"
PDF_NAME = f"2026-03-10 - Rahandusministeerium - {SUBJECT}.pdf"

UNRELATED = letter(
    date=dt.date(2026, 1, 5),
    subject="Arvamus hoopis teise valdkonna maaelu seaduse kohta",
    reference="4/9",
)


def sent_item(imported_snapshot, *, topic=TOPIC, sent=SENT):
    """One eligible record; every other record deliberately is not."""
    item = LegalWorkItem.objects.filter(snapshot=imported_snapshot).first()
    LegalWorkItem.objects.filter(pk=item.pk).update(
        topic=topic,
        sent_status=SentStatus.SENT,
        sent_date=sent,
        received_date=RECEIVED,
        recipient="Rahandusministeerium",
    )
    LegalWorkItem.objects.filter(snapshot=imported_snapshot).exclude(pk=item.pk).update(
        sent_status=SentStatus.PENDING, sent_date=None
    )
    item.refresh_from_db()
    return item


def public_site(**overrides):
    payload = overrides.pop("pdf_payload", None)
    return simple_public_site(
        slug="koda-esitas-arvamuse-130se",
        title=ARTICLE_TITLE,
        date=dt.date(2026, 3, 12),
        pdf_name=overrides.pop("pdf_name", PDF_NAME),
        pdf_payload=payload
        if payload is not None
        else opinion_pdf(subject=SUBJECT, our_date="10.03.2026"),
        **overrides,
    )


def primary_relation():
    return LegalOpinionDocumentRelation.objects.get(
        decision__snapshot__is_current=True, is_primary=True
    )


class TestIdenticalPrivateAndPublicBytes:
    """Phase 24: one blob, two provenances, one logical document."""

    @pytest.fixture
    def world(self, opinion_roots, opinion_source, imported_snapshot, patch_public_site):
        source, _store = opinion_roots
        item = sent_item(imported_snapshot)
        payload = opinion_pdf(subject=SUBJECT, our_date="10.03.2026")
        publish_catalogue(source, [(PDF_NAME, payload), UNRELATED])
        patch_public_site(public_site(pdf_payload=payload))
        synchronize_public_opinions(full=True)
        report = run_opinion_matching()
        return item, report

    def test_one_blob_and_one_extraction_exist(self, world):
        blobs = OpinionDocumentBlob.objects.filter(public_documents__isnull=False)
        assert blobs.count() == 1
        blob = blobs.get()
        assert blob.catalogue_entries.exists()
        assert blob.extractions.count() == 1

    def test_one_matched_relation_carries_both_provenances(self, world):
        item, report = world
        assert report.matched == 1
        relation = primary_relation()
        assert relation.entry is not None
        assert relation.public_document is not None
        assert relation.blob_id == relation.entry.blob_id
        assert relation.blob_id == relation.public_document.blob_id

    def test_one_resource_and_a_public_preference_with_private_fallback(
        self, world, client, authenticate_viewer
    ):
        item, _report = world
        assert OpinionResource.objects.count() == 1
        resource = OpinionResource.objects.get()

        authenticate_viewer(client)
        response = client.get(reverse("opinion-resource", args=[resource.public_id]))
        content = response.content.decode()

        relation = primary_relation()
        assert relation.public_document.pdf_url in content
        protected = reverse("opinion-document", args=[relation.blob.public_id])
        assert protected in content  # the fallback is offered, not hidden
        # One logical document: the letter appears as one card, not two.
        assert content.count(relation.public_document.pdf_url) == 1

    def test_the_protected_route_still_serves_the_bytes(self, world, client, authenticate_viewer):
        relation = primary_relation()
        authenticate_viewer(client)
        response = client.get(reverse("opinion-document", args=[relation.blob.public_id]))
        assert response.status_code == 200


class TestPublicOnlyDocument:
    """Phase 25: a letter Koda.ee published and the private folder never got."""

    @pytest.fixture
    def world(self, opinion_roots, opinion_source, imported_snapshot, patch_public_site):
        source, _store = opinion_roots
        item = sent_item(imported_snapshot)
        publish_catalogue(source, [UNRELATED])
        patch_public_site(public_site())
        synchronize_public_opinions(full=True)
        report = run_opinion_matching()
        return item, report

    def test_the_matter_is_matched_from_the_public_source_alone(self, world):
        item, report = world
        assert report.matched == 1
        relation = primary_relation()
        assert relation.entry is None
        assert relation.public_document is not None
        assert relation.decision.legal_item_id == item.pk

    def test_the_sent_topic_resolves_to_the_stable_resource(self, world):
        item, _report = world
        links = resolve_opinion_links([item.pk])
        resource = OpinionResource.objects.get(matter__decisions__legal_item=item)
        assert links == {item.pk: reverse("opinion-resource", args=[resource.public_id])}

    def test_the_resource_page_offers_the_public_opinion(self, world, client, authenticate_viewer):
        relation = primary_relation()
        resource = OpinionResource.objects.get()
        authenticate_viewer(client)
        content = client.get(
            reverse("opinion-resource", args=[resource.public_id])
        ).content.decode()
        assert relation.public_document.pdf_url in content
        assert 'rel="noopener noreferrer"' in content

    def test_no_private_catalogue_entry_was_invented(self, world):
        relation = primary_relation()
        assert relation.entry is None
        assert relation.blob.catalogue_entries.count() == 0

    def test_the_match_snapshot_names_all_three_inputs(self, world):
        snapshot = LegalOpinionMatchSnapshot.objects.get(is_current=True)
        assert snapshot.public_opinion_snapshot == PublicOpinionSnapshot.objects.get(
            is_current=True
        )
        assert run_opinion_matching().result == "unchanged"


class TestPrivateOnlyUnchanged:
    """Phase 26: no public source exists, and nothing about 1.1 behaviour moves."""

    @pytest.fixture
    def world(self, opinion_roots, opinion_source, imported_snapshot):
        source, _store = opinion_roots
        item = sent_item(imported_snapshot)
        publish_catalogue(
            source,
            [(PDF_NAME, opinion_pdf(subject=SUBJECT, our_date="10.03.2026")), UNRELATED],
        )
        report = run_opinion_matching()
        return item, report

    def test_the_match_works_exactly_as_before(self, world):
        item, report = world
        assert report.matched == 1
        relation = primary_relation()
        assert relation.entry is not None
        assert relation.public_document is None

    def test_the_snapshot_records_that_no_public_corpus_existed(self, world):
        snapshot = LegalOpinionMatchSnapshot.objects.get(is_current=True)
        assert snapshot.public_opinion_snapshot is None

    def test_the_protected_route_remains_the_main_action(self, world, client, authenticate_viewer):
        relation = primary_relation()
        resource = OpinionResource.objects.get()
        authenticate_viewer(client)
        content = client.get(
            reverse("opinion-resource", args=[resource.public_id])
        ).content.decode()
        assert reverse("opinion-document", args=[relation.blob.public_id]) in content
        assert "koda.ee" not in content.lower().replace("koda.ee-s", "")  # no public link


class TestArticleOnly:
    """Phase 27: a page confirms the position; no document is invented."""

    @pytest.fixture
    def world(self, opinion_roots, opinion_source, imported_snapshot, patch_public_site):
        source, _store = opinion_roots
        item = sent_item(imported_snapshot)
        publish_catalogue(source, [UNRELATED])
        patch_public_site(public_site(pdf_name=None))
        synchronize_public_opinions(full=True)
        report = run_opinion_matching()
        return item, report

    def test_no_document_relation_exists(self, world):
        _item, report = world
        assert report.matched == 0
        assert LegalOpinionDocumentRelation.objects.count() == 0

    def test_a_confident_page_relation_is_recorded(self, world):
        item, report = world
        assert report.page_relations == 1
        relation = LegalOpinionPageRelation.objects.get()
        assert relation.decision.legal_item_id == item.pk
        assert relation.page.title == ARTICLE_TITLE

    def test_the_topic_still_resolves_to_the_resource(self, world):
        item, _report = world
        links = resolve_opinion_links([item.pk])
        assert item.pk in links

    def test_the_resource_page_shows_an_article_never_a_pdf(
        self, world, client, authenticate_viewer
    ):
        item, _report = world
        resource = OpinionResource.objects.get(matter__decisions__legal_item=item)
        authenticate_viewer(client)
        content = client.get(
            reverse("opinion-resource", args=[resource.public_id])
        ).content.decode()
        assert "Vaata arvamust Koda.ee-s" in content
        assert "Artikkel" in content
        assert "Ava arvamuse" not in content
        assert ".pdf" not in content.lower()

    def test_an_unconfident_page_never_attaches(
        self, opinion_roots, opinion_source, imported_snapshot, patch_public_site
    ):
        """Subject overlap without date agreement is exactly the plausible
        wrong link the bar exists to refuse."""
        source, _store = opinion_roots
        sent_item(imported_snapshot, sent=dt.date(2026, 3, 10))
        publish_catalogue(source, [UNRELATED])
        site = simple_public_site(
            slug="vana-artikkel",
            title=ARTICLE_TITLE,
            date=dt.date(2025, 6, 1),  # nine months adrift
            pdf_name=None,
        )
        patch_public_site(site)
        synchronize_public_opinions(full=True)
        report = run_opinion_matching()

        assert report.page_relations == 0
        assert not LegalOpinionPageRelation.objects.exists()


class TestCompetingClaims:
    """Phase 28: two matters claiming one public letter is recorded, not resolved."""

    def test_the_weaker_claim_is_demoted_and_marked(
        self, opinion_roots, opinion_source, imported_snapshot, patch_public_site
    ):
        source, _store = opinion_roots
        items = list(LegalWorkItem.objects.filter(snapshot=imported_snapshot))
        stronger, weaker = items[0], items[1]
        LegalWorkItem.objects.filter(pk=stronger.pk).update(
            topic=TOPIC,
            sent_status=SentStatus.SENT,
            sent_date=SENT,
            received_date=RECEIVED,
            recipient="Rahandusministeerium",
        )
        LegalWorkItem.objects.filter(pk=weaker.pk).update(
            topic=f"{TOPIC} (teine ring)",
            sent_status=SentStatus.SENT,
            sent_date=SENT + dt.timedelta(days=15),
            received_date=RECEIVED,
            recipient="Rahandusministeerium",
        )
        LegalWorkItem.objects.filter(snapshot=imported_snapshot).exclude(
            pk__in=[stronger.pk, weaker.pk]
        ).update(sent_status=SentStatus.PENDING, sent_date=None)

        publish_catalogue(source, [UNRELATED])
        patch_public_site(public_site())
        synchronize_public_opinions(full=True)
        report = run_opinion_matching()

        decisions = {
            row.legal_item_id: row
            for row in LegalOpinionDecision.objects.filter(snapshot__is_current=True)
        }
        assert decisions[stronger.pk].decision == MatchDecision.MATCHED
        assert decisions[weaker.pk].decision == MatchDecision.AMBIGUOUS
        assert "competing-primary-claim" in decisions[weaker.pk].contradiction_codes
        assert report.matched == 1


class TestLifecycle:
    """Phase 49: published publicly first, filed privately later, delisted at last."""

    def test_the_four_days(
        self, opinion_roots, opinion_source, imported_snapshot, patch_public_site
    ):
        source, _store = opinion_roots
        item = sent_item(imported_snapshot)
        payload = opinion_pdf(subject=SUBJECT, our_date="10.03.2026")

        # Day 1: sent, no private PDF, nothing published yet.
        publish_catalogue(source, [UNRELATED])
        report = run_opinion_matching()
        assert report.matched == 0
        assert resolve_opinion_links([item.pk]) == {}

        # Day 2: Koda.ee publishes the article with the exact PDF.
        patch_public_site(public_site(pdf_payload=payload))
        synchronize_public_opinions(full=True)
        report = run_opinion_matching()
        assert report.matched == 1
        resource = OpinionResource.objects.get(matter__decisions__legal_item=item)
        assert item.pk in resolve_opinion_links([item.pk])
        relation = primary_relation()
        assert relation.entry is None and relation.public_document is not None
        blob_count = OpinionDocumentBlob.objects.count()

        # Day 3: the private folder receives the same bytes.
        publish_catalogue(source, [UNRELATED, (PDF_NAME, payload)])
        report = run_opinion_matching()
        assert report.matched == 1
        assert OpinionDocumentBlob.objects.count() == blob_count  # same SHA reused
        relation = primary_relation()
        assert relation.entry is not None and relation.public_document is not None
        assert OpinionResource.objects.get(matter__decisions__legal_item=item) == resource
        assert (
            LegalOpinionDocumentRelation.objects.filter(
                decision__snapshot__is_current=True, is_primary=True
            ).count()
            == 1
        )

        # Day 30: the article leaves the listing but still answers. Provenance
        # and the resource survive; the public address is still preferred.
        site = public_site(pdf_payload=payload)
        site.pages["/et/meie-arvamus"] = site.pages["/et/meie-arvamus"].replace(
            "meie-arvamus--teaser", "meie-arvamus--endine"
        )
        site.pages["/et/uudised"] = site.pages["/et/uudised"].replace(
            "news--teaser", "news--endine"
        )
        patch_public_site(site)
        synchronize_public_opinions(full=True)
        report = run_opinion_matching()

        relation = primary_relation()
        assert relation.public_document is not None
        assert relation.public_document.is_present
        assert item.pk in resolve_opinion_links([item.pk])


class TestQueryCounts:
    """Phase 41: link resolution stays bounded however many rows a page draws."""

    def test_opinion_link_resolution_is_two_queries(
        self,
        opinion_roots,
        opinion_source,
        imported_snapshot,
        patch_public_site,
        django_assert_max_num_queries,
    ):
        source, _store = opinion_roots
        item = sent_item(imported_snapshot)
        payload = opinion_pdf(subject=SUBJECT, our_date="10.03.2026")
        publish_catalogue(source, [(PDF_NAME, payload), UNRELATED])
        patch_public_site(public_site(pdf_payload=payload))
        synchronize_public_opinions(full=True)
        run_opinion_matching()

        every_id = list(
            LegalWorkItem.objects.filter(snapshot__is_current=True).values_list("pk", flat=True)
        )
        with django_assert_max_num_queries(2):
            links = resolve_opinion_links(every_id)
        assert item.pk in links
