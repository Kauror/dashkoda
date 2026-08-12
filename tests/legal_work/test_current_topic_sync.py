"""Publishing the current-topic catalogue: all-or-nothing, and never destructive.

Synthetic pages only. A failure here must leave the previously published
catalogue exactly where it was, and must not touch the legal-work workbook feed,
which is a separate source with a separate state row.
"""

from __future__ import annotations

import pytest

from apps.audit.models import AuditEvent
from apps.core.feeds import FeedResult
from apps.core.public_http import PublicFetchError
from apps.legal_work.audit_actions import LegalWorkAudit
from apps.legal_work.models import (
    CurrentTopicFeedState,
    CurrentTopicItem,
    CurrentTopicSnapshot,
    SnapshotImmutable,
    SyncResult,
)

from .current_topic_factory import DETAIL_PREFIX, LISTING_PATH, FakeSite, card, detail, listing

pytestmark = pytest.mark.django_db


def site(*slugs: str) -> FakeSite:
    pages = {LISTING_PATH: listing(*(card(slug, f"Teema {slug}") for slug in slugs))}
    for slug in slugs:
        pages[f"{DETAIL_PREFIX}{slug}"] = detail(title=f"Teema {slug}")
    return FakeSite(pages)


def test_a_successful_collection_publishes_one_current_snapshot(publish_current_topics):
    outcome = publish_current_topics(site("alpha", "beeta"))

    assert outcome.result == FeedResult.IMPORTED
    snapshot = CurrentTopicSnapshot.objects.get(is_current=True)
    assert snapshot.item_count == 2
    assert snapshot.items.count() == 2
    assert snapshot.import_run.status == "succeeded"


def test_the_registered_artifact_is_metadata_only(publish_current_topics):
    publish_current_topics(site("alpha"))

    artifact = CurrentTopicSnapshot.objects.get(is_current=True).artifact
    assert artifact.sha256
    assert artifact.mime_type == "application/json"
    assert not artifact.file
    assert artifact.external_reference == "koda-public:current-topics"


def test_no_raw_html_is_stored_anywhere(publish_current_topics):
    publish_current_topics(site("alpha"))

    for item in CurrentTopicItem.objects.all():
        for value in (item.title, item.listing_summary, item.body_text):
            assert "<" not in value
            assert "script" not in value.lower()


def test_an_unchanged_listing_publishes_no_second_snapshot(publish_current_topics):
    publish_current_topics(site("alpha"))
    outcome = publish_current_topics(site("alpha"))

    assert outcome.result == FeedResult.UNCHANGED
    assert CurrentTopicSnapshot.objects.count() == 1


def test_changed_content_retires_the_previous_snapshot(publish_current_topics):
    publish_current_topics(site("alpha"))
    outcome = publish_current_topics(site("alpha", "beeta"))

    assert outcome.result == FeedResult.IMPORTED
    assert CurrentTopicSnapshot.objects.count() == 2
    assert CurrentTopicSnapshot.objects.filter(is_current=True).count() == 1
    assert CurrentTopicSnapshot.objects.get(is_current=True).item_count == 2


def test_a_dry_run_publishes_nothing(publish_current_topics):
    outcome = publish_current_topics(site("alpha"), dry_run=True)

    assert outcome.dry_run is True
    assert not CurrentTopicSnapshot.objects.exists()


def test_a_failed_collection_keeps_the_last_good_catalogue(publish_current_topics):
    publish_current_topics(site("alpha"))
    good = CurrentTopicSnapshot.objects.get(is_current=True)

    broken = FakeSite({}, errors={LISTING_PATH: PublicFetchError("Allikas vastas koodiga 503.")})
    outcome = publish_current_topics(broken)

    assert outcome.result == FeedResult.FAILED
    assert CurrentTopicSnapshot.objects.get(is_current=True) == good
    state = CurrentTopicFeedState.objects.get()
    assert state.last_result == SyncResult.FAILED
    assert state.current_snapshot == good


def test_a_failure_summary_carries_no_page_content(publish_current_topics):
    broken = FakeSite({}, errors={LISTING_PATH: PublicFetchError("Allikas vastas koodiga 503.")})
    publish_current_topics(broken)

    state = CurrentTopicFeedState.objects.get()
    assert "koda.ee" not in state.last_error_summary
    assert "http" not in state.last_error_summary


def test_a_koda_failure_does_not_touch_the_workbook_feed(publish_current_topics, imported_snapshot):
    from apps.legal_work.models import LegalWorkFeedState, LegalWorkSnapshot

    broken = FakeSite({}, errors={LISTING_PATH: PublicFetchError("Allikas vastas koodiga 503.")})
    publish_current_topics(broken)

    assert LegalWorkSnapshot.objects.get(is_current=True) == imported_snapshot
    assert not LegalWorkFeedState.objects.filter(last_result=SyncResult.FAILED).exists()


def test_the_two_feeds_use_separate_sources(publish_current_topics, legal_work_source):
    publish_current_topics(site("alpha"))

    topic_source = CurrentTopicSnapshot.objects.get(is_current=True).source
    assert topic_source != legal_work_source
    assert topic_source.slug == "koda-public-current-topics"


# -- immutability -----------------------------------------------------------


def test_a_published_snapshot_refuses_every_change_but_is_current(publish_current_topics):
    publish_current_topics(site("alpha"))
    snapshot = CurrentTopicSnapshot.objects.get(is_current=True)

    snapshot.item_count = 99
    with pytest.raises(SnapshotImmutable):
        snapshot.save()

    snapshot.refresh_from_db()
    snapshot.is_current = False
    snapshot.save(update_fields=["is_current"])


def test_a_published_row_refuses_every_change(publish_current_topics):
    publish_current_topics(site("alpha"))
    item = CurrentTopicItem.objects.first()

    item.title = "Midagi muud"
    with pytest.raises(SnapshotImmutable):
        item.save()


# -- audit ------------------------------------------------------------------


def test_a_publication_is_audited_without_source_content(publish_current_topics):
    publish_current_topics(site("alpha"))

    event = AuditEvent.objects.get(action=LegalWorkAudit.CURRENT_TOPIC_SNAPSHOT_IMPORTED)
    summary = event.change_summary
    assert summary["item_count"] == 1
    assert set(summary) == {
        "source",
        "sha256",
        "item_count",
        "pages_fetched",
        "details_fetched",
        "snapshot_id",
    }
    serialised = str(summary)
    assert "http" not in serialised
    assert "Teema" not in serialised


def test_an_unchanged_check_is_audited(publish_current_topics):
    publish_current_topics(site("alpha"))
    publish_current_topics(site("alpha"))

    assert AuditEvent.objects.filter(action=LegalWorkAudit.CURRENT_TOPIC_SYNC_UNCHANGED).exists()


def test_a_failure_is_audited(publish_current_topics):
    broken = FakeSite({}, errors={LISTING_PATH: PublicFetchError("Allikas vastas koodiga 503.")})
    publish_current_topics(broken)

    event = AuditEvent.objects.get(action=LegalWorkAudit.CURRENT_TOPIC_SYNC_FAILED)
    assert "http" not in str(event.change_summary)
