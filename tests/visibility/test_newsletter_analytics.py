"""The newsletter-analytics section: what it shows and what it refuses to.

The numbers below are synthetic. What is pinned down is the arithmetic a board
would act on, and in particular the three ways it could be quietly wrong:

- an aggregate rate computed as the mean of per-issue percentages, which would
  weight a send to 30 people the same as one to 300;
- an audience point summed from a partial reading, which draws a cliff on the
  chart the day a segment is renamed;
- an unrelated mailing counted as a newsletter issue.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.visibility.models import (
    SmailyCampaign,
    SmailyCampaignStats,
    VisibilityMetric,
)
from apps.visibility.newsletter_page import ALL_NEWSLETTERS, build_newsletter_section
from apps.visibility.smaily import SegmentReading, SegmentRow
from apps.visibility.smaily_selectors import (
    get_campaign_performance,
    get_newsletter_aggregate,
    get_subscriber_series,
)
from apps.visibility.smaily_sync import synchronize_smaily

pytestmark = pytest.mark.django_db

ETEATAJA = VisibilityMetric.NEWSLETTER_ETEATAJA
ENEWS = VisibilityMetric.NEWSLETTER_ENEWS

DAY = dt.date(2026, 7, 1)


class FakeCollector:
    def __init__(self, segments):
        self.segments = segments

    def collect_segments(self, *, observed_on=None):
        return SegmentReading(observed_on=observed_on, segments=self.segments).validate()


def read(day, *, members=100, others=200, enews=30, evestnik=40, drop=()):
    rows = [
        SegmentRow(2690, "E-teataja list", members),
        SegmentRow(2691, "E-teataja list mitteliikmed", others),
        SegmentRow(2711, "E-News list", enews),
        SegmentRow(2692, "E-vestnik list - liikmed ja mitteliikmed koos", evestnik),
    ]
    rows = tuple(row for row in rows if row.segment_id not in drop)
    synchronize_smaily(observed_on=day, collector=FakeCollector(rows))


def issue(campaign_id, *, newsletter=ETEATAJA, days_ago=1, delivered=1000, opened=500, clicks=50):
    campaign = SmailyCampaign.objects.create(
        campaign_id=campaign_id,
        name=f"Number {campaign_id}",
        template_name="e-Teataja",
        newsletter=newsletter,
        status="COMPLETED",
        completed_at=timezone.now() - dt.timedelta(days=days_ago),
    )
    if delivered is None:
        return campaign
    from apps.sources.models import ImportRun, SourceArtifact

    artifact = SourceArtifact.objects.first()
    run = ImportRun.objects.first()
    SmailyCampaignStats.objects.create(
        campaign=campaign,
        artifact=artifact,
        import_run=run,
        observed_at=timezone.now(),
        checksum=f"{campaign_id:064d}",
        revision=1,
        is_current=True,
        total_count=delivered + 10,
        delivered_count=delivered,
        opened_count=opened,
        unique_click_count=clicks,
    )
    return campaign


# -- the audience series ----------------------------------------------------


def test_eteataja_sums_its_two_segments_per_day():
    read(DAY)
    read(DAY + dt.timedelta(days=1), members=110, others=210)

    series = get_subscriber_series(ETEATAJA)
    assert [point.subscribers for point in series.points] == [300, 320]
    assert series.change == 20


def test_a_day_missing_one_segment_produces_no_point_at_all():
    """Summing what was read would draw a cliff, and a cliff reads as a
    collapse."""
    read(DAY)
    read(DAY + dt.timedelta(days=1), drop=(2691,))

    series = get_subscriber_series(ETEATAJA)
    assert [point.observed_on for point in series.points] == [DAY]
    # The newsletter that was fully read is unaffected.
    assert len(get_subscriber_series(ENEWS).points) == 2


def test_a_single_reading_is_a_figure_not_a_trend():
    read(DAY)
    series = get_subscriber_series(ENEWS)

    assert series.has_points
    assert not series.is_drawable
    assert series.change is None


def test_no_reading_produces_no_points_rather_than_zeros():
    series = get_subscriber_series(ENEWS)
    assert series.points == ()
    assert series.latest is None


# -- aggregate rates --------------------------------------------------------


def test_an_aggregate_rate_weights_by_size_rather_than_averaging_percentages():
    """The failure this test exists for.

    Two issues: 1 000 delivered with 500 opens (50%), and 100 delivered with 10
    opens (10%). The mean of the percentages is 30%. The truthful figure is
    510/1 100 = 46,4%, because the big send is most of what happened.
    """
    read(DAY)
    issue(1, delivered=1000, opened=500)
    issue(2, delivered=100, opened=10)

    aggregate = get_newsletter_aggregate(ETEATAJA)
    assert aggregate.delivered == 1100
    assert aggregate.opened == 510
    assert aggregate.open_rate == pytest.approx(510 / 1100)
    assert aggregate.open_rate != pytest.approx(0.30)


def test_click_to_open_uses_opens_as_its_denominator():
    read(DAY)
    issue(1, delivered=1000, opened=500, clicks=50)

    aggregate = get_newsletter_aggregate(ETEATAJA)
    assert aggregate.click_rate == pytest.approx(50 / 1000)
    assert aggregate.click_to_open_rate == pytest.approx(50 / 500)


def test_an_aggregate_with_nothing_measured_has_no_rate():
    aggregate = get_newsletter_aggregate(ETEATAJA)
    assert not aggregate.has_data
    assert aggregate.open_rate is None


# -- which issues count -----------------------------------------------------


def test_an_unclassified_mailing_is_listed_under_muu():
    """Event calendars and one-off letters are sends, and they are shown.

    This test used to assert the opposite — that an unclassified campaign was
    absent from the list. That was the defect: the list excluded every campaign
    the classifier did not recognise, which on the real account meant 2 105 of
    3 194 sends. Classification labels a send; it does not decide whether the
    send happened.
    """
    from apps.visibility.smaily_campaigns import OTHER_LABEL

    read(DAY)
    issue(1)
    SmailyCampaign.objects.create(
        campaign_id=9001,
        name="Kaubanduskoja sündmuste kalender",
        template_name="Ürituste kalender 04.08.26",
        newsletter="",
        status="COMPLETED",
        completed_at=timezone.now(),
    )

    rows = {row.campaign_id: row for row in get_campaign_performance()}
    assert set(rows) == {1, 9001}
    assert rows[9001].newsletter_label == OTHER_LABEL


def test_filtering_by_newsletter_shows_only_its_issues():
    read(DAY)
    issue(1, newsletter=ETEATAJA)
    issue(2, newsletter=ENEWS)

    assert [row.campaign_id for row in get_campaign_performance(metric=ENEWS)] == [2]


def test_an_issue_with_no_statistics_shows_a_dash_not_a_zero():
    read(DAY)
    issue(1, delivered=None)

    row = get_campaign_performance()[0]
    assert row.delivered is None
    assert not row.has_statistics
    assert row.delivered_label == ""
    assert row.open_rate_label == ""


def test_a_rate_label_is_a_percentage_not_a_fraction():
    """`floatformat:"-1%"` would have rendered a 50% rate as `0,5%`."""
    read(DAY)
    issue(1, delivered=1000, opened=509)

    row = get_campaign_performance()[0]
    assert row.open_rate_label.startswith("50,9")
    assert row.open_rate_label.endswith("%")


# -- the section ------------------------------------------------------------


def test_the_section_defaults_to_all_newsletters():
    section = build_newsletter_section()
    assert section.active == ALL_NEWSLETTERS
    assert not section.is_filtered
    assert len(section.audience) == 3


def test_a_hand_typed_filter_falls_back_rather_than_reaching_a_query():
    section = build_newsletter_section(newsletter_key="'; drop table--")
    assert section.active == ALL_NEWSLETTERS


def test_filtering_narrows_the_audience_cards_and_adds_the_rates():
    read(DAY)
    issue(1)

    section = build_newsletter_section(newsletter_key=ETEATAJA)
    assert section.is_filtered
    assert [card.series.metric for card in section.audience] == [ETEATAJA]
    assert section.figures
    assert any("kohale toimetatud" in figure.note.lower() for figure in section.figures)


def test_an_empty_section_says_history_cannot_be_backfilled():
    section = build_newsletter_section()
    assert not section.has_any_data
    assert section.coverage_note == ""


def test_the_coverage_note_is_computed_but_no_longer_shown():
    """The sentence was struck out on the board's marked-up print.

    It is still derived — it is the honest statement of where the history
    begins, and the limitation it describes is real — but the section does not
    print it. `docs/newsletter-audience.md` carries the same fact.
    """
    read(DAY)
    read(DAY + dt.timedelta(days=1), enews=31)

    note = build_newsletter_section().coverage_note
    assert "01.07.2026" in note


def test_the_page_does_not_print_the_coverage_note(viewer_client):
    from django.urls import reverse

    read(DAY)
    page = viewer_client.get(reverse("visibility")).content.decode()

    assert "Varasemat ajalugu ei ole võimalik koguda" not in page


# -- the page ---------------------------------------------------------------


def test_the_page_renders_the_section(viewer_client):
    read(DAY)
    issue(1)

    page = viewer_client.get(reverse("visibility")).content.decode()
    assert "Uudiskirjade tulemused" in page
    assert "Avamismäär" in page


def test_the_page_carries_the_filter_through(viewer_client):
    read(DAY)
    issue(1, newsletter=ETEATAJA)
    issue(2, newsletter=ENEWS)

    page = viewer_client.get(reverse("visibility"), {"uudiskiri": str(ENEWS)}).content.decode()
    assert "Number 2" in page
    assert "Number 1" not in page
