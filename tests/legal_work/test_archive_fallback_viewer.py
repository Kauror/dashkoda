"""Archive fallback links as a viewer meets them, and the precedence between sources.

The whole feature is a precedence rule, so these cases are written against the
rendered page: current listing first, archive second, plain text otherwise — and
never a link for a record whose opinion has already gone out.
"""

from __future__ import annotations

import re

import pytest

from apps.legal_work.archived_topic_match_sync import run_archive_matching
from apps.legal_work.current_topic_match_sync import run_current_topic_matching
from apps.legal_work.models import (
    ArchivedTopicItem,
    ArchivedTopicSnapshot,
    CurrentTopicItem,
    LegalArchivedTopicMatch,
    LegalArchivedTopicMatchSnapshot,
    LegalCurrentTopicMatch,
    LegalCurrentTopicMatchSnapshot,
    LegalWorkItem,
    MatchDecision,
    SentStatus,
)

from .archive_factory import simple_archive
from .current_topic_factory import DETAIL_PREFIX, LISTING_PATH, FakeSite, card, detail, listing

pytestmark = pytest.mark.django_db

LEGAL_URL = "/oigusloome/"
OVERVIEW_URL = "/"


def current_catalogue(*slugs: str) -> FakeSite:
    pages = {LISTING_PATH: listing(*(card(slug, f"Teema {slug}") for slug in slugs))}
    for slug in slugs:
        pages[f"{DETAIL_PREFIX}{slug}"] = detail(title=f"Teema {slug}")
    return FakeSite(pages)


def force_current(item, *, decision, candidate=None):
    snapshot = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)
    LegalCurrentTopicMatch.objects.filter(snapshot=snapshot, legal_item=item).update(
        decision=decision, best_candidate=candidate
    )


def force_archive(item, *, decision, candidate=None):
    snapshot = LegalArchivedTopicMatchSnapshot.objects.get(is_current=True)
    LegalArchivedTopicMatch.objects.filter(snapshot=snapshot, legal_item=item).update(
        decision=decision, best_candidate=candidate
    )


@pytest.fixture
def viewer(client, authenticate_viewer):
    authenticate_viewer(client)
    return client


@pytest.fixture
def both_published(imported_snapshot, publish_current_topics, publish_archived_topics):
    """A current catalogue, an archive, and a run of each matcher."""
    publish_current_topics(current_catalogue("alpha"))
    run_current_topic_matching()
    publish_archived_topics(simple_archive("vana", "vanem"))
    run_archive_matching()
    return imported_snapshot


def page(viewer, url=LEGAL_URL) -> str:
    response = viewer.get(url)
    assert response.status_code == 200
    return _without_register(response.content.decode("utf-8"))


def _without_register(markup: str) -> str:
    """Cut the register explorer's own drill-down out of the markup.

    Unconditional on the overview since 2026-08-18, and its row layout draws a
    record's topic as plain text always — `_register_results.html` labels the
    resource link by what it is ("Koja arvamus", "Avalik konsultatsioon")
    rather than wrapping the topic name the way `legal_topic.html` does for
    `Hetkel töös` and `Viimati välja läinud`. A regex built for that shared
    component's shape does not — and should not have to — understand the
    register's, so this scopes the cross-list consistency checks below to the
    lists that actually share it. `test_register.py` and
    `test_register_search.py` hold the register's own rendering to account.
    """
    start = markup.find('id="section-register"')
    if start == -1:
        return markup
    end = markup.find('id="section-open"', start)
    if end == -1:
        return markup
    return markup[:start] + markup[end:]


def linked(markup: str, topic: str, url: str) -> int:
    pattern = (
        r'<a class="dk-link[^"]*" href="' + re.escape(url) + r'"[^>]*>\s*' + re.escape(topic) + r"<"
    )
    return len(re.findall(pattern, markup))


def plain(markup: str, topic: str) -> int:
    return len(re.findall(r'<span class="[^"]*text-text">' + re.escape(topic) + r"</span>", markup))


@pytest.fixture
def eligible(both_published):
    return LegalWorkItem.objects.filter(
        snapshot=both_published, is_open=True, sent_status=SentStatus.PENDING
    ).first()


@pytest.fixture
def current_candidate():
    return CurrentTopicItem.objects.get(snapshot__is_current=True)


@pytest.fixture
def archive_candidate():
    return ArchivedTopicItem.objects.filter(
        snapshot__is_current=True, canonical_url__endswith="vana"
    ).first()


# -- precedence --------------------------------------------------------------


def test_a_current_match_wins_over_an_archive_match(
    viewer, eligible, current_candidate, archive_candidate
):
    """Both matched: the live consultation is the better destination."""
    force_current(eligible, decision=MatchDecision.MATCHED, candidate=current_candidate)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=archive_candidate)

    markup = page(viewer)

    assert linked(markup, eligible.topic, current_candidate.canonical_url) >= 1
    assert linked(markup, eligible.topic, archive_candidate.canonical_url) == 0


def test_an_archive_match_is_used_when_the_current_decision_is_ambiguous(
    viewer, eligible, current_candidate, archive_candidate
):
    force_current(eligible, decision=MatchDecision.AMBIGUOUS, candidate=current_candidate)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=archive_candidate)

    markup = page(viewer)

    assert linked(markup, eligible.topic, archive_candidate.canonical_url) >= 1


def test_an_archive_match_is_used_when_the_current_decision_is_unmatched(
    viewer, eligible, archive_candidate
):
    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=archive_candidate)

    markup = page(viewer)

    assert linked(markup, eligible.topic, archive_candidate.canonical_url) >= 1


def test_neither_source_matching_leaves_plain_text(viewer, eligible):
    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.UNMATCHED, candidate=None)

    markup = page(viewer)

    assert "meie-moju/hetkel-kasil" not in markup
    assert plain(markup, eligible.topic) >= 1


def test_an_ambiguous_archive_decision_stays_plain_text(viewer, eligible, archive_candidate):
    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.AMBIGUOUS, candidate=archive_candidate)

    markup = page(viewer)

    assert linked(markup, eligible.topic, archive_candidate.canonical_url) == 0
    assert plain(markup, eligible.topic) >= 1


# -- sent and closed records -------------------------------------------------


def test_a_sent_record_never_carries_a_consultation_link(
    viewer, both_published, archive_candidate, current_candidate
):
    """The rule the whole eligibility change exists for.

    Even with both matchers having said `matched`, an opinion that has gone out
    means the consultation page is finished business. What a reader wants next
    is the opinion, which DashKoda does not have yet.
    """
    sent = LegalWorkItem.objects.filter(
        snapshot=both_published, sent_status=SentStatus.SENT
    ).first()
    assert sent is not None
    # Force stored matches for it even though the matchers would not create them.
    current_snapshot = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)
    archive_snapshot = LegalArchivedTopicMatchSnapshot.objects.get(is_current=True)
    LegalCurrentTopicMatch.objects.create(
        snapshot=current_snapshot,
        legal_item=sent,
        best_candidate=current_candidate,
        decision=MatchDecision.MATCHED,
        score=99,
        runner_up_score=0,
        score_margin=99,
        candidate_count=1,
        evidence_codes=[],
    )
    LegalArchivedTopicMatch.objects.create(
        snapshot=archive_snapshot,
        legal_item=sent,
        best_candidate=archive_candidate,
        decision=MatchDecision.MATCHED,
        score=99,
        runner_up_score=0,
        score_margin=99,
        candidate_count=1,
        evidence_codes=[],
    )

    markup = page(viewer)

    assert linked(markup, sent.topic, current_candidate.canonical_url) == 0
    assert linked(markup, sent.topic, archive_candidate.canonical_url) == 0


def test_no_matcher_considers_a_sent_record(both_published):
    sent_ids = set(
        LegalWorkItem.objects.filter(
            snapshot=both_published, sent_status=SentStatus.SENT
        ).values_list("pk", flat=True)
    )
    assert sent_ids

    current = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)
    archive = LegalArchivedTopicMatchSnapshot.objects.get(is_current=True)

    assert not current.matches.filter(legal_item_id__in=sent_ids).exists()
    assert not archive.matches.filter(legal_item_id__in=sent_ids).exists()


def test_no_matcher_considers_a_closed_record(both_published):
    closed_ids = set(
        LegalWorkItem.objects.filter(snapshot=both_published, is_open=False).values_list(
            "pk", flat=True
        )
    )
    assert closed_ids

    archive = LegalArchivedTopicMatchSnapshot.objects.get(is_current=True)

    assert not archive.matches.filter(legal_item_id__in=closed_ids).exists()


# -- staleness ---------------------------------------------------------------


def test_a_retired_archive_match_snapshot_is_never_applied(viewer, eligible, archive_candidate):
    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=archive_candidate)
    assert linked(page(viewer), eligible.topic, archive_candidate.canonical_url) >= 1

    LegalArchivedTopicMatchSnapshot.objects.filter(is_current=True).update(is_current=False)

    assert linked(page(viewer), eligible.topic, archive_candidate.canonical_url) == 0


def test_a_retired_archive_catalogue_is_never_applied(
    viewer, eligible, archive_candidate, publish_archived_topics
):
    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=archive_candidate)
    assert linked(page(viewer), eligible.topic, archive_candidate.canonical_url) >= 1

    publish_archived_topics(simple_archive("vana", "vanem", "uus"))

    assert linked(page(viewer), eligible.topic, archive_candidate.canonical_url) == 0


def test_a_retired_current_match_snapshot_invalidates_the_archive_fallback(
    viewer, eligible, archive_candidate
):
    """The archive defers to a specific current run; retiring it retires this too."""
    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=archive_candidate)
    assert linked(page(viewer), eligible.topic, archive_candidate.canonical_url) >= 1

    LegalCurrentTopicMatchSnapshot.objects.filter(is_current=True).update(is_current=False)

    assert linked(page(viewer), eligible.topic, archive_candidate.canonical_url) == 0


def test_an_index_only_candidate_is_never_rendered(viewer, eligible):
    """Belt and braces: the matcher refuses it, and so does the read path."""
    from apps.legal_work.models import DetailStatus

    pending = ArchivedTopicItem.objects.filter(snapshot__is_current=True).first()
    ArchivedTopicItem.objects.filter(pk=pending.pk).update(
        detail_status=DetailStatus.PENDING, body_text=""
    )
    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=pending)

    markup = page(viewer)

    assert linked(markup, eligible.topic, pending.canonical_url) == 0


def test_an_invalid_stored_archive_url_is_never_rendered(viewer, eligible, archive_candidate):
    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=archive_candidate)
    ArchivedTopicItem.objects.filter(pk=archive_candidate.pk).update(
        canonical_url="https://example.org/et/meie-moju/hetkel-kasil/vana"
    )

    markup = page(viewer)

    assert "example.org" not in markup


# -- consistency, cost and leakage -------------------------------------------


def test_an_archive_link_is_identical_everywhere_the_record_appears(
    viewer, eligible, archive_candidate
):
    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=archive_candidate)

    markup = page(viewer)

    assert linked(markup, eligible.topic, archive_candidate.canonical_url) >= 1
    assert plain(markup, eligible.topic) == 0


def test_the_overview_uses_the_same_archive_link(viewer, eligible, archive_candidate):
    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=archive_candidate)

    legal_page = page(viewer, LEGAL_URL)
    overview = page(viewer, OVERVIEW_URL)

    if linked(overview, eligible.topic, archive_candidate.canonical_url):
        assert linked(legal_page, eligible.topic, archive_candidate.canonical_url) >= 1


def test_the_page_costs_a_fixed_number_of_queries(
    viewer, eligible, archive_candidate, django_assert_num_queries
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=archive_candidate)

    with CaptureQueriesContext(connection) as first:
        viewer.get(LEGAL_URL)
    with CaptureQueriesContext(connection) as second:
        viewer.get(LEGAL_URL)

    assert len(first) == len(second)


def test_no_archive_terminology_reaches_the_viewer(viewer, eligible, archive_candidate):
    force_current(eligible, decision=MatchDecision.UNMATCHED, candidate=None)
    force_archive(eligible, decision=MatchDecision.MATCHED, candidate=archive_candidate)

    markup = page(viewer)

    for word in (
        "arhiiv",
        "Arhiiv",
        "archive",
        "backfill",
        "hydrated",
        "detail_status",
        "Skoor",
        "Tõendikood",
        "Sobitaja",
        "ambiguous",
        "unmatched",
    ):
        assert word not in markup, f"{word!r} leaked into the viewer page"


def test_the_freshness_denominator_is_still_four(both_published):
    from apps.dashboard import freshness

    assert freshness.current_freshness().total_sources == 4


def test_the_archive_snapshot_is_current_and_unique(both_published):
    assert ArchivedTopicSnapshot.objects.filter(is_current=True).count() == 1
    assert LegalArchivedTopicMatchSnapshot.objects.filter(is_current=True).count() == 1
