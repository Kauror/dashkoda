"""The intelligence dashboard's metric definitions.

Every figure the redesigned Õigusloome page shows is asserted here against a
synthetic register whose reporting date is fixed, so the rules that are easy to
get quietly wrong — the year-to-date cutoff, the same-date comparison, a missing
count that is not a zero — are pinned rather than trusted.

Nothing here uses the reference screenshots as expected values. Those are manual
source QA against real data; a test that hard-coded them would fail the next time
the lawyers sent an opinion.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.legal_work import analytics
from apps.legal_work.importer import import_artifact
from apps.legal_work.workbook import DATA_COLUMNS_V12

from .workbook_factory import synthetic_row, write_workbook

REPORTING = dt.date(2026, 8, 10)


def row(given=None, asked=None, **kwargs) -> list:
    """A schema 1.2 DATA row: the base row plus the two appended counts."""
    return synthetic_row(**kwargs) + [given, asked]


@pytest.fixture
def publish(register_workbook, tmp_path):
    """Publish a synthetic 1.2 register at a fixed reporting date."""
    counter = {"index": 0}

    def build(rows, *, reporting_date: dt.date = REPORTING):
        counter["index"] += 1
        path = write_workbook(
            tmp_path / f"analytics-{counter['index']}.xlsx",
            rows=rows,
            schema_version="1.2",
            columns=DATA_COLUMNS_V12,
            control_overrides={"reporting_date": reporting_date},
        )
        return import_artifact(register_workbook(path), dry_run=False).snapshot

    return build


# --------------------------------------------------------------------------
# Headline volumes and the year-on-year comparison
# --------------------------------------------------------------------------


@pytest.fixture
def sent_register(publish):
    """Opinions either side of the reporting date, in both years.

    2026 sends: 10 Jan, 3 Feb, 1 Aug  -> 3 up to 10 August
    2026 send after the cutoff: 20 Sep -> excluded from every year-to-date figure
    2025 sends: 5 Jan, 9 Aug          -> 2 up to 10 August
    2025 send after the cutoff: 30 Nov -> in the annual series, not in the YTD
    """
    return publish(
        [
            row(
                record_id="S-1",
                source_year=2026,
                source_row=2,
                sent_status="sent",
                sent_date=dt.date(2026, 1, 10),
                is_open=False,
            ),
            row(
                record_id="S-2",
                source_year=2026,
                source_row=3,
                sent_status="sent",
                sent_date=dt.date(2026, 2, 3),
                is_open=False,
            ),
            row(
                record_id="S-3",
                source_year=2026,
                source_row=4,
                sent_status="sent",
                sent_date=dt.date(2026, 8, 1),
                is_open=False,
            ),
            row(
                record_id="S-4",
                source_year=2026,
                source_row=5,
                sent_status="sent",
                sent_date=dt.date(2026, 9, 20),
                is_open=False,
            ),
            row(
                record_id="S-5",
                source_year=2025,
                source_row=2,
                sent_status="sent",
                sent_date=dt.date(2025, 1, 5),
                is_open=False,
            ),
            row(
                record_id="S-6",
                source_year=2025,
                source_row=3,
                sent_status="sent",
                sent_date=dt.date(2025, 8, 9),
                is_open=False,
            ),
            row(
                record_id="S-7",
                source_year=2025,
                source_row=4,
                sent_status="sent",
                sent_date=dt.date(2025, 11, 30),
                is_open=False,
            ),
            # Three statuses that are not a send, and so must never be counted.
            row(record_id="P-1", source_year=2026, source_row=6, sent_status="pending"),
            row(record_id="N-1", source_year=2026, source_row=7, sent_status="not_sent"),
            row(record_id="I-1", source_year=2026, source_row=8, sent_status="invalid"),
        ]
    )


def test_current_year_topics_use_the_registers_own_annual_grouping(sent_register):
    """`source_year`, not the arrival date: it is the sheet the matter sits on."""
    assert analytics.count_topics_for_year(sent_register, 2026) == 7
    assert analytics.count_topics_for_year(sent_register, 2025) == 3


def test_sent_year_to_date_stops_at_the_reporting_date(sent_register):
    """A send dated after the workbook's own cutoff is not year-to-date work.

    The 20 September row is real and stays imported; it simply cannot be part of
    a figure describing the year up to 10 August.
    """
    assert analytics.count_sent_in_year(sent_register, 2026, cutoff=REPORTING) == 3
    # Without the clamp the same year holds the later send as well.
    assert analytics.count_sent_in_year(sent_register, 2026) == 4


def test_only_the_sent_status_counts_as_an_opinion(sent_register):
    """Pending, not-sent and invalid rows are not opinions, whatever else exists."""
    assert analytics.count_sent_in_year(sent_register, 2026, cutoff=dt.date(2026, 12, 31)) == 4


def test_year_on_year_compares_the_same_calendar_period(sent_register):
    """2026-01-01..08-10 against 2025-01-01..08-10, never against all of 2025."""
    comparison = analytics.sent_year_on_year(sent_register)

    assert comparison.current_cutoff == REPORTING
    assert comparison.previous_cutoff == dt.date(2025, 8, 10)
    assert comparison.current == 3
    # 5 January and 9 August, but not 30 November.
    assert comparison.previous == 2
    assert comparison.absolute_change == 1
    assert comparison.percent_change == pytest.approx(50.0)
    assert comparison.direction == "up"


def test_a_zero_baseline_yields_no_percentage(publish):
    """Change from nothing is not an infinite percentage; the delta still stands."""
    snapshot = publish(
        [
            row(
                record_id="S-1",
                source_year=2026,
                source_row=2,
                sent_status="sent",
                sent_date=dt.date(2026, 3, 1),
                is_open=False,
            ),
        ]
    )

    comparison = analytics.sent_year_on_year(snapshot)

    assert comparison.previous == 0
    assert comparison.absolute_change == 1
    assert comparison.percent_change is None


def test_a_leap_day_cutoff_resolves_to_28_february(publish):
    """29 February has no counterpart, and `replace()` would raise."""
    assert analytics.same_date_last_year(dt.date(2024, 2, 29)) == dt.date(2023, 2, 28)

    snapshot = publish(
        [
            row(
                record_id="S-1",
                source_year=2024,
                source_row=2,
                sent_status="sent",
                sent_date=dt.date(2024, 2, 29),
                is_open=False,
            ),
            # 28 February counts for the baseline; 29 February 2023 does not exist.
            row(
                record_id="S-2",
                source_year=2023,
                source_row=2,
                sent_status="sent",
                sent_date=dt.date(2023, 2, 28),
                is_open=False,
            ),
        ],
        reporting_date=dt.date(2024, 2, 29),
    )

    comparison = analytics.sent_year_on_year(snapshot)

    assert comparison.previous_cutoff == dt.date(2023, 2, 28)
    assert comparison.current == 1
    assert comparison.previous == 1
    assert comparison.absolute_change == 0
    assert comparison.direction == "flat"


# --------------------------------------------------------------------------
# Active stock and the mandatory stage chart
# --------------------------------------------------------------------------


def test_stage_bars_reconcile_exactly_with_the_active_total(publish):
    """The chart's `kokku` must equal the open count, blanks included.

    A row with no stage is still active work. Dropping it would leave the bars
    describing fewer matters than the headline beside them claims.
    """
    snapshot = publish(
        [
            row(
                record_id="A-1",
                source_year=2026,
                source_row=2,
                is_open=True,
                stage="Kooskõlastusringil",
                stage_key="kooskõlastusringil",
            ),
            row(
                record_id="A-2",
                source_year=2026,
                source_row=3,
                is_open=True,
                stage="kooskõlastusringil",
                stage_key="kooskõlastusringil",
            ),
            row(
                record_id="A-3",
                source_year=2026,
                source_row=4,
                is_open=True,
                stage="Riigikogus",
                stage_key="riigikogus",
            ),
            row(
                record_id="A-4",
                source_year=2026,
                source_row=5,
                is_open=True,
                stage="",
                stage_key="",
            ),
            row(
                record_id="C-1",
                source_year=2026,
                source_row=6,
                is_open=False,
                sent_status="sent",
                sent_date=dt.date(2026, 5, 1),
            ),
        ]
    )

    breakdown = analytics.stage_breakdown(snapshot)

    assert breakdown.total == 4
    assert sum(stage.count for stage in breakdown.stages) == breakdown.total
    assert breakdown.stages[0].count == 2
    # Two spellings of one key group together, and the label is the commoner one.
    assert breakdown.stages[0].stage_key == "kooskõlastusringil"
    assert [stage.label for stage in breakdown.stages][-1] == analytics.UNKNOWN_STAGE_LABEL
    assert breakdown.largest_share == pytest.approx(50.0)


def test_an_unseen_stage_survives_without_a_code_change(publish):
    """The vocabulary is free text and gains entries between workbooks."""
    snapshot = publish(
        [
            row(
                record_id="A-1",
                source_year=2026,
                source_row=2,
                is_open=True,
                stage="Ootan ELi õiguse ülevõtmist",
                stage_key="ootan eli õiguse ülevõtmist",
            ),
        ]
    )

    breakdown = analytics.stage_breakdown(snapshot)

    assert breakdown.stages[0].label == "Ootan ELi õiguse ülevõtmist"
    assert breakdown.total == 1


# --------------------------------------------------------------------------
# Monthly flow
# --------------------------------------------------------------------------


def test_monthly_new_topics_bucket_by_arrival_and_never_by_source_year(publish):
    """A December arrival on the following year's sheet belongs to December."""
    snapshot = publish(
        [
            # Sits on the 2026 sheet but arrived in December 2025.
            row(
                record_id="R-1", source_year=2026, source_row=2, received_date=dt.date(2025, 12, 18)
            ),
            row(record_id="R-2", source_year=2026, source_row=3, received_date=dt.date(2026, 1, 5)),
            row(
                record_id="R-3", source_year=2026, source_row=4, received_date=dt.date(2026, 1, 20)
            ),
            row(record_id="R-4", source_year=2026, source_row=5, received_date=dt.date(2026, 3, 2)),
            # No arrival date at all: it belongs to no month.
            row(
                record_id="R-5",
                source_year=2026,
                source_row=6,
                received_date=None,
                deadline_date=None,
            ),
        ]
    )

    flow = analytics.monthly_new_topics(snapshot, 2026)

    assert flow.counts[0] == 2  # January
    assert flow.counts[1] == 0  # February is a measured zero
    assert flow.counts[2] == 1  # March
    assert flow.missing_date_count == 1
    assert flow.total == 3

    # The December 2025 arrival is in the previous year's series, not this one.
    assert analytics.monthly_new_topics(snapshot, 2025).counts[11] == 1


def test_the_current_year_stops_at_the_reporting_month(publish):
    """No empty bars for months the data has not reached."""
    snapshot = publish(
        [row(record_id="R-1", source_year=2026, source_row=2, received_date=dt.date(2026, 2, 1))]
    )

    flow = analytics.monthly_new_topics(snapshot, 2026)

    assert len(flow.counts) == 8  # January through August only
    assert flow.partial_month == 8
    assert flow.complete_through_month == 7


def test_a_month_end_reporting_date_is_a_complete_month(publish):
    """31 July describes all of July; nothing should be marked provisional."""
    snapshot = publish(
        [row(record_id="R-1", source_year=2026, source_row=2, received_date=dt.date(2026, 7, 1))],
        reporting_date=dt.date(2026, 7, 31),
    )

    flow = analytics.monthly_new_topics(snapshot, 2026)

    assert flow.partial_month is None
    assert flow.complete_through_month == 7


def test_a_completed_year_draws_all_twelve_months(publish):
    snapshot = publish(
        [row(record_id="R-1", source_year=2025, source_row=2, received_date=dt.date(2025, 12, 31))]
    )

    flow = analytics.monthly_new_topics(snapshot, 2025)

    assert len(flow.counts) == 12
    assert flow.counts[11] == 1
    assert flow.partial_month is None


def test_monthly_sends_reconcile_with_the_annual_figure(sent_register):
    """Guaranteed by the model constraint, and asserted so it stays true."""
    flow = analytics.monthly_sent_opinions(sent_register, 2026)

    assert flow.total == analytics.count_sent_in_year(sent_register, 2026, cutoff=REPORTING)
    assert flow.counts[0] == 1  # January
    assert flow.counts[7] == 1  # August


# --------------------------------------------------------------------------
# Annual series
# --------------------------------------------------------------------------


def test_the_annual_send_series_marks_the_current_year_partial(sent_register):
    series = {point.year: point for point in analytics.annual_sent_opinions(sent_register)}

    assert series[2025].count == 3
    assert series[2025].is_partial is False
    # The September send is beyond the reporting date and is in no annual bar.
    assert series[2026].count == 3
    assert series[2026].is_partial is True


# --------------------------------------------------------------------------
# Response window
# --------------------------------------------------------------------------


@pytest.fixture
def window_register(publish):
    """Consultation windows of 0, 10, 20 and 30 days, plus two unusable rows."""
    return publish(
        [
            row(
                record_id="W-0",
                source_year=2026,
                source_row=2,
                received_date=dt.date(2026, 3, 1),
                deadline_date=dt.date(2026, 3, 1),
            ),
            row(
                record_id="W-10",
                source_year=2026,
                source_row=3,
                received_date=dt.date(2026, 3, 1),
                deadline_date=dt.date(2026, 3, 11),
            ),
            row(
                record_id="W-20",
                source_year=2026,
                source_row=4,
                received_date=dt.date(2026, 3, 1),
                deadline_date=dt.date(2026, 3, 21),
            ),
            row(
                record_id="W-30",
                source_year=2026,
                source_row=5,
                received_date=dt.date(2026, 3, 1),
                deadline_date=dt.date(2026, 3, 31),
            ),
            # Deadline before arrival: a source-quality problem, not a zero.
            row(
                record_id="W-NEG",
                source_year=2026,
                source_row=6,
                received_date=dt.date(2026, 4, 10),
                deadline_date=dt.date(2026, 4, 1),
            ),
            # No deadline at all.
            row(
                record_id="W-NONE",
                source_year=2026,
                source_row=7,
                received_date=dt.date(2026, 5, 1),
                deadline_date=None,
            ),
        ]
    )


def test_a_same_day_deadline_is_zero_days_and_is_eligible(window_register):
    """Zero is a real measurement; only a negative interval is excluded."""
    year = {y.year: y for y in analytics.response_window_by_year(window_register)}[2026]

    assert year.eligible == 4
    assert year.median == pytest.approx(15.0)  # median of 0, 10, 20, 30
    assert year.mean == pytest.approx(15.0)


def test_a_negative_interval_is_excluded_and_counted(window_register):
    """Never made positive, never repaired to zero — reported instead."""
    year = {y.year: y for y in analytics.response_window_by_year(window_register)}[2026]

    assert year.invalid_interval == 1
    assert year.missing_dates == 1
    assert year.eligible == 4


def test_the_median_is_the_middle_of_an_odd_cohort(publish):
    snapshot = publish(
        [
            row(
                record_id="W-2",
                source_year=2026,
                source_row=2,
                received_date=dt.date(2026, 3, 1),
                deadline_date=dt.date(2026, 3, 3),
            ),
            row(
                record_id="W-4",
                source_year=2026,
                source_row=3,
                received_date=dt.date(2026, 3, 1),
                deadline_date=dt.date(2026, 3, 5),
            ),
            row(
                record_id="W-90",
                source_year=2026,
                source_row=4,
                received_date=dt.date(2026, 3, 1),
                deadline_date=dt.date(2026, 5, 30),
            ),
        ]
    )

    year = {y.year: y for y in analytics.response_window_by_year(snapshot)}[2026]

    # The long tail moves the mean far above the typical matter; the median holds.
    assert year.median == pytest.approx(4.0)
    assert year.mean == pytest.approx(32.0)


def test_the_response_cohort_is_the_year_the_matter_arrived(publish):
    """A December 2025 arrival measures a 2025 window even on the 2026 sheet."""
    snapshot = publish(
        [
            row(
                record_id="W-1",
                source_year=2026,
                source_row=2,
                received_date=dt.date(2025, 12, 1),
                deadline_date=dt.date(2025, 12, 11),
            ),
        ]
    )

    years = {y.year: y for y in analytics.response_window_by_year(snapshot)}

    assert years[2025].eligible == 1
    assert 2026 not in years or years[2026].eligible == 0


def test_the_distribution_bands_and_short_window_share(window_register):
    distribution = analytics.response_window_distribution(window_register, year=2026)

    assert dict(distribution.bands)["0–7 päeva"] == 1  # the 0-day window
    assert dict(distribution.bands)["8–14 päeva"] == 1  # 10 days
    assert dict(distribution.bands)["15–21 päeva"] == 1  # 20 days
    assert dict(distribution.bands)["22–30 päeva"] == 1  # 30 days
    assert distribution.eligible == 4
    # 0 and 10 days are at or under the fourteen-day mark.
    assert distribution.short_window_count == 2
    assert distribution.short_window_share == pytest.approx(50.0)


def test_sent_by_deadline_counts_only_sends_that_state_a_deadline(publish):
    snapshot = publish(
        [
            row(
                record_id="D-EARLY",
                source_year=2026,
                source_row=2,
                sent_status="sent",
                received_date=dt.date(2026, 3, 1),
                deadline_date=dt.date(2026, 3, 20),
                sent_date=dt.date(2026, 3, 15),
                is_open=False,
            ),
            row(
                record_id="D-ON",
                source_year=2026,
                source_row=3,
                sent_status="sent",
                received_date=dt.date(2026, 3, 1),
                deadline_date=dt.date(2026, 3, 20),
                sent_date=dt.date(2026, 3, 20),
                is_open=False,
            ),
            row(
                record_id="D-LATE",
                source_year=2026,
                source_row=4,
                sent_status="sent",
                received_date=dt.date(2026, 3, 1),
                deadline_date=dt.date(2026, 3, 20),
                sent_date=dt.date(2026, 3, 25),
                is_open=False,
            ),
            # Sent but with no deadline: outside the denominator entirely.
            row(
                record_id="D-NONE",
                source_year=2026,
                source_row=5,
                sent_status="sent",
                received_date=dt.date(2026, 3, 1),
                deadline_date=None,
                sent_date=dt.date(2026, 3, 25),
                is_open=False,
            ),
            # Never sent: outside the denominator entirely.
            row(
                record_id="D-PEND",
                source_year=2026,
                source_row=6,
                received_date=dt.date(2026, 3, 1),
                deadline_date=dt.date(2026, 3, 20),
            ),
        ]
    )

    measure = analytics.sent_by_deadline(snapshot)

    assert measure.eligible == 3
    assert measure.on_or_before == 2  # on the deadline still counts
    assert measure.after == 1


# --------------------------------------------------------------------------
# Active age and deadline pressure
# --------------------------------------------------------------------------


def test_active_age_is_measured_from_the_reporting_date_not_today(publish):
    """Wall-clock ageing would move a published figure between two page loads."""
    snapshot = publish(
        [
            row(
                record_id="A-10",
                source_year=2026,
                source_row=2,
                is_open=True,
                received_date=REPORTING - dt.timedelta(days=10),
            ),
            row(
                record_id="A-100",
                source_year=2026,
                source_row=3,
                is_open=True,
                received_date=REPORTING - dt.timedelta(days=100),
            ),
            row(
                record_id="A-800",
                source_year=2026,
                source_row=4,
                is_open=True,
                received_date=REPORTING - dt.timedelta(days=800),
            ),
        ]
    )

    age = analytics.active_topic_age(snapshot)

    assert age.measured == 3
    assert age.median == pytest.approx(100.0)
    bands = dict(age.bands)
    assert bands["Alla 30 päeva"] == 1
    assert bands["91–180 päeva"] == 1
    assert bands["Üle 2 aasta"] == 1


def test_a_future_arrival_never_produces_a_negative_age(publish):
    """A known workbook anomaly: excluded from the statistic and counted."""
    snapshot = publish(
        [
            row(
                record_id="A-FUT",
                source_year=2026,
                source_row=2,
                is_open=True,
                received_date=dt.date(2026, 12, 30),
                deadline_date=None,
            ),
            row(
                record_id="A-NONE",
                source_year=2026,
                source_row=3,
                is_open=True,
                received_date=None,
                deadline_date=None,
            ),
        ]
    )

    age = analytics.active_topic_age(snapshot)

    assert age.measured == 0
    assert age.median is None
    assert age.future_received_date == 1
    assert age.missing_received_date == 1


def test_deadline_bands_are_mutually_exclusive(publish):
    snapshot = publish(
        [
            row(
                record_id="D-2",
                source_year=2026,
                source_row=2,
                is_open=True,
                deadline_date=REPORTING + dt.timedelta(days=2),
            ),
            row(
                record_id="D-6",
                source_year=2026,
                source_row=3,
                is_open=True,
                deadline_date=REPORTING + dt.timedelta(days=6),
            ),
            row(
                record_id="D-30",
                source_year=2026,
                source_row=4,
                is_open=True,
                deadline_date=REPORTING + dt.timedelta(days=30),
            ),
            row(
                record_id="D-NONE", source_year=2026, source_row=5, is_open=True, deadline_date=None
            ),
        ]
    )

    pressure = analytics.deadline_pressure(snapshot)
    bands = dict(pressure.bands)

    assert bands["0–3 päeva"] == 1
    assert bands["4–7 päeva"] == 1
    assert bands["Hiljem"] == 1
    assert pressure.upcoming_total == 3
    assert pressure.due_within_7 == 2
    assert pressure.without_deadline == 1


def test_a_passed_deadline_is_split_by_whether_the_opinion_went_out(publish):
    """An open matter whose opinion already went is not outstanding work.

    Labelling it overdue would manufacture a backlog out of ordinary process:
    a topic legitimately stays open after Koda has submitted its opinion.
    """
    snapshot = publish(
        [
            row(
                record_id="O-PEND",
                source_year=2026,
                source_row=2,
                is_open=True,
                deadline_date=REPORTING - dt.timedelta(days=5),
            ),
            row(
                record_id="O-SENT",
                source_year=2026,
                source_row=3,
                is_open=True,
                sent_status="sent",
                sent_date=REPORTING - dt.timedelta(days=6),
                deadline_date=REPORTING - dt.timedelta(days=5),
            ),
        ]
    )

    pressure = analytics.deadline_pressure(snapshot)

    assert pressure.overdue_pending == 1
    assert pressure.overdue_already_sent == 1


# --------------------------------------------------------------------------
# Member feedback
# --------------------------------------------------------------------------


@pytest.fixture
def feedback_register(publish):
    """Tracked positives, a measured zero, and two untracked rows."""
    return publish(
        [
            row(4, 40, record_id="F-4", source_year=2026, source_row=2),
            row(6, 60, record_id="F-6", source_year=2026, source_row=3),
            row(0, 10, record_id="F-0", source_year=2026, source_row=4),
            row(None, None, record_id="F-NONE", source_year=2026, source_row=5),
            # Tracked in an earlier year, so coverage differs by year.
            row(None, None, record_id="F-OLD", source_year=2019, source_row=2),
        ]
    )


def test_an_untracked_count_is_never_a_zero(feedback_register):
    """`None` means nobody measured; `0` means somebody measured nothing."""
    summary = analytics.feedback_summary(feedback_register, year=2026)

    assert summary.tracked_topics == 3
    assert summary.untracked_topics == 1
    assert summary.with_feedback == 2
    assert summary.measured_zero == 1


def test_feedback_instances_sum_only_tracked_rows(feedback_register):
    summary = analytics.feedback_summary(feedback_register, year=2026)

    assert summary.feedback_instances == 10  # 4 + 6 + 0
    assert summary.median_per_topic == pytest.approx(5.0)  # median of 4 and 6
    assert summary.requested_instances == 110  # 40 + 60 + 10


def test_a_register_with_no_tracking_reports_absence_rather_than_zero(publish):
    """An unmeasured register must not read as a measured total of nothing."""
    snapshot = publish([row(None, None, record_id="F-NONE", source_year=2019, source_row=2)])

    summary = analytics.feedback_summary(snapshot)

    assert summary.tracked_topics == 0
    assert summary.has_coverage is False
    assert summary.feedback_instances is None
    assert summary.median_per_topic is None


def test_coverage_by_year_shows_where_measurement_actually_begins(feedback_register):
    """A year that predates tracking is not drawn as a year of zero feedback."""
    coverage = {year.year: year for year in analytics.feedback_coverage_by_year(feedback_register)}

    assert coverage[2019].tracked_topics == 0
    assert coverage[2019].feedback_instances is None
    assert coverage[2026].tracked_topics == 3
    assert coverage[2026].coverage_share == pytest.approx(75.0)
    assert analytics.first_tracked_feedback_year(feedback_register) == 2026


def test_the_summary_offers_no_response_rate(feedback_register):
    """The two counts are not a valid numerator and denominator.

    Members also answer through newsletters and general calls, and the register
    contains matters where more members answered than were asked directly — so a
    ratio would exceed 100% and would not mean what its name claimed.
    """
    summary = analytics.feedback_summary(feedback_register, year=2026)

    assert not hasattr(summary, "response_rate")
    assert not any("rate" in name for name in vars(summary))


def test_more_answers_than_direct_requests_is_representable(publish):
    """Proof the subset assumption fails: the model must not reject this row."""
    snapshot = publish([row(9, 2, record_id="F-MORE", source_year=2026, source_row=2)])

    summary = analytics.feedback_summary(snapshot, year=2026)

    assert summary.feedback_instances == 9
    assert summary.requested_instances == 2


# --------------------------------------------------------------------------
# Category breakdowns and data quality
# --------------------------------------------------------------------------


def test_a_thin_category_reports_no_median(publish):
    """Below the sample floor the figure is withheld, not drawn small."""
    rows = [
        row(
            record_id=f"C-{index}",
            source_year=2026,
            source_row=index + 2,
            recipient="Sünteetiline ministeerium A",
            received_date=dt.date(2026, 3, 1),
            deadline_date=dt.date(2026, 3, 15),
        )
        for index in range(analytics.MIN_COMPARISON_SAMPLE)
    ]
    rows.append(
        row(
            record_id="C-THIN",
            source_year=2026,
            source_row=99,
            recipient="Sünteetiline ministeerium B",
            received_date=dt.date(2026, 3, 1),
            deadline_date=dt.date(2026, 3, 8),
        )
    )
    snapshot = publish(rows)

    by_label = {entry.label: entry for entry in analytics.recipient_breakdown(snapshot)}

    assert by_label["Sünteetiline ministeerium A"].has_enough_sample is True
    assert by_label["Sünteetiline ministeerium A"].median_window == pytest.approx(14.0)
    assert by_label["Sünteetiline ministeerium B"].has_enough_sample is False
    assert by_label["Sünteetiline ministeerium B"].median_window is None


def test_similar_looking_categories_are_never_merged(publish):
    """`MKM` and the full ministry name stay apart; no fuzzy rule joins them."""
    snapshot = publish(
        [
            row(record_id="C-1", source_year=2026, source_row=2, recipient="MKM"),
            row(
                record_id="C-2",
                source_year=2026,
                source_row=3,
                recipient="Majandus- ja Kommunikatsiooniministeerium",
            ),
        ]
    )

    labels = {entry.label for entry in analytics.recipient_breakdown(snapshot)}

    assert labels == {"MKM", "Majandus- ja Kommunikatsiooniministeerium"}


def test_data_quality_counts_the_anomalies_it_excluded(publish):
    snapshot = publish(
        [
            row(
                record_id="Q-1",
                source_year=2026,
                source_row=2,
                received_date=dt.date(2026, 12, 30),
                deadline_date=None,
            ),
            row(
                record_id="Q-2",
                source_year=2026,
                source_row=3,
                received_date=dt.date(2026, 4, 10),
                deadline_date=dt.date(2026, 4, 1),
            ),
            row(
                record_id="Q-3",
                source_year=2026,
                source_row=4,
                received_date=None,
                deadline_date=None,
                warning_codes="missing_received_date",
            ),
        ]
    )

    quality = analytics.data_quality(snapshot)

    assert quality.total == 3
    assert quality.future_received == 1
    assert quality.negative_window == 1
    assert quality.warning_records == 1
    assert dict(analytics.warning_code_counts(snapshot)) == {"missing_received_date": 1}

    coverage = {entry.label: entry for entry in quality.coverage}
    assert coverage["Saabumise kuupäev"].present == 2


def test_every_metric_is_empty_rather_than_wrong_without_a_snapshot():
    """No published snapshot is an empty state, never a page of zeroes."""
    assert analytics.count_topics_for_year(None, 2026) == 0
    assert analytics.sent_year_on_year(None) is None
    assert analytics.stage_breakdown(None).total == 0
    assert analytics.annual_sent_opinions(None) == ()
    assert analytics.response_window_by_year(None) == ()
    assert analytics.active_topic_age(None).median is None
    assert analytics.feedback_summary(None).has_coverage is False
    assert analytics.data_quality(None).total == 0
