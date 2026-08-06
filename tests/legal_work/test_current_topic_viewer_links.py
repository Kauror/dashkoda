"""Automatic topic links as a viewer actually meets them.

Everything here goes through the rendered page rather than through the resolver,
because the failures worth catching are the ones that only appear at the far end:
a link that survives into a newer legal snapshot, a topic that is clickable in
one list and plain text in another, a score leaking into the markup.

The counterpart file `test_current_topic_link_resolution.py` covers the resolver
itself — the URL rules and the query budget — where the cases are cheaper to
state directly.
"""

from __future__ import annotations

import re

import pytest

from apps.legal_work.current_topic_match_sync import run_current_topic_matching
from apps.legal_work.models import (
    CurrentTopicItem,
    CurrentTopicSnapshot,
    LegalCurrentTopicMatch,
    LegalCurrentTopicMatchSnapshot,
    LegalWorkItem,
    MatchDecision,
)

from .current_topic_factory import DETAIL_PREFIX, LISTING_PATH, FakeSite, card, detail, listing

pytestmark = pytest.mark.django_db

LEGAL_URL = "/oigusloome/"
OVERVIEW_URL = "/"


def catalogue(*slugs: str) -> FakeSite:
    pages = {LISTING_PATH: listing(*(card(slug, f"Teema {slug}") for slug in slugs))}
    for slug in slugs:
        pages[f"{DETAIL_PREFIX}{slug}"] = detail(title=f"Teema {slug}")
    return FakeSite(pages)


def force_decision(item, *, decision, candidate=None):
    """Rewrite one published decision so a viewer case can be stated directly.

    The matcher's own thresholds are exercised in `test_current_topic_matching`.
    What these cases are about is what the *viewer* does with a decision, and
    reverse-engineering synthetic prose that scores 62.00 would test the wording
    of a fixture rather than the behaviour under test.

    Rows are immutable through the ORM, so this goes around `save()` on purpose
    and only ever inside a test.
    """
    snapshot = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)
    LegalCurrentTopicMatch.objects.filter(snapshot=snapshot, legal_item=item).update(
        decision=decision,
        best_candidate=candidate,
    )
    return snapshot


@pytest.fixture
def published(imported_snapshot, publish_current_topics):
    publish_current_topics(catalogue("alpha", "beeta"))
    run_current_topic_matching()
    return imported_snapshot


@pytest.fixture
def open_item(published):
    return LegalWorkItem.objects.filter(snapshot=published, is_open=True).first()


@pytest.fixture
def candidate():
    return CurrentTopicItem.objects.get(snapshot__is_current=True, canonical_url__endswith="alpha")


@pytest.fixture
def three_list_item(imported_snapshot, publish_current_topics, make_workbook, register_workbook):
    """A record the Õigusloome page draws in three different lists.

    Open, received recently and carrying a deadline inside the horizon, so it is
    listed under `Lähenevad tähtajad`, in the `Hetkel töös` table and in
    `Uusimad sisse tulnud`. Cross-list consistency is only testable against a
    record that genuinely appears more than once.
    """
    import datetime as dt

    from apps.legal_work.importer import import_artifact

    from .workbook_factory import synthetic_row

    today = dt.date.today()
    rows = [
        synthetic_row(
            record_id="SYN-MULTI",
            topic="Sünteetiline mitmes loendis teema",
            received_date=today - dt.timedelta(days=3),
            deadline_date=today + dt.timedelta(days=5),
            is_open=True,
            source_row=2,
        ),
        synthetic_row(
            record_id="SYN-OTHER",
            topic="Sünteetiline teine teema",
            received_date=today - dt.timedelta(days=40),
            deadline_date=None,
            is_open=True,
            source_row=3,
        ),
    ]
    snapshot = import_artifact(register_workbook(make_workbook(rows=rows)), dry_run=False).snapshot
    publish_current_topics(catalogue("alpha", "beeta"))
    run_current_topic_matching()
    return LegalWorkItem.objects.get(snapshot=snapshot, record_id="SYN-MULTI")


@pytest.fixture
def viewer(client, authenticate_viewer):
    authenticate_viewer(client)
    return client


def page(viewer, url=LEGAL_URL) -> str:
    response = viewer.get(url)
    assert response.status_code == 200
    return response.content.decode("utf-8")


def links_to(markup: str, url: str) -> int:
    return markup.count(f'href="{url}"')


def linked_renderings(markup: str, topic: str, url: str) -> int:
    """How many times `topic` is drawn as an anchor pointing at `url`.

    Counts renderings of *this* topic rather than anchors on the page, so a test
    cannot pass because some other record happened to be linked.
    """
    pattern = (
        r'<a class="dk-link[^"]*" href="' + re.escape(url) + r'"[^>]*>\s*' + re.escape(topic) + r"<"
    )
    return len(re.findall(pattern, markup))


def plain_renderings(markup: str, topic: str) -> int:
    """How many times `topic` is drawn as plain text by the shared component."""
    pattern = r'<span class="[^"]*text-text">' + re.escape(topic) + r"</span>"
    return len(re.findall(pattern, markup))


def _replacement_rows():
    """A different workbook, so the import is a genuinely new snapshot."""
    import datetime as dt

    from .workbook_factory import synthetic_row

    today = dt.date.today()
    return [
        synthetic_row(
            record_id="SYN-NEXT",
            topic="Sünteetiline järgmise hetkeseisu teema",
            received_date=today - dt.timedelta(days=2),
            is_open=True,
            source_row=2,
        )
    ]


# -- what a viewer sees -----------------------------------------------------


def test_a_matched_open_record_renders_its_koda_page_as_a_link(
    viewer, open_item, candidate, published
):
    force_decision(open_item, decision=MatchDecision.MATCHED, candidate=candidate)

    markup = page(viewer)

    assert links_to(markup, candidate.canonical_url) >= 1
    assert "(avaneb uuel lehel)" in markup


def test_an_ambiguous_record_stays_plain_text(viewer, open_item, candidate, published):
    force_decision(open_item, decision=MatchDecision.AMBIGUOUS, candidate=candidate)

    markup = page(viewer)

    assert links_to(markup, candidate.canonical_url) == 0
    assert open_item.topic in markup


def test_an_unmatched_record_stays_plain_text(viewer, open_item, candidate, published):
    force_decision(open_item, decision=MatchDecision.UNMATCHED, candidate=candidate)

    markup = page(viewer)

    assert links_to(markup, candidate.canonical_url) == 0
    assert open_item.topic in markup


def test_a_matched_decision_can_never_exist_without_a_candidate(open_item, published):
    """A link with nothing to point at cannot be stored in the first place.

    The read path refuses `best_candidate=None` as well, but that is the second
    line of defence: the check constraint means the row never reaches it.
    """
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        force_decision(open_item, decision=MatchDecision.MATCHED, candidate=None)


def test_without_any_match_snapshot_every_topic_is_plain_text(
    viewer, imported_snapshot, publish_current_topics
):
    publish_current_topics(catalogue("alpha"))

    markup = page(viewer)

    assert "meie-moju/hetkel-kasil" not in markup
    assert "Hetkel töös" in markup


def test_without_a_catalogue_the_page_still_renders(viewer, imported_snapshot):
    markup = page(viewer)

    assert "meie-moju/hetkel-kasil" not in markup
    assert "Õigusloome" in markup


# -- staleness --------------------------------------------------------------


def test_a_match_from_an_older_legal_snapshot_is_never_applied(
    viewer, open_item, candidate, published, make_workbook, register_workbook
):
    """The workbook moved on overnight and matching has not run yet."""
    from apps.legal_work.importer import import_artifact

    force_decision(open_item, decision=MatchDecision.MATCHED, candidate=candidate)
    assert links_to(page(viewer), candidate.canonical_url) >= 1

    # A new legal snapshot: the match snapshot now describes retired rows. The
    # rows must differ from the published ones, because an identical workbook is
    # the same content and the artifact registry rejects it as already imported.
    import_artifact(register_workbook(make_workbook(rows=_replacement_rows())), dry_run=False)

    markup = page(viewer)

    assert links_to(markup, candidate.canonical_url) == 0
    assert "Hetkel töös" in markup


def test_a_candidate_from_an_older_catalogue_snapshot_is_never_applied(
    viewer, open_item, candidate, published, publish_current_topics
):
    force_decision(open_item, decision=MatchDecision.MATCHED, candidate=candidate)
    assert links_to(page(viewer), candidate.canonical_url) >= 1

    # A new catalogue retires the snapshot the candidate belongs to.
    publish_current_topics(catalogue("alpha", "beeta", "gamma"))

    markup = page(viewer)

    assert links_to(markup, candidate.canonical_url) == 0


def test_a_page_that_leaves_the_listing_stops_supplying_a_link(
    viewer, open_item, candidate, published, publish_current_topics
):
    """Current-only semantics, end to end.

    The consultation closes, Koda.ee drops it from the listing, the next
    collection publishes a catalogue without it and the next match run decides
    again. The legal record stays on the page as plain text; it does not keep
    yesterday's link, and nothing carries it forward by `record_id`.
    """
    force_decision(open_item, decision=MatchDecision.MATCHED, candidate=candidate)
    gone_url = candidate.canonical_url
    assert links_to(page(viewer), gone_url) >= 1

    publish_current_topics(catalogue("beeta"))
    run_current_topic_matching()

    markup = page(viewer)

    assert links_to(markup, gone_url) == 0
    assert not CurrentTopicItem.objects.filter(
        snapshot__is_current=True, canonical_url=gone_url
    ).exists()
    assert open_item.topic in markup


def test_a_retired_match_snapshot_is_never_applied(viewer, open_item, candidate, published):
    force_decision(open_item, decision=MatchDecision.MATCHED, candidate=candidate)
    assert links_to(page(viewer), candidate.canonical_url) >= 1

    LegalCurrentTopicMatchSnapshot.objects.filter(is_current=True).update(is_current=False)

    assert links_to(page(viewer), candidate.canonical_url) == 0


def test_a_match_for_a_different_record_never_links_this_one(
    viewer, published, candidate, open_item
):
    """One record's decision must not leak onto its neighbour."""
    others = list(
        LegalWorkItem.objects.filter(snapshot=published, is_open=True).exclude(pk=open_item.pk)
    )
    assert others, "the fixture needs a second open record for this to mean anything"
    force_decision(open_item, decision=MatchDecision.MATCHED, candidate=candidate)
    for other in others:
        force_decision(other, decision=MatchDecision.UNMATCHED, candidate=None)

    markup = page(viewer)

    assert linked_renderings(markup, open_item.topic, candidate.canonical_url) >= 1
    for other in others:
        assert linked_renderings(markup, other.topic, candidate.canonical_url) == 0
        assert plain_renderings(markup, other.topic) >= 1


# -- consistency across every list on the page ------------------------------


def test_one_record_is_linked_identically_in_every_list_it_appears_in(
    viewer, three_list_item, candidate
):
    """A record can appear in three of this page's lists at once.

    `Lähenevad tähtajad`, the `Hetkel töös` table and `Uusimad sisse tulnud` all
    draw the same row. All three agree because one mapping feeds them, and this
    asserts the agreement rather than merely counting anchors: every rendering of
    the topic is a link to the same address, and none of them is a bare span.
    """
    force_decision(three_list_item, decision=MatchDecision.MATCHED, candidate=candidate)

    markup = page(viewer)

    linked = linked_renderings(markup, three_list_item.topic, candidate.canonical_url)
    assert linked == 3, "expected the deadline strip, the open table and the received table"
    assert plain_renderings(markup, three_list_item.topic) == 0


def test_a_record_with_no_link_is_plain_text_in_every_list(viewer, three_list_item, candidate):
    force_decision(three_list_item, decision=MatchDecision.AMBIGUOUS, candidate=candidate)

    markup = page(viewer)

    assert linked_renderings(markup, three_list_item.topic, candidate.canonical_url) == 0
    assert plain_renderings(markup, three_list_item.topic) == 3


def test_the_overview_card_links_the_same_record_as_the_legal_page(
    viewer, three_list_item, candidate
):
    force_decision(three_list_item, decision=MatchDecision.MATCHED, candidate=candidate)

    legal_page = page(viewer, LEGAL_URL)
    overview = page(viewer, OVERVIEW_URL)

    assert linked_renderings(legal_page, three_list_item.topic, candidate.canonical_url) >= 1
    assert linked_renderings(overview, three_list_item.topic, candidate.canonical_url) >= 1


# -- what the viewer must never see -----------------------------------------


def test_no_matching_internals_reach_the_legal_page(viewer, open_item, candidate, published):
    force_decision(open_item, decision=MatchDecision.MATCHED, candidate=candidate)

    markup = page(viewer)

    for word in (
        "matched",
        "ambiguous",
        "unmatched",
        "Ebaselge",
        "Sidumata",
        "score",
        "Skoor",
        "runner_up",
        "score_margin",
        "evidence",
        "Tõendikood",
        "matcher_version",
        "Sobitaja",
        "deadline-exact",
        "unique-token-hit",
        "narrow-margin",
    ):
        assert word not in markup, f"{word!r} leaked into the viewer page"


def test_no_matching_internals_reach_the_overview(viewer, open_item, candidate, published):
    force_decision(open_item, decision=MatchDecision.MATCHED, candidate=candidate)

    markup = page(viewer, OVERVIEW_URL)

    for word in ("Skoor", "Tõendikood", "Sobitaja", "ambiguous", "narrow-margin"):
        assert word not in markup


def test_the_link_carries_the_accessible_destination_note(viewer, open_item, candidate, published):
    force_decision(open_item, decision=MatchDecision.MATCHED, candidate=candidate)

    markup = page(viewer)

    anchor = re.search(
        r'<a class="dk-link[^"]*" href="' + re.escape(candidate.canonical_url) + r'"[^>]*>',
        markup,
    )
    assert anchor is not None
    assert 'rel="noopener noreferrer"' in anchor.group(0)


# -- nothing imported is touched -------------------------------------------


def test_rendering_the_page_changes_no_imported_row(viewer, published, open_item, candidate):
    force_decision(open_item, decision=MatchDecision.MATCHED, candidate=candidate)
    before = {
        item.pk: (item.topic, item.record_id, item.is_open)
        for item in LegalWorkItem.objects.filter(snapshot=published)
    }

    page(viewer)
    page(viewer, OVERVIEW_URL)

    after = {
        item.pk: (item.topic, item.record_id, item.is_open)
        for item in LegalWorkItem.objects.filter(snapshot=published)
    }
    assert after == before


def test_legal_work_items_still_have_no_public_url_field():
    assert "public_url" not in {field.name for field in LegalWorkItem._meta.get_fields()}


def test_the_current_snapshots_are_still_unique(published):
    assert CurrentTopicSnapshot.objects.filter(is_current=True).count() == 1
    assert LegalCurrentTopicMatchSnapshot.objects.filter(is_current=True).count() == 1
