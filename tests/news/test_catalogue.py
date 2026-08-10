"""The durable news catalogue: what it remembers, and what it refuses to invent.

The feed is ten items deep and its snapshots are pruned weekly. Without this,
a three-year traffic ranking could count an article's views exactly and then
label the row with its URL.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.news.catalogue import record_feed_items, titles_for, uncatalogued_paths
from apps.news.discovery import discover_news_titles, parse_title
from apps.news.public_models import NewsResource, TitleOrigin

pytestmark = pytest.mark.django_db

PUBLISHED = timezone.make_aware(dt.datetime(2024, 3, 5, 9, 0))


class Entry:
    """Stands in for a `NewsItem` the sync just published."""

    def __init__(self, url, title, published_at=PUBLISHED):
        self.canonical_url = url
        self.title = title
        self.published_at = published_at


# -- what the sync records -------------------------------------------------


def test_an_article_is_catalogued_the_first_time_it_is_seen():
    added, refreshed = record_feed_items(
        [Entry("https://www.koda.ee/et/uudised/esimene", "Esimene lugu")]
    )

    assert (added, refreshed) == (1, 0)
    resource = NewsResource.objects.get()
    assert resource.path == "/et/uudised/esimene"
    assert resource.title == "Esimene lugu"
    assert resource.title_origin == TitleOrigin.FEED


def test_an_article_seen_again_is_refreshed_rather_than_duplicated():
    record_feed_items([Entry("https://www.koda.ee/et/uudised/lugu", "Vana pealkiri")])
    added, refreshed = record_feed_items(
        [Entry("https://www.koda.ee/et/uudised/lugu", "Parandatud pealkiri")]
    )

    assert (added, refreshed) == (0, 1)
    assert NewsResource.objects.count() == 1
    assert NewsResource.objects.get().title == "Parandatud pealkiri"


def test_the_catalogue_outlives_the_snapshot_that_produced_it():
    """The whole point. A snapshot is pruned after a week; the article is not
    forgotten with it."""
    record_feed_items([Entry("https://www.koda.ee/et/uudised/vana", "Vana lugu")])

    from apps.news.models import NewsSnapshot

    NewsSnapshot.objects.all().delete()

    assert titles_for(["/et/uudised/vana"])["/et/uudised/vana"][0] == "Vana lugu"


def test_tracking_parameters_do_not_create_a_second_row():
    record_feed_items([Entry("https://www.koda.ee/et/uudised/lugu?utm_source=x", "Lugu")])
    record_feed_items([Entry("https://koda.ee/et/uudised/lugu/", "Lugu")])

    assert NewsResource.objects.count() == 1


def test_an_item_with_no_usable_url_is_not_catalogued():
    added, _ = record_feed_items([Entry("", "Pealkirjata viide")])

    assert added == 0
    assert NewsResource.objects.count() == 0


# -- reading it ------------------------------------------------------------


def test_titles_come_back_for_many_paths_in_one_query(django_assert_num_queries):
    record_feed_items(
        [Entry(f"https://www.koda.ee/et/uudised/{n}", f"Lugu {n}") for n in range(15)]
    )

    with django_assert_num_queries(1):
        found = titles_for([f"/et/uudised/{n}" for n in range(15)])

    assert len(found) == 15


def test_an_unknown_path_is_absent_rather_than_named():
    record_feed_items([Entry("https://www.koda.ee/et/uudised/a", "A")])

    assert "/et/uudised/tundmatu" not in titles_for(["/et/uudised/a", "/et/uudised/tundmatu"])


def test_uncatalogued_paths_keeps_the_order_it_was_given():
    record_feed_items([Entry("https://www.koda.ee/et/uudised/known", "Known")])

    missing = uncatalogued_paths(["/et/uudised/b", "/et/uudised/known", "/et/uudised/a"])

    assert missing == ("/et/uudised/b", "/et/uudised/a")


# -- reading titles off the public site ------------------------------------


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (
            '<meta property="og:title" content="Riigikogu võttis vastu"><title>x</title>',
            "Riigikogu võttis vastu",
        ),
        (
            "<title>Koda tegi ettepaneku | Eesti Kaubandus-Tööstuskoda</title>",
            "Koda tegi ettepaneku",
        ),
        ("<title>Eksport &amp; import - Eesti Kaubandus-Tööstuskoda</title>", "Eksport & import"),
        ("<html><body>ei ühtegi pealkirja</body></html>", ""),
    ],
)
def test_a_page_yields_its_own_name_or_nothing(html, expected):
    assert parse_title(html) == expected


class FakePage:
    def __init__(self, html):
        self._html = html

    def text(self, **kwargs):
        return self._html


def test_a_page_read_never_overwrites_what_the_feed_said(monkeypatch):
    """The Chamber wrote the feed title and knows the publication date. A page
    read is a reading of the public site and must not replace either."""
    record_feed_items([Entry("https://www.koda.ee/et/uudised/lugu", "Voo pealkiri")])
    monkeypatch.setattr(
        "apps.news.discovery.unnamed_news_paths", lambda **kwargs: ("/et/uudised/lugu",)
    )
    monkeypatch.setattr(
        "apps.news.discovery.fetch",
        lambda *a, **k: FakePage("<title>Lehe pealkiri</title>"),
    )

    discover_news_titles(limit=5, sleep=lambda: None)

    resource = NewsResource.objects.get()
    assert resource.title == "Voo pealkiri"
    assert resource.published_at == PUBLISHED


def test_a_page_with_no_title_is_left_uncatalogued(monkeypatch):
    """Better an honest path than a row named after nothing."""
    monkeypatch.setattr(
        "apps.news.discovery.unnamed_news_paths", lambda **kwargs: ("/et/uudised/tyhi",)
    )
    monkeypatch.setattr(
        "apps.news.discovery.fetch", lambda *a, **k: FakePage("<html><body>x</body></html>")
    )

    tally = discover_news_titles(limit=5, sleep=lambda: None)

    assert tally.unnamed == 1
    assert NewsResource.objects.count() == 0


def test_a_fetch_failure_does_not_stop_the_run(monkeypatch):
    from apps.core.public_http import PublicFetchError

    paths = ("/et/uudised/kadunud", "/et/uudised/olemas")
    monkeypatch.setattr("apps.news.discovery.unnamed_news_paths", lambda **kwargs: paths)

    def flaky(url, **kwargs):
        if "kadunud" in url:
            raise PublicFetchError("gone")
        return FakePage("<title>Olemas lugu</title>")

    monkeypatch.setattr("apps.news.discovery.fetch", flaky)

    tally = discover_news_titles(limit=5, sleep=lambda: None)

    assert tally.failed == 1
    assert tally.named == 1
    assert NewsResource.objects.get().title == "Olemas lugu"


def test_a_dry_run_reads_without_cataloguing(monkeypatch):
    monkeypatch.setattr(
        "apps.news.discovery.unnamed_news_paths", lambda **kwargs: ("/et/uudised/a",)
    )
    monkeypatch.setattr("apps.news.discovery.fetch", lambda *a, **k: FakePage("<title>A</title>"))

    tally = discover_news_titles(limit=5, dry_run=True, sleep=lambda: None)

    assert tally.named == 1
    assert NewsResource.objects.count() == 0
