"""What the public opinion collector reads, refuses and records.

Every page here is synthetic markup carrying the same structural handles the
live site uses. No test contacts a network; the transport is a fake routed in
through the one seam the module exposes.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.core.public_http import FetchFailure, PublicFetchError
from apps.legal_work.public_opinion_models import (
    PublicFetchState,
    PublicOpinionDocument,
    PublicOpinionPage,
    PublicOpinionSnapshot,
    PublicPageType,
)
from apps.legal_work.public_opinion_sync import (
    RESULT_FAILED,
    RESULT_IMPORTED,
    synchronize_public_opinions,
)
from apps.legal_work.public_opinions import (
    PageAttachment,
    attachment_filename,
    attachment_filename_date,
    canonical_article_url,
    is_article_url,
    is_attachment_url,
    opinion_evidence,
    parse_meie_arvamus_listing,
    parse_news_detail,
    parse_news_listing,
)

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
    ma_detail,
    news_card,
    pdf_path,
    simple_public_site,
)

pytestmark = pytest.mark.django_db

DATE = dt.date(2026, 3, 9)


# -- parsing ------------------------------------------------------------------


class TestListingParsing:
    def test_meie_arvamus_cards_yield_urls_and_titles(self):
        cards = parse_meie_arvamus_listing(
            listing(ma_card("uks", "Esimene arvamus"), ma_card("kaks", "Teine arvamus"))
        )
        assert [card.title for card in cards] == ["Esimene arvamus", "Teine arvamus"]
        assert cards[0].url == f"{DETAIL_PREFIX}uks"

    def test_news_cards_carry_their_full_date(self):
        cards = parse_news_listing(listing(news_card("uks", "Uudis", DATE)))
        assert cards[0].card_date == DATE

    def test_navigation_links_are_not_cards(self):
        cards = parse_meie_arvamus_listing(listing())
        assert cards == []


class TestDetailParsing:
    def test_title_date_body_and_attachment_are_read(self):
        html = detail(
            title="Koda esitas arvamuse",
            date=DATE,
            attachments=attachment_link("2026-03-09 - Ministeerium - Arvamus.pdf"),
        )
        parsed = parse_news_detail(html, base_url="https://www.koda.ee/et/uudised/x")
        assert parsed.title == "Koda esitas arvamuse"
        assert parsed.published_date == DATE
        assert "arvamuse" in parsed.body_text
        assert len(parsed.attachments) == 1
        assert parsed.attachments[0].url.startswith("https://www.koda.ee/sites/default/files/")

    def test_the_sideblock_rendering_cannot_supply_date_or_attachments(self):
        """The same node rendered again further down must not double anything."""
        html = detail(title="Artikkel", date=DATE)
        parsed = parse_news_detail(html, base_url="https://www.koda.ee/et/uudised/x")
        assert parsed.published_date == DATE  # not the sideblock's 01.01.2001
        assert parsed.attachments == ()  # not the sideblock's PDF

    def test_several_attachments_keep_their_order(self):
        html = detail(
            title="Artikkel",
            attachments=(
                attachment_link("2026-03-09 - A - Arvamus.pdf")
                + attachment_link("2026-03-09 - B - Lisa 1.pdf")
            ),
        )
        parsed = parse_news_detail(html, base_url="https://www.koda.ee/et/uudised/x")
        names = [attachment_filename(a) for a in parsed.attachments]
        assert names == ["2026-03-09 - A - Arvamus.pdf", "2026-03-09 - B - Lisa 1.pdf"]

    def test_a_duplicate_attachment_url_is_recorded_once(self):
        link = attachment_link("2026-03-09 - A - Arvamus.pdf")
        html = detail(title="Artikkel", attachments=link + link)
        parsed = parse_news_detail(html, base_url="https://www.koda.ee/et/uudised/x")
        assert len(parsed.attachments) == 1

    def test_an_off_domain_file_link_is_refused(self):
        html = detail(
            title="Artikkel",
            attachments=(
                '<div class="field field--name-ekt-content-files">'
                '<a href="https://evil.example/x.pdf" class="btn btn--file ext-pdf">X</a>'
                "</div>"
            ),
        )
        parsed = parse_news_detail(html, base_url="https://www.koda.ee/et/uudised/x")
        assert parsed.attachments == ()

    def test_a_file_link_outside_the_fixed_path_is_refused(self):
        html = detail(
            title="Artikkel",
            attachments=(
                '<div class="field field--name-ekt-content-files">'
                '<a href="/muu/koht/x.pdf" class="btn btn--file ext-pdf">X</a>'
                "</div>"
            ),
        )
        parsed = parse_news_detail(html, base_url="https://www.koda.ee/et/uudised/x")
        assert parsed.attachments == ()

    def test_malformed_markup_degrades_to_no_content_rather_than_crashing(self):
        parsed = parse_news_detail("<div><<<broken", base_url="https://www.koda.ee/x")
        assert parsed.title == ""
        assert parsed.attachments == ()

    def test_the_older_meie_arvamus_article_shape_is_read(self):
        """A meie-arvamus node, a classless h1, the current-draft date class."""
        html = ma_detail(
            title="Kestlikkusaruande kohustust tuleb edasi lükata",
            date=dt.date(2025, 4, 29),
            attachments=attachment_link(
                "29 04 2025 Raamatupidamise seaduse muutmise seaduse eelnõu.pdf",
                folder="2025-05",
            ),
        )
        parsed = parse_news_detail(html, base_url="https://www.koda.ee/et/meie-arvamus/x")
        assert parsed.title == "Kestlikkusaruande kohustust tuleb edasi lükata"
        assert parsed.published_date == dt.date(2025, 4, 29)
        assert len(parsed.attachments) == 1


class TestAttachmentFilenameDate:
    def test_public_upload_names_date_themselves(self):
        assert attachment_filename_date("29 04 2025 Raamatupidamise seadus.pdf") == dt.date(
            2025, 4, 29
        )
        assert attachment_filename_date("25 02 26 Tarbijakaitseseaduse arvamus.pdf") == dt.date(
            2026, 2, 25
        )

    def test_a_non_date_prefix_yields_nothing(self):
        assert attachment_filename_date("Arvamus ilma kuupäevata.pdf") is None
        assert attachment_filename_date("45 13 26 võimatu.pdf") is None
        assert attachment_filename_date("") is None


class TestUrlRules:
    def test_only_article_paths_qualify(self):
        assert is_article_url("https://www.koda.ee/et/uudised/mingi-artikkel")
        assert is_article_url("https://www.koda.ee/et/meie-arvamus/vanem-arvamuslugu")
        assert not is_article_url("https://www.koda.ee/et/uudised")
        assert not is_article_url("https://www.koda.ee/et/meie-arvamus")
        assert not is_article_url("https://www.koda.ee/et/pood/asi")
        assert not is_article_url("http://www.koda.ee/et/uudised/mingi-artikkel")
        assert not is_article_url("https://evil.example/et/uudised/mingi-artikkel")

    def test_attachments_must_be_koda_hosted_pdfs(self):
        assert is_attachment_url("https://www.koda.ee/sites/default/files/a/b.pdf")
        assert is_attachment_url(
            "https://www.koda.ee/sites/default/files/a/Nimi%20T%C3%BChikuga.pdf"
        )
        assert not is_attachment_url("https://www.koda.ee/sites/default/files/a/b.docx")
        assert not is_attachment_url("https://evil.example/sites/default/files/a/b.pdf")
        assert not is_attachment_url("javascript:alert(1)")
        assert not is_attachment_url("data:application/pdf;base64,AAAA")

    def test_canonicalisation_drops_query_fragment_and_trailing_slash(self):
        assert (
            canonical_article_url("https://www.koda.ee/et/uudised/artikkel/?utm=1#p")
            == "https://www.koda.ee/et/uudised/artikkel"
        )


class TestOpinionEvidence:
    def test_the_meie_arvamus_listing_is_evidence_alone(self):
        codes = opinion_evidence(
            listed_in_meie_arvamus=True, title="Suvaline", body_text="Tekst.", attachments=[]
        )
        assert "listed-meie-arvamus" in codes

    def test_position_wording_is_evidence(self):
        codes = opinion_evidence(
            listed_in_meie_arvamus=False,
            title="Uudis eelnõust",
            body_text="Koja hinnangul vajab eelnõu muutmist.",
            attachments=[],
        )
        assert "opinion-vocabulary" in codes

    def test_an_opinion_shaped_attachment_is_evidence(self):
        codes = opinion_evidence(
            listed_in_meie_arvamus=False,
            title="Uudis",
            body_text="Tavaline tekst.",
            attachments=[
                PageAttachment(
                    url="https://www.koda.ee/sites/default/files/a/"
                    "2026-03-09%20-%20Ministeerium%20-%20Arvamus%20eelnou%20kohta.pdf",
                    label="Arvamus",
                )
            ],
        )
        assert "opinion-attachment" in codes

    def test_a_statute_named_in_ordinary_news_is_not_evidence(self):
        codes = opinion_evidence(
            listed_in_meie_arvamus=False,
            title="Riigikogu võttis vastu tulumaksuseaduse muudatused",
            body_text="Seadus jõustub järgmisel aastal.",
            attachments=[],
        )
        assert codes == []


# -- crawl boundaries -----------------------------------------------------------


class TestCrawlBoundaries:
    def test_a_full_run_publishes_the_corpus(self, patch_public_site, opinion_roots, db):
        patch_public_site(simple_public_site())
        report = synchronize_public_opinions(full=True)

        assert report.result == RESULT_IMPORTED
        snapshot = PublicOpinionSnapshot.objects.get(is_current=True)
        page = snapshot.pages.get()
        assert page.page_type == PublicPageType.MEIE_ARVAMUS
        assert page.published_date == DATE
        assert page.fetch_state == PublicFetchState.FETCHED
        document = snapshot.documents.get()
        assert document.blob is not None and document.blob.is_valid
        assert document.extraction is not None
        assert document.filename_date == DATE

    def test_meie_arvamus_pagination_is_followed(self, patch_public_site, opinion_roots, db):
        site = FakePublicSite(
            pages={
                MA_LISTING_PATH: listing(ma_card("uks", "Esimene")),
                f"{MA_LISTING_PATH}?page=1": listing(ma_card("kaks", "Teine")),
                NEWS_LISTING_PATH: listing(),
                f"{DETAIL_PREFIX}uks": detail(title="Esimene", date=DATE),
                f"{DETAIL_PREFIX}kaks": detail(title="Teine", date=DATE),
            }
        )
        end_listings(site)
        patch_public_site(site)
        report = synchronize_public_opinions(full=True)
        assert report.result == RESULT_IMPORTED
        assert PublicOpinionPage.objects.filter(snapshot__is_current=True).count() == 2
        assert f"{MA_LISTING_PATH}?page=1" in site.requested

    def test_pages_older_than_the_window_are_not_activated(
        self, patch_public_site, opinion_roots, db
    ):
        """Not activated, and not paid for: no attachment fetch, no blob."""
        old = dt.date(2024, 5, 5)
        name = "2024-05-05 - Ministeerium - Arvamus vana asja kohta.pdf"
        site = FakePublicSite(
            pages={
                MA_LISTING_PATH: listing(ma_card("vana", "Vana arvamus")),
                NEWS_LISTING_PATH: listing(),
                f"{DETAIL_PREFIX}vana": detail(
                    title="Vana arvamus", date=old, attachments=attachment_link(name)
                ),
            },
            files={pdf_path(name): b"%PDF-1.7 never fetched"},
        )
        end_listings(site)
        patch_public_site(site)
        report = synchronize_public_opinions(full=True)
        assert report.result == RESULT_IMPORTED
        assert PublicOpinionPage.objects.count() == 0
        assert report.documents_fetched == 0
        assert not any(".pdf" in path.lower() for path in site.requested)
        from apps.legal_work.opinion_models import OpinionDocumentBlob

        assert OpinionDocumentBlob.objects.count() == 0

    def test_ordinary_news_without_evidence_is_not_collected(
        self, patch_public_site, opinion_roots, db
    ):
        site = FakePublicSite(
            pages={
                MA_LISTING_PATH: listing(),
                NEWS_LISTING_PATH: listing(news_card("uudis", "Riigikogu uudis", DATE)),
                f"{DETAIL_PREFIX}uudis": detail(
                    title="Riigikogu uudis",
                    date=DATE,
                    body="Seadus võeti vastu ja jõustub järgmisel aastal.",
                ),
            }
        )
        end_listings(site)
        patch_public_site(site)
        report = synchronize_public_opinions(full=True)
        assert report.result == RESULT_IMPORTED
        assert PublicOpinionPage.objects.count() == 0

    def test_a_failing_listing_fails_the_run_and_keeps_the_previous_corpus(
        self, patch_public_site, opinion_roots, db
    ):
        patch_public_site(simple_public_site())
        synchronize_public_opinions(full=True)
        first = PublicOpinionSnapshot.objects.get(is_current=True)

        broken = FakePublicSite(
            errors={
                MA_LISTING_PATH: PublicFetchError(
                    "Allikas vastas koodiga 500.",
                    failure=FetchFailure.SERVER_ERROR,
                    status_code=500,
                )
            }
        )
        patch_public_site(broken)
        report = synchronize_public_opinions(full=True)

        assert report.result == RESULT_FAILED
        assert PublicOpinionSnapshot.objects.get(is_current=True) == first

    def test_a_new_detail_page_that_cannot_be_read_fails_the_run(
        self, patch_public_site, opinion_roots, db
    ):
        site = simple_public_site()
        del site.pages[f"{DETAIL_PREFIX}koda-esitas-arvamuse"]
        patch_public_site(site)
        report = synchronize_public_opinions(full=True)
        assert report.result == RESULT_FAILED
        assert PublicOpinionSnapshot.objects.count() == 0

    def test_an_oversized_response_fails_like_any_other_transport_error(
        self, patch_public_site, opinion_roots, db
    ):
        site = simple_public_site()
        site.errors[f"{DETAIL_PREFIX}koda-esitas-arvamuse"] = PublicFetchError(
            "Vastus ületab lubatud suuruse.", failure=FetchFailure.TOO_LARGE
        )
        patch_public_site(site)
        report = synchronize_public_opinions(full=True)
        assert report.result == RESULT_FAILED

    def test_a_wrong_content_type_on_a_pdf_records_failed_provenance(
        self, patch_public_site, opinion_roots, db
    ):
        name = (
            "2026-03-09 - Rahandusministeerium - Arvamus maksukorralduse seaduse eelnou kohta.pdf"
        )
        site = simple_public_site()
        site.errors[pdf_path(name)] = PublicFetchError(
            "Ootamatu sisutüüp: text/html.", failure=FetchFailure.UNEXPECTED_CONTENT
        )
        patch_public_site(site)
        report = synchronize_public_opinions(full=True)

        assert report.result == RESULT_IMPORTED
        document = PublicOpinionDocument.objects.get(snapshot__is_current=True)
        assert document.fetch_state == PublicFetchState.FAILED
        assert document.blob is None

    def test_a_dry_run_writes_nothing(self, patch_public_site, opinion_roots, db):
        patch_public_site(simple_public_site())
        report = synchronize_public_opinions(full=True, dry_run=True)

        assert report.result == RESULT_IMPORTED
        assert PublicOpinionSnapshot.objects.count() == 0
        assert PublicOpinionPage.objects.count() == 0
        from apps.legal_work.opinion_models import OpinionDocumentBlob

        assert OpinionDocumentBlob.objects.count() == 0
