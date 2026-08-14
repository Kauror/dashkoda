"""The measurement rules of the Sündmused dashboard.

These tests exist to hold three claims that are easy to break and impossible to
see once broken:

- the measurement object is one **canonical programme event**, and the current
  snapshot is the only population;
- an undated event stays a record, enters no period figure, and is never given
  a date;
- every distribution is mutually exclusive and reconciles with its population,
  blanks included.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.event_programme import analytics
from apps.event_programme.models import EventProgrammeItem
from apps.event_programme.selectors import get_current_event_programme_snapshot

from .conftest import programme_years
from .workbook_factory import synthetic_row

pytestmark = pytest.mark.django_db


def _at(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time())


@pytest.fixture
def snapshot(publish_programme, synthetic_rows):
    publish_programme(rows=synthetic_rows)
    return get_current_event_programme_snapshot()


@pytest.fixture
def synthetic_rows():
    from .conftest import synthetic_programme

    return synthetic_programme()


# ---------------------------------------------------------------------------
# Grain and population
# ---------------------------------------------------------------------------


def test_only_the_current_snapshot_is_counted(publish_programme, synthetic_rows):
    """An old snapshot is a prior revision, never extra years of history.

    Every export carries the whole available history, so unioning two of them
    would double the programme. This is the single most consequential rule on
    the page and it is asserted directly rather than trusted.
    """
    publish_programme(rows=synthetic_rows)
    first = get_current_event_programme_snapshot()

    # A second, smaller export of the same programme. Above the collapse floor,
    # so it publishes normally.
    kept = len(synthetic_rows) - 2
    publish_programme(rows=synthetic_rows[:kept])
    second = get_current_event_programme_snapshot()

    assert second.pk != first.pk
    # Both snapshots' rows are still in the table — the old one is a retired
    # revision, not deleted — and exactly one of them is the population.
    assert EventProgrammeItem.objects.count() == len(synthetic_rows) + kept
    assert analytics.items_for(second).count() == kept
    volume = analytics.build_volume(second, year=None)
    assert volume.total_count == kept


def test_population_by_year_uses_event_year_not_source_year(snapshot):
    """`source_year` is the annual sheet a row sat on, never when it happened.

    The first synthetic row deliberately carries a `source_year` a year earlier
    than its own date, so a selector reading the wrong column produces a
    different answer.
    """
    old_year, _mid_year = programme_years()
    cohort = analytics.population(snapshot, year=old_year)
    assert cohort.count() == 2
    assert not cohort.filter(source_year=old_year).exists()


# ---------------------------------------------------------------------------
# Undated events
# ---------------------------------------------------------------------------


def test_undated_event_is_kept_but_enters_no_period(snapshot):
    volume = analytics.build_volume(snapshot, year=None)

    assert volume.undated_count == 1
    assert volume.dated_count + volume.undated_count == volume.total_count
    # It is in no year, in no month and in no quarter.
    assert sum(row.count for row in volume.years) == volume.dated_count
    assert not analytics.items_for(snapshot).filter(start_date=None).exclude(event_year=None)


def test_undated_event_is_never_given_a_date(snapshot):
    undated = analytics.items_for(snapshot).get(start_date=None)
    assert undated.event_year is None
    assert undated.event_month_key == ""
    assert undated.event_quarter == ""
    assert undated.end_date is None


def test_monthly_counts_reconcile_with_the_year(snapshot):
    """Undated events stay outside monthly totals rather than being forced in."""
    old_year, _mid = programme_years()
    months = analytics.counts_by_month(snapshot, year=old_year)
    yearly = analytics.counts_by_year(snapshot)
    assert sum(months.values()) == yearly[old_year]


# ---------------------------------------------------------------------------
# Distributions reconcile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key_field", "label_field"),
    [("tag_key", "tag_label"), ("event_type_key", "event_type_label")],
)
def test_distribution_reconciles_with_its_population(snapshot, key_field, label_field):
    cohort = analytics.population(snapshot, year=None)
    distribution = analytics.labelled_distribution(
        cohort, key_field=key_field, label_field=label_field, dimension="x", top=None
    )
    assert distribution.total == cohort.count()
    assert distribution.counted == distribution.total
    assert sum(row.share for row in distribution.all_rows) == pytest.approx(100.0)


def test_delivery_mode_reconciles_and_blank_is_its_own_row(publish_programme):
    """A blank delivery mode is `Määramata`, never `Kohapeal`."""
    rows = [
        synthetic_row(event_id="E-1", service_code="1", delivery_mode="onsite", source_row=2),
        synthetic_row(event_id="E-2", service_code="2", delivery_mode="online", source_row=3),
        synthetic_row(event_id="E-3", service_code="3", delivery_mode="", source_row=4),
    ]
    publish_programme(rows=rows)
    snapshot = get_current_event_programme_snapshot()

    distribution = analytics.delivery_mode_distribution(analytics.population(snapshot))
    assert distribution.total == 3
    assert distribution.counted == 3
    unknown = [row for row in distribution.all_rows if row.is_unknown]
    assert [row.count for row in unknown] == [1]
    onsite = next(row for row in distribution.all_rows if row.key == "onsite")
    assert onsite.count == 1


def test_a_new_source_key_does_not_break_the_ranking(publish_programme):
    """The taxonomy is hand-maintained: a value nobody has seen must import."""
    rows = [
        synthetic_row(
            event_id="E-1",
            service_code="1",
            event_type_key="taiesti_uus_tuup",
            event_type_label="Täiesti uus tüüp",
            source_row=2,
        )
    ]
    publish_programme(rows=rows)
    snapshot = get_current_event_programme_snapshot()

    distribution = analytics.labelled_distribution(
        analytics.population(snapshot),
        key_field="event_type_key",
        label_field="event_type_label",
        dimension="type",
    )
    assert [row.key for row in distribution.rows] == ["taiesti_uus_tuup"]
    assert distribution.rows[0].label == "Täiesti uus tüüp"


def test_top_n_remainder_is_the_real_remainder(publish_programme):
    rows = [
        synthetic_row(
            event_id=f"E-{index}",
            service_code=str(index),
            tag_key=f"tag{index}",
            tag_label=f"Silt {index}",
            source_row=index + 1,
        )
        for index in range(1, 16)
    ]
    publish_programme(rows=rows)
    snapshot = get_current_event_programme_snapshot()

    distribution = analytics.labelled_distribution(
        analytics.population(snapshot),
        key_field="tag_key",
        label_field="tag_label",
        dimension="tag",
        top=10,
    )
    assert len(distribution.rows) == 10
    assert distribution.remainder is not None
    assert distribution.remainder.count == 5
    assert distribution.counted == 15


# ---------------------------------------------------------------------------
# Duration
# ---------------------------------------------------------------------------


def test_a_multi_day_event_is_one_event(publish_programme):
    """Nine September to twenty-one October is one programme event, not 43."""
    rows = [
        synthetic_row(
            event_id="E-1",
            service_code="1",
            start_date=_at(dt.date(2099, 9, 9)),
            end_date=_at(dt.date(2099, 10, 21)),
            date_parse_status="parsed_range",
            source_row=2,
        )
    ]
    publish_programme(rows=rows)
    snapshot = get_current_event_programme_snapshot()

    distribution = analytics.duration_distribution(analytics.population(snapshot))
    assert distribution.total == 1
    assert [row.label for row in distribution.rows] == ["8+ päeva"]
    assert analytics.build_volume(snapshot, year=None).total_count == 1


def test_an_event_with_no_end_date_lasts_one_day(publish_programme):
    rows = [
        synthetic_row(
            event_id="E-1", service_code="1", start_date=_at(dt.date(2099, 3, 4)), source_row=2
        )
    ]
    publish_programme(rows=rows)
    snapshot = get_current_event_programme_snapshot()
    distribution = analytics.duration_distribution(analytics.population(snapshot))
    assert [row.label for row in distribution.rows] == ["1 päev"]


# ---------------------------------------------------------------------------
# Current state derives from dates, not from a stale snapshot field
# ---------------------------------------------------------------------------


def test_temporal_state_ignores_a_stale_imported_status(publish_programme):
    """A fortnight-old export still calls a finished event `upcoming`.

    The imported `event_status` is kept for the register's filter, where it is
    the workbook's own statement. Anything claiming to describe *now* derives
    from the dates and today's date instead.
    """
    today = timezone.localdate()
    rows = [
        synthetic_row(
            event_id="E-1",
            service_code="1",
            start_date=_at(today - dt.timedelta(days=3)),
            # What the generator wrote a fortnight ago, and it is now wrong.
            event_status="upcoming",
            source_row=2,
        )
    ]
    publish_programme(rows=rows)
    snapshot = get_current_event_programme_snapshot()

    state = analytics.temporal_state(analytics.population(snapshot), today=today)
    assert state.past == 1
    assert state.upcoming == 0
    assert analytics.items_for(snapshot).get().event_status == "upcoming"


def test_temporal_state_reconciles(snapshot):
    cohort = analytics.population(snapshot, year=None)
    state = analytics.temporal_state(cohort)
    assert state.total == cohort.count()


def test_an_event_ending_today_is_still_under_way(publish_programme):
    today = timezone.localdate()
    rows = [
        synthetic_row(
            event_id="E-1",
            service_code="1",
            start_date=_at(today - dt.timedelta(days=2)),
            end_date=_at(today),
            date_parse_status="parsed_range",
            source_row=2,
        )
    ]
    publish_programme(rows=rows)
    snapshot = get_current_event_programme_snapshot()
    state = analytics.temporal_state(analytics.population(snapshot), today=today)
    assert state.ongoing == 1
    assert state.past == 0


# ---------------------------------------------------------------------------
# Year-on-year comparison
# ---------------------------------------------------------------------------


def test_year_to_date_stops_at_the_same_day_in_both_years(publish_programme):
    """The like-for-like half of the comparison must not compare a full year
    against eight months of one."""
    today = dt.date(2026, 6, 15)
    rows = [
        synthetic_row(
            event_id="E-1", service_code="1", start_date=_at(dt.date(2025, 3, 1)), source_row=2
        ),
        synthetic_row(
            event_id="E-2", service_code="2", start_date=_at(dt.date(2025, 11, 1)), source_row=3
        ),
        synthetic_row(
            event_id="E-3", service_code="3", start_date=_at(dt.date(2026, 3, 1)), source_row=4
        ),
        synthetic_row(
            event_id="E-4", service_code="4", start_date=_at(dt.date(2026, 12, 1)), source_row=5
        ),
    ]
    publish_programme(rows=rows)
    snapshot = get_current_event_programme_snapshot()

    assert analytics.count_year_to_date(snapshot, year=2026, today=today) == 1
    assert analytics.count_year_to_date(snapshot, year=2025, today=today) == 1
    # The whole-programme comparison is a different question and sees both.
    assert analytics.counts_by_year(snapshot)[2026] == 2


def test_year_to_date_clamps_a_leap_day(publish_programme):
    rows = [
        synthetic_row(
            event_id="E-1", service_code="1", start_date=_at(dt.date(2023, 2, 27)), source_row=2
        )
    ]
    publish_programme(rows=rows)
    snapshot = get_current_event_programme_snapshot()
    # 29 February 2024 has no counterpart in 2023; the window must not roll into
    # March.
    assert analytics.count_year_to_date(snapshot, year=2023, today=dt.date(2024, 2, 29)) == 1


# ---------------------------------------------------------------------------
# Seasonality
# ---------------------------------------------------------------------------


def test_seasonality_uses_complete_years_only(publish_programme):
    today = timezone.localdate()
    rows = []
    index = 0
    for year in (today.year - 3, today.year - 2, today.year - 1, today.year):
        for month in (3, 9):
            index += 1
            rows.append(
                synthetic_row(
                    event_id=f"E-{index}",
                    service_code=str(index),
                    start_date=_at(dt.date(year, month, 5)),
                    source_row=index + 1,
                )
            )
    publish_programme(rows=rows)
    snapshot = get_current_event_programme_snapshot()

    complete = analytics.complete_years_for(snapshot, today=today)
    assert today.year not in complete
    season = analytics.seasonality(snapshot, complete_years=complete)
    march = next(row for row in season if row.month == 3)
    assert march.years == len(complete)
    assert march.median == 1.0


def test_seasonality_needs_two_complete_years(publish_programme):
    rows = [
        synthetic_row(
            event_id="E-1", service_code="1", start_date=_at(dt.date(2099, 3, 4)), source_row=2
        )
    ]
    publish_programme(rows=rows)
    snapshot = get_current_event_programme_snapshot()
    assert analytics.seasonality(snapshot, complete_years=(2099,)) == ()
