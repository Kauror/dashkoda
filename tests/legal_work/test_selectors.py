"""Selector rules: current snapshot only, and honest ordering."""

import datetime as dt

import pytest

from apps.legal_work.importer import import_artifact
from apps.legal_work.models import SyncResult
from apps.legal_work.selectors import (
    count_received_since,
    count_sent_since,
    get_current_snapshot,
    get_latest_sent_items,
    get_legal_work_summary,
    get_newest_received_items,
    get_open_items,
    get_upcoming_deadlines,
)
from apps.legal_work.sync import get_feed_state

from .workbook_factory import synthetic_row

pytestmark = pytest.mark.django_db


def test_open_items_exclude_closed_records(imported_snapshot):
    topics = [item.topic for item in get_open_items(imported_snapshot)]

    assert "Sünteetiline avatud teema" in topics
    assert "Sünteetiline saadetud teema" not in topics


def test_latest_sent_is_ordered_newest_first(make_workbook, register_workbook):
    rows = [
        synthetic_row(
            record_id="SYN-OLD",
            topic="Vanem saadetud",
            sent_date=dt.date(2099, 1, 5),
            sent_status="sent",
            is_open=False,
            source_row=2,
        ),
        synthetic_row(
            record_id="SYN-NEW",
            topic="Uuem saadetud",
            sent_date=dt.date(2099, 2, 5),
            sent_status="sent",
            is_open=False,
            source_row=3,
        ),
    ]
    snapshot = import_artifact(register_workbook(make_workbook(rows=rows)), dry_run=False).snapshot

    topics = [item.topic for item in get_latest_sent_items(snapshot)]

    assert topics == ["Uuem saadetud", "Vanem saadetud"]


def test_not_sent_records_never_appear_as_sent(imported_snapshot):
    topics = [item.topic for item in get_latest_sent_items(imported_snapshot)]

    assert "Sünteetiline saatmata teema" not in topics


def test_future_received_dates_are_excluded_from_newest_received(
    make_workbook, register_workbook, frozen_today
):
    """A future date would otherwise sit permanently at the top of the list."""
    rows = [
        synthetic_row(
            record_id="SYN-TODAY",
            topic="Eilne teema",
            received_date=dt.date(2099, 3, 1),
            source_row=2,
        ),
        synthetic_row(
            record_id="SYN-FUTURE",
            topic="Tuleviku teema",
            received_date=dt.date(2099, 12, 31),
            warning_codes="received_date_in_future",
            source_row=3,
        ),
    ]
    snapshot = import_artifact(register_workbook(make_workbook(rows=rows)), dry_run=False).snapshot
    frozen_today(dt.date(2099, 3, 2))

    topics = [item.topic for item in get_newest_received_items(snapshot)]

    assert topics == ["Eilne teema"]
    # The record itself is still imported; only the preview excludes it.
    assert snapshot.items.filter(record_id="SYN-FUTURE").exists()


def test_recent_lists_are_limited(make_workbook, register_workbook):
    today = dt.date.today()
    rows = [
        synthetic_row(
            record_id=f"SYN-{index:03d}",
            topic=f"Teema {index}",
            # All in the past, so the future-exclusion rule does not apply here.
            received_date=today - dt.timedelta(days=index + 1),
            source_row=index + 2,
        )
        for index in range(20)
    ]
    snapshot = import_artifact(register_workbook(make_workbook(rows=rows)), dry_run=False).snapshot

    assert len(get_newest_received_items(snapshot, limit=15)) == 15


def test_only_the_current_snapshot_is_read(make_workbook, register_workbook):
    first = import_artifact(
        register_workbook(make_workbook(rows=[synthetic_row(record_id="SYN-OLD", topic="Vana")])),
        dry_run=False,
    ).snapshot
    import_artifact(
        register_workbook(make_workbook(rows=[synthetic_row(record_id="SYN-NEW", topic="Uus")])),
        dry_run=False,
    )

    current = get_current_snapshot()
    topics = [item.topic for item in get_open_items()]

    assert current.pk != first.pk
    assert topics == ["Uus"]


def test_without_a_snapshot_everything_is_empty(db):
    assert get_current_snapshot() is None
    assert list(get_open_items()) == []
    assert list(get_latest_sent_items()) == []
    assert list(get_newest_received_items()) == []


# -- activity window ----------------------------------------------------


def test_activity_counts_respect_both_ends_of_the_window(make_workbook, register_workbook):
    """The upper bound matters as much as the lower one.

    The workbook is known to carry the occasional future received date, and
    counting one would make the window report more arrivals than arrived.
    """
    today = dt.date.today()
    rows = [
        synthetic_row(
            record_id="SYN-IN",
            topic="Aknas",
            received_date=today - dt.timedelta(days=5),
            sent_date=today - dt.timedelta(days=4),
            sent_status="sent",
            is_open=False,
            source_row=2,
        ),
        synthetic_row(
            record_id="SYN-OLD",
            topic="Aknast väljas",
            received_date=today - dt.timedelta(days=200),
            source_row=3,
        ),
        synthetic_row(
            record_id="SYN-FUTURE",
            topic="Tulevikus",
            received_date=today + dt.timedelta(days=5),
            warning_codes="received_date_in_future",
            source_row=4,
        ),
    ]
    snapshot = import_artifact(register_workbook(make_workbook(rows=rows)), dry_run=False).snapshot
    window_start = today - dt.timedelta(days=30)

    assert count_received_since(snapshot, window_start) == 1
    assert count_sent_since(snapshot, window_start) == 1


def test_upcoming_deadlines_cover_only_open_topics_still_inside_the_horizon(
    make_workbook, register_workbook
):
    today = dt.date.today()
    rows = [
        synthetic_row(
            record_id="SYN-SOON",
            topic="Kolme päeva pärast",
            deadline_date=today + dt.timedelta(days=3),
            is_open=True,
            source_row=2,
        ),
        synthetic_row(
            record_id="SYN-PAST",
            topic="Eile möödas",
            deadline_date=today - dt.timedelta(days=1),
            is_open=True,
            source_row=3,
        ),
        synthetic_row(
            record_id="SYN-FAR",
            topic="Kaugel tulevikus",
            deadline_date=today + dt.timedelta(days=400),
            is_open=True,
            source_row=4,
        ),
        synthetic_row(
            record_id="SYN-CLOSED",
            topic="Juba lõpetatud",
            deadline_date=today + dt.timedelta(days=2),
            is_open=False,
            sent_status="not_sent",
            source_row=5,
        ),
    ]
    snapshot = import_artifact(register_workbook(make_workbook(rows=rows)), dry_run=False).snapshot

    deadlines = get_upcoming_deadlines(snapshot)

    assert [deadline.item.topic for deadline in deadlines] == ["Kolme päeva pärast"]
    assert deadlines[0].days_remaining == 3
    assert deadlines[0].is_urgent is True
    assert deadlines[0].variant == "danger"
    assert deadlines[0].remaining_label == "3 päeva"


def test_a_deadline_further_out_is_marked_less_urgent(make_workbook, register_workbook):
    today = dt.date.today()
    rows = [
        synthetic_row(
            record_id="SYN-WEEK",
            topic="Nädala pärast",
            deadline_date=today + dt.timedelta(days=8),
            is_open=True,
            source_row=2,
        ),
        synthetic_row(
            record_id="SYN-LATER",
            topic="Kahe nädala pärast",
            deadline_date=today + dt.timedelta(days=15),
            is_open=True,
            source_row=3,
        ),
    ]
    snapshot = import_artifact(register_workbook(make_workbook(rows=rows)), dry_run=False).snapshot

    by_topic = {deadline.item.topic: deadline for deadline in get_upcoming_deadlines(snapshot)}

    assert by_topic["Nädala pärast"].variant == "warning"
    assert by_topic["Kahe nädala pärast"].variant == "info"
    assert by_topic["Nädala pärast"].is_urgent is False


def test_without_a_snapshot_there_are_no_deadlines_and_no_counts(db):
    today = dt.date.today()

    assert get_upcoming_deadlines() == ()
    assert count_received_since(None, today - dt.timedelta(days=30)) == 0


# -- summary ------------------------------------------------------------


def test_summary_without_data_reports_never_connected(db):
    summary = get_legal_work_summary()

    assert summary.has_data is False
    assert summary.state_label == "Ühendamata"
    assert summary.last_result == SyncResult.NEVER_RUN


def test_summary_with_data_reports_the_workbook_reporting_date(imported_snapshot):
    summary = get_legal_work_summary()

    assert summary.has_data is True
    assert summary.reporting_date == imported_snapshot.reporting_date
    assert summary.open_count == imported_snapshot.open_record_count


def test_summary_marks_data_stale_after_a_failed_check(imported_snapshot, legal_work_source):
    state = get_feed_state(legal_work_source)
    state.last_result = SyncResult.FAILED
    state.last_error_summary = "Sünteetiline viga."
    state.save()

    summary = get_legal_work_summary()

    assert summary.last_sync_failed is True
    assert summary.is_stale_after_failure is True
    assert summary.state_label == "Vananenud"
    assert summary.state_variant == "warning"
