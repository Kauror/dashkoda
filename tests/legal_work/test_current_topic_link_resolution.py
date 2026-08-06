"""The link resolver: what it will publish, and what it costs.

Two concerns that are cheaper to state against the resolver than against a
rendered page — the defensive URL rules, and the promise that a page costs one
link query however many rows it draws. How the resolved address then behaves in
the interface is covered in `test_current_topic_viewer_links.py`.
"""

from __future__ import annotations

import pytest

from apps.legal_work.current_topic_match_sync import run_current_topic_matching
from apps.legal_work.models import (
    CurrentTopicItem,
    LegalCurrentTopicMatch,
    LegalCurrentTopicMatchSnapshot,
    LegalWorkItem,
    MatchDecision,
)
from apps.legal_work.topic_links import (
    LegalTopicPresentation,
    is_publishable_topic_url,
    present_topics,
    resolve_links_for,
    resolve_topic_links,
)

from .current_topic_factory import DETAIL_PREFIX, LISTING_PATH, FakeSite, card, detail, listing

VALID = "https://www.koda.ee/et/meie-moju/hetkel-kasil/alfa-eelnou"


def catalogue(*slugs: str) -> FakeSite:
    pages = {LISTING_PATH: listing(*(card(slug, f"Teema {slug}") for slug in slugs))}
    for slug in slugs:
        pages[f"{DETAIL_PREFIX}{slug}"] = detail(title=f"Teema {slug}")
    return FakeSite(pages)


# -- the defensive URL rules ------------------------------------------------


def test_a_canonical_current_topic_url_is_publishable():
    assert is_publishable_topic_url(VALID)
    assert is_publishable_topic_url("https://koda.ee/et/meie-moju/hetkel-kasil/beeta")


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://www.koda.ee/et/meie-moju/hetkel-kasil/alfa", "not HTTPS"),
        ("//www.koda.ee/et/meie-moju/hetkel-kasil/alfa", "no scheme"),
        ("https://example.org/et/meie-moju/hetkel-kasil/alfa", "not a Koda.ee host"),
        ("https://koda.ee.example.org/et/meie-moju/hetkel-kasil/alfa", "lookalike host"),
        ("https://www.koda.ee/et/meie-moju/hetkel-kasil", "the listing itself"),
        ("https://www.koda.ee/et/meie-moju/hetkel-kasil/", "the listing with a slash"),
        ("https://www.koda.ee/et/meie-moju/hetkel-kasil/arhiiv", "the archive"),
        ("https://www.koda.ee/et/uudised/midagi", "the wrong path"),
        ("https://www.koda.ee/et/meie-moju/", "a parent path"),
        ("https://user:pw@www.koda.ee/et/meie-moju/hetkel-kasil/alfa", "carries credentials"),
        ("https://www.koda.ee/et/meie-moju/hetkel-kasil/" + "x" * 600, "far too long"),
        ("", "empty"),
        ("javascript:alert(1)", "not a URL at all"),
    ],
)
def test_an_unsafe_or_out_of_scope_url_is_refused(url, why):
    assert is_publishable_topic_url(url) is False, why


def test_the_archive_is_refused_even_with_a_trailing_slash():
    assert is_publishable_topic_url("https://koda.ee/et/meie-moju/hetkel-kasil/arhiiv/") is False


# -- resolution against the database ---------------------------------------


@pytest.fixture
def published(imported_snapshot, publish_current_topics):
    publish_current_topics(catalogue("alpha", "beeta"))
    run_current_topic_matching()
    return imported_snapshot


@pytest.fixture
def matched_pair(published):
    """One open record decided `matched` against one catalogue entry."""
    item = LegalWorkItem.objects.filter(snapshot=published, is_open=True).first()
    candidate = CurrentTopicItem.objects.get(
        snapshot__is_current=True, canonical_url__endswith="alpha"
    )
    snapshot = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)
    LegalCurrentTopicMatch.objects.filter(snapshot=snapshot, legal_item=item).update(
        decision=MatchDecision.MATCHED, best_candidate=candidate
    )
    return item, candidate


@pytest.mark.django_db
def test_an_eligible_match_resolves_to_its_candidate_url(matched_pair):
    item, candidate = matched_pair

    assert resolve_topic_links([item.pk]) == {item.pk: candidate.canonical_url}


@pytest.mark.django_db
def test_an_empty_request_asks_the_database_nothing(django_assert_num_queries):
    with django_assert_num_queries(0):
        assert resolve_topic_links([]) == {}


@pytest.mark.django_db
def test_a_stored_url_that_fails_validation_resolves_to_nothing(matched_pair):
    """A row written before a rule tightened must not become a link."""
    item, candidate = matched_pair
    CurrentTopicItem.objects.filter(pk=candidate.pk).update(
        canonical_url="https://example.org/et/meie-moju/hetkel-kasil/alfa"
    )

    assert resolve_topic_links([item.pk]) == {}


@pytest.mark.django_db
def test_the_archive_url_is_refused_even_when_stored(matched_pair):
    item, candidate = matched_pair
    CurrentTopicItem.objects.filter(pk=candidate.pk).update(
        canonical_url="https://www.koda.ee/et/meie-moju/hetkel-kasil/arhiiv"
    )

    assert resolve_topic_links([item.pk]) == {}


@pytest.mark.django_db
def test_an_ambiguous_decision_resolves_to_nothing(matched_pair):
    item, _candidate = matched_pair
    LegalCurrentTopicMatch.objects.filter(legal_item=item).update(decision=MatchDecision.AMBIGUOUS)

    assert resolve_topic_links([item.pk]) == {}


@pytest.mark.django_db
def test_a_record_outside_the_requested_set_is_never_returned(matched_pair, published):
    item, _candidate = matched_pair
    other = (
        LegalWorkItem.objects.filter(snapshot=published, is_open=True).exclude(pk=item.pk).first()
    )

    assert resolve_topic_links([other.pk]) == {}


@pytest.mark.django_db
def test_a_retired_match_snapshot_resolves_to_nothing(matched_pair):
    item, _candidate = matched_pair
    LegalCurrentTopicMatchSnapshot.objects.filter(is_current=True).update(is_current=False)

    assert resolve_topic_links([item.pk]) == {}


# -- the query budget -------------------------------------------------------


@pytest.mark.django_db
def test_resolution_costs_one_query_regardless_of_how_many_records(
    matched_pair, published, django_assert_num_queries
):
    item, _candidate = matched_pair
    every_id = list(LegalWorkItem.objects.filter(snapshot=published).values_list("pk", flat=True))
    assert len(every_id) > 1

    with django_assert_num_queries(1):
        resolve_topic_links([item.pk])

    with django_assert_num_queries(1):
        resolve_topic_links(every_id)


@pytest.mark.django_db
def test_resolving_several_collections_together_still_costs_one_query(
    published, django_assert_num_queries
):
    items = list(LegalWorkItem.objects.filter(snapshot=published))
    assert len(items) > 1

    with django_assert_num_queries(1):
        resolve_links_for(items, items[:1], items[1:])


@pytest.mark.django_db
def test_the_legal_page_query_count_does_not_grow_with_its_rows(
    client,
    authenticate_viewer,
    published,
    django_assert_num_queries,
    make_workbook,
    register_workbook,
    publish_current_topics,
):
    """Rendering more rows must not cost more queries.

    Measured as an exact equality rather than a ceiling: an N+1 would show up as
    a difference between the two counts, and a ceiling generous enough to pass
    today would hide one tomorrow.
    """
    import datetime as dt

    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.legal_work.importer import import_artifact

    from .workbook_factory import synthetic_row

    authenticate_viewer(client)
    today = dt.date.today()

    def render_and_count(row_count: int) -> int:
        rows = [
            synthetic_row(
                record_id=f"SYN-{index:04d}",
                topic=f"Sünteetiline teema {index}",
                received_date=today - dt.timedelta(days=index + 1),
                deadline_date=today + dt.timedelta(days=5),
                is_open=True,
                source_row=index + 2,
            )
            for index in range(row_count)
        ]
        import_artifact(register_workbook(make_workbook(rows=rows)), dry_run=False)
        publish_current_topics(catalogue("alpha", "beeta"))
        run_current_topic_matching()
        with CaptureQueriesContext(connection) as captured:
            response = client.get("/oigusloome/")
            assert response.status_code == 200
        return len(captured)

    with_one = render_and_count(1)
    with_many = render_and_count(12)

    assert with_one == with_many, "the page gained a query per row"


# -- presentation objects ---------------------------------------------------


@pytest.mark.django_db
def test_a_presentation_never_mutates_the_record_it_wraps(matched_pair):
    item, candidate = matched_pair

    presented = present_topics([item], {item.pk: candidate.canonical_url})[0]

    assert presented.item is item
    assert presented.public_url == candidate.canonical_url
    assert presented.topic == item.topic
    assert not hasattr(item, "public_url")


def test_a_presentation_without_an_address_is_not_linked():
    presented = LegalTopicPresentation(item=LegalWorkItem(topic="Sünteetiline"), public_url="")

    assert presented.public_url == ""
    assert presented.is_linked is False


def test_a_presentation_is_frozen():
    import dataclasses

    presented = LegalTopicPresentation(item=LegalWorkItem(topic="Sünteetiline"))

    with pytest.raises(dataclasses.FrozenInstanceError):
        presented.public_url = "https://www.koda.ee/et/meie-moju/hetkel-kasil/x"


# -- no outbound request at render time ------------------------------------


@pytest.mark.django_db
def test_rendering_a_page_makes_no_outbound_request(
    client, authenticate_viewer, matched_pair, monkeypatch
):
    """Every HTTP path this project owns is made to explode for one render.

    `requests.Session.request` is the floor: every outbound call in this
    repository goes through `apps.core.public_http`, which goes through it. The
    database connection is deliberately left alone — patching sockets wholesale
    would break PostgreSQL and prove nothing about HTTP.
    """
    import requests

    def forbidden(*args, **kwargs):
        raise AssertionError("a page render attempted an outbound HTTP request")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "get", forbidden)
    monkeypatch.setattr("apps.core.public_http.fetch", forbidden)
    monkeypatch.setattr("apps.legal_work.current_topics.fetch", forbidden)

    authenticate_viewer(client)
    assert client.get("/oigusloome/").status_code == 200
    assert client.get("/").status_code == 200
