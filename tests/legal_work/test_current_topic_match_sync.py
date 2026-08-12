"""Publishing a match run: verified, atomic, immutable and shadow-only.

The matcher's own rules are covered in `test_current_topic_matching.py`. What
these cases are about is everything around it — that every open record gets
exactly one decision, that rows reference the exact snapshots they were computed
from, that a repeat run recomputes nothing, and above all that no match result
ever reaches a `LegalWorkItem` or a viewer page.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.audit.models import AuditEvent
from apps.legal_work.audit_actions import LegalWorkAudit
from apps.legal_work.current_topic_match_sync import run_current_topic_matching
from apps.legal_work.current_topic_matching import MATCHER_VERSION
from apps.legal_work.models import (
    CurrentTopicSnapshot,
    LegalCurrentTopicMatch,
    LegalCurrentTopicMatchSnapshot,
    LegalWorkItem,
    LegalWorkSnapshot,
    MatchDecision,
    SnapshotImmutable,
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
def ready(imported_snapshot, publish_current_topics):
    """A current legal snapshot and a current catalogue, both published."""
    publish_current_topics(catalogue("alpha", "beeta"))
    return imported_snapshot


# -- publication ------------------------------------------------------------


def test_every_open_record_receives_exactly_one_decision(ready):
    report = run_current_topic_matching()

    assert report.result == SyncResult.IMPORTED
    snapshot = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)
    open_ids = set(
        LegalWorkItem.objects.filter(snapshot=ready, is_open=True).values_list("pk", flat=True)
    )
    decided = set(snapshot.matches.values_list("legal_item_id", flat=True))

    assert decided == open_ids
    assert snapshot.matches.count() == len(open_ids)
    assert snapshot.legal_item_count == len(open_ids)


def test_closed_records_are_outside_this_phase(ready):
    run_current_topic_matching()

    snapshot = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)
    closed_ids = set(
        LegalWorkItem.objects.filter(snapshot=ready, is_open=False).values_list("pk", flat=True)
    )

    assert closed_ids
    assert not snapshot.matches.filter(legal_item_id__in=closed_ids).exists()


def test_the_declared_counts_equal_the_rows(ready):
    run_current_topic_matching()

    snapshot = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)
    for decision, declared in (
        (MatchDecision.MATCHED, snapshot.matched_count),
        (MatchDecision.AMBIGUOUS, snapshot.ambiguous_count),
        (MatchDecision.UNMATCHED, snapshot.unmatched_count),
    ):
        assert snapshot.matches.filter(decision=decision).count() == declared
    assert (
        snapshot.matched_count + snapshot.ambiguous_count + snapshot.unmatched_count
        == snapshot.legal_item_count
    )


def test_rows_reference_the_exact_source_snapshots(ready):
    run_current_topic_matching()

    snapshot = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)
    topic_snapshot = CurrentTopicSnapshot.objects.get(is_current=True)

    assert snapshot.legal_snapshot == ready
    assert snapshot.current_topic_snapshot == topic_snapshot
    for match in snapshot.matches.select_related("legal_item", "best_candidate"):
        assert match.legal_item.snapshot_id == ready.pk
        if match.best_candidate is not None:
            assert match.best_candidate.snapshot_id == topic_snapshot.pk


def test_a_dry_run_publishes_nothing(ready):
    report = run_current_topic_matching(dry_run=True)

    assert report.dry_run is True
    assert report.legal_item_count > 0
    assert not LegalCurrentTopicMatchSnapshot.objects.exists()
    assert not LegalCurrentTopicMatch.objects.exists()


# -- run identity -----------------------------------------------------------


def test_identical_inputs_and_version_report_unchanged(ready):
    first = run_current_topic_matching()
    second = run_current_topic_matching()

    assert second.result == SyncResult.UNCHANGED
    assert second.snapshot_id == first.snapshot_id
    assert LegalCurrentTopicMatchSnapshot.objects.count() == 1


def test_a_new_matcher_version_produces_a_new_snapshot(ready, monkeypatch):
    first = run_current_topic_matching()

    monkeypatch.setattr("apps.legal_work.current_topic_match_sync.MATCHER_VERSION", "9.9-synthetic")
    second = run_current_topic_matching()

    assert second.result == SyncResult.IMPORTED
    assert second.snapshot_id != first.snapshot_id
    assert LegalCurrentTopicMatchSnapshot.objects.count() == 2
    assert LegalCurrentTopicMatchSnapshot.objects.filter(is_current=True).count() == 1
    assert LegalCurrentTopicMatchSnapshot.objects.get(is_current=True).pk == second.snapshot_id


def test_a_new_catalogue_snapshot_produces_a_new_match_snapshot(ready, publish_current_topics):
    first = run_current_topic_matching()

    publish_current_topics(catalogue("alpha", "beeta", "gamma"))
    second = run_current_topic_matching()

    assert second.result == SyncResult.IMPORTED
    assert second.snapshot_id != first.snapshot_id


def test_the_same_inputs_cannot_be_published_twice(ready):
    run_current_topic_matching()
    snapshot = LegalCurrentTopicMatchSnapshot.objects.get()

    duplicate = LegalCurrentTopicMatchSnapshot(
        legal_snapshot=snapshot.legal_snapshot,
        current_topic_snapshot=snapshot.current_topic_snapshot,
        matcher_version=snapshot.matcher_version,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        duplicate.save()


def test_two_current_match_snapshots_cannot_coexist(ready, monkeypatch):
    run_current_topic_matching()
    monkeypatch.setattr("apps.legal_work.current_topic_match_sync.MATCHER_VERSION", "9.9-synthetic")
    run_current_topic_matching()

    stale = LegalCurrentTopicMatchSnapshot.objects.filter(is_current=False).get()
    stale.is_current = True
    with pytest.raises(IntegrityError), transaction.atomic():
        stale.save(update_fields=["is_current"])


# -- failure safety ---------------------------------------------------------


def test_a_run_without_a_current_legal_snapshot_fails_cleanly(publish_current_topics):
    publish_current_topics(catalogue("alpha"))

    report = run_current_topic_matching()

    assert report.result == SyncResult.FAILED
    assert not LegalCurrentTopicMatchSnapshot.objects.exists()


def test_a_run_without_a_current_catalogue_fails_cleanly(imported_snapshot):
    report = run_current_topic_matching()

    assert report.result == SyncResult.FAILED
    assert not LegalCurrentTopicMatchSnapshot.objects.exists()


def test_a_failed_run_keeps_the_last_good_match_snapshot(ready, monkeypatch):
    run_current_topic_matching()
    good = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)

    def explode(*args, **kwargs):
        raise RuntimeError("synthetic matcher failure")

    monkeypatch.setattr("apps.legal_work.current_topic_match_sync.MATCHER_VERSION", "9.9-synth")
    monkeypatch.setattr("apps.legal_work.current_topic_match_sync.match_all", explode)

    report = run_current_topic_matching()

    assert report.result == SyncResult.FAILED
    assert LegalCurrentTopicMatchSnapshot.objects.get(is_current=True) == good
    assert LegalCurrentTopicMatchSnapshot.objects.count() == 1


def test_a_verification_failure_writes_nothing(ready, monkeypatch):
    """A decision naming a record outside the snapshot must abort the run."""
    from apps.legal_work import current_topic_match_sync as module

    real = module.match_all

    def drop_one(legal_items, candidate_items):
        return real(legal_items, candidate_items)[:-1]

    monkeypatch.setattr(module, "match_all", drop_one)

    report = run_current_topic_matching()

    assert report.result == SyncResult.FAILED
    assert "otsuseta" in report.detail
    assert not LegalCurrentTopicMatchSnapshot.objects.exists()
    assert not LegalCurrentTopicMatch.objects.exists()


# -- immutability and isolation --------------------------------------------


def test_a_published_match_snapshot_refuses_every_change_but_is_current(ready):
    run_current_topic_matching()
    snapshot = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)

    snapshot.matched_count = 99
    with pytest.raises(SnapshotImmutable):
        snapshot.save()


def test_a_published_match_row_refuses_every_change(ready):
    run_current_topic_matching()
    match = LegalCurrentTopicMatch.objects.first()

    match.decision = MatchDecision.MATCHED
    with pytest.raises(SnapshotImmutable):
        match.save()


def test_matching_does_not_change_a_single_imported_legal_row(ready):
    before = {
        item.pk: (item.topic, item.record_id, item.is_open, item.sent_status)
        for item in LegalWorkItem.objects.filter(snapshot=ready)
    }
    legal_before = LegalWorkSnapshot.objects.get(is_current=True).pk

    run_current_topic_matching()

    after = {
        item.pk: (item.topic, item.record_id, item.is_open, item.sent_status)
        for item in LegalWorkItem.objects.filter(snapshot=ready)
    }
    assert after == before
    assert LegalWorkSnapshot.objects.get(is_current=True).pk == legal_before


def test_legal_work_items_expose_no_reverse_accessor_to_matches(ready):
    """`related_name="+"` keeps a selector from decorating workbook rows."""
    run_current_topic_matching()
    item = LegalWorkItem.objects.filter(snapshot=ready, is_open=True).first()

    assert not hasattr(item, "legalcurrenttopicmatch_set")
    assert not any(field.name.startswith("legalcurrenttopic") for field in item._meta.get_fields())


def test_no_legal_work_item_gains_a_public_url_field():
    assert "public_url" not in {field.name for field in LegalWorkItem._meta.get_fields()}


# -- audit ------------------------------------------------------------------


def test_a_match_run_is_audited_without_topic_or_url(ready):
    run_current_topic_matching()

    event = AuditEvent.objects.get(action=LegalWorkAudit.CURRENT_TOPIC_MATCH_GENERATED)
    summary = event.change_summary
    assert summary["matcher_version"] == MATCHER_VERSION
    assert set(summary) == {
        "snapshot_id",
        "legal_snapshot_id",
        "current_topic_snapshot_id",
        "matcher_version",
        "legal_item_count",
        "current_topic_count",
        "matched_count",
        "ambiguous_count",
        "unmatched_count",
    }
    serialised = str(summary)
    assert "http" not in serialised
    assert "Teema" not in serialised
    assert "Sünteetiline" not in serialised


def test_an_unchanged_run_is_audited(ready):
    run_current_topic_matching()
    run_current_topic_matching()

    assert AuditEvent.objects.filter(action=LegalWorkAudit.CURRENT_TOPIC_MATCH_UNCHANGED).exists()


def test_a_failed_run_is_audited(imported_snapshot):
    run_current_topic_matching()

    event = AuditEvent.objects.get(action=LegalWorkAudit.CURRENT_TOPIC_MATCH_FAILED)
    assert event.change_summary["matcher_version"] == MATCHER_VERSION
