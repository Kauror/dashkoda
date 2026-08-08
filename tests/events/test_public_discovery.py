"""Sitemap discovery of public event pages.

Every page here is synthetic. No live koda.ee page is committed and nothing in
this file makes a network request — `fetch` is replaced in both modules the
crawl reaches through.

The properties under test are the ones the catalogue's usefulness rests on:
past events are kept, nothing is ever removed, a partial run says it is partial,
and category listings never become resources.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.events.public_discovery import (
    WARN_DETAIL_CAP,
    WARN_DETAIL_FAILED,
    WARN_SITEMAP_UNREADABLE,
    discover_public_events,
)
from apps.events.public_models import (
    DiscoveryMode,
    DiscoveryOrigin,
    PublicEventDiscoverySnapshot,
    PublicEventResource,
)

from .test_collector import FakeSite, category_page, detail

ROOT = "https://www.koda.ee"
SITEMAP = f"{ROOT}/et/sitemap.xml"


def url_for(slug: str) -> str:
    return f"{ROOT}/et/sundmused/{slug}"


def sitemap(*locations: str) -> str:
    entries = "".join(f"<url><loc>{location}</loc></url>" for location in locations)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset>{entries}</urlset>'


def sitemap_index(*children: str) -> str:
    entries = "".join(f"<sitemap><loc>{child}</loc></sitemap>" for child in children)
    return f'<?xml version="1.0" encoding="UTF-8"?><sitemapindex>{entries}</sitemapindex>'


class XmlSite(FakeSite):
    """`FakeSite`, but answering XML with an XML content type.

    The crawl asks for `application/xml` on the sitemap and `text/html` on the
    detail pages, and `fetch` enforces that. Serving everything as `text/html`
    would make the test pass against a collector that had stopped checking.
    """

    def __call__(self, url, **kwargs):
        result = super().__call__(url, **kwargs)
        if url.endswith(".xml"):
            return type(result)(
                status_code=result.status_code,
                content=result.content,
                content_type="application/xml",
                etag="",
                last_modified="",
                final_host="www.koda.ee",
            )
        return result


@pytest.fixture
def patch_fetch(monkeypatch):
    """Replace `fetch` where discovery calls it.

    Discovery has its own `_fetch`, so patching the collector's name alone
    would leave the real one in place and the test would try to reach koda.ee.
    """

    def apply(site):
        monkeypatch.setattr("apps.events.public_discovery.fetch", site)
        return site

    return apply


def past(days: int) -> str:
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


# -- what discovery finds ------------------------------------------------


def test_it_records_a_page_for_an_event_that_already_happened(patch_fetch, db):
    """The whole reason this exists beside the calendar feed."""
    patch_fetch(
        XmlSite(
            {
                "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
                "/et/sitemap-1.xml": sitemap(url_for("vana")),
                "/et/sundmused/vana": detail(name="Ammune koolitus", start=past(900)),
            }
        )
    )

    tally = discover_public_events(mode=DiscoveryMode.FULL)

    assert tally.created == 1
    resource = PublicEventResource.objects.get()
    assert resource.title == "Ammune koolitus"
    assert resource.canonical_url == url_for("vana")
    assert resource.stable_key == "vana"
    assert resource.discovered_from == DiscoveryOrigin.SITEMAP


def test_a_category_listing_never_becomes_a_resource(patch_fetch, db):
    """`/et/sundmused/koolitused` is a category page, not an event.

    It is rejected because its page carries no `Event` structured data, never
    because its slug was recognised — a new category must be rejected too.
    """
    patch_fetch(
        XmlSite(
            {
                "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
                "/et/sitemap-1.xml": sitemap(url_for("koolitused"), url_for("paris")),
                "/et/sundmused/koolitused": category_page(),
                "/et/sundmused/paris": detail(name="Päris sündmus", start=past(10)),
            }
        )
    )

    tally = discover_public_events(mode=DiscoveryMode.FULL)

    assert tally.created == 1
    assert list(PublicEventResource.objects.values_list("stable_key", flat=True)) == ["paris"]


def test_urls_outside_the_event_prefix_are_never_fetched(patch_fetch, db):
    """The sitemap names 18,000 URLs. Only event pages may cost a request."""
    site = patch_fetch(
        XmlSite(
            {
                "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
                "/et/sitemap-1.xml": sitemap(
                    f"{ROOT}/et/uudised/mingi-uudis",
                    f"{ROOT}/et/liige/mingi-liige",
                    f"{ROOT}/et/sundmused/alfa/lisa",
                    url_for("alfa"),
                ),
                "/et/sundmused/alfa": detail(name="Alfa", start=past(5)),
            }
        )
    )

    discover_public_events(mode=DiscoveryMode.FULL)

    fetched = [path for path in site.requested if not path.endswith(".xml")]
    assert fetched == ["/et/sundmused/alfa"]


def test_a_url_on_another_host_is_refused(patch_fetch, db):
    patch_fetch(
        XmlSite(
            {
                "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
                "/et/sitemap-1.xml": sitemap("https://example.invalid/et/sundmused/kuri"),
            }
        )
    )

    tally = discover_public_events(mode=DiscoveryMode.FULL)

    assert tally.urls_seen == 0
    assert not PublicEventResource.objects.exists()


# -- re-observation ------------------------------------------------------


def test_seeing_an_unchanged_page_again_is_not_a_change(patch_fetch, db):
    pages = {
        "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
        "/et/sitemap-1.xml": sitemap(url_for("alfa")),
        "/et/sundmused/alfa": detail(name="Alfa", start=past(5)),
    }
    patch_fetch(XmlSite(pages))

    discover_public_events(mode=DiscoveryMode.FULL)
    before = PublicEventResource.objects.get()

    second = discover_public_events(mode=DiscoveryMode.FULL)
    after = PublicEventResource.objects.get()

    assert second.created == 0
    assert second.unchanged == 1
    assert after.last_changed_at == before.last_changed_at
    assert after.last_seen_at >= before.last_seen_at


def test_a_corrected_title_is_recorded_as_a_change(patch_fetch, db):
    pages = {
        "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
        "/et/sitemap-1.xml": sitemap(url_for("alfa")),
        "/et/sundmused/alfa": detail(name="Alfa", start=past(5)),
    }
    patch_fetch(XmlSite(pages))
    discover_public_events(mode=DiscoveryMode.FULL)
    first = PublicEventResource.objects.get()

    pages["/et/sundmused/alfa"] = detail(name="Alfa (parandatud)", start=past(5))
    tally = discover_public_events(mode=DiscoveryMode.FULL)

    resource = PublicEventResource.objects.get()
    assert tally.updated == 1
    assert resource.pk == first.pk, "a correction must not mint a new identity"
    assert resource.title == "Alfa (parandatud)"
    assert resource.first_seen_at == first.first_seen_at


def test_an_incremental_run_leaves_known_pages_alone(patch_fetch, db):
    pages = {
        "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
        "/et/sitemap-1.xml": sitemap(url_for("alfa"), url_for("beeta")),
        "/et/sundmused/alfa": detail(name="Alfa", start=past(5)),
        "/et/sundmused/beeta": detail(name="Beeta", start=past(6)),
    }
    site = patch_fetch(XmlSite(pages))
    discover_public_events(mode=DiscoveryMode.FULL)

    site.requested.clear()
    tally = discover_public_events(mode=DiscoveryMode.INCREMENTAL)

    assert [path for path in site.requested if not path.endswith(".xml")] == []
    assert tally.created == tally.updated == tally.unchanged == 0


def test_an_incremental_run_reads_a_page_it_has_never_seen(patch_fetch, db):
    pages = {
        "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
        "/et/sitemap-1.xml": sitemap(url_for("alfa")),
        "/et/sundmused/alfa": detail(name="Alfa", start=past(5)),
    }
    patch_fetch(XmlSite(pages))
    discover_public_events(mode=DiscoveryMode.FULL)

    pages["/et/sitemap-1.xml"] = sitemap(url_for("alfa"), url_for("uus"))
    pages["/et/sundmused/uus"] = detail(name="Uus", start=past(1))
    tally = discover_public_events(mode=DiscoveryMode.INCREMENTAL)

    assert tally.created == 1
    assert PublicEventResource.objects.count() == 2


def test_a_page_stale_beyond_the_window_is_re_read(patch_fetch, db, settings):
    settings.KODA_EVENT_PAGES_RECHECK_AFTER_DAYS = 7
    pages = {
        "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
        "/et/sitemap-1.xml": sitemap(url_for("alfa")),
        "/et/sundmused/alfa": detail(name="Alfa", start=past(5)),
    }
    patch_fetch(XmlSite(pages))
    discover_public_events(mode=DiscoveryMode.FULL)

    stale = timezone.now() - dt.timedelta(days=30)
    PublicEventResource.objects.update(last_seen_at=stale)

    tally = discover_public_events(mode=DiscoveryMode.INCREMENTAL)

    assert tally.unchanged == 1
    assert PublicEventResource.objects.get().last_seen_at > stale


# -- nothing is ever removed ---------------------------------------------


def test_a_page_that_disappears_from_the_sitemap_keeps_its_row(patch_fetch, db):
    """A link that worked yesterday must not vanish because a sitemap changed."""
    pages = {
        "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
        "/et/sitemap-1.xml": sitemap(url_for("alfa"), url_for("beeta")),
        "/et/sundmused/alfa": detail(name="Alfa", start=past(5)),
        "/et/sundmused/beeta": detail(name="Beeta", start=past(6)),
    }
    patch_fetch(XmlSite(pages))
    discover_public_events(mode=DiscoveryMode.FULL)

    pages["/et/sitemap-1.xml"] = sitemap(url_for("alfa"))
    discover_public_events(mode=DiscoveryMode.FULL)

    assert PublicEventResource.objects.count() == 2


def test_a_page_that_starts_failing_keeps_its_row(patch_fetch, db):
    from apps.core.public_http import PublicFetchError

    pages = {
        "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
        "/et/sitemap-1.xml": sitemap(url_for("alfa")),
        "/et/sundmused/alfa": detail(name="Alfa", start=past(5)),
    }
    patch_fetch(XmlSite(pages))
    discover_public_events(mode=DiscoveryMode.FULL)

    patch_fetch(
        XmlSite(pages, errors={"/et/sundmused/alfa": PublicFetchError("Allikat ei leitud (404).")})
    )
    tally = discover_public_events(mode=DiscoveryMode.FULL)

    assert tally.errors == 1
    assert tally.is_complete is False
    assert WARN_DETAIL_FAILED in tally.warnings
    assert PublicEventResource.objects.get().title == "Alfa"


# -- a partial run says it is partial ------------------------------------


def test_hitting_the_detail_budget_marks_the_run_incomplete(patch_fetch, db):
    slugs = [f"s{index}" for index in range(5)]
    pages = {
        "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
        "/et/sitemap-1.xml": sitemap(*(url_for(slug) for slug in slugs)),
    }
    for index, slug in enumerate(slugs):
        pages[f"/et/sundmused/{slug}"] = detail(name=f"Sündmus {slug}", start=past(index + 1))
    patch_fetch(XmlSite(pages))

    tally = discover_public_events(mode=DiscoveryMode.FULL, max_detail_pages=2)

    assert tally.created == 2
    assert tally.is_complete is False
    assert WARN_DETAIL_CAP in tally.warnings


def test_a_capped_backfill_is_continued_by_running_again(patch_fetch, db):
    """No cursor, no resume state: 'already known' is the database."""
    slugs = [f"s{index}" for index in range(5)]
    pages = {
        "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
        "/et/sitemap-1.xml": sitemap(*(url_for(slug) for slug in slugs)),
    }
    for index, slug in enumerate(slugs):
        pages[f"/et/sundmused/{slug}"] = detail(name=f"Sündmus {slug}", start=past(index + 1))
    patch_fetch(XmlSite(pages))

    first = discover_public_events(mode=DiscoveryMode.INCREMENTAL, max_detail_pages=2)
    second = discover_public_events(mode=DiscoveryMode.INCREMENTAL, max_detail_pages=2)
    third = discover_public_events(mode=DiscoveryMode.INCREMENTAL, max_detail_pages=2)

    assert (first.created, second.created, third.created) == (2, 2, 1)
    assert first.is_complete is False
    assert third.is_complete is True
    assert PublicEventResource.objects.count() == 5


def test_an_unreadable_child_sitemap_does_not_lose_the_others(patch_fetch, db):
    from apps.core.public_http import PublicFetchError

    patch_fetch(
        XmlSite(
            {
                "/et/sitemap.xml": sitemap_index(
                    f"{ROOT}/et/sitemap-1.xml", f"{ROOT}/et/sitemap-2.xml"
                ),
                "/et/sitemap-2.xml": sitemap(url_for("alfa")),
                "/et/sundmused/alfa": detail(name="Alfa", start=past(5)),
            },
            errors={"/et/sitemap-1.xml": PublicFetchError("Ajalõpp.")},
        )
    )

    tally = discover_public_events(mode=DiscoveryMode.FULL)

    assert tally.created == 1
    assert tally.is_complete is False
    assert WARN_SITEMAP_UNREADABLE in tally.warnings


def test_a_dry_run_writes_nothing_but_still_counts(patch_fetch, db):
    patch_fetch(
        XmlSite(
            {
                "/et/sitemap.xml": sitemap_index(f"{ROOT}/et/sitemap-1.xml"),
                "/et/sitemap-1.xml": sitemap(url_for("alfa"), url_for("beeta")),
                "/et/sundmused/alfa": detail(name="Alfa", start=past(5)),
                "/et/sundmused/beeta": detail(name="Beeta", start=past(6)),
            }
        )
    )

    tally = discover_public_events(mode=DiscoveryMode.FULL, dry_run=True)

    assert tally.created == 2
    assert not PublicEventResource.objects.exists()
    assert not PublicEventDiscoverySnapshot.objects.exists()
