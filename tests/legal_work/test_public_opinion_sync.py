"""The public corpus lifecycle: accumulate, carry forward, never regress.

These tests run the real sync twice or more against fake sites that change
between runs, because the properties under test — idempotence, carry-forward,
historical immutability, dedup by digest — are properties of consecutive runs
rather than of any single one.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.legal_work.opinion_models import (
    OpinionDocumentBlob,
    OpinionDocumentExtraction,
)
from apps.legal_work.public_opinion_models import (
    PublicFetchState,
    PublicOpinionDocument,
    PublicOpinionFeedState,
    PublicOpinionPage,
    PublicOpinionSnapshot,
)
from apps.legal_work.public_opinion_sync import (
    RESULT_FAILED,
    RESULT_IMPORTED,
    RESULT_UNCHANGED,
    synchronize_public_opinions,
)

from .opinion_factory import make_pdf, opinion_pdf
from .opinion_match_factory import letter, publish_catalogue
from .public_opinion_factory import (
    DETAIL_PREFIX,
    MA_LISTING_PATH,
    NEWS_LISTING_PATH,
    FakePublicSite,
    attachment_link,
    detail,
    end_listings,
    listing,
    ma_card,
    news_card,
    not_found,
    pdf_path,
    simple_public_site,
)

pytestmark = pytest.mark.django_db

DATE = dt.date(2026, 3, 9)
LATER = dt.date(2026, 4, 20)


def test_a_second_identical_run_publishes_nothing(patch_public_site, opinion_roots, db):
    patch_public_site(simple_public_site())
    first = synchronize_public_opinions(full=True)
    second = synchronize_public_opinions(full=True)

    assert first.result == RESULT_IMPORTED
    assert second.result == RESULT_UNCHANGED
    assert PublicOpinionSnapshot.objects.count() == 1
    assert second.snapshot_id == first.snapshot_id


def test_incremental_refuses_to_run_before_the_backfill(patch_public_site, opinion_roots, db):
    patch_public_site(simple_public_site())
    report = synchronize_public_opinions(full=False)
    assert report.result == RESULT_FAILED
    assert PublicOpinionSnapshot.objects.count() == 0


def test_an_incremental_run_reads_the_edge_and_carries_the_rest(
    patch_public_site, opinion_roots, db
):
    """A page older than the refresh overlap is never re-read; a new edge page
    joins; a page inside the overlap is re-read because Koda.ee attaches the
    letter a day or two after publishing the article."""
    january = dt.date(2026, 1, 15)
    first_site = FakePublicSite(
        pages={
            MA_LISTING_PATH: listing(
                ma_card("martsi-arvamus", "Märtsi arvamus"),
                ma_card("jaanuari-arvamus", "Jaanuari arvamus"),
            ),
            NEWS_LISTING_PATH: listing(),
            f"{DETAIL_PREFIX}martsi-arvamus": detail(title="Märtsi arvamus", date=DATE),
            f"{DETAIL_PREFIX}jaanuari-arvamus": detail(title="Jaanuari arvamus", date=january),
        }
    )
    end_listings(first_site)
    patch_public_site(first_site)
    synchronize_public_opinions(full=True)
    old_page = PublicOpinionPage.objects.get(snapshot__is_current=True, title="Jaanuari arvamus")

    new_name = "2026-04-20 - Kliimaministeerium - Arvamus kliimaseaduse eelnou kohta.pdf"
    edge_site = FakePublicSite(
        pages={
            MA_LISTING_PATH: listing(
                ma_card("uus-arvamus", "Uus arvamus"),
                ma_card("martsi-arvamus", "Märtsi arvamus"),
            ),
            NEWS_LISTING_PATH: listing(news_card("uus-arvamus", "Uus arvamus", LATER)),
            f"{DETAIL_PREFIX}uus-arvamus": detail(
                title="Uus arvamus",
                date=LATER,
                attachments=attachment_link(new_name, folder="2026-04"),
            ),
            # Inside the overlap window, so the run re-reads it.
            f"{DETAIL_PREFIX}martsi-arvamus": detail(title="Märtsi arvamus", date=DATE),
        },
        files={pdf_path(new_name, folder="2026-04"): opinion_pdf(our_date="20.04.2026")},
    )
    end_listings(edge_site)
    patch_public_site(edge_site)
    report = synchronize_public_opinions(full=False)

    assert report.result == RESULT_IMPORTED
    current = PublicOpinionSnapshot.objects.get(is_current=True)
    assert current.page_count == 3

    carried = current.pages.get(content_key=old_page.content_key)
    assert carried.fetch_state == PublicFetchState.CARRIED
    assert carried.first_seen_at == old_page.first_seen_at
    assert carried.is_present
    # The January page was never requested this run.
    assert f"{DETAIL_PREFIX}jaanuari-arvamus" not in edge_site.requested

    fresh = current.pages.get(title="Uus arvamus")
    assert fresh.fetch_state == PublicFetchState.FETCHED
    assert fresh.published_date == LATER
    assert fresh.documents.get().blob is not None


def test_history_survives_a_page_disappearing(patch_public_site, opinion_roots, db):
    """A 404 on a full re-walk moves availability, never existence."""
    patch_public_site(simple_public_site())
    synchronize_public_opinions(full=True)

    gone = FakePublicSite(
        pages={MA_LISTING_PATH: listing(), NEWS_LISTING_PATH: listing()},
        errors={f"{DETAIL_PREFIX}koda-esitas-arvamuse": not_found()},
    )
    end_listings(gone)
    patch_public_site(gone)
    report = synchronize_public_opinions(full=True)

    assert report.result == RESULT_IMPORTED
    page = PublicOpinionPage.objects.get(snapshot__is_current=True)
    assert not page.is_present
    assert page.title == "Koda esitas arvamuse maksukorralduse seaduse eelnõu kohta"
    document = page.documents.get()
    assert document.blob is not None  # the bytes and their provenance survive


def test_a_vanished_attachment_is_history_not_deletion(patch_public_site, opinion_roots, db):
    patch_public_site(simple_public_site())
    synchronize_public_opinions(full=True)

    site = simple_public_site(pdf_name=None)
    patch_public_site(site)
    report = synchronize_public_opinions(full=True)

    assert report.result == RESULT_IMPORTED
    document = PublicOpinionDocument.objects.get(snapshot__is_current=True)
    assert not document.is_present
    assert document.blob is not None


def test_two_urls_with_identical_bytes_are_one_blob(patch_public_site, opinion_roots, db):
    payload = opinion_pdf()
    name_a = "2026-03-09 - Rahandusministeerium - Arvamus maksukorralduse seaduse eelnou kohta.pdf"
    name_b = "2026-03-09 - Rahandusministeerium - Arvamus maksukorralduse seaduse kohta koopia.pdf"
    site = FakePublicSite(
        pages={
            MA_LISTING_PATH: listing(
                ma_card("esimene", "Esimene artikkel"), ma_card("teine", "Teine artikkel")
            ),
            NEWS_LISTING_PATH: listing(),
            f"{DETAIL_PREFIX}esimene": detail(
                title="Esimene artikkel", date=DATE, attachments=attachment_link(name_a)
            ),
            f"{DETAIL_PREFIX}teine": detail(
                title="Teine artikkel", date=DATE, attachments=attachment_link(name_b)
            ),
        },
        files={pdf_path(name_a): payload, pdf_path(name_b): payload},
    )
    end_listings(site)
    patch_public_site(site)
    report = synchronize_public_opinions(full=True)

    assert report.result == RESULT_IMPORTED
    assert OpinionDocumentBlob.objects.count() == 1
    assert PublicOpinionDocument.objects.filter(snapshot__is_current=True).count() == 2
    assert OpinionDocumentExtraction.objects.count() == 1


def test_public_bytes_identical_to_private_reuse_blob_and_extraction(
    patch_public_site, opinion_roots, opinion_source, db
):
    """The Phase 24 core: one blob, one extraction, two provenances."""
    source_root, _store = opinion_roots
    name, payload = letter(date=DATE)
    publish_catalogue(source_root, [(name, payload)])
    assert OpinionDocumentBlob.objects.count() == 1
    assert OpinionDocumentExtraction.objects.count() == 1

    patch_public_site(simple_public_site(pdf_payload=payload))
    report = synchronize_public_opinions(full=True)

    assert report.result == RESULT_IMPORTED
    assert report.known_blobs == 1
    assert report.new_blobs == 0
    assert report.reused_extractions == 1
    assert OpinionDocumentBlob.objects.count() == 1
    assert OpinionDocumentExtraction.objects.count() == 1

    blob = OpinionDocumentBlob.objects.get()
    assert blob.catalogue_entries.exists()
    assert blob.public_documents.exists()


def test_an_invalid_public_pdf_is_quarantined_not_extracted(patch_public_site, opinion_roots, db):
    broken = make_pdf(broken=True)
    patch_public_site(simple_public_site(pdf_payload=broken))
    report = synchronize_public_opinions(full=True)

    assert report.result == RESULT_IMPORTED
    assert report.invalid_documents == 1
    document = PublicOpinionDocument.objects.get(snapshot__is_current=True)
    assert document.blob is not None
    assert not document.blob.is_valid
    assert document.extraction is None
    assert not document.is_matchable


def test_a_failed_download_is_retried_on_the_next_run(patch_public_site, opinion_roots, db):
    name = "2026-03-09 - Rahandusministeerium - Arvamus maksukorralduse seaduse eelnou kohta.pdf"
    site = simple_public_site()
    payload = site.files[pdf_path(name)]
    site.errors[pdf_path(name)] = not_found()
    patch_public_site(site)
    synchronize_public_opinions(full=True)

    failed = PublicOpinionDocument.objects.get(snapshot__is_current=True)
    assert failed.fetch_state == PublicFetchState.FAILED
    assert failed.blob is None

    healed = simple_public_site(pdf_payload=payload)
    patch_public_site(healed)
    report = synchronize_public_opinions(full=True)

    assert report.result == RESULT_IMPORTED
    recovered = PublicOpinionDocument.objects.get(snapshot__is_current=True)
    assert recovered.blob is not None
    assert recovered.first_seen_at == failed.first_seen_at


def test_the_feed_state_records_failure_without_touching_the_corpus(
    patch_public_site, opinion_roots, db
):
    patch_public_site(simple_public_site())
    synchronize_public_opinions(full=True)

    broken = FakePublicSite(errors={MA_LISTING_PATH: not_found()})
    patch_public_site(broken)
    report = synchronize_public_opinions(full=True)

    assert report.result == RESULT_FAILED
    state = PublicOpinionFeedState.objects.get()
    assert state.last_result == "failed"
    assert state.current_snapshot is not None
    assert PublicOpinionSnapshot.objects.get(is_current=True) == state.current_snapshot
