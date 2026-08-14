"""Planning lead and price structure, and the distinctions that make them safe.

Two claims are held here above all:

- **`planning_lead_days` is the source's own arithmetic.** The importer stores
  what the generator computed rather than deriving a second figure, so a change
  in the generator's definition surfaces as a changed number instead of a silent
  disagreement between two rules;
- **an unknown price is never a free one.** `missing`, `tba` and `review` are
  three separate statements that the source could not say, and `free` is a
  statement that it could.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.event_programme import analytics
from apps.event_programme.selectors import get_current_event_programme_snapshot

from .workbook_factory import synthetic_row

pytestmark = pytest.mark.django_db


def _at(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time())


START = dt.date(2099, 6, 1)


def _row(index: int, **kwargs):
    kwargs.setdefault("start_date", _at(START))
    return synthetic_row(
        event_id=f"E-{index}", service_code=str(index), source_row=index + 1, **kwargs
    )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_the_stored_lead_is_the_source_figure_not_a_recomputation(publish_programme):
    """A workbook whose lead disagrees with its own dates is stored as given.

    Recomputing would hide the disagreement, and the generator owns the
    definition. `docs/event-programme-feed.md` records that the two reconcile on
    every row of the real export; this test is what would fail if that stopped
    being true and somebody "fixed" it here.
    """
    publish_programme(
        rows=[_row(1, added_date=_at(START - dt.timedelta(days=30)), planning_lead_days=99)]
    )
    item = analytics.items_for(get_current_event_programme_snapshot()).get()
    assert item.planning_lead_days == 99
    assert item.added_date == START - dt.timedelta(days=30)


def test_prices_and_status_reach_the_model(publish_programme):
    publish_programme(
        rows=[_row(1, price_status="paid", member_price_eur=40, nonmember_price_eur=78)]
    )
    item = analytics.items_for(get_current_event_programme_snapshot()).get()
    assert item.member_price_eur == Decimal("40.00")
    assert item.nonmember_price_eur == Decimal("78.00")
    assert item.price_status == "paid"
    assert item.member_price_advantage == Decimal("38.00")


def test_the_discarded_columns_have_no_field(publish_programme):
    """Raw echoes, discounts, groups and later prices stay out of the model.

    A field that does not exist cannot leak, and the workbook contract still
    verifies these columns so a generator change cannot pass unnoticed.
    """
    publish_programme(rows=[_row(1)])
    item = analytics.items_for(get_current_event_programme_snapshot()).get()
    for absent in (
        "member_price_raw",
        "nonmember_price_raw",
        "later_member_price_eur",
        "later_nonmember_price_eur",
        "discount_code",
        "discount_raw",
        "group_raw",
        "group_secondary_raw",
    ):
        assert not hasattr(item, absent), f"{absent} must not be a model field"


def test_member_price_advantage_needs_both_prices(publish_programme):
    publish_programme(
        rows=[_row(1, price_status="tba", member_price_eur=40, nonmember_price_eur=None)]
    )
    item = analytics.items_for(get_current_event_programme_snapshot()).get()
    assert item.member_price_advantage is None


# ---------------------------------------------------------------------------
# Planning statistics
# ---------------------------------------------------------------------------


def test_a_negative_lead_is_excluded_and_disclosed(publish_programme):
    """An event entered after it ran is not a short planning lead."""
    publish_programme(
        rows=[
            _row(1, added_date=_at(START - dt.timedelta(days=10))),
            _row(2, added_date=_at(START - dt.timedelta(days=20))),
            _row(3, added_date=_at(START + dt.timedelta(days=40))),
        ]
    )
    planning = analytics.build_planning(get_current_event_programme_snapshot())

    assert planning.measured == 2
    assert planning.retroactive == 1
    assert planning.median_lead == 15.0
    # Not clamped: the row is still there with its real negative figure.
    rows = analytics.items_for(get_current_event_programme_snapshot())
    assert rows.filter(planning_lead_days__lt=0).count() == 1


def test_a_missing_added_date_is_counted_not_assumed(publish_programme):
    publish_programme(
        rows=[
            _row(1, added_date=_at(START - dt.timedelta(days=10))),
            _row(2, added_date=None),
        ]
    )
    planning = analytics.build_planning(get_current_event_programme_snapshot())
    assert planning.measured == 1
    assert planning.missing == 1
    assert planning.population == 2
    assert planning.coverage == pytest.approx(50.0)


def test_planning_bands_reconcile_with_the_measured_population(publish_programme):
    publish_programme(
        rows=[
            _row(index, added_date=_at(START - dt.timedelta(days=days)))
            for index, days in enumerate((3, 20, 45, 70, 200), start=1)
        ]
    )
    planning = analytics.build_planning(get_current_event_programme_snapshot())
    assert sum(row.count for row in planning.bands) == planning.measured == 5
    assert [row.count for row in planning.bands] == [1, 1, 1, 1, 1]


def test_a_thin_event_type_is_not_ranked(publish_programme):
    """A median of two events is one number wearing a statistic's authority."""
    rows = [
        _row(
            index,
            event_type_key="seminar",
            added_date=_at(START - dt.timedelta(days=30 + index)),
        )
        for index in range(1, analytics.MIN_SAMPLE + 1)
    ]
    rows += [
        _row(
            100 + index,
            event_type_key="haruldane",
            event_type_label="Haruldane",
            added_date=_at(START - dt.timedelta(days=5)),
        )
        for index in range(1, 3)
    ]
    publish_programme(rows=rows)
    planning = analytics.build_planning(get_current_event_programme_snapshot())
    assert [stat.key for stat in planning.by_type] == ["seminar"]


# ---------------------------------------------------------------------------
# Price structure
# ---------------------------------------------------------------------------


def test_unknown_price_is_not_free(publish_programme):
    publish_programme(
        rows=[
            _row(1, price_status="free", member_price_eur=0, nonmember_price_eur=0),
            _row(2, price_status="missing", member_price_eur=None, nonmember_price_eur=None),
            _row(3, price_status="tba", member_price_eur=None, nonmember_price_eur=None),
            _row(4, price_status="review", member_price_eur=None, nonmember_price_eur=None),
        ]
    )
    prices = analytics.build_prices(get_current_event_programme_snapshot())

    assert prices.free_count == 1
    assert prices.unknown_count == 3
    labels = {row.key: row.count for row in prices.status.all_rows}
    assert labels == {"free": 1, "missing": 1, "tba": 1, "review": 1}


def test_a_stated_zero_is_a_real_price(publish_programme):
    """`0` from the generator is a measurement; a blank is an absence."""
    publish_programme(
        rows=[_row(1, price_status="free", member_price_eur=0, nonmember_price_eur=0)]
    )
    item = analytics.items_for(get_current_event_programme_snapshot()).get()
    assert item.member_price_eur == Decimal("0.00")
    assert item.member_price_eur is not None


def test_price_status_reconciles_with_the_population(publish_programme):
    publish_programme(
        rows=[
            _row(1, price_status="paid"),
            _row(2, price_status="free", member_price_eur=0, nonmember_price_eur=0),
            _row(3, price_status="mixed"),
            _row(4, price_status=""),
        ]
    )
    prices = analytics.build_prices(get_current_event_programme_snapshot())
    assert prices.status.total == 4
    assert prices.status.counted == 4
    assert any(row.is_unknown for row in prices.status.all_rows)


def test_an_unknown_price_status_still_renders(publish_programme):
    """A seventh status the generator invents must import and show its key."""
    publish_programme(rows=[_row(1, price_status="uus_olek")])
    prices = analytics.build_prices(get_current_event_programme_snapshot())
    keys = {row.key for row in prices.status.all_rows}
    assert "uus_olek" in keys


def test_median_price_by_type_needs_a_sample(publish_programme):
    rows = [
        _row(index, event_type_key="seminar", member_price_eur=40 + index)
        for index in range(1, analytics.MIN_SAMPLE + 1)
    ]
    rows += [_row(100, event_type_key="haruldane", member_price_eur=900)]
    publish_programme(rows=rows)
    prices = analytics.build_prices(get_current_event_programme_snapshot())
    assert [stat.key for stat in prices.member_by_type] == ["seminar"]
