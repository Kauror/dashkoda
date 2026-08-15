"""When the executive overview may compare news reading with the window before.

The news page refuses a previous window that reaches before GA4 collection
began — a partial sum standing in for a full one describes the collector's
history, not the readers'. The executive summary feeds the front page's
`news-views` signal from the same arithmetic, so it must apply the same
refusal: both now go through `analytics.previous_traffic_within`, and these
tests pin the two behaviours that rule produces.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.news import analytics
from apps.news.executive import get_news_executive
from apps.news.selectors import get_news_summary
from apps.visibility.website_period import parse_period

from .conftest import article

pytestmark = pytest.mark.django_db


def test_a_previous_window_outside_coverage_yields_no_comparison(ga4):
    """Coverage of 45 days: the default window's predecessor is refused.

    The article was read inside the covered tail of the previous window. An
    ungated sum would report those views as the whole previous period and
    announce a 75% fall that never happened; the rule returns no comparison
    at all, and no signal can state one.
    """
    item = article("varane", published=dt.date(2026, 1, 2))
    ga4(
        start=dt.date(2026, 1, 1),
        end=dt.date(2026, 2, 14),
        views={item.path: {dt.date(2026, 1, 10): 40, dt.date(2026, 2, 1): 10}},
    )

    executive = get_news_executive(get_news_summary())

    assert executive.news_views == 10
    assert executive.previous_news_views is None
    assert executive.change_pct is None
    assert executive.signals == ()


def test_a_fully_measured_previous_window_still_compares(ga4):
    """Six months of coverage: the comparison exists and the signal may fire."""
    item = article("pikk", published=dt.date(2026, 1, 2))
    ga4(
        views={
            item.path: {
                dt.date(2026, 6, 10): 30,  # inside the default 30-day window
                dt.date(2026, 5, 20): 10,  # inside the previous window
            }
        }
    )

    executive = get_news_executive(get_news_summary())

    assert executive.news_views == 30
    assert executive.previous_news_views == 10
    assert executive.change_pct == pytest.approx(200.0)
    assert [signal.key for signal in executive.signals] == ["news-views"]


def test_the_executive_and_the_page_share_one_refusal(ga4):
    """`previous_traffic_within` is the single rule both surfaces read."""
    item = article("uks", published=dt.date(2026, 1, 2))
    ga4(
        start=dt.date(2026, 1, 1),
        end=dt.date(2026, 2, 14),
        views={item.path: {dt.date(2026, 1, 10): 40}},
    )
    coverage = analytics.get_coverage()
    period = parse_period(None, coverage)

    refused = analytics.previous_traffic_within(period.start, period.end, coverage)

    assert refused.news_views is None
    assert not refused.has_data
