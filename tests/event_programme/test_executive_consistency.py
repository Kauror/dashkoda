"""The Kaasamine pillar counts what the Sündmused dashboard counts.

The executive overview reads this domain through `get_events_executive`, and
its figures must equal the domain's own analytics over the same snapshot and
the same day. These tests pin that equality with known values, so the overview
can never grow a private definition of "events this year" — a different grain,
a different cutoff, or the occurrence sheet the domain deliberately refuses.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.event_programme import analytics
from apps.event_programme.executive import get_events_executive
from apps.event_programme.selectors import (
    count_events_starting_within,
    get_event_programme_summary,
)

from .workbook_factory import synthetic_row

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 8, 10)


@pytest.fixture
def summary(publish_programme, monkeypatch):
    """Three events: one past this year, one soon, one past last year.

    `timezone.localdate` is pinned so "year to date" is deterministic — the
    executive and the selectors both read the application day through it.
    """
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)
    publish_programme(
        rows=[
            synthetic_row(
                event_id="E-1",
                service_code="K-1",
                start_date=dt.datetime(2026, 3, 4, 10),
                event_status="past",
                source_year=2026,
                source_sheet="KOOD 2026",
                source_row=2,
            ),
            synthetic_row(
                event_id="E-2",
                service_code="K-2",
                start_date=dt.datetime(2026, 9, 1, 10),
                event_status="upcoming",
                source_year=2026,
                source_sheet="KOOD 2026",
                source_row=3,
            ),
            synthetic_row(
                event_id="E-3",
                service_code="K-3",
                start_date=dt.datetime(2025, 5, 1, 10),
                event_status="past",
                source_year=2025,
                source_sheet="KOOD 2025",
                source_row=2,
            ),
        ]
    )
    return get_event_programme_summary()


def test_the_pillar_counts_are_the_domains_own_counts(summary):
    executive = get_events_executive(summary)
    snapshot = summary.snapshot

    assert (
        executive.events_ytd
        == analytics.count_year_to_date(snapshot, year=TODAY.year, today=TODAY)
        == 1
    )
    assert (
        executive.events_ytd_previous
        == analytics.count_year_to_date(snapshot, year=TODAY.year - 1, today=TODAY)
        == 1
    )
    assert executive.completed_ytd == analytics.count_completed_in_year(
        snapshot, year=TODAY.year, today=TODAY
    )
    assert executive.starting_soon == count_events_starting_within(snapshot) == 1


def test_the_change_is_the_difference_of_the_two_domain_counts(summary):
    executive = get_events_executive(summary)

    # One event by 10 August in each year: no movement, and the sentence says
    # so rather than inventing a direction.
    assert executive.change == 0
    assert executive.meaning == "Sündmusi on sama ajaks täpselt sama palju kui eelmisel aastal."
