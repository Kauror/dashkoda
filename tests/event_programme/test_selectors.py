"""What the event-programme selectors read, and what they refuse to read.

Everything goes through the real parser and importer: no test writes an
`EventProgrammeItem` row directly, so the derived calendar fields, the controlled
vocabularies and the immutable publication are all exercised as production
produces them.

The rules being guarded are the ones the dashboard depends on: one current
snapshot answers everything, a failed later check leaves the previous snapshot
readable, and every period question is asked of the event's own dates rather than
of the annual sheet the operational workbook held the row on.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.event_programme.models import EventProgrammeSnapshot, EventStatus, SyncResult
from apps.event_programme.public_download import PublicDownloadError
from apps.event_programme.selectors import (
    LINK_LINKED,
    LINK_UNLINKED,
    REVIEW_CLEAR,
    REVIEW_REQUIRED,
    ProgrammeFilters,
    count_events_for_year,
    count_events_started_within,
    count_events_starting_within,
    count_linked_events,
    count_review_required_events,
    count_unknown_date_events,
    get_current_event_programme_snapshot,
    get_event_programme_filter_options,
    get_event_programme_summary,
    get_filtered_event_programme_items,
    get_upcoming_programme_events,
)
from apps.event_programme.sync import synchronize_public_workbook

from .conftest import FakeDownloader, programme_years, synthetic_programme
from .workbook_factory import default_control, synthetic_row

pytestmark = pytest.mark.django_db

TOTAL_ROWS = 9

ALL_YEARS = ProgrammeFilters(year=None)


@pytest.fixture
def programme(publish_programme):
    """One published snapshot holding the whole synthetic programme."""
    publish_programme(rows=synthetic_programme())
    return get_current_event_programme_snapshot()


def codes(rows) -> list[str]:
    return [row.service_code for row in rows]


def code_set(rows) -> set[str]:
    return set(codes(rows))


# -- which snapshot answers ---------------------------------------------


def test_the_current_snapshot_is_the_one_that_answers(publish_programme):
    publish_programme(rows=synthetic_programme())
    first = get_current_event_programme_snapshot()

    later = synthetic_programme()[:3]
    publish_programme(rows=later, control=default_control(later))
    second = get_current_event_programme_snapshot()

    assert EventProgrammeSnapshot.objects.count() == 2
    assert second.pk != first.pk
    assert second.canonical_event_count == 3
    assert get_filtered_event_programme_items(second, filters=ALL_YEARS).count() == 3


def test_the_first_successful_import_is_readable(publish_programme):
    outcome = publish_programme(rows=synthetic_programme())

    assert outcome.result == SyncResult.IMPORTED
    summary = get_event_programme_summary()
    assert summary.has_data is True
    assert summary.item_count == TOTAL_ROWS
    assert summary.observed_at is not None


def test_a_failed_later_sync_leaves_the_previous_snapshot_visible(programme):
    """The dashboard keeps the last good programme and discloses the failure."""
    outcome = synchronize_public_workbook(
        downloader=FakeDownloader(error=PublicDownloadError("Sünteetiline tõrge."))
    )

    assert outcome.result == SyncResult.FAILED
    summary = get_event_programme_summary()
    assert summary.snapshot.pk == programme.pk
    assert summary.item_count == TOTAL_ROWS
    assert summary.is_stale_after_failure is True
    assert summary.state_label == "Vananenud"


def test_the_history_comes_from_one_snapshot(programme):
    """Several years are read from the current snapshot, not from an archive.

    The workbook exports the whole programme every morning, so combining older
    snapshots would let two exports of different vintages contribute to one
    total.
    """
    old_year, mid_year = programme_years()
    options = get_event_programme_filter_options(programme)

    assert old_year in options.years
    assert mid_year in options.years
    assert timezone.localdate().year in options.years
    assert all(
        row.snapshot_id == programme.pk
        for row in get_filtered_event_programme_items(programme, filters=ALL_YEARS)
    )


# -- filtering ----------------------------------------------------------


def test_the_year_filter_reads_the_event_year_not_the_source_year(programme):
    """The first synthetic row sits on an earlier annual sheet than it ran in."""
    old_year, _mid_year = programme_years()

    matched = get_filtered_event_programme_items(programme, filters=ProgrammeFilters(year=old_year))

    assert codes(matched) == ["8002", "8001"]
    assert (
        get_filtered_event_programme_items(
            programme, filters=ProgrammeFilters(year=old_year - 1)
        ).count()
        == 0
    )
    assert old_year - 1 not in get_event_programme_filter_options(programme).years


def test_the_month_filter_uses_the_derived_calendar_month(programme):
    """February across every year, read off the stored `YYYY-MM` key.

    Membership rather than an exact list: the rows placed relative to today land
    in whichever month the suite happens to run in, and the filter's meaning does
    not depend on that.
    """
    matched = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, month="02")
    )

    assert "8001" in code_set(matched)
    assert all(row.event_month_key.endswith("-02") for row in matched)
    assert "8002" not in code_set(matched), "May is not February"


def test_the_quarter_filter_separates_a_boundary(programme):
    first = code_set(
        get_filtered_event_programme_items(
            programme, filters=ProgrammeFilters(year=None, quarter="Q1")
        )
    )
    second = code_set(
        get_filtered_event_programme_items(
            programme, filters=ProgrammeFilters(year=None, quarter="Q2")
        )
    )

    # 31 March and 1 April are consecutive days in different quarters.
    assert {"8003", "8008"} <= first
    assert "8009" in second
    assert "8008" not in second and "8009" not in first


def test_the_tag_filter_uses_the_stable_key(programme):
    matched = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, tag="konverents")
    )

    assert code_set(matched) == {"8002", "8007"}


def test_the_event_type_filter_uses_the_stable_key(programme):
    matched = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, event_type="conference")
    )

    assert code_set(matched) == {"8002", "8007"}


def test_the_delivery_mode_filter_uses_the_stored_vocabulary(programme):
    matched = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, delivery_mode="hybrid")
    )

    assert code_set(matched) == {"8003", "8007"}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (EventStatus.PAST, {"8001", "8002", "8003", "8006", "8008", "8009"}),
        (EventStatus.ONGOING, {"8007"}),
        (EventStatus.UPCOMING, {"8005"}),
        (EventStatus.DATE_UNKNOWN, {"8004"}),
    ],
)
def test_the_status_filter_covers_every_state(programme, status, expected):
    matched = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, status=status)
    )

    assert code_set(matched) == expected


def test_linked_and_unlinked_filtering(programme):
    linked = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, public_link=LINK_LINKED)
    )
    unlinked = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, public_link=LINK_UNLINKED)
    )

    assert code_set(linked) == {"8001", "8005"}
    assert all(row.public_url for row in linked)
    assert all(row.public_url == "" for row in unlinked)
    assert linked.count() + unlinked.count() == TOTAL_ROWS


def test_review_filtering(programme):
    required = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, review=REVIEW_REQUIRED)
    )
    clear = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, review=REVIEW_CLEAR)
    )

    assert code_set(required) == {"8003", "8004"}
    assert required.count() + clear.count() == TOTAL_ROWS


def test_search_finds_an_event_by_name(programme):
    matched = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, q="KONVERENTS")
    )

    assert codes(matched) == ["8002"], "case-insensitive, and the name is what is searched"


def test_search_finds_an_event_by_service_code(programme):
    matched = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, q="8003")
    )

    assert codes(matched) == ["8003"]


def test_combined_filters_narrow_together(programme):
    old_year, _mid_year = programme_years()

    matched = get_filtered_event_programme_items(
        programme,
        filters=ProgrammeFilters(
            year=old_year, quarter="Q1", tag="seminar", public_link=LINK_LINKED
        ),
    )

    assert codes(matched) == ["8001"]


def test_an_unknown_date_record_stays_a_record(programme):
    """Never hidden, never given a date, and reachable across every year."""
    matched = get_filtered_event_programme_items(
        programme, filters=ProgrammeFilters(year=None, status=EventStatus.DATE_UNKNOWN)
    )

    (undated,) = list(matched)
    assert undated.start_date is None
    assert undated.end_date is None
    assert undated.event_year is None
    assert undated.event_month_key == ""
    assert undated.event_quarter == ""
    # It still carries a source year, which is not a date and is never shown as
    # one.
    assert undated.source_year == 2099
    assert count_unknown_date_events(programme) == 1


def test_a_date_range_keeps_both_ends(programme):
    _old_year, mid_year = programme_years()

    (ranged,) = list(
        get_filtered_event_programme_items(
            programme, filters=ProgrammeFilters(year=mid_year, q="mitmepäevane")
        )
    )

    assert ranged.start_date == dt.date(mid_year, 3, 4)
    assert ranged.end_date == dt.date(mid_year, 3, 6)


# -- ordering -----------------------------------------------------------


def test_known_dates_come_first_newest_first_and_undated_last(programme):
    rows = list(get_filtered_event_programme_items(programme, filters=ALL_YEARS))

    dated = [row for row in rows if row.start_date is not None]
    assert len(dated) == TOTAL_ROWS - 1
    assert [row.start_date for row in dated] == sorted(
        (row.start_date for row in dated), reverse=True
    )
    assert rows[-1].service_code == "8004", "the undated record sorts after every dated one"


def test_ordering_is_deterministic_for_two_events_on_one_day(publish_programme):
    """A same-day tie breaks on name, then service code, so pages never swap."""
    _old_year, mid_year = programme_years()
    day = dt.datetime(mid_year, 6, 1)
    rows = [
        synthetic_row(
            event_id="EVENT-7003",
            service_code="7003",
            event_name="Sünteetiline sama päeva sündmus",
            start_date=day,
            source_row=4,
        ),
        synthetic_row(
            event_id="EVENT-7001",
            service_code="7001",
            event_name="Sünteetiline sama päeva sündmus",
            start_date=day,
            source_row=2,
        ),
        synthetic_row(
            event_id="EVENT-7002",
            service_code="7002",
            event_name="Aabits sünteetiline sündmus",
            start_date=day,
            source_row=3,
        ),
    ]
    publish_programme(rows=rows, control=default_control(rows))
    snapshot = get_current_event_programme_snapshot()

    ordered = get_filtered_event_programme_items(snapshot, filters=ALL_YEARS)

    assert codes(ordered) == ["7002", "7001", "7003"]


# -- counts -------------------------------------------------------------


def test_the_forward_and_backward_windows_count_different_events(programme):
    assert count_events_starting_within(programme) == 1, "only the event five days out"
    assert count_events_started_within(programme) == 2, "the ongoing one and the recent one"


def test_today_belongs_to_the_forward_window_only(publish_programme):
    today = timezone.localdate()
    rows = [
        synthetic_row(
            event_id="EVENT-7100",
            service_code="7100",
            start_date=dt.datetime.combine(today, dt.time()),
            event_status="ongoing",
            source_row=2,
        )
    ]
    publish_programme(rows=rows, control=default_control(rows))
    snapshot = get_current_event_programme_snapshot()

    assert count_events_starting_within(snapshot) == 1
    assert count_events_started_within(snapshot) == 0


def test_the_year_count_and_the_whole_programme_count(programme):
    old_year, mid_year = programme_years()

    assert count_events_for_year(programme, old_year) == 2
    assert count_events_for_year(programme, mid_year) == 3
    assert count_events_for_year(programme, None) == TOTAL_ROWS


def test_the_linked_and_review_counts(programme):
    assert count_linked_events(programme) == 2
    assert count_review_required_events(programme) == 2


def test_nothing_published_counts_nothing(db):
    assert get_current_event_programme_snapshot() is None
    assert get_event_programme_summary().has_data is False
    assert count_events_starting_within(None) == 0
    assert count_events_started_within(None) == 0
    assert count_unknown_date_events(None) == 0
    assert count_linked_events(None) == 0
    assert count_review_required_events(None) == 0
    assert get_event_programme_filter_options(None).years == ()


# -- the overview preview -----------------------------------------------


def test_the_upcoming_preview_skips_what_has_already_finished(programme):
    upcoming = get_upcoming_programme_events(programme, limit=10)

    assert codes(upcoming) == ["8007", "8005"], "the ongoing event first, then the future one"


def test_the_filter_options_are_derived_and_not_hard_coded(programme):
    options = get_event_programme_filter_options(programme)

    assert [option.value for option in options.tags] == ["konverents", "koolitus", "seminar"]
    assert [option.label for option in options.tags] == [
        "Sünteetiline konverents",
        "Sünteetiline koolitus",
        "Sünteetiline seminar",
    ]
    assert {option.value for option in options.event_types} == {"conference", "training"}
    assert [option.value for option in options.delivery_modes] == ["onsite", "online", "hybrid"]
    assert {option.value for option in options.quarters} >= {"Q1", "Q2"}
    assert "02" in {option.value for option in options.months}
    assert options.months[0].value < options.months[-1].value, "months in calendar order"
