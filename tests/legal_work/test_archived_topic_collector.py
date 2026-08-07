"""The archive crawl: bounded, resumable, and honest about what it has read.

Synthetic pages only. No test here contacts Koda.ee or any other host.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.core.public_http import FetchFailure, PublicFetchError, RetryableFetchError
from apps.legal_work.archived_topics import (
    ArchiveCollectionError,
    _fetch_html,
    collect_archive_index,
    content_key_for,
    discover_last_page,
    hydrate_detail,
    hydration_cutoff,
)
from apps.legal_work.models import DetailStatus

from .archive_factory import (
    ARCHIVE_PATH,
    CURRENT_PATH,
    DETAIL_PREFIX,
    archive_card,
    archive_listing,
    archive_site,
    pager,
    simple_archive,
)
from .current_topic_factory import FakeSite, detail, not_found, refused


@pytest.fixture
def patch_fetch(monkeypatch):
    def apply(site):
        monkeypatch.setattr("apps.legal_work.archived_topics.fetch", site)
        return site

    return apply


@pytest.fixture(autouse=True)
def no_pause(settings):
    """Tests do not wait between requests; production does."""
    settings.KODA_ARCHIVE_REQUEST_PAUSE_SECONDS = 0


def index(**kwargs):
    kwargs.setdefault("full", True)
    return collect_archive_index(**kwargs)


# -- the listing walk -------------------------------------------------------


def test_a_single_page_archive_is_collected(patch_fetch):
    patch_fetch(simple_archive("alpha", "beeta"))

    result = index()

    assert [entry.title for entry in result.entries] == ["Arhiveeritud alpha", "Arhiveeritud beeta"]
    assert result.pages_fetched == 1
    assert result.reached_end is True


def test_pagination_is_followed_to_the_advertised_last_page(patch_fetch):
    patch_fetch(
        archive_site(
            {
                0: [("a1", "Üks"), ("a2", "Kaks")],
                1: [("b1", "Kolm")],
                2: [("c1", "Neli")],
            }
        )
    )

    result = index()

    assert result.pages_fetched == 3
    assert len(result.entries) == 4
    assert result.reached_end is True
    assert [entry.source_page for entry in result.entries] == [0, 0, 1, 2]


def test_the_last_page_is_read_from_the_pager_not_probed():
    html = archive_listing(archive_card("a", "A"), current=0, last=142)

    assert discover_last_page(html) == 142


def test_a_pager_advertising_nothing_falls_back_to_walking(patch_fetch):
    """No `pager__item--last` is survivable: the walk just stops at the end."""
    pages = {
        ARCHIVE_PATH: archive_listing(archive_card("a", "A")),
        f"{ARCHIVE_PATH}?page=1": archive_listing(),
    }
    patch_fetch(FakeSite(pages))

    result = index()

    assert len(result.entries) == 1
    assert result.reached_end is True


def test_a_page_repeating_an_earlier_page_is_refused(patch_fetch):
    """A looping pager would otherwise walk to the cap collecting one page."""
    same = archive_listing(archive_card("a", "A"), current=0, last=3)
    patch_fetch(
        FakeSite(
            {
                ARCHIVE_PATH: same,
                f"{ARCHIVE_PATH}?page=1": archive_listing(
                    archive_card("a", "A"), current=1, last=3
                ),
            }
        )
    )

    with pytest.raises(ArchiveCollectionError, match="kordab"):
        index()


def test_the_page_cap_bounds_the_walk(patch_fetch, settings):
    settings.KODA_ARCHIVE_MAX_PAGES = 2
    patch_fetch(archive_site({0: [("a", "A")], 1: [("b", "B")], 2: [("c", "C")], 3: [("d", "D")]}))

    result = index()

    assert result.pages_fetched == 2


def test_the_item_cap_refuses_an_oversized_archive(patch_fetch, settings):
    settings.KODA_ARCHIVE_MAX_ITEMS = 2
    patch_fetch(archive_site({0: [(f"s{i}", f"T{i}") for i in range(4)]}))

    with pytest.raises(ArchiveCollectionError, match="lubatud mahu"):
        index()


def test_a_failed_listing_page_fails_the_run(patch_fetch):
    patch_fetch(
        FakeSite({}, errors={ARCHIVE_PATH: PublicFetchError("Allikas vastas koodiga 503.")})
    )

    with pytest.raises(ArchiveCollectionError):
        index()


def test_an_empty_archive_is_refused(patch_fetch):
    patch_fetch(FakeSite({ARCHIVE_PATH: archive_listing()}))

    with pytest.raises(ArchiveCollectionError, match="ühtegi kirjet"):
        index()


# -- URL rules --------------------------------------------------------------


@pytest.mark.parametrize(
    ("href", "match"),
    [
        ("https://example.org/et/meie-moju/hetkel-kasil/x", "koda.ee"),
        ("http://www.koda.ee/et/meie-moju/hetkel-kasil/x", "koda.ee"),
        ("/et/uudised/midagi", "teerada"),
        (CURRENT_PATH, "hetkel käsil loendile"),
        (ARCHIVE_PATH, "arhiivi loendile"),
        ("https://user:pw@www.koda.ee/et/meie-moju/hetkel-kasil/x", "kasutajaandmeid"),
        ("/et/meie-moju/hetkel-kasil/x?page=2", "päringustringi"),
    ],
)
def test_an_invalid_entry_link_is_refused(patch_fetch, href, match):
    patch_fetch(FakeSite({ARCHIVE_PATH: archive_listing(archive_card("a", "A", href=href))}))

    with pytest.raises(ArchiveCollectionError, match=match):
        index()


def test_a_repeated_entry_link_is_refused(patch_fetch):
    patch_fetch(
        FakeSite(
            {ARCHIVE_PATH: archive_listing(archive_card("a", "A"), archive_card("a", "A uuesti"))}
        )
    )

    with pytest.raises(ArchiveCollectionError, match="kordub"):
        index()


def test_an_entry_without_a_title_is_refused(patch_fetch):
    patch_fetch(FakeSite({ARCHIVE_PATH: archive_listing(archive_card("a", ""))}))

    with pytest.raises(ArchiveCollectionError, match="pealkiri"):
        index()


def test_both_host_spellings_reach_the_same_content_key():
    """A consultation keeps its identity across the move into the archive."""
    assert content_key_for(
        "https://www.koda.ee/et/meie-moju/hetkel-kasil/alpha"
    ) == content_key_for("https://koda.ee/et/meie-moju/hetkel-kasil/alpha/")


def test_the_bare_host_is_normalised_to_www(patch_fetch):
    """One spelling per page, so the overlap check compares equal strings."""
    patch_fetch(
        FakeSite(
            {
                ARCHIVE_PATH: archive_listing(
                    archive_card("a", "A", href="https://koda.ee/et/meie-moju/hetkel-kasil/a")
                )
            }
        )
    )

    entry = index().entries[0]

    assert entry.canonical_url.startswith("https://www.koda.ee/")


# -- what the listing does and does not give --------------------------------


def test_the_listing_gives_no_publication_date(patch_fetch):
    """The archive card prints a day and a month and no year, so nothing dates it."""
    patch_fetch(simple_archive("alpha"))

    entry = index().entries[0]

    assert not hasattr(entry, "published_date")
    assert entry.title and entry.listing_summary


def test_no_navigation_or_script_becomes_an_entry(patch_fetch):
    patch_fetch(simple_archive("alpha"))

    result = index()

    assert len(result.entries) == 1
    blob = " ".join(e.title + e.listing_summary for e in result.entries)
    assert "<" not in blob
    assert "Uudised" not in blob
    assert "kummitus" not in blob


# -- detail hydration -------------------------------------------------------


def test_a_detail_page_is_parsed_and_dated(patch_fetch):
    site = patch_fetch(simple_archive("alpha"))

    result = hydrate_detail(f"https://www.koda.ee{DETAIL_PREFIX}alpha", session=site)

    assert result.status == DetailStatus.HYDRATED
    assert result.detail_title
    assert result.body_text
    assert result.published_date == dt.date(2026, 8, 5)
    assert result.content_hash


def test_a_detail_page_without_a_deadline_is_still_hydrated(patch_fetch):
    site = patch_fetch(
        archive_site(
            {0: [("alpha", "Alfa")]},
            details={"alpha": detail(title="Alfa", intro="Ilma tähtajata.", body="Sisu.")},
        )
    )

    result = hydrate_detail(f"https://www.koda.ee{DETAIL_PREFIX}alpha", session=site)

    assert result.status == DetailStatus.HYDRATED
    assert result.feedback_deadline is None


def test_a_missing_detail_page_is_recorded_not_raised(patch_fetch):
    site = patch_fetch(archive_site({0: [("alpha", "Alfa")]}))

    result = hydrate_detail(f"https://www.koda.ee{DETAIL_PREFIX}puudub", session=site)

    assert result.status == DetailStatus.FAILED
    assert result.failure_code == "http_404"
    assert result.body_text == ""


def test_an_unparsable_detail_page_is_recorded_as_failed(patch_fetch):
    site = patch_fetch(
        archive_site(
            {0: [("alpha", "Alfa")]},
            details={"alpha": "<!doctype html><html><body><p>ei midagi</p></body></html>"},
        )
    )

    result = hydrate_detail(f"https://www.koda.ee{DETAIL_PREFIX}alpha", session=site)

    assert result.status == DetailStatus.FAILED
    assert result.failure_code == "unparsable"


def test_a_failure_code_carries_no_url_or_message(patch_fetch):
    site = patch_fetch(archive_site({0: [("alpha", "Alfa")]}))

    result = hydrate_detail(f"https://www.koda.ee{DETAIL_PREFIX}puudub", session=site)

    assert "koda.ee" not in result.failure_code
    assert "http" not in result.failure_code.replace("http_404", "")
    assert len(result.failure_code) <= 32


def test_the_language_switcher_never_reaches_the_body(patch_fetch):
    site = patch_fetch(simple_archive("alpha"))

    result = hydrate_detail(f"https://www.koda.ee{DETAIL_PREFIX}alpha", session=site)

    assert "Language switcher" not in result.body_text
    assert "Русский" not in result.body_text
    assert "<" not in result.body_text


# -- the hydration window ---------------------------------------------------


def test_the_window_cutoff_follows_the_setting(settings):
    settings.KODA_ARCHIVE_HYDRATION_WINDOW_DAYS = 30
    today = dt.date(2026, 8, 6)

    assert hydration_cutoff(today) == dt.date(2026, 7, 7)


def test_the_default_window_is_a_year(settings):
    assert settings.KODA_ARCHIVE_HYDRATION_WINDOW_DAYS == 365


# -- incremental behaviour --------------------------------------------------


def test_an_incremental_walk_stops_after_known_unchanged_pages(patch_fetch, settings):
    settings.KODA_ARCHIVE_KNOWN_PAGES_BEFORE_STOP = 1
    site = patch_fetch(
        archive_site({0: [("a", "A")], 1: [("b", "B")], 2: [("c", "C")], 3: [("d", "D")]})
    )
    known = {content_key_for(f"https://www.koda.ee{DETAIL_PREFIX}a")}

    result = collect_archive_index(session=site, full=False, known_keys=frozenset(known))

    assert result.stopped_early is True
    assert result.pages_fetched == 1


def test_an_incremental_walk_continues_past_a_page_with_new_entries(patch_fetch, settings):
    settings.KODA_ARCHIVE_KNOWN_PAGES_BEFORE_STOP = 1
    site = patch_fetch(archive_site({0: [("a", "A")], 1: [("b", "B")], 2: [("c", "C")]}))

    result = collect_archive_index(session=site, full=False, known_keys=frozenset())

    assert result.pages_fetched > 1


def test_a_full_walk_ignores_what_is_already_known(patch_fetch, settings):
    settings.KODA_ARCHIVE_KNOWN_PAGES_BEFORE_STOP = 1
    site = patch_fetch(archive_site({0: [("a", "A")], 1: [("b", "B")]}))
    every_key = {
        content_key_for(f"https://www.koda.ee{DETAIL_PREFIX}{slug}") for slug in ("a", "b")
    }

    result = collect_archive_index(session=site, full=True, known_keys=frozenset(every_key))

    assert result.stopped_early is False
    assert result.pages_fetched == 2


# -- pacing and transport ---------------------------------------------------


def test_every_request_carries_the_koda_allowlist(patch_fetch, settings):
    seen = []
    site = simple_archive("alpha")
    original = site.__call__

    def recording(url, **kwargs):
        seen.append(kwargs["allowed_hosts"])
        return original(url, **kwargs)

    patch_fetch(recording)
    index()

    assert seen and set(seen) == {settings.KODA_ALLOWED_HOSTS}


def test_the_response_cap_is_passed_to_the_transport(patch_fetch, settings):
    settings.KODA_ARCHIVE_MAX_BYTES = 1234
    seen = []
    site = simple_archive("alpha")
    original = site.__call__

    def recording(url, **kwargs):
        seen.append(kwargs["max_bytes"])
        return original(url, **kwargs)

    patch_fetch(recording)
    index()

    assert seen and set(seen) == {1234}


def test_the_pager_helper_marks_its_own_last_page():
    assert "pager__item--last" in pager(current=0, last=5)
    assert pager(current=0, last=0) == ""


# -- failure classification ---------------------------------------------
#
# A detail failure used to be classified by searching the error message for
# `"404"` and for the Estonian word `"keeldus"`. Both are display strings, so
# rewording or translating one silently reclassified every failure that
# depended on it — and any message that happened to contain `404` for an
# unrelated reason was recorded as a missing page. Classification now reads
# `PublicFetchError.failure`, which is part of the contract.


def _detail_failure(patch_fetch, error):
    site = patch_fetch(
        archive_site({0: [("alpha", "Alfa")]}, errors={f"{DETAIL_PREFIX}alpha": error})
    )
    return hydrate_detail(f"https://www.koda.ee{DETAIL_PREFIX}alpha", session=site)


def test_a_404_is_recorded_as_missing(patch_fetch):
    result = _detail_failure(patch_fetch, not_found())

    assert result.status == DetailStatus.FAILED
    assert result.failure_code == "http_404"


@pytest.mark.parametrize("status", [401, 403])
def test_an_access_refusal_is_recorded_as_refused(patch_fetch, status):
    result = _detail_failure(patch_fetch, refused(status))

    assert result.failure_code == "http_refused"


def test_a_timeout_is_recorded_as_unavailable(patch_fetch):
    result = _detail_failure(
        patch_fetch, RetryableFetchError("Päring aegus.", failure=FetchFailure.TIMEOUT)
    )

    assert result.failure_code == "unavailable"


def test_a_transport_failure_is_recorded_as_unavailable(patch_fetch):
    result = _detail_failure(
        patch_fetch,
        RetryableFetchError(
            "Ühendus ebaõnnestus: ConnectionError.", failure=FetchFailure.TRANSPORT
        ),
    )

    assert result.failure_code == "unavailable"


def test_a_server_error_is_recorded_as_unavailable(patch_fetch):
    result = _detail_failure(
        patch_fetch,
        RetryableFetchError(
            "Allikas vastas koodiga 503.", failure=FetchFailure.SERVER_ERROR, status_code=503
        ),
    )

    assert result.failure_code == "unavailable"


def test_an_unexpected_content_type_is_recorded_as_unavailable(patch_fetch):
    result = _detail_failure(
        patch_fetch,
        PublicFetchError("Ootamatu sisutüüp: image/png.", failure=FetchFailure.UNEXPECTED_CONTENT),
    )

    assert result.failure_code == "unavailable"


def test_an_unknown_source_error_is_recorded_as_unavailable(patch_fetch):
    """The safe default: unreachable for a reason we do not model separately."""
    result = _detail_failure(patch_fetch, PublicFetchError("Midagi läks valesti."))

    assert result.failure_code == "unavailable"


def test_a_malformed_page_is_still_unparsable_rather_than_unavailable(patch_fetch):
    """A page that arrives and cannot be read is a different failure."""
    site = patch_fetch(
        archive_site(
            {0: [("alpha", "Alfa")]},
            details={"alpha": "<!doctype html><html><body><p>ei midagi</p></body></html>"},
        )
    )

    result = hydrate_detail(f"https://www.koda.ee{DETAIL_PREFIX}alpha", session=site)

    assert result.failure_code == "unparsable"


def test_the_message_alone_no_longer_decides_the_classification(patch_fetch):
    """The regression, from both directions.

    A refusal whose message happens to contain `404` used to be recorded as a
    missing page; a missing page whose message was reworded used to fall through
    to `unavailable`.
    """
    misleading = _detail_failure(
        patch_fetch,
        PublicFetchError(
            "Allikas keeldus ligipääsust (403). Vaata viidet 404 dokumentatsioonis.",
            failure=FetchFailure.REFUSED,
            status_code=403,
        ),
    )
    assert misleading.failure_code == "http_refused"

    reworded = _detail_failure(
        patch_fetch,
        PublicFetchError("Lehte ei ole olemas.", failure=FetchFailure.NOT_FOUND, status_code=404),
    )
    assert reworded.failure_code == "http_404"


def test_the_structured_failure_survives_the_wrapping(patch_fetch):
    """`ArchiveCollectionError` must carry the classification, not drop it."""
    site = patch_fetch(
        archive_site({0: [("alpha", "Alfa")]}, errors={f"{DETAIL_PREFIX}alpha": refused(403)})
    )

    with pytest.raises(ArchiveCollectionError) as error:
        _fetch_html(f"https://www.koda.ee{DETAIL_PREFIX}alpha", session=site)

    assert error.value.failure == FetchFailure.REFUSED
    assert error.value.status_code == 403


def test_a_failure_code_is_still_free_of_any_source_message(patch_fetch):
    """No raw source text may reach stored viewer-facing state."""
    result = _detail_failure(
        patch_fetch,
        PublicFetchError(
            "Sisemine viga: /srv/secret/path leaked", failure=FetchFailure.SERVER_ERROR
        ),
    )

    assert result.failure_code == "unavailable"
    assert "secret" not in result.failure_code
    assert result.body_text == ""


# -- the hydration cutoff uses application time --------------------------


def test_the_hydration_cutoff_follows_the_application_date(settings, monkeypatch):
    """`Europe/Tallinn`, not the container's UTC clock.

    The archive runs overnight, and between midnight and 03:00 Tallinn the
    container's UTC date is still yesterday — so `date.today()` moved the window
    by a day for exactly the runs that use it. Pinning `timezone.localdate` to a
    date that is not today is what separates the two: the old implementation
    ignored it and answered from the real system clock.
    """
    from apps.legal_work import archived_topics

    window = settings.KODA_ARCHIVE_HYDRATION_WINDOW_DAYS
    tallinn_today = dt.date(2026, 7, 1)
    monkeypatch.setattr(archived_topics.timezone, "localdate", lambda: tallinn_today)

    assert hydration_cutoff() == tallinn_today - dt.timedelta(days=window)
    assert hydration_cutoff() != dt.date.today() - dt.timedelta(days=window)


def test_the_cutoff_still_honours_an_explicit_date(settings):
    window = settings.KODA_ARCHIVE_HYDRATION_WINDOW_DAYS

    assert hydration_cutoff(dt.date(2026, 3, 10)) == dt.date(2026, 3, 10) - dt.timedelta(
        days=window
    )


def test_no_container_local_date_remains_in_the_archive_collector():
    """The whole module reads application time, not the system clock."""
    import inspect

    from apps.legal_work import archived_topics

    assert "date.today()" not in inspect.getsource(archived_topics)
