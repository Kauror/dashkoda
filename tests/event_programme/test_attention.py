"""Web attention windows, and the difference between unmeasured and zero.

The rules held here are the ones that make a comparison between two events
honest:

- a **complete** 30-day pre-event window or no figure at all. A half-covered
  window produces a smaller number for the same event, which is worse than no
  number because it looks like a measurement;
- an unmeasured page is `None`, never `0`;
- the three windows — recent, pre-event and total — stay distinct.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.event_programme import attention
from apps.event_programme.selectors import get_current_event_programme_snapshot
from apps.visibility.models import Ga4DailySnapshot, Ga4PageDaily

from .workbook_factory import synthetic_row

pytestmark = pytest.mark.django_db

PAGE = "https://www.koda.ee/et/sundmused/sunteetiline-1"
PATH = "/et/sundmused/sunteetiline-1"
OTHER_PAGE = "https://www.koda.ee/et/sundmused/sunteetiline-2"
OTHER_PATH = "/et/sundmused/sunteetiline-2"


def _at(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time())


@pytest.fixture
def measure_days():
    """Publish one GA4 reporting day per date, with page detail."""
    from apps.sources.services import build_import_run, register_external_reference
    from apps.visibility.bootstrap import ensure_ga4_source

    source = ensure_ga4_source()
    artifact = register_external_reference(
        source=source,
        external_reference="synthetic:event-attention",
        original_name="synthetic.json",
        mime_type="application/json",
        sha256="b2" * 32,
        size_bytes=10,
    )
    run = build_import_run(
        artifact=artifact,
        importer_name="synthetic_event_attention",
        schema_version="2.0",
        dry_run=False,
    )

    def publish(start: dt.date, end: dt.date, pages: dict[str, int]):
        day = start
        while day <= end:
            snapshot = Ga4DailySnapshot.objects.create(
                source=source,
                artifact=artifact,
                import_run=run,
                report_date=day,
                observed_at=timezone.now(),
                checksum=f"{day.toordinal():064d}",
                is_current_for_date=True,
                has_page_detail=True,
                sessions=1,
            )
            for path, views in pages.items():
                Ga4PageDaily.objects.create(
                    snapshot=snapshot, report_date=day, path=path, page_views=views
                )
            day += dt.timedelta(days=1)

    return publish


def _publish(publish_programme, rows):
    publish_programme(rows=rows)
    from apps.event_programme import analytics

    return list(analytics.items_for(get_current_event_programme_snapshot()))


def test_a_complete_window_is_measured(publish_programme, measure_days):
    today = dt.date(2026, 6, 30)
    start = dt.date(2026, 6, 1)
    # Coverage comfortably contains [start-29, start].
    measure_days(dt.date(2026, 4, 1), today, {PATH: 3})

    items = _publish(
        publish_programme,
        [
            synthetic_row(
                event_id="E-1",
                service_code="1",
                start_date=_at(start),
                public_url=PAGE,
                public_link_status="linked_embedded_latest",
                source_row=2,
            )
        ],
    )
    result = attention.attach_attention(items, today=today)
    row = result["E-1"]

    assert row.window_is_complete is True
    assert row.pre_event_views == 30 * 3
    assert row.late_views == 7 * 3
    assert row.total_views == 91 * 3


def test_a_partial_window_yields_no_figure(publish_programme, measure_days):
    """Measurement began inside the window, so there is no comparable number."""
    start = dt.date(2026, 6, 1)
    measure_days(dt.date(2026, 5, 20), dt.date(2026, 6, 30), {PATH: 5})

    items = _publish(
        publish_programme,
        [
            synthetic_row(
                event_id="E-1",
                service_code="1",
                start_date=_at(start),
                public_url=PAGE,
                public_link_status="linked_embedded_latest",
                source_row=2,
            )
        ],
    )
    row = attention.attach_attention(items, today=dt.date(2026, 6, 30))["E-1"]

    assert row.window_is_complete is False
    assert row.pre_event_views is None
    assert row.has_fair_window is False
    # The total is still a real measurement and is still shown.
    assert row.total_views is not None


def test_an_unmeasured_page_is_none_not_zero(publish_programme, measure_days):
    measure_days(dt.date(2026, 4, 1), dt.date(2026, 6, 30), {PATH: 4})
    items = _publish(
        publish_programme,
        [
            synthetic_row(
                event_id="E-1",
                service_code="1",
                start_date=_at(dt.date(2026, 6, 1)),
                public_url=OTHER_PAGE,
                public_link_status="linked_embedded_latest",
                source_row=2,
            )
        ],
    )
    row = attention.attach_attention(items, today=dt.date(2026, 6, 30))["E-1"]
    assert row.total_views is None
    assert row.pre_event_views is None
    assert row.is_measured is False


def test_an_event_with_no_public_page_is_absent(publish_programme, measure_days):
    """Absent from the result, not present with zeros."""
    measure_days(dt.date(2026, 4, 1), dt.date(2026, 6, 30), {PATH: 4})
    items = _publish(
        publish_programme,
        [
            synthetic_row(
                event_id="E-1", service_code="1", start_date=_at(dt.date(2026, 6, 1)), source_row=2
            )
        ],
    )
    assert attention.attach_attention(items, today=dt.date(2026, 6, 30)) == {}


def test_an_undated_event_gets_recent_views_but_no_window(publish_programme, measure_days):
    measure_days(dt.date(2026, 4, 1), dt.date(2026, 6, 30), {PATH: 2})
    items = _publish(
        publish_programme,
        [
            synthetic_row(
                event_id="E-1",
                service_code="1",
                start_date=None,
                end_date=None,
                event_status="date_unknown",
                date_parse_status="unparsed",
                public_url=PAGE,
                public_link_status="linked_embedded_latest",
                source_row=2,
            )
        ],
    )
    row = attention.attach_attention(items, today=dt.date(2026, 6, 30))["E-1"]
    assert row.pre_event_views is None
    assert row.window_is_complete is False
    assert row.recent_views == 30 * 2


def test_two_events_sharing_a_page_get_their_own_windows(publish_programme, measure_days):
    """One page, two events, two different questions about it.

    The traffic is the page's, so both rows see it — but each sees the window
    ending on **its own** start date, and neither is dropped.
    """
    measure_days(dt.date(2026, 1, 1), dt.date(2026, 6, 30), {PATH: 1})
    items = _publish(
        publish_programme,
        [
            synthetic_row(
                event_id="E-1",
                service_code="1",
                start_date=_at(dt.date(2026, 3, 1)),
                public_url=PAGE,
                public_link_status="linked_embedded_latest",
                source_row=2,
            ),
            synthetic_row(
                event_id="E-2",
                service_code="2",
                start_date=_at(dt.date(2026, 6, 1)),
                public_url=PAGE,
                public_link_status="linked_embedded_latest",
                source_row=3,
            ),
        ],
    )
    result = attention.attach_attention(items, today=dt.date(2026, 6, 30))
    assert set(result) == {"E-1", "E-2"}
    assert result["E-1"].pre_event_views == 30
    assert result["E-2"].pre_event_views == 30
    # The page's total is the page's; it is not doubled by having two readers.
    assert result["E-1"].total_views == result["E-2"].total_views


def test_the_windows_are_three_different_questions(publish_programme, measure_days):
    """A long history and a busy final month must not produce one number."""
    today = dt.date(2026, 6, 30)
    start = dt.date(2026, 6, 1)
    measure_days(dt.date(2026, 1, 1), dt.date(2026, 5, 2), {PATH: 10})
    measure_days(dt.date(2026, 5, 3), today, {PATH: 1})

    items = _publish(
        publish_programme,
        [
            synthetic_row(
                event_id="E-1",
                service_code="1",
                start_date=_at(start),
                public_url=PAGE,
                public_link_status="linked_embedded_latest",
                source_row=2,
            )
        ],
    )
    row = attention.attach_attention(items, today=today)["E-1"]
    assert row.pre_event_views == 30
    assert row.recent_views == 30
    assert row.total_views == 122 * 10 + 59 * 1


def test_the_benchmark_falls_back_when_a_type_is_thin(publish_programme, measure_days):
    measure_days(dt.date(2026, 1, 1), dt.date(2026, 6, 30), {PATH: 2, OTHER_PATH: 2})
    rows = [
        synthetic_row(
            event_id=f"E-{index}",
            service_code=str(index),
            start_date=_at(dt.date(2026, 5, 1)),
            event_type_key="seminar",
            public_url=PAGE,
            public_link_status="linked_embedded_latest",
            source_row=index + 1,
        )
        for index in range(1, attention.MIN_BENCHMARK_SAMPLE + 1)
    ]
    rows.append(
        synthetic_row(
            event_id="E-rare",
            service_code="99",
            start_date=_at(dt.date(2026, 5, 1)),
            event_type_key="haruldane",
            event_type_label="Haruldane",
            public_url=OTHER_PAGE,
            public_link_status="linked_embedded_latest",
            source_row=99,
        )
    )
    items = _publish(publish_programme, rows)
    measured = attention.attach_attention(items, today=dt.date(2026, 6, 30))
    by_type, overall = attention.benchmark_pools(items, measured)

    rare = next(item for item in items if item.event_id == "E-rare")
    benchmark = attention.benchmark_for(rare, measured, by_type=by_type, overall=overall)
    assert benchmark is not None
    assert benchmark.scope == "all"

    typical = next(item for item in items if item.event_id == "E-1")
    typed = attention.benchmark_for(typical, measured, by_type=by_type, overall=overall)
    assert typed.scope == "type"
    assert typed.sample >= attention.MIN_BENCHMARK_SAMPLE


def test_the_distribution_needs_a_sample(publish_programme, measure_days):
    measure_days(dt.date(2026, 1, 1), dt.date(2026, 6, 30), {PATH: 2})
    items = _publish(
        publish_programme,
        [
            synthetic_row(
                event_id="E-1",
                service_code="1",
                start_date=_at(dt.date(2026, 5, 1)),
                public_url=PAGE,
                public_link_status="linked_embedded_latest",
                source_row=2,
            )
        ],
    )
    measured = attention.attach_attention(items, today=dt.date(2026, 6, 30))
    distribution = attention.distribution_of(measured)
    assert distribution.has_data is False
    assert distribution.median is None
    assert distribution.eligible == 1


def test_coverage_states_its_denominators(publish_programme, measure_days):
    measure_days(dt.date(2026, 1, 1), dt.date(2026, 6, 30), {PATH: 2})
    items = _publish(
        publish_programme,
        [
            synthetic_row(
                event_id="E-1",
                service_code="1",
                start_date=_at(dt.date(2026, 5, 1)),
                public_url=PAGE,
                public_link_status="linked_embedded_latest",
                source_row=2,
            ),
            synthetic_row(
                event_id="E-2",
                service_code="2",
                start_date=_at(dt.date(2026, 5, 1)),
                public_url=OTHER_PAGE,
                public_link_status="linked_embedded_latest",
                source_row=3,
            ),
            synthetic_row(
                event_id="E-3",
                service_code="3",
                start_date=_at(dt.date(2026, 5, 1)),
                source_row=4,
            ),
        ],
    )
    measured = attention.attach_attention(items, today=dt.date(2026, 6, 30))
    coverage = attention.coverage_report(items, measured)

    assert coverage.population == 3
    assert coverage.with_page == 2
    assert coverage.measured == 1
    assert coverage.complete_window == 1


def test_no_ga4_coverage_yields_nothing_rather_than_zeros(publish_programme):
    items = _publish(
        publish_programme,
        [
            synthetic_row(
                event_id="E-1",
                service_code="1",
                start_date=_at(dt.date(2026, 5, 1)),
                public_url=PAGE,
                public_link_status="linked_embedded_latest",
                source_row=2,
            )
        ],
    )
    assert attention.attach_attention(items, today=timezone.localdate()) == {}
