"""What automatic linking must leave alone.

The enrichment now reaches the interface, so the boundary worth guarding moved:
it is no longer "no link appears" but "nothing *else* changed". A catalogue that
quietly became a fifth freshness source, a workbook sync that started depending
on Koda.ee, or an overview that broke because the legal card changed shape would
all be regressions a test about links would happily pass.
"""

from __future__ import annotations

import pytest
from django.template.loader import render_to_string

from apps.core.public_http import PublicFetchError
from apps.dashboard import freshness
from apps.legal_work.current_topic_match_sync import run_current_topic_matching
from apps.legal_work.models import (
    CurrentTopicSnapshot,
    LegalCurrentTopicMatchSnapshot,
    LegalWorkFeedState,
    LegalWorkItem,
    LegalWorkSnapshot,
    SyncResult,
)

from .current_topic_factory import DETAIL_PREFIX, LISTING_PATH, FakeSite, card, detail, listing

pytestmark = pytest.mark.django_db


def catalogue(*slugs: str) -> FakeSite:
    pages = {LISTING_PATH: listing(*(card(slug, f"Teema {slug}") for slug in slugs))}
    for slug in slugs:
        pages[f"{DETAIL_PREFIX}{slug}"] = detail(title=f"Teema {slug}")
    return FakeSite(pages)


@pytest.fixture
def published(imported_snapshot, publish_current_topics):
    publish_current_topics(catalogue("alpha", "beeta"))
    run_current_topic_matching()
    return imported_snapshot


# -- the shared component's own contract -----------------------------------


def test_the_component_renders_plain_text_without_an_address():
    class Item:
        topic = "Sünteetiline teema"
        public_url = ""

    rendered = render_to_string("dashboard/components/legal_topic.html", {"item": Item()})

    assert "<a" not in rendered
    assert "Sünteetiline teema" in rendered


def test_the_component_renders_a_link_when_given_one():
    class Item:
        topic = "Sünteetiline teema"
        public_url = "https://www.koda.ee/et/meie-moju/hetkel-kasil/alpha"

    rendered = render_to_string("dashboard/components/legal_topic.html", {"item": Item()})

    assert 'href="https://www.koda.ee/et/meie-moju/hetkel-kasil/alpha"' in rendered
    assert 'rel="noopener noreferrer"' in rendered
    assert "(avaneb uuel vahelehel)" in rendered


def test_the_component_keeps_its_default_layout_classes():
    """The card lists rely on the flex classes; the tables opt out explicitly."""

    class Item:
        topic = "Sünteetiline teema"
        public_url = ""

    rendered = render_to_string("dashboard/components/legal_topic.html", {"item": Item()})

    assert "min-w-0 flex-1 truncate text-sm" in rendered


def test_the_component_accepts_a_caller_supplied_class():
    class Item:
        topic = "Sünteetiline teema"
        public_url = ""

    rendered = render_to_string(
        "dashboard/components/legal_topic.html", {"item": Item(), "class_name": "text-sm"}
    )

    assert "truncate" not in rendered
    assert 'class="text-sm text-text"' in rendered


def test_the_component_decides_nothing_about_matching():
    """It renders what it is handed; the decision lives in `topic_links`."""
    source = (
        __import__("pathlib")
        .Path("apps/dashboard/templates/dashboard/components/legal_topic.html")
        .read_text(encoding="utf-8")
    )
    body = source.split("{% endcomment %}", 1)[1]

    for word in ("matched", "decision", "score", "snapshot", "LegalCurrentTopic"):
        assert word not in body


# -- global freshness -------------------------------------------------------


def test_the_freshness_denominator_is_still_four(published):
    assert freshness.current_freshness().total_sources == 4


def test_a_failed_catalogue_collection_does_not_make_the_dashboard_stale(
    imported_snapshot, publish_current_topics
):
    before = freshness.current_freshness()

    publish_current_topics(
        FakeSite({}, errors={LISTING_PATH: PublicFetchError("Allikas vastas koodiga 503.")})
    )
    after = freshness.current_freshness()

    assert after.total_sources == before.total_sources
    assert after.stale_sources == before.stale_sources
    assert after.connected_sources == before.connected_sources


# -- independence from the workbook feed -----------------------------------


def test_a_koda_failure_leaves_the_workbook_feed_untouched(
    imported_snapshot, publish_current_topics
):
    publish_current_topics(
        FakeSite({}, errors={LISTING_PATH: PublicFetchError("Allikas vastas koodiga 503.")})
    )

    assert LegalWorkSnapshot.objects.get(is_current=True) == imported_snapshot
    assert not LegalWorkFeedState.objects.filter(last_result=SyncResult.FAILED).exists()


def test_a_failed_match_run_leaves_the_page_working(
    client, authenticate_viewer, published, monkeypatch
):
    good = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)

    def explode(*args, **kwargs):
        raise RuntimeError("synthetic matcher failure")

    monkeypatch.setattr("apps.legal_work.current_topic_match_sync.MATCHER_VERSION", "9.9-synth")
    monkeypatch.setattr("apps.legal_work.current_topic_match_sync.match_all", explode)
    report = run_current_topic_matching()

    authenticate_viewer(client)
    response = client.get("/oigusloome/")

    assert report.result == SyncResult.FAILED
    assert response.status_code == 200
    assert LegalCurrentTopicMatchSnapshot.objects.get(is_current=True) == good


def test_a_failed_collection_leaves_the_page_working(
    client, authenticate_viewer, published, publish_current_topics
):
    good = CurrentTopicSnapshot.objects.get(is_current=True)

    publish_current_topics(
        FakeSite({}, errors={LISTING_PATH: PublicFetchError("Allikas vastas koodiga 503.")})
    )

    authenticate_viewer(client)
    response = client.get("/oigusloome/")

    assert response.status_code == 200
    assert CurrentTopicSnapshot.objects.get(is_current=True) == good


# -- the overview still works ----------------------------------------------


def test_the_overview_renders_with_the_enrichment_published(client, authenticate_viewer, published):
    authenticate_viewer(client)

    response = client.get("/")

    assert response.status_code == 200
    assert "Õigusloome" in response.content.decode("utf-8")


def test_the_overview_renders_without_any_enrichment(
    client, authenticate_viewer, imported_snapshot
):
    authenticate_viewer(client)

    response = client.get("/")

    assert response.status_code == 200


def test_the_overview_card_still_carries_the_legal_records(published):
    from apps.dashboard.overview import build_overview
    from apps.event_programme.selectors import get_event_programme_summary
    from apps.legal_work.selectors import get_legal_work_summary
    from apps.membership.selectors import get_membership_summary
    from apps.news.selectors import get_news_summary

    page = build_overview(
        legal_work=get_legal_work_summary(),
        membership=get_membership_summary(),
        news=get_news_summary(),
        events=get_event_programme_summary(),
    )

    assert page.legal_work_open
    # Presentation objects, not model instances: the imported row is reached
    # through `.item` and is never modified to carry an address.
    first = page.legal_work_open[0]
    assert isinstance(first.item, LegalWorkItem)
    assert hasattr(first, "public_url")
    assert not hasattr(first.item, "public_url")


# -- imported data is still untouched --------------------------------------


def test_the_workbook_contract_columns_are_unchanged():
    from apps.legal_work.workbook import DATA_COLUMNS, DATA_COLUMNS_V12

    assert DATA_COLUMNS == (
        "record_id",
        "source_year",
        "source_nr",
        "topic",
        "act_type",
        "received_date",
        "deadline_date",
        "sent_date",
        "sent_status",
        "recipient",
        "stage",
        "stage_key",
        "next_step",
        "is_open",
        "warning_codes",
        "source_row",
        "refreshed_at",
    )
    assert DATA_COLUMNS_V12 == DATA_COLUMNS + (
        "feedback_member_count",
        "feedback_requested_member_count",
    )


def test_no_match_field_was_added_to_the_imported_row():
    names = {field.name for field in LegalWorkItem._meta.get_fields()}

    for forbidden in (
        "public_url",
        "current_topic_url",
        "current_topic",
        "match",
        "match_score",
        "match_decision",
    ):
        assert forbidden not in names


def test_matching_changes_no_imported_row(published):
    before = {
        item.pk: (item.topic, item.record_id, item.is_open, item.sent_status)
        for item in LegalWorkItem.objects.filter(snapshot=published)
    }

    run_current_topic_matching()

    after = {
        item.pk: (item.topic, item.record_id, item.is_open, item.sent_status)
        for item in LegalWorkItem.objects.filter(snapshot=published)
    }
    assert after == before
