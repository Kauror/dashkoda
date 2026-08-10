"""Traffic counters beside news and event rows.

The counter beside an item is **total measured views over all GA4 coverage** —
a different question from the period ranking on Nähtavus, and the two must not
drift into each other.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.visibility.item_analytics import attach_page_views, event_url, news_url
from apps.visibility.models import Ga4DailySnapshot, Ga4PageDaily

pytestmark = pytest.mark.django_db

DAY = dt.date(2026, 8, 1)


@pytest.fixture
def measured():
    """Publish page views for a set of paths, on one reporting day."""
    from apps.sources.services import build_import_run, register_external_reference
    from apps.visibility.bootstrap import ensure_ga4_source

    source = ensure_ga4_source()
    artifact = register_external_reference(
        source=source,
        external_reference="synthetic:item-traffic",
        original_name="synthetic.json",
        mime_type="application/json",
        sha256="a1" * 32,
        size_bytes=10,
    )
    run = build_import_run(
        artifact=artifact,
        importer_name="synthetic_item_traffic",
        schema_version="2.0",
        dry_run=False,
    )

    def _measured(pages, *, report_date=DAY):
        snapshot = Ga4DailySnapshot.objects.create(
            source=source,
            artifact=artifact,
            import_run=run,
            report_date=report_date,
            observed_at=timezone.now(),
            checksum=f"{report_date.toordinal():064d}",
            is_current_for_date=True,
            has_page_detail=True,
            sessions=1,
        )
        for path, views in pages:
            Ga4PageDaily.objects.create(
                snapshot=snapshot, report_date=report_date, path=path, page_views=views
            )
        return snapshot

    return _measured


class Item:
    """Stands in for a news item or a linked event."""

    def __init__(self, url=""):
        self.canonical_url = url


class Linked:
    """Stands in for an event whose public link has been resolved."""

    def __init__(self, url=""):
        self.public_link = type("Link", (), {"url": url})() if url else None


# -- the counter ----------------------------------------------------------


def test_a_measured_item_carries_its_total(measured):
    measured((("/et/uudised/a", 40),))
    measured((("/et/uudised/a", 60),), report_date=DAY + dt.timedelta(days=1))

    (item,) = attach_page_views([Item("https://www.koda.ee/et/uudised/a")])

    assert item.page_views.total == 100
    assert item.page_views_label == "100 vaatamist"
    assert item.page_views_label_long == "100 lehevaatamist"


def test_an_unmeasured_item_carries_nothing_rather_than_a_zero(measured):
    """`0 vaatamist` and "nobody measured this" look alike on a screen and mean
    opposite things."""
    measured((("/et/uudised/a", 40),))

    (item,) = attach_page_views([Item("https://www.koda.ee/et/uudised/never")])

    assert item.page_views is None
    assert item.page_views_label == ""


def test_an_item_with_no_public_url_carries_nothing(measured):
    measured((("/et/uudised/a", 40),))

    (item,) = attach_page_views([Linked()], url_of=event_url)

    assert item.page_views is None


def test_one_query_serves_the_whole_list(measured, django_assert_num_queries):
    measured(tuple((f"/et/uudised/{n}", n + 1) for n in range(20)))
    items = [Item(f"https://www.koda.ee/et/uudised/{n}") for n in range(20)]

    # One coverage aggregate, one grouped total — the same two whatever the
    # list length.
    with django_assert_num_queries(2):
        attach_page_views(items)

    assert items[19].page_views.total == 20


# -- events share their public page ---------------------------------------


def test_two_events_on_one_public_page_each_show_that_page_s_traffic(measured):
    """The matcher deliberately lets several programme occurrences point at one
    public page. The traffic belongs to the **page**, so both occurrences show
    it whole — halving it would invent an attribution nobody measured."""
    measured((("/et/sundmused/sari", 2418),))
    url = "https://www.koda.ee/et/sundmused/sari"

    first, second = attach_page_views([Linked(url), Linked(url)], url_of=event_url)

    assert first.page_views.total == 2418
    assert second.page_views.total == 2418
    assert first.page_views.total + second.page_views.total != 2418


def test_an_event_uses_the_link_that_was_already_resolved(measured):
    """Never re-derived here: two answers to "which page is this event on" is
    how traffic lands on the wrong row."""
    measured((("/et/sundmused/workbook-page", 10), ("/et/sundmused/other", 999)))

    (item,) = attach_page_views(
        [Linked("https://www.koda.ee/et/sundmused/workbook-page")], url_of=event_url
    )

    assert item.page_views.total == 10


# -- accessors -------------------------------------------------------------


def test_the_url_accessor_reads_what_each_kind_of_row_holds():
    assert news_url(Item("https://www.koda.ee/et/uudised/a")) == "https://www.koda.ee/et/uudised/a"
    assert event_url(Linked("https://www.koda.ee/et/sundmused/a")).endswith("/sundmused/a")
    assert event_url(Linked()) == ""
    assert news_url(Item()) == ""
