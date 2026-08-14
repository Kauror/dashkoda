"""The news archive: what it can reach, how it orders it, and what it refuses.

The page was built on the current feed snapshot, which is ten rolling items —
so "what did we publish this year" was a question it could not answer however
the filter was written. It reads the durable catalogue now, and the first test
below is the one that proves it: thirty catalogued articles behind a feed that
still shows ten.

Nothing here contacts Google or Koda.ee, and nothing renders a page from a live
source: every figure comes from stored `Ga4PageDaily` rows.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.news.archive import PER_PAGE, build_news_archive
from apps.news.periods import SORT_NEWEST, SORT_VIEWS, resolve_period
from apps.news.public_models import NewsResource, TitleOrigin

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 8, 11)


def article(slug: str, *, days_ago: int | None = None, title: str = "", published=None):
    """One catalogued article, dated relative to `TODAY` unless told otherwise."""
    if published is None and days_ago is not None:
        published = timezone.make_aware(
            dt.datetime.combine(TODAY - dt.timedelta(days=days_ago), dt.time(9, 0))
        )
    return NewsResource.objects.create(
        canonical_url=f"https://www.koda.ee/et/uudised/{slug}",
        path=f"/et/uudised/{slug}",
        title=title or f"Uudis {slug}",
        published_at=published,
        title_origin=TitleOrigin.FEED if published else TitleOrigin.PAGE,
        last_seen_at=timezone.now(),
    )


@pytest.fixture
def measured():
    """Give catalogued paths measured GA4 page views on one published day."""
    from apps.sources.services import build_import_run, register_external_reference
    from apps.visibility.bootstrap import ensure_ga4_source
    from apps.visibility.models import Ga4DailySnapshot, Ga4PageDaily

    state = {"n": 0}

    def _measure(views_by_path: dict[str, int], *, report_date=dt.date(2026, 6, 1)):
        source = ensure_ga4_source()
        state["n"] += 1
        artifact = register_external_reference(
            source=source,
            external_reference=f"synthetic:news-archive:{state['n']}",
            original_name="synthetic.json",
            mime_type="application/json",
            sha256=f"{state['n']:064d}",
            size_bytes=10,
        )
        run = build_import_run(
            artifact=artifact,
            importer_name="synthetic_news_archive",
            schema_version="2.0",
            dry_run=False,
        )
        snapshot = Ga4DailySnapshot.objects.create(
            source=source,
            artifact=artifact,
            import_run=run,
            report_date=report_date,
            observed_at=timezone.now(),
            checksum=f"{state['n']:064d}",
            is_current_for_date=True,
            has_page_detail=True,
            sessions=1,
            page_views=sum(views_by_path.values()),
        )
        for path, views in views_by_path.items():
            Ga4PageDaily.objects.create(
                snapshot=snapshot, report_date=report_date, path=path, page_views=views
            )
        return snapshot

    return _measure


def paths(archive):
    return [row.url.replace("https://www.koda.ee", "") for row in archive.rows]


def build(**kwargs):
    kwargs.setdefault("today", TODAY)
    return build_news_archive(**kwargs)


# -- the regression this page exists for -------------------------------------


def test_the_archive_reaches_articles_the_feed_no_longer_lists(viewer_client, measured):
    """The test the whole redesign is for.

    Thirty catalogued articles, ten of them in the current feed snapshot. The
    old page could show ten rows because ten rows were all it had; this one has
    to reach all thirty, across pages, without the feed growing.
    """
    from apps.news.bootstrap import ensure_news_source
    from apps.news.models import NewsItem, NewsSnapshot
    from apps.news.selectors import get_current_news_snapshot
    from apps.sources.services import build_import_run, register_external_reference

    for index in range(30):
        article(f"lugu-{index:02d}", days_ago=index)

    # A feed snapshot holding only the ten most recent, exactly as production.
    source = ensure_news_source()
    artifact = register_external_reference(
        source=source,
        external_reference="synthetic:news-feed",
        original_name="feed.xml",
        mime_type="application/rss+xml",
        sha256="f" * 64,
        size_bytes=10,
    )
    run = build_import_run(
        artifact=artifact, importer_name="synthetic_feed", schema_version="1.0", dry_run=False
    )
    snapshot = NewsSnapshot.objects.create(
        source=source,
        artifact=artifact,
        import_run=run,
        observed_at=timezone.now(),
        is_current=True,
        item_count=10,
    )
    for index in range(10):
        NewsItem.objects.create(
            snapshot=snapshot,
            guid=f"seed-{index}",
            title=f"Uudis lugu-{index:02d}",
            canonical_url=f"https://www.koda.ee/et/uudised/lugu-{index:02d}",
            published_at=timezone.now() - dt.timedelta(days=index),
            source_order=index,
        )

    assert get_current_news_snapshot().item_count == 10

    seen = set()
    for page in (1, 2):
        archive = build(period_key="koik", page=page)
        seen.update(paths(archive))
    assert len(seen) == 30, "the archive is still bounded by the feed"

    # And through the real view, so the reachability is not only a selector fact.
    # The archive is the `arhiiv` focus now — the page no longer opens on it —
    # but it is the same builder answering the same question.
    response = viewer_client.get(reverse("news"), {"periood": "koik", "fookus": "arhiiv"})
    assert response.status_code == 200
    assert response.context["archive"].total == 30


# -- periods ------------------------------------------------------------------


def test_each_preset_selects_its_own_publication_window():
    article("hiljutine", days_ago=10)
    article("kuu-tagune", days_ago=45)
    article("mullune", days_ago=200)
    article("ammune", days_ago=400)

    assert paths(build(period_key="30")) == ["/et/uudised/hiljutine"]
    assert paths(build(period_key="90")) == ["/et/uudised/hiljutine", "/et/uudised/kuu-tagune"]
    assert paths(build(period_key="1a")) == [
        "/et/uudised/hiljutine",
        "/et/uudised/kuu-tagune",
        "/et/uudised/mullune",
    ]
    assert len(build(period_key="koik").rows) == 4


def test_the_default_is_thirty_days_newest_first(viewer_client):
    article("hiljutine", days_ago=10)
    article("vana", days_ago=200)

    response = viewer_client.get(reverse("news"), {"fookus": "arhiiv"})
    archive = response.context["archive"]

    assert archive.period.key == "30"
    assert archive.sort == SORT_NEWEST


def test_the_default_does_not_depend_on_whether_analytics_exist(viewer_client, measured):
    """A page whose default silently changed with the data would be a page
    nobody could describe."""
    article("mõõdetud", days_ago=5)
    measured({"/et/uudised/mõõdetud": 500})

    archive = viewer_client.get(reverse("news"), {"fookus": "arhiiv"}).context["archive"]

    assert archive.period.key == "30"
    assert archive.sort == SORT_NEWEST


def test_a_window_includes_everything_published_on_its_last_day():
    """The midnight bug this is written against.

    `published_at__lte=<date>` compares a moment with midnight and drops
    everything published after it, so an article published at four in the
    afternoon on the window's final day disappears.
    """
    late = timezone.make_aware(dt.datetime.combine(TODAY, dt.time(23, 59)))
    early = timezone.make_aware(dt.datetime.combine(TODAY - dt.timedelta(days=29), dt.time(0, 1)))
    article("täna-hilja", published=late)
    article("piiril-vara", published=early)
    # One second before the window opens.
    article(
        "väljas",
        published=timezone.make_aware(
            dt.datetime.combine(TODAY - dt.timedelta(days=30), dt.time(23, 59, 59))
        ),
    )

    found = set(paths(build(period_key="30")))

    assert "/et/uudised/täna-hilja" in found
    assert "/et/uudised/piiril-vara" in found
    assert "/et/uudised/väljas" not in found


def test_an_undated_article_belongs_to_no_window_but_is_in_everything():
    """Most of the real catalogue is undated: public-page discovery refuses to
    invent a publication date. They cannot honestly answer "published in the
    last 30 days" either way, so only `Kõik` claims them."""
    article("dateeritud", days_ago=3)
    article("kuupäevata")

    assert paths(build(period_key="30")) == ["/et/uudised/dateeritud"]
    assert len(build(period_key="koik").rows) == 2
    # And it sorts to the end rather than to the top.
    assert paths(build(period_key="koik"))[-1] == "/et/uudised/kuupäevata"


# -- custom range -------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-03-01", "2026-03-31"),
        ("2026-03-10", "2026-03-10"),
        # Reversed: the reader asked for that span, not for an error.
        ("2026-03-31", "2026-03-01"),
    ],
)
def test_a_custom_range_selects_exactly_its_dates(start, end):
    article("marts", published=timezone.make_aware(dt.datetime(2026, 3, 10, 12, 0)))
    article("aprill", published=timezone.make_aware(dt.datetime(2026, 4, 10, 12, 0)))

    archive = build(period_key="kohandatud", date_from=start, date_to=end)

    if start == end == "2026-03-10":
        assert paths(archive) == ["/et/uudised/marts"]
    else:
        assert "/et/uudised/marts" in paths(archive)
    assert "/et/uudised/aprill" not in paths(archive)


@pytest.mark.parametrize(
    ("query"),
    [
        {"periood": "kohandatud", "alates": "banana", "kuni": "2026-03-01"},
        {"periood": "kohandatud", "alates": "2026-03-01", "kuni": ""},
        {"periood": "kohandatud", "kuni": "2026-03-01"},
        {"periood": "kohandatud"},
        {"periood": "kohandatud", "alates": "2026-03-01", "kuni": "2099-01-01"},
        {"periood": "kohandatud", "alates": "2026-13-45", "kuni": "2026-99-99"},
        {"periood": "puudub", "lk": "-4", "sort": "väljamõeldud"},
        {"alates": "2026-03-01"},
    ],
)
def test_a_malformed_query_renders_a_page_rather_than_an_error(viewer_client, query):
    article("olemas", days_ago=5)

    response = viewer_client.get(reverse("news"), query)

    assert response.status_code == 200


def test_the_fields_show_the_window_that_was_actually_applied():
    """A control that disagrees with the list under it is worse than no control."""
    archive = build(period_key="kohandatud", date_from="2026-03-31", date_to="2026-03-01")

    assert archive.period.start == dt.date(2026, 3, 1)
    assert archive.period.end == dt.date(2026, 3, 31)


# -- sorting ------------------------------------------------------------------


def test_most_viewed_ranks_the_whole_population_not_the_page(measured):
    """The ordering must happen before the slice.

    Thirty-five articles, and the most-read one is the oldest — so it is on the
    second page under `Uusimad` and must be the first row under `Enim vaadatud`.
    Sorting a fetched page would leave it where it was.
    """
    for index in range(35):
        article(f"lugu-{index:02d}", days_ago=index)
    measured({"/et/uudised/lugu-34": 9000, "/et/uudised/lugu-00": 5})

    newest = build(period_key="koik", sort=SORT_NEWEST)
    assert "/et/uudised/lugu-34" not in paths(newest)

    ranked = build(period_key="koik", sort=SORT_VIEWS)
    assert paths(ranked)[0] == "/et/uudised/lugu-34"
    assert paths(ranked)[1] == "/et/uudised/lugu-00"


def test_unmeasured_articles_follow_measured_ones_and_are_never_zero(measured):
    article("mõõdetud", days_ago=1)
    article("mõõtmata", days_ago=2)
    measured({"/et/uudised/mõõdetud": 7})

    rows = build(period_key="koik", sort=SORT_VIEWS).rows

    assert rows[0].views == 7
    assert rows[1].views is None, "an unmeasured article must not be ranked as a zero"


def test_a_measured_zero_outranks_an_unmeasured_article(measured):
    """They are different facts. A reported zero is a reading."""
    article("null", days_ago=1)
    article("mõõtmata", days_ago=2)
    measured({"/et/uudised/null": 0})

    rows = build(period_key="koik", sort=SORT_VIEWS).rows

    assert rows[0].views == 0
    assert rows[1].views is None


def test_ties_are_ordered_deterministically(measured):
    for index in range(4):
        article(f"sama-{index}", days_ago=index)
    measured({f"/et/uudised/sama-{index}": 100 for index in range(4)})

    first = paths(build(period_key="koik", sort=SORT_VIEWS))
    second = paths(build(period_key="koik", sort=SORT_VIEWS))

    assert first == second
    assert first == sorted(first) or first == [
        "/et/uudised/sama-0",
        "/et/uudised/sama-1",
        "/et/uudised/sama-2",
        "/et/uudised/sama-3",
    ]


# -- analytics ----------------------------------------------------------------


def test_a_measured_article_shows_its_total(measured):
    article("loetud", days_ago=2)
    measured({"/et/uudised/loetud": 123})

    assert build(period_key="koik").rows[0].views == 123


def test_a_superseded_revision_is_not_counted_twice(measured):
    """Two revisions of one reporting day are provenance, not arithmetic."""
    from apps.visibility.models import Ga4DailySnapshot

    article("uudis", days_ago=2)
    first = measured({"/et/uudised/uudis": 100})
    Ga4DailySnapshot.objects.filter(pk=first.pk).update(is_current_for_date=False)
    measured({"/et/uudised/uudis": 140})

    assert build(period_key="koik").rows[0].views == 140


def test_the_page_renders_without_contacting_anything(viewer_client, monkeypatch):
    """No Google call, no Koda.ee fetch, no socket at all."""
    import socket

    article("uudis", days_ago=1)

    def refuse(*args, **kwargs):
        raise AssertionError("the news page must not open a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    assert viewer_client.get(reverse("news"), {"periood": "koik"}).status_code == 200


def test_the_view_total_matches_the_shared_selector(measured):
    """The annotation and the dictionary are two spellings of one definition.

    If they ever disagree, two surfaces are printing different totals for the
    same article and nothing on either page would reveal it.
    """
    from apps.visibility.ga4_selectors import get_page_view_totals

    article("uudis", days_ago=2)
    measured({"/et/uudised/uudis": 456})

    shared = get_page_view_totals(["/et/uudised/uudis"])["/et/uudised/uudis"].total

    assert build(period_key="koik").rows[0].views == shared


# -- pagination ---------------------------------------------------------------


def test_pagination_walks_the_whole_population_without_repeating_a_row():
    for index in range(PER_PAGE * 2 + 5):
        article(f"lugu-{index:03d}", days_ago=index)

    first = build(period_key="koik", page=1)
    assert first.total_pages == 3
    assert len(first.rows) == PER_PAGE

    seen = []
    for page in range(1, first.total_pages + 1):
        seen.extend(paths(build(period_key="koik", page=page)))

    assert len(seen) == len(set(seen)) == PER_PAGE * 2 + 5


def test_every_page_link_carries_the_whole_query():
    for index in range(PER_PAGE + 1):
        article(f"lugu-{index:03d}", days_ago=index)

    archive = build(period_key="koik", sort=SORT_VIEWS, search="lugu", page=1)
    link = archive.next_query

    assert "periood=koik" in link
    assert "sort=vaadatud" in link
    assert "otsing=lugu" in link
    assert "lk=2" in link


def test_a_custom_range_survives_paging():
    for index in range(PER_PAGE + 1):
        article(f"lugu-{index:03d}", published=timezone.make_aware(dt.datetime(2026, 3, 10, 12, 0)))

    archive = build(period_key="kohandatud", date_from="2026-03-01", date_to="2026-03-31")

    assert "alates=2026-03-01" in archive.next_query
    assert "kuni=2026-03-31" in archive.next_query


def test_a_page_beyond_the_end_falls_back_to_the_last_one():
    article("ainus", days_ago=1)

    archive = build(period_key="koik", page=99)

    assert archive.page_number == 1
    assert archive.has_rows


# -- search -------------------------------------------------------------------


def test_search_matches_the_title_and_the_path():
    article("liikmemaks", title="Liikmemaks tõuseb", days_ago=1)
    article("muu", title="Midagi muud", days_ago=2)

    assert paths(build(period_key="koik", search="tõuseb")) == ["/et/uudised/liikmemaks"]
    assert paths(build(period_key="koik", search="/et/uudised/liikmemaks")) == [
        "/et/uudised/liikmemaks"
    ]


def test_an_oversized_search_term_is_bounded(viewer_client):
    article("uudis", days_ago=1)

    response = viewer_client.get(
        reverse("news"), {"otsing": "x" * 5000, "periood": "koik", "fookus": "arhiiv"}
    )

    assert response.status_code == 200
    assert len(response.context["archive"].search) <= 120


# -- what the page says -------------------------------------------------------


def test_an_empty_window_is_not_reported_as_a_missing_source():
    """A working page answering the question it was asked is not a broken
    pipeline, and the two must not read the same."""
    article("vana", days_ago=300)

    archive = build(period_key="30")

    assert not archive.has_rows
    assert not archive.catalogue_is_empty
    assert "ei ole veel ühendatud" not in archive.empty_message


def test_an_empty_catalogue_says_the_source_is_not_connected():
    archive = build(period_key="koik")

    assert archive.catalogue_is_empty
    assert "ei ole veel ühendatud" in archive.empty_message


def test_the_page_does_not_claim_to_hold_the_whole_chamber_archive():
    article("uudis", days_ago=1)
    article("kuupäevata")

    caveat = build(period_key="koik").coverage_caveat

    assert "kõiki DashKodale teadaolevaid" in caveat
    assert "läbi aegade" not in caveat
    assert "1 uudisel ei ole teadaolevat avaldamiskuupäeva" in caveat


def test_the_coverage_note_is_stated_once_not_per_row(measured):
    article("uudis", days_ago=2)
    measured({"/et/uudised/uudis": 5})

    archive = build(period_key="koik")

    assert "Google Analyticsi mõõdetud lehevaatamised alates" in archive.coverage_note


def test_the_rendered_page_is_a_compact_list_without_summaries(viewer_client, measured):
    article("uudis", title="Pealkiri", days_ago=2)
    measured({"/et/uudised/uudis": 42})

    page = viewer_client.get(
        reverse("news"), {"periood": "koik", "fookus": "arhiiv"}
    ).content.decode()

    # The archive's own furniture.
    assert "Lehevaatamised" in page
    assert "Pealkiri" in page
    assert "42" in page
    # And none of what was removed.
    assert "Avaldatud viimase kuu jooksul" not in page
    assert "Uudiseid voos" not in page
    assert "Viimane edukas sünkroonimine" not in page
    assert "Muud kanalid" not in page
    assert "Meediakajastused" not in page

    # The unit belongs in the column heading, not on every row. Asserted over
    # the rows themselves rather than the whole document: `Andmete kohta` spells
    # the word once, deliberately, to say that a page view is not a reader.
    rows = page.partition('id="news-results"')[2].partition("</section>")[0]
    assert "lehevaatamist" not in rows, "the unit belongs in the column heading, not every row"


def test_the_resolved_period_round_trips_through_its_own_query():
    resolved = resolve_period("kohandatud", "2026-03-01", "2026-03-31", today=TODAY)

    assert "alates=2026-03-01" in resolved.query
    assert "kuni=2026-03-31" in resolved.query
