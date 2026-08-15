"""The Huvikaitse pillar states the register's own figures, not its own.

The executive overview reads this domain through `get_legal_work_executive`,
and every figure it shows must equal what the Õigusloome dashboard computes
from the same snapshot. These tests pin that equality with known values, so a
future change that gave the overview a private definition of "sent this year"
— a different cutoff, a different population, a different snapshot — fails
here rather than shipping two disagreeing numbers.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.legal_work import analytics
from apps.legal_work.executive import get_legal_work_executive
from apps.legal_work.importer import import_artifact
from apps.legal_work.selectors import get_legal_work_summary
from apps.legal_work.workbook import DATA_COLUMNS_V12

from .workbook_factory import synthetic_row, write_workbook

pytestmark = pytest.mark.django_db

REPORTING = dt.date(2026, 8, 10)


def row(**kwargs) -> list:
    """A schema 1.2 DATA row with both feedback counts blank."""
    return synthetic_row(**kwargs) + [None, None]


@pytest.fixture
def summary(register_workbook, tmp_path):
    """A register with sends either side of the cutoff, in both years.

    2026 sends: 10 Jan, 1 Aug -> 2 up to the 10 August reporting date
    2026 send after the cutoff: 20 Sep -> excluded from every YTD figure
    2025 send: 1 Mar -> 1 up to the same calendar day
    Plus one open topic, so the stock figure is non-trivial.
    """
    path = write_workbook(
        tmp_path / "executive-consistency.xlsx",
        rows=[
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
                sent_date=dt.date(2026, 8, 1),
                is_open=False,
            ),
            row(
                record_id="S-3",
                source_year=2026,
                source_row=4,
                sent_status="sent",
                sent_date=dt.date(2026, 9, 20),
                is_open=False,
            ),
            row(
                record_id="S-4",
                source_year=2025,
                source_row=2,
                sent_status="sent",
                sent_date=dt.date(2025, 3, 1),
                is_open=False,
            ),
            row(record_id="O-1", source_year=2026, source_row=5, is_open=True),
        ],
        schema_version="1.2",
        columns=DATA_COLUMNS_V12,
        control_overrides={"reporting_date": REPORTING},
    )
    import_artifact(register_workbook(path), dry_run=False)
    return get_legal_work_summary()


def test_the_pillar_headline_is_the_registers_own_year_on_year(summary):
    executive = get_legal_work_executive(summary)

    assert executive.sent == analytics.sent_year_on_year(summary.snapshot)
    # The known values prove the comparison is the same-calendar-day rule, not
    # merely that two calls of one function agree with each other.
    assert executive.sent.current == 2
    assert executive.sent.previous == 1


def test_the_supporting_facts_are_the_pages_own_counts(summary):
    executive = get_legal_work_executive(summary)
    pressure = analytics.deadline_pressure(summary.snapshot)

    assert executive.open_topics == summary.open_count == 1
    assert executive.topics_this_year == analytics.topics_year_on_year(summary.snapshot)
    assert executive.due_within_7 == pressure.due_within_7
    assert executive.overdue_pending == pressure.overdue_pending
    assert executive.reporting_date == summary.reporting_date == REPORTING
