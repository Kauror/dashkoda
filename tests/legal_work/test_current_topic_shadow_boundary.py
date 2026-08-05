"""The shadow boundary: nothing a viewer can see changes in this phase.

This file exists because the failure it guards against is silent. A match
snapshot that quietly reached `/oigusloome/`, a topic that quietly became a
link, a fifth source that quietly changed what "3/4 connected" means — none of
those would break a test written about the matcher, and all of them would be
wrong. So they are asserted here, from the outside, against the rendered page.
"""

from __future__ import annotations

import pytest
from django.template.loader import render_to_string

from apps.dashboard import freshness
from apps.legal_work.current_topic_match_sync import run_current_topic_matching
from apps.legal_work.models import LegalCurrentTopicMatchSnapshot, MatchDecision

from .current_topic_factory import DETAIL_PREFIX, LISTING_PATH, FakeSite, card, detail, listing

pytestmark = pytest.mark.django_db

LEGAL_URL = "/oigusloome/"


def catalogue(*slugs: str) -> FakeSite:
    """A catalogue whose entries are written to match the synthetic workbook."""
    pages = {
        LISTING_PATH: listing(
            *(
                card(
                    slug, f"Mida arvad {slug} muudatustest?", summary=f"Sünteetiline {slug} teema."
                )
                for slug in slugs
            )
        )
    }
    for slug in slugs:
        pages[f"{DETAIL_PREFIX}{slug}"] = detail(
            title=f"Mida arvad {slug} muudatustest?",
            intro=f"Sünteetiline {slug} teema. Anna hiljemalt 18. augustiks teada.",
            body=f"Sünteetiline avatud teema {slug} kohta.",
        )
    return FakeSite(pages)


@pytest.fixture
def shadow_run(imported_snapshot, publish_current_topics):
    publish_current_topics(catalogue("alpha", "beeta"))
    run_current_topic_matching()
    return LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)


def render_legal_page(client, authenticate_viewer):
    authenticate_viewer(client)
    response = client.get(LEGAL_URL)
    assert response.status_code == 200
    return response.content.decode("utf-8")


# -- the viewer page --------------------------------------------------------


def test_the_legal_page_renders_identically_before_and_after_a_match_run(
    client, authenticate_viewer, imported_snapshot, publish_current_topics
):
    before = render_legal_page(client, authenticate_viewer)

    publish_current_topics(catalogue("alpha", "beeta"))
    run_current_topic_matching()
    after = render_legal_page(client, authenticate_viewer)

    assert after == before


def test_no_koda_topic_address_reaches_the_legal_page(client, authenticate_viewer, shadow_run):
    body = render_legal_page(client, authenticate_viewer)

    assert "meie-moju/hetkel-kasil" not in body


def test_no_score_or_matching_vocabulary_reaches_the_legal_page(
    client, authenticate_viewer, shadow_run
):
    body = render_legal_page(client, authenticate_viewer)

    for word in ("score", "Skoor", "sobitamine", "Sobitaja", "ambiguous", "Ebaselge"):
        assert word not in body


def test_the_overview_is_unchanged_by_a_match_run(
    client, authenticate_viewer, imported_snapshot, publish_current_topics
):
    authenticate_viewer(client)
    before = client.get("/").content

    publish_current_topics(catalogue("alpha", "beeta"))
    run_current_topic_matching()
    after = client.get("/").content

    assert after == before


def test_a_match_run_produced_decisions_so_the_assertions_above_mean_something(shadow_run):
    """Guards the tests above from passing because nothing was matched at all."""
    assert shadow_run.legal_item_count > 0
    assert shadow_run.matches.count() == shadow_run.legal_item_count
    assert set(shadow_run.matches.values_list("decision", flat=True)) <= set(MatchDecision.values)


# -- the shared legal-topic component --------------------------------------


def test_the_legal_topic_component_still_renders_plain_text_without_an_address():
    class Item:
        topic = "Sünteetiline teema"
        public_url = ""

    rendered = render_to_string("dashboard/components/legal_topic.html", {"item": Item()})

    assert "<a" not in rendered
    assert "Sünteetiline teema" in rendered


def test_the_legal_topic_component_still_renders_a_link_when_given_one():
    class Item:
        topic = "Sünteetiline teema"
        public_url = "https://www.koda.ee/et/meie-moju/hetkel-kasil/alpha"

    rendered = render_to_string("dashboard/components/legal_topic.html", {"item": Item()})

    assert 'href="https://www.koda.ee/et/meie-moju/hetkel-kasil/alpha"' in rendered
    assert 'rel="noopener noreferrer"' in rendered


def test_nothing_in_this_phase_supplies_the_component_with_an_address(shadow_run):
    """The component's contract is unchanged; what changed is nothing feeding it."""
    from apps.legal_work.selectors import (
        get_current_snapshot,
        get_latest_sent_items,
        get_newest_received_items,
        get_open_items,
    )

    snapshot = get_current_snapshot()
    sections = (
        get_open_items(snapshot),
        get_latest_sent_items(snapshot),
        get_newest_received_items(snapshot),
    )

    for section in sections:
        assert section
        for item in section:
            assert not getattr(item, "public_url", "")


# -- global freshness -------------------------------------------------------


def test_the_freshness_denominator_is_unchanged(shadow_run):
    assert freshness.current_freshness().total_sources == 4


def test_a_failed_catalogue_collection_does_not_make_the_dashboard_stale(
    imported_snapshot, publish_current_topics
):
    from apps.core.public_http import PublicFetchError

    before = freshness.current_freshness()

    publish_current_topics(
        FakeSite({}, errors={LISTING_PATH: PublicFetchError("Allikas vastas koodiga 503.")})
    )
    after = freshness.current_freshness()

    assert after.total_sources == before.total_sources
    assert after.stale_sources == before.stale_sources
    assert after.connected_sources == before.connected_sources
