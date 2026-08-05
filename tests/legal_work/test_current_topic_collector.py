"""The `Hetkel käsil` crawl: fixed boundary, normalised text, no raw markup.

Every page is synthetic. No test here contacts Koda.ee or any other host.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.core.public_http import PublicFetchError
from apps.legal_work.current_topics import (
    CurrentTopicCollectionError,
    collect_current_topics,
    content_key_for,
    extract_feedback_deadline,
    extract_named_organization,
    parse_detail,
    parse_listing,
)

from .current_topic_factory import (
    ARCHIVE_PATH,
    DETAIL_PREFIX,
    LISTING_PATH,
    FakeSite,
    card,
    detail,
    listing,
    simple_site,
)


@pytest.fixture
def patch_fetch(monkeypatch):
    def apply(site):
        monkeypatch.setattr("apps.legal_work.current_topics.fetch", site)
        return site

    return apply


# -- valid crawls -----------------------------------------------------------


def test_a_valid_listing_yields_every_linked_topic(patch_fetch):
    patch_fetch(simple_site(alpha="Alfa eelnõu", beeta="Beeta eelnõu"))

    collection = collect_current_topics()

    assert [entry.title for entry in collection.entries] == ["Alfa eelnõu", "Beeta eelnõu"]
    assert [entry.source_order for entry in collection.entries] == [0, 1]
    assert all(
        entry.canonical_url.startswith("https://www.koda.ee") for entry in collection.entries
    )
    assert collection.details_fetched == 2


def test_the_detail_page_supplies_the_publication_date(patch_fetch):
    """The listing card carries a day and a month and no year at all."""
    patch_fetch(simple_site(alpha="Alfa"))

    entry = collect_current_topics().entries[0]

    assert entry.published_date == dt.date(2026, 8, 5)


def test_the_listing_summary_is_kept_as_plain_text(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(
                    card("alpha", "Alfa", summary="Kliimaministeerium on koostanud eelnõu.")
                ),
                f"{DETAIL_PREFIX}alpha": detail(title="Alfa"),
            }
        )
    )

    entry = collect_current_topics().entries[0]

    assert entry.listing_summary == "Kliimaministeerium on koostanud eelnõu."


def test_a_missing_listing_summary_falls_back_to_the_page_intro(patch_fetch):
    """A card with no summary is not a broken page; the intro says the same."""
    patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(card("alpha", "Alfa", summary="")),
                f"{DETAIL_PREFIX}alpha": detail(title="Alfa", intro="Sünteetiline sissejuhatus."),
            }
        )
    )

    entry = collect_current_topics().entries[0]

    assert entry.listing_summary == "Sünteetiline sissejuhatus."


def test_pagination_is_followed(patch_fetch):
    site = patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(card("alpha", "Alfa"), next_page=True),
                f"{LISTING_PATH}?page=1": listing(card("beeta", "Beeta")),
                f"{DETAIL_PREFIX}alpha": detail(title="Alfa"),
                f"{DETAIL_PREFIX}beeta": detail(title="Beeta"),
            }
        )
    )

    collection = collect_current_topics()

    assert collection.pages_fetched == 2
    assert [entry.title for entry in collection.entries] == ["Alfa", "Beeta"]
    assert f"{LISTING_PATH}?page=1" in site.requested


# -- the collection boundary ------------------------------------------------


def test_a_non_koda_link_is_refused(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(
                    card("alpha", "Alfa", href="https://example.org/et/meie-moju/hetkel-kasil/x")
                )
            }
        )
    )

    with pytest.raises(CurrentTopicCollectionError, match="koda.ee"):
        collect_current_topics()


def test_a_link_outside_the_path_prefix_is_refused(patch_fetch):
    patch_fetch(FakeSite({LISTING_PATH: listing(card("alpha", "Alfa", href="/et/uudised/midagi"))}))

    with pytest.raises(CurrentTopicCollectionError, match="teerada"):
        collect_current_topics()


def test_the_archive_is_refused_even_though_it_shares_the_prefix(patch_fetch):
    patch_fetch(FakeSite({LISTING_PATH: listing(card("arhiiv", "Arhiiv", href=ARCHIVE_PATH))}))

    with pytest.raises(CurrentTopicCollectionError, match="arhiiv"):
        collect_current_topics()


def test_the_pages_own_archive_link_is_not_collected(patch_fetch):
    """The archive link sits outside a teaser card, so it is never a candidate."""
    site = patch_fetch(simple_site(alpha="Alfa"))

    collection = collect_current_topics()

    assert len(collection.entries) == 1
    assert ARCHIVE_PATH not in site.requested


def test_a_link_back_to_the_listing_is_refused(patch_fetch):
    patch_fetch(FakeSite({LISTING_PATH: listing(card("alpha", "Alfa", href=LISTING_PATH))}))

    with pytest.raises(CurrentTopicCollectionError, match="loendile"):
        collect_current_topics()


def test_a_repeated_detail_link_is_refused(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(card("alpha", "Alfa"), card("alpha", "Alfa uuesti")),
                f"{DETAIL_PREFIX}alpha": detail(title="Alfa"),
            }
        )
    )

    with pytest.raises(CurrentTopicCollectionError, match="kordub"):
        collect_current_topics()


def test_the_item_cap_refuses_an_oversized_listing_rather_than_truncating(patch_fetch, settings):
    settings.KODA_CURRENT_TOPICS_MAX_ITEMS = 2
    patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(
                    *(card(f"topic-{index}", f"Teema {index}") for index in range(4))
                )
            }
        )
    )

    with pytest.raises(CurrentTopicCollectionError, match="lubatud mahu"):
        collect_current_topics()


def test_the_page_cap_bounds_the_crawl(patch_fetch, settings):
    settings.KODA_CURRENT_TOPICS_MAX_PAGES = 1
    site = patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(card("alpha", "Alfa"), next_page=True),
                f"{LISTING_PATH}?page=1": listing(card("beeta", "Beeta")),
                f"{DETAIL_PREFIX}alpha": detail(title="Alfa"),
                f"{DETAIL_PREFIX}beeta": detail(title="Beeta"),
            }
        )
    )

    collection = collect_current_topics()

    assert collection.pages_fetched == 1
    assert f"{LISTING_PATH}?page=1" not in site.requested


def test_the_response_size_cap_is_passed_to_the_transport(patch_fetch, settings):
    settings.KODA_CURRENT_TOPICS_MAX_BYTES = 4321
    seen: list[int] = []

    site = simple_site(alpha="Alfa")
    original = site.__call__

    def recording(url, **kwargs):
        seen.append(kwargs["max_bytes"])
        return original(url, **kwargs)

    patch_fetch(recording)
    collect_current_topics()

    assert seen and set(seen) == {4321}


def test_an_oversized_response_fails_the_run(patch_fetch):
    patch_fetch(
        FakeSite(
            {LISTING_PATH: listing(card("alpha", "Alfa"))},
            errors={LISTING_PATH: PublicFetchError("Vastus ületab lubatud suuruse (10 baiti).")},
        )
    )

    with pytest.raises(CurrentTopicCollectionError, match="suuruse"):
        collect_current_topics()


def test_an_unreachable_detail_page_fails_the_whole_run(patch_fetch):
    """A hole in the catalogue would make the matcher report a false `unmatched`."""
    patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(card("alpha", "Alfa"), card("beeta", "Beeta")),
                f"{DETAIL_PREFIX}alpha": detail(title="Alfa"),
            }
        )
    )

    with pytest.raises(CurrentTopicCollectionError):
        collect_current_topics()


def test_a_redirect_off_the_allowlist_is_refused_before_it_is_requested(monkeypatch, settings):
    """Every hop is checked, so a redirect cannot walk the crawl off koda.ee."""
    from apps.core import public_http

    class Redirecting:
        def __init__(self):
            self.requested: list[str] = []

        def get(self, url, *, headers=None, timeout=None, allow_redirects=None, stream=None):
            assert allow_redirects is False, "redirects must be followed explicitly"
            self.requested.append(url)
            return _FakeResponse(302, {"Location": "https://evil.example.org/et/x"}, url)

        def close(self):
            pass

    session = Redirecting()
    with pytest.raises(PublicFetchError, match="evil.example.org"):
        public_http.fetch(
            f"https://www.koda.ee{LISTING_PATH}",
            allowed_hosts=settings.KODA_ALLOWED_HOSTS,
            max_bytes=1000,
            session=session,
        )
    assert session.requested == [f"https://www.koda.ee{LISTING_PATH}"]


class _FakeResponse:
    def __init__(self, status_code, headers, url):
        self.status_code = status_code
        self.headers = headers
        self.url = url

    def close(self):
        pass

    def iter_content(self, chunk_size=None):
        return iter(())


def test_every_request_carries_the_koda_allowlist(patch_fetch, settings):
    seen: list[frozenset[str]] = []
    site = simple_site(alpha="Alfa")
    original = site.__call__

    def recording(url, **kwargs):
        seen.append(kwargs["allowed_hosts"])
        return original(url, **kwargs)

    patch_fetch(recording)
    collect_current_topics()

    assert seen and set(seen) == {settings.KODA_ALLOWED_HOSTS}
    assert settings.KODA_ALLOWED_HOSTS == frozenset({"www.koda.ee", "koda.ee"})


# -- what is stored ---------------------------------------------------------


def test_a_missing_title_is_refused(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(card("alpha", "")),
                f"{DETAIL_PREFIX}alpha": detail(title=""),
            }
        )
    )

    with pytest.raises(CurrentTopicCollectionError, match="pealkiri"):
        collect_current_topics()


def test_scripts_styles_and_navigation_are_never_stored(patch_fetch):
    patch_fetch(simple_site(alpha="Alfa"))

    entry = collect_current_topics().entries[0]

    stored = " ".join([entry.title, entry.listing_summary, entry.body_text])
    assert "<" not in stored
    assert "tracking" not in stored
    assert "Uudised" not in stored
    assert "Sünteetiline jalus" not in stored


def test_the_language_switcher_is_not_mistaken_for_the_body(patch_fetch):
    patch_fetch(simple_site(alpha="Alfa"))

    entry = collect_current_topics().entries[0]

    assert "Language switcher" not in entry.body_text
    assert "Русский" not in entry.body_text


def test_the_repeated_sideblock_does_not_double_the_stored_text(patch_fetch):
    patch_fetch(simple_site(alpha="Alfa"))

    entry = collect_current_topics().entries[0]

    assert "Kordus" not in entry.body_text
    assert entry.published_date == dt.date(2026, 8, 5)


def test_the_body_is_bounded(patch_fetch, settings):
    settings.KODA_CURRENT_TOPICS_BODY_MAX_LENGTH = 60
    patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(card("alpha", "Alfa")),
                f"{DETAIL_PREFIX}alpha": detail(title="Alfa", body="pikk " * 400),
            }
        )
    )

    entry = collect_current_topics().entries[0]

    assert len(entry.body_text) <= 61


# -- deadline extraction ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Anna hiljemalt 4. märtsiks teada.", dt.date(2026, 3, 4)),
        ("Anna meile hiljemalt 26. märtsiks teada.", dt.date(2026, 3, 26)),
        ("Ootame vastust hiljemalt 12. märtsil.", dt.date(2026, 3, 12)),
        ("Anna hiljemalt 18. augustiks teada.", dt.date(2026, 8, 18)),
        ("Anna hiljemalt 09.03.2027 teada.", dt.date(2027, 3, 9)),
        ("Anna hiljemalt 9. märtsiks 2027 teada.", dt.date(2027, 3, 9)),
    ],
)
def test_ordinary_estonian_deadline_forms_are_parsed(text, expected):
    assert extract_feedback_deadline(text, published_on=dt.date(2026, 3, 1)) == expected


def test_a_deadline_in_the_next_calendar_year_rolls_forward():
    """A December announcement naming a January deadline means next January."""
    assert extract_feedback_deadline(
        "Anna hiljemalt 15. jaanuariks teada.", published_on=dt.date(2026, 12, 20)
    ) == dt.date(2027, 1, 15)


def test_a_deadline_without_calendar_context_is_left_null():
    assert extract_feedback_deadline("Anna hiljemalt 4. märtsiks teada.", published_on=None) is None


def test_two_disagreeing_deadlines_are_left_null():
    text = "Anna hiljemalt 4. märtsiks teada. Anna hiljemalt 19. aprilliks teada."

    assert extract_feedback_deadline(text, published_on=dt.date(2026, 3, 1)) is None


def test_an_unanchored_date_is_not_read_as_a_deadline():
    """A commencement date in the prose is not the Chamber's feedback deadline."""
    text = "Määrus jõustub 1. jaanuaril ja seda kohaldatakse 4. märtsist."

    assert extract_feedback_deadline(text, published_on=dt.date(2026, 3, 1)) is None


def test_a_missing_deadline_is_accepted_rather_than_rejected(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(card("alpha", "Alfa", summary="Ilma tähtajata teade.")),
                f"{DETAIL_PREFIX}alpha": detail(
                    title="Alfa", intro="Ilma tähtajata.", body="Ilma tähtajata."
                ),
            }
        )
    )

    entry = collect_current_topics().entries[0]

    assert entry.feedback_deadline is None
    assert entry.title == "Alfa"


# -- organisation extraction ------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Rahandusministeeriumis on valminud eelnõu.", "Rahandusministeerium"),
        ("Kliimaministeerium on koostanud eelnõu.", "Kliimaministeerium"),
        (
            "Majandus- ja kommunikatsiooniministeerium on koostanud eelnõu.",
            "Majandus- ja Kommunikatsiooniministeerium",
        ),
        ("Euroopa Komisjon on algatanud küsitluse.", "Euroopa Komisjon"),
        ("Keegi tundmatu on koostanud eelnõu.", ""),
    ],
)
def test_named_organizations_are_read_from_a_closed_vocabulary(text, expected):
    assert extract_named_organization(text) == expected


def test_the_summary_is_searched_before_the_body():
    assert (
        extract_named_organization(
            "Kliimaministeerium on koostanud eelnõu.",
            "Rahandusministeerium kommenteeris hiljem.",
        )
        == "Kliimaministeerium"
    )


# -- change detection -------------------------------------------------------


def test_markup_churn_produces_the_same_checksum(patch_fetch):
    first = patch_fetch(simple_site(alpha="Alfa"))
    before = collect_current_topics()

    noisy = {
        path: html.replace("<div class=", '<div  data-build="9134" class=')
        for path, html in first.pages.items()
    }
    patch_fetch(FakeSite(noisy))
    after = collect_current_topics()

    assert before.sha256 == after.sha256


def test_reordering_two_unchanged_cards_produces_the_same_checksum(patch_fetch):
    patch_fetch(simple_site(alpha="Alfa", beeta="Beeta"))
    before = collect_current_topics()

    patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(card("beeta", "Beeta"), card("alpha", "Alfa")),
                f"{DETAIL_PREFIX}alpha": detail(title="Alfa"),
                f"{DETAIL_PREFIX}beeta": detail(title="Beeta"),
            }
        )
    )
    after = collect_current_topics()

    assert before.sha256 == after.sha256


def test_changed_content_produces_a_new_checksum(patch_fetch):
    patch_fetch(simple_site(alpha="Alfa"))
    before = collect_current_topics()

    patch_fetch(
        FakeSite(
            {
                LISTING_PATH: listing(card("alpha", "Alfa")),
                f"{DETAIL_PREFIX}alpha": detail(title="Alfa", body="Hoopis teine sisu."),
            }
        )
    )
    after = collect_current_topics()

    assert before.sha256 != after.sha256


def test_an_empty_listing_is_refused(patch_fetch):
    patch_fetch(FakeSite({LISTING_PATH: listing()}))

    with pytest.raises(CurrentTopicCollectionError, match="ühtegi teemat"):
        collect_current_topics()


# -- parser units -----------------------------------------------------------


def test_the_content_key_is_stable_across_host_and_trailing_slash():
    assert content_key_for(
        "https://www.koda.ee/et/meie-moju/hetkel-kasil/alpha"
    ) == content_key_for("https://koda.ee/et/meie-moju/hetkel-kasil/alpha/")


def test_two_different_paths_have_different_content_keys():
    assert content_key_for(f"https://koda.ee{DETAIL_PREFIX}alpha") != content_key_for(
        f"https://koda.ee{DETAIL_PREFIX}beeta"
    )


def test_the_listing_parser_ignores_links_outside_a_card():
    cards = parse_listing(listing(card("alpha", "Alfa")))

    assert [entry["url"] for entry in cards] == [f"{DETAIL_PREFIX}alpha"]


def test_the_detail_parser_separates_block_elements():
    parsed = parse_detail(detail(title="Alfa", body="<p>Esimene.</p><p>Teine.</p>"))

    assert "Esimene. Teine." in parsed["body"]
