"""Which segment feeds which newsletter, and what happens when one stops.

No database. The mapping is a registry decision and these are the tests that
stop it drifting into a guess — in particular, the two that would each have been
a silent wrong number on a board's dashboard:

- e-Teataja is two lists added together, and the sum must be the sum;
- a segment whose name no longer matches is **withheld**, not substituted.
"""

from __future__ import annotations

import datetime as dt

from apps.visibility.models import VisibilityMetric
from apps.visibility.smaily import SegmentReading, SegmentRow
from apps.visibility.smaily_segments import (
    MAPPED_SEGMENT_IDS,
    NEWSLETTERS,
    NEWSLETTERS_BY_METRIC,
    resolve_all,
    resolve_audience,
)

DAY = dt.date(2026, 8, 10)

#: The shape of the real account, with invented sizes.
FULL = SegmentReading(
    observed_on=DAY,
    segments=(
        SegmentRow(2690, "E-teataja list", 100),
        SegmentRow(2691, "E-teataja list mitteliikmed", 200),
        SegmentRow(2711, "E-News list", 30),
        SegmentRow(2692, "E-vestnik list - liikmed ja mitteliikmed koos", 40),
        # An unmapped one-off send audience, of which the account holds dozens.
        SegmentRow(3090, "09.06.26 emta", 672),
    ),
)


def audience_for(metric, reading=FULL):
    return resolve_audience(NEWSLETTERS_BY_METRIC[metric], reading)


# -- the mapping ------------------------------------------------------------


def test_every_newsletter_metric_is_mapped():
    assert {spec.metric for spec in NEWSLETTERS} == {
        VisibilityMetric.NEWSLETTER_ETEATAJA,
        VisibilityMetric.NEWSLETTER_ENEWS,
        VisibilityMetric.NEWSLETTER_EVESTNIK,
    }


def test_eteataja_is_the_two_lists_added():
    audience = audience_for(VisibilityMetric.NEWSLETTER_ETEATAJA)
    assert audience.is_available
    assert audience.total == 300
    assert [(part.label, part.subscribers) for part in audience.visible_parts] == [
        ("Liikmed", 100),
        ("Mitteliikmed", 200),
    ]


def test_a_single_list_newsletter_shows_no_split():
    audience = audience_for(VisibilityMetric.NEWSLETTER_ENEWS)
    assert audience.total == 30
    assert audience.visible_parts == ()


def test_an_unmapped_segment_contributes_to_nothing():
    """The account is full of dated one-off segments. None may reach a metric."""
    assert 3090 not in MAPPED_SEGMENT_IDS
    assert sum(a.total or 0 for a in resolve_all(FULL)) == 100 + 200 + 30 + 40


def test_the_three_newsletters_are_never_added_together():
    """Each resolves on its own; nothing in this module produces a combined
    audience, because a reader on two lists would be counted twice."""
    audiences = resolve_all(FULL)
    assert [a.total for a in audiences] == [300, 30, 40]
    assert not hasattr(audiences, "total")


# -- withholding ------------------------------------------------------------


def test_a_missing_segment_withholds_the_metric_rather_than_shrinking_it():
    """The failure that would otherwise be invisible.

    Dropping the non-members list would leave e-Teataja reporting 100 instead of
    300 — a plausible-looking number, on a real chart, with no error anywhere.
    """
    without = SegmentReading(
        observed_on=DAY,
        segments=tuple(row for row in FULL.segments if row.segment_id != 2691),
    )
    audience = audience_for(VisibilityMetric.NEWSLETTER_ETEATAJA, without)
    assert not audience.is_available
    assert audience.total is None
    assert "2691" in audience.withheld_reason


def test_a_renamed_segment_is_withheld_not_trusted():
    """An id can be reused. A name that has become something else is a stop."""
    renamed = SegmentReading(
        observed_on=DAY,
        segments=(SegmentRow(2711, "Jõulukampaania 2026", 5000),),
    )
    audience = audience_for(VisibilityMetric.NEWSLETTER_ENEWS, renamed)
    assert not audience.is_available
    assert audience.total is None
    assert "2711" in audience.withheld_reason


def test_a_tidied_up_name_still_matches():
    """The guard must not fire on ordinary editing, or it is just noise."""
    tidied = SegmentReading(
        observed_on=DAY,
        segments=(SegmentRow(2711, "E-News (liikmed ja mitteliikmed)", 30),),
    )
    assert audience_for(VisibilityMetric.NEWSLETTER_ENEWS, tidied).total == 30


def test_withholding_one_newsletter_leaves_the_others_publishable():
    partial = SegmentReading(
        observed_on=DAY,
        segments=tuple(row for row in FULL.segments if row.segment_id != 2690),
    )
    by_metric = {a.metric: a for a in resolve_all(partial)}
    assert by_metric[VisibilityMetric.NEWSLETTER_ETEATAJA].total is None
    assert by_metric[VisibilityMetric.NEWSLETTER_ENEWS].total == 30
    assert by_metric[VisibilityMetric.NEWSLETTER_EVESTNIK].total == 40


def test_an_empty_reading_withholds_everything_and_reports_no_zeros():
    empty = SegmentReading(observed_on=DAY, segments=())
    for audience in resolve_all(empty):
        assert audience.total is None
        assert audience.withheld_reason
