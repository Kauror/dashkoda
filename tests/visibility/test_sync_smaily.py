"""Publishing a Smaily reading: revisions, idempotency and the feed contract.

The transport is always faked. **No Smaily credential, subdomain or response
body exists anywhere in this file**; the service is exercised through a
collector double, and the one test that touches configuration asserts what
happens when it is absent.

What is pinned down here is the behaviour the newsletter history depends on: the
same reading twice publishes once, a list that moves publishes a second revision
without rewriting the first, a withheld newsletter publishes no figure at all,
and a failed run never disturbs what is already published.
"""

from __future__ import annotations

import datetime as dt
import json
from io import StringIO

import pytest
from django.core.management import call_command

from apps.audit.models import AuditAction, AuditEvent
from apps.core.feeds import FeedResult
from apps.sources.models import ImportRun, ImportStatus
from apps.visibility.models import (
    CollectionMethod,
    SmailyAudienceSnapshot,
    SmailyFeedState,
    SmailySegmentDaily,
    VisibilityMetric,
    VisibilityObservation,
)
from apps.visibility.smaily import (
    SegmentReading,
    SegmentRow,
    SmailyResponseError,
)
from apps.visibility.smaily_sync import ReadingAction, synchronize_smaily

pytestmark = pytest.mark.django_db

DAY = dt.date(2026, 7, 1)
LATER = dt.date(2026, 7, 2)


def rows(eteataja_members=100, eteataja_others=200, enews=30, evestnik=40, extra=()):
    return (
        SegmentRow(2690, "E-teataja list", eteataja_members),
        SegmentRow(2691, "E-teataja list mitteliikmed", eteataja_others),
        SegmentRow(2711, "E-News list", enews),
        SegmentRow(2692, "E-vestnik list - liikmed ja mitteliikmed koos", evestnik),
        *extra,
    )


class FakeCollector:
    """Answers with a fixed set of segment rows.

    Same signature as `SmailyApiClient.collect_segments`, so nothing about the
    service knows it is talking to a double.
    """

    def __init__(self, segments=None, *, error=None):
        self.segments = rows() if segments is None else segments
        self.error = error
        self.calls = []

    def collect_segments(self, *, observed_on=None):
        self.calls.append(observed_on)
        if self.error is not None:
            raise self.error
        return SegmentReading(observed_on=observed_on, segments=self.segments).validate()


def sync(**kwargs):
    kwargs.setdefault("observed_on", DAY)
    kwargs.setdefault("collector", FakeCollector())
    return synchronize_smaily(**kwargs)


def current_value(metric, day=DAY):
    observation = VisibilityObservation.objects.filter(
        metric=metric, observation_date=day, is_current_for_date=True
    ).first()
    return observation.value if observation is not None else None


# -- the first reading ------------------------------------------------------


def test_a_first_reading_publishes_a_snapshot_and_its_segments():
    outcome = sync()

    assert outcome.result == FeedResult.IMPORTED
    snapshot = SmailyAudienceSnapshot.objects.get()
    assert snapshot.observed_on == DAY
    assert snapshot.revision == 1
    assert snapshot.is_current_for_date is True
    assert snapshot.supersedes is None
    assert SmailySegmentDaily.objects.filter(snapshot=snapshot).count() == 4


def test_every_segment_is_stored_including_the_unmapped_ones():
    """A one-off send audience has no metric, but it still gets history."""
    collector = FakeCollector(rows(extra=(SegmentRow(3090, "09.06.26 emta", 672),)))
    sync(collector=collector)
    assert SmailySegmentDaily.objects.count() == 5
    assert SmailySegmentDaily.objects.filter(segment_id=3090).exists()


def test_the_newsletter_totals_reach_the_shared_observation_table():
    """Every existing reader asks `VisibilityObservation`, so the collector must
    write there too — marked automatic, and with no manual batch."""
    sync()

    assert current_value(VisibilityMetric.NEWSLETTER_ETEATAJA) == 300
    assert current_value(VisibilityMetric.NEWSLETTER_ENEWS) == 30
    assert current_value(VisibilityMetric.NEWSLETTER_EVESTNIK) == 40

    observation = VisibilityObservation.objects.get(metric=VisibilityMetric.NEWSLETTER_ENEWS)
    assert observation.collection_method == CollectionMethod.AUTOMATIC
    assert observation.batch is None
    assert observation.published_at is not None


def test_the_import_run_completes_and_the_artifact_holds_no_secret():
    sync()
    run = ImportRun.objects.get()
    assert run.status == ImportStatus.SUCCEEDED
    reference = run.artifact.external_reference
    assert reference == f"smaily:list-api:{DAY.isoformat()}"
    assert "sendsmaily" not in reference
    assert "@" not in reference


def test_the_feed_state_records_the_reading():
    sync()
    state = SmailyFeedState.objects.get()
    assert state.last_result == FeedResult.IMPORTED
    assert state.last_error_summary == ""
    assert state.last_period_end == DAY
    assert state.current_snapshot is not None


# -- reading the same thing twice -------------------------------------------


def test_an_unchanged_reading_publishes_nothing_the_second_time():
    sync()
    outcome = sync()

    assert outcome.result == FeedResult.UNCHANGED
    assert outcome.extra["action"] == ReadingAction.UNCHANGED
    assert SmailyAudienceSnapshot.objects.count() == 1
    assert SmailySegmentDaily.objects.count() == 4
    assert AuditEvent.objects.filter(action=AuditAction.SMAILY_SYNC_UNCHANGED).exists()


def test_an_unchanged_reading_does_not_supersede_the_observation():
    """A list that did not move must not fill the history with corrections."""
    sync()
    sync()
    assert (
        VisibilityObservation.objects.filter(metric=VisibilityMetric.NEWSLETTER_ENEWS).count() == 1
    )


# -- a list that moves ------------------------------------------------------


def test_a_changed_reading_on_the_same_day_publishes_a_second_revision():
    sync()
    outcome = sync(collector=FakeCollector(rows(enews=31)))

    assert outcome.result == FeedResult.IMPORTED
    assert outcome.extra["action"] == ReadingAction.REVISED

    snapshots = SmailyAudienceSnapshot.objects.order_by("revision")
    assert [s.revision for s in snapshots] == [1, 2]
    assert snapshots[1].supersedes_id == snapshots[0].pk
    # Exactly one current revision, and the first one keeps its figures.
    assert [s.is_current_for_date for s in snapshots] == [False, True]
    assert SmailySegmentDaily.objects.get(snapshot=snapshots[0], segment_id=2711).subscribers == 30


def test_a_correction_supersedes_the_observation_without_rewriting_it():
    sync()
    sync(collector=FakeCollector(rows(enews=31)))

    observations = VisibilityObservation.objects.filter(
        metric=VisibilityMetric.NEWSLETTER_ENEWS
    ).order_by("id")
    assert [o.value for o in observations] == [30, 31]
    assert [o.is_current_for_date for o in observations] == [False, True]
    assert observations[1].supersedes_id == observations[0].pk


def test_a_later_day_is_its_own_reading_and_does_not_supersede_the_earlier_one():
    sync()
    sync(observed_on=LATER, collector=FakeCollector(rows(enews=31)))

    assert SmailyAudienceSnapshot.objects.count() == 2
    assert SmailyAudienceSnapshot.objects.filter(is_current_for_date=True).count() == 2
    assert current_value(VisibilityMetric.NEWSLETTER_ENEWS, DAY) == 30
    assert current_value(VisibilityMetric.NEWSLETTER_ENEWS, LATER) == 31


# -- withholding ------------------------------------------------------------


def test_a_missing_segment_withholds_that_newsletter_and_publishes_the_rest():
    """The others still publish. Absent is not zero, and not last week's number
    wearing today's date either."""
    partial = tuple(row for row in rows() if row.segment_id != 2691)
    outcome = sync(collector=FakeCollector(partial))

    assert outcome.result == FeedResult.IMPORTED
    assert outcome.extra["newsletters_withheld"] == 1
    assert VisibilityMetric.NEWSLETTER_ETEATAJA in outcome.extra["withheld"]

    assert current_value(VisibilityMetric.NEWSLETTER_ETEATAJA) is None
    assert current_value(VisibilityMetric.NEWSLETTER_ENEWS) == 30


def test_a_withheld_newsletter_never_publishes_a_zero():
    sync(collector=FakeCollector(()))
    assert VisibilityObservation.objects.count() == 0
    # The segment rows are still stored: the reading happened, it just mapped
    # onto nothing.
    assert SmailyAudienceSnapshot.objects.count() == 1


def test_a_renamed_segment_withholds_rather_than_reporting_another_list():
    renamed = (
        SegmentRow(2690, "E-teataja list", 100),
        SegmentRow(2691, "E-teataja list mitteliikmed", 200),
        SegmentRow(2711, "Jõulukampaania 2026", 5000),
        SegmentRow(2692, "E-vestnik list - liikmed ja mitteliikmed koos", 40),
    )
    sync(collector=FakeCollector(renamed))
    assert current_value(VisibilityMetric.NEWSLETTER_ENEWS) is None
    assert current_value(VisibilityMetric.NEWSLETTER_ETEATAJA) == 300


# -- failure ----------------------------------------------------------------


def test_a_failure_leaves_the_last_good_reading_published():
    sync()
    outcome = sync(collector=FakeCollector(error=SmailyResponseError("Smaily vastas ootamatult.")))

    assert outcome.result == FeedResult.FAILED
    assert SmailyAudienceSnapshot.objects.count() == 1
    assert current_value(VisibilityMetric.NEWSLETTER_ENEWS) == 30

    state = SmailyFeedState.objects.get()
    assert state.last_result == FeedResult.FAILED
    assert state.current_snapshot is not None


def test_a_transport_failure_summary_names_no_host_or_credential():
    import requests

    sync(
        collector=FakeCollector(
            error=requests.ConnectionError("could not reach example.sendsmaily.net")
        )
    )
    summary = SmailyFeedState.objects.get().last_error_summary
    assert "sendsmaily" not in summary
    assert "example" not in summary
    assert AuditEvent.objects.filter(action=AuditAction.SMAILY_SYNC_FAILED).exists()


def test_missing_configuration_names_the_settings_and_publishes_nothing():
    outcome = synchronize_smaily(
        observed_on=DAY,
        collector=None,
    )
    # No credential is configured in the test settings, so the real client
    # refuses to be built and the run fails with a sentence naming what is
    # missing rather than with a traceback.
    assert outcome.result == FeedResult.FAILED
    assert "SMAILY_" in SmailyFeedState.objects.get().last_error_summary
    assert SmailyAudienceSnapshot.objects.count() == 0


# -- immutability -----------------------------------------------------------


def test_a_published_snapshot_cannot_be_rewritten_or_deleted():
    from apps.visibility.models import VisibilityRecordImmutable

    sync()
    snapshot = SmailyAudienceSnapshot.objects.get()
    snapshot.checksum = "0" * 64
    with pytest.raises(VisibilityRecordImmutable):
        snapshot.save()
    with pytest.raises(VisibilityRecordImmutable):
        snapshot.delete()


# -- dry run and the command ------------------------------------------------


def test_a_dry_run_publishes_nothing():
    outcome = sync(dry_run=True)
    assert outcome.dry_run is True
    assert SmailyAudienceSnapshot.objects.count() == 0
    assert VisibilityObservation.objects.count() == 0


def test_the_command_emits_json_carrying_no_secret():
    stdout = StringIO()
    call_command("sync_smaily", "--dry-run", "--json", stdout=stdout)
    payload = json.loads(stdout.getvalue())

    assert set(payload) == {
        "result",
        "detail",
        "dry_run",
        "observed_on",
        "action",
        "segments_read",
        "segment_rows_written",
        "newsletters_available",
        "newsletters_withheld",
        "withheld",
        "api_requests",
        "api_retries",
    }
    body = stdout.getvalue()
    assert "password" not in body.lower()
    assert "sendsmaily" not in body
