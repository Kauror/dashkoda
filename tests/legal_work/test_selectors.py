"""Selector rules: current snapshot only, and honest ordering."""

import datetime as dt

import pytest

from apps.legal_work.importer import import_artifact
from apps.legal_work.models import SyncResult
from apps.legal_work.selectors import (
    get_current_snapshot,
    get_latest_sent_items,
    get_legal_work_summary,
    get_newest_received_items,
    get_open_items,
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
