"""Reading a publication date off a Koda.ee article page.

This exists because the opposite was asserted in code and believed for months.
`apps/news/discovery.py` said an article page "does not reliably carry a
publication date" and catalogued every recovered article undated on that basis —
3 602 of 3 614 rows — which left the news archive's period filters with twelve
articles to work on. The pages carry `datePublished` in schema.org JSON-LD, back
to at least 2017, on forty of forty sampled.

Nothing here contacts Koda.ee: every page is a fixture, and the fetch is
replaced.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.news.catalogue import undated_paths
from apps.news.discovery import backfill_news_dates, parse_published_at
from apps.news.public_models import NewsResource, TitleOrigin

pytestmark = pytest.mark.django_db


def page(*, published: str | None = "2017-02-20T15:47:09+02:00", kind: str = "NewsArticle") -> str:
    """An article page shaped like the real one."""
    date_field = f'"datePublished": "{published}",' if published is not None else ""
    return f"""
    <html><head>
      <script type="application/ld+json">
      {{"@context":"https://schema.org","@graph":[
        {{"@type":"WebSite","name":"Koda"}},
        {{"@type":"{kind}", {date_field} "headline":"Lugu"}}
      ]}}
      </script>
    </head><body>Lugu</body></html>
    """


class FakePage:
    def __init__(self, html):
        self._html = html

    def text(self, **kwargs):
        return self._html


def catalogued(path: str, *, published=None) -> NewsResource:
    return NewsResource.objects.create(
        canonical_url=f"https://www.koda.ee{path}",
        path=path,
        title="Kataloogitud lugu",
        published_at=published,
        title_origin=TitleOrigin.PAGE,
        last_seen_at=timezone.now(),
    )


# -- the parser --------------------------------------------------------------


def test_a_real_article_page_yields_its_publication_moment():
    moment = parse_published_at(page())

    assert moment is not None
    assert moment.year == 2017
    assert moment.month == 2
    assert moment.day == 20
    assert timezone.is_aware(moment)


def test_a_listing_page_is_not_dated_from_whatever_it_lists():
    """`/en/news` is a real path under the news prefix with no `datePublished`.
    Dating it from the first article it happens to show would put a wrong date
    on a page that is not an article at all."""
    listing = """
    <html><head><script type="application/ld+json">
      {"@context":"https://schema.org","@type":"CollectionPage","name":"Uudised"}
    </script></head><body></body></html>
    """

    assert parse_published_at(listing) is None


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>ei mingit struktuuri</body></html>",
        '<html><head><script type="application/ld+json">{ broken json</script></head></html>',
    ],
)
def test_an_unreadable_page_yields_no_date_rather_than_an_error(html):
    assert parse_published_at(html) is None


def test_a_date_without_a_time_is_accepted_at_midnight():
    """ "Published on the 4th" is a true and useful fact, and the archive's
    windows are day-grained anyway."""
    moment = parse_published_at(page(published="2019-12-04"))

    assert moment is not None
    assert (moment.year, moment.month, moment.day) == (2019, 12, 4)
    assert timezone.is_aware(moment)


def test_a_date_far_in_the_future_is_refused():
    """The same guard the feed collector applies: a mis-dated article must not
    be able to pin itself to the top of the archive forever."""
    ahead = (timezone.now() + dt.timedelta(days=400)).isoformat()

    assert parse_published_at(page(published=ahead)) is None


def test_a_plain_article_type_is_read_too():
    assert parse_published_at(page(kind="Article")) is not None


# -- which rows the backfill selects -----------------------------------------


def test_only_undated_rows_are_selected():
    catalogued("/et/uudised/kuupäevata")
    catalogued("/et/uudised/dateeritud", published=timezone.now() - dt.timedelta(days=5))

    assert undated_paths(limit=10) == ("/et/uudised/kuupäevata",)


def test_the_backfill_dates_a_catalogued_article(monkeypatch):
    catalogued("/et/uudised/lugu")
    monkeypatch.setattr("apps.news.discovery.fetch", lambda *a, **k: FakePage(page()))

    tally = backfill_news_dates(limit=5, sleep=lambda: None)

    assert tally.named == 1
    resource = NewsResource.objects.get()
    assert resource.published_at is not None
    assert resource.published_at.year == 2017


def test_the_backfill_leaves_the_title_alone(monkeypatch):
    """It writes a date and nothing else. Re-deciding the title here would let
    one pass quietly undo the other."""
    catalogued("/et/uudised/lugu")
    monkeypatch.setattr("apps.news.discovery.fetch", lambda *a, **k: FakePage(page()))

    backfill_news_dates(limit=5, sleep=lambda: None)

    assert NewsResource.objects.get().title == "Kataloogitud lugu"


def test_a_date_the_feed_published_is_never_overwritten(monkeypatch):
    """The Chamber wrote the feed entry and knows when it published. A page
    read is a reading of the public site and does not outrank it."""
    from apps.news.catalogue import record_feed_items

    feed_moment = timezone.make_aware(dt.datetime(2024, 3, 5, 9, 0))

    class Entry:
        canonical_url = "https://www.koda.ee/et/uudised/lugu"
        title = "Voo pealkiri"
        published_at = feed_moment

    record_feed_items([Entry()])
    monkeypatch.setattr("apps.news.discovery.fetch", lambda *a, **k: FakePage(page()))

    tally = backfill_news_dates(limit=5, sleep=lambda: None)

    assert tally.considered == 0, "a dated row must not even be selected"
    assert NewsResource.objects.get().published_at == feed_moment


def test_a_page_that_states_no_date_stays_undated(monkeypatch):
    catalogued("/et/uudised/lugu")
    monkeypatch.setattr("apps.news.discovery.fetch", lambda *a, **k: FakePage(page(published=None)))

    tally = backfill_news_dates(limit=5, sleep=lambda: None)

    assert tally.unnamed == 1
    assert tally.named == 0
    assert NewsResource.objects.get().published_at is None


def test_a_fetch_failure_leaves_the_row_as_it_was(monkeypatch):
    """One bad fetch must never blank a date or stop the run."""
    from apps.core.public_http import PublicFetchError

    catalogued("/et/uudised/kadunud")
    catalogued("/et/uudised/olemas")

    def fetch(url, *args, **kwargs):
        if "kadunud" in url:
            raise PublicFetchError("404")
        return FakePage(page())

    monkeypatch.setattr("apps.news.discovery.fetch", fetch)

    tally = backfill_news_dates(limit=5, sleep=lambda: None)

    assert tally.failed == 1
    assert tally.named == 1
    assert NewsResource.objects.get(path="/et/uudised/kadunud").published_at is None


def test_a_dry_run_reads_without_writing(monkeypatch):
    catalogued("/et/uudised/lugu")
    monkeypatch.setattr("apps.news.discovery.fetch", lambda *a, **k: FakePage(page()))

    tally = backfill_news_dates(limit=5, dry_run=True, sleep=lambda: None)

    assert tally.named == 1
    assert NewsResource.objects.get().published_at is None


def test_the_backfill_is_resumable(monkeypatch):
    """What is left is a query, not a cursor, so a second run continues."""
    for index in range(5):
        catalogued(f"/et/uudised/lugu-{index}")
    monkeypatch.setattr("apps.news.discovery.fetch", lambda *a, **k: FakePage(page()))

    backfill_news_dates(limit=2, sleep=lambda: None)
    assert NewsResource.objects.filter(published_at__isnull=True).count() == 3

    backfill_news_dates(limit=10, sleep=lambda: None)
    assert NewsResource.objects.filter(published_at__isnull=True).count() == 0


# -- what it means for the archive -------------------------------------------


def test_dated_articles_become_reachable_by_period(monkeypatch):
    """The point of the whole exercise: a catalogued article with a date can be
    found by a publication window, and an undated one cannot."""
    from apps.news.archive import build_news_archive

    catalogued("/et/uudised/hiljutine")
    recent = timezone.now() - dt.timedelta(days=3)
    monkeypatch.setattr(
        "apps.news.discovery.fetch",
        lambda *a, **k: FakePage(page(published=recent.isoformat())),
    )

    assert build_news_archive(period_key="30").total == 0

    backfill_news_dates(limit=5, sleep=lambda: None)

    assert build_news_archive(period_key="30").total == 1


def test_the_title_pass_now_dates_what_it_catalogues(monkeypatch):
    """A newly discovered article should not need a second pass to be dated."""
    from apps.news.discovery import discover_news_titles

    monkeypatch.setattr(
        "apps.news.discovery.unnamed_news_paths", lambda **kwargs: ("/et/uudised/uus",)
    )
    monkeypatch.setattr(
        "apps.news.discovery.fetch",
        lambda *a, **k: FakePage("<title>Uus lugu</title>" + page()),
    )

    discover_news_titles(limit=5, sleep=lambda: None)

    resource = NewsResource.objects.get()
    assert resource.title == "Uus lugu"
    assert resource.published_at is not None
