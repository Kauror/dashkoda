"""The newsletter-analytics section: what it shows and what it refuses to.

The section is rendered by `/uudised/` and its presenter belongs to
`apps.visibility`, which is why these stayed here when the section moved: they
are about the arithmetic and the wording, not about which page includes it.
`tests/news/test_newsletter_sections.py` covers the placement itself.

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

#: The newsletter material is the `uudiskirjad` focus of `/uudised/` now. Still
#: on Uudised, still owned by `apps.visibility`; only the address gained a
#: parameter naming which of the five views is on screen.
NEWSLETTERS = {"fookus": "uudiskirjad"}

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


def _provenance():
    """An artifact and import run for a synthetic send to hang off.

    `SmailyCampaignStats` requires both, and this used to reach for
    `SourceArtifact.objects.first()` — which is `None` unless some earlier call
    in the same test happened to run `read()` and leave one behind. A test that
    only issues campaigns got a null and a `NotNullViolation`, so the helper
    made a test's correctness depend on the order of the lines above it.
    """
    from apps.sources.models import ImportRun, SourceArtifact
    from apps.sources.services import build_import_run, register_external_reference
    from apps.visibility.bootstrap import ensure_smaily_source

    artifact = SourceArtifact.objects.first()
    if artifact is None:
        artifact = register_external_reference(
            source=ensure_smaily_source(),
            external_reference="synthetic:smaily-campaign-stats",
            original_name="synthetic.json",
            mime_type="application/json",
            sha256="c" * 64,
            size_bytes=10,
        )
    run = ImportRun.objects.first() or build_import_run(
        artifact=artifact,
        importer_name="synthetic_smaily_test",
        schema_version="1.0",
        dry_run=False,
    )
    return artifact, run


def issue(
    campaign_id,
    *,
    name=None,
    newsletter=ETEATAJA,
    days_ago=1,
    delivered=1000,
    opened=500,
    clicks=50,
):
    campaign = SmailyCampaign.objects.create(
        campaign_id=campaign_id,
        name=name or f"Number {campaign_id}",
        template_name="e-Teataja",
        newsletter=newsletter,
        status="COMPLETED",
        completed_at=timezone.now() - dt.timedelta(days=days_ago),
    )
    if delivered is None:
        return campaign
    artifact, run = _provenance()
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


def test_a_hand_typed_filter_falls_back_rather_than_reaching_a_query():
    section = build_newsletter_section(newsletter_key="'; drop table--")
    assert section.active == ALL_NEWSLETTERS


def test_filtering_narrows_the_issues_and_adds_the_rates():
    read(DAY)
    issue(1, newsletter=ETEATAJA)
    issue(2, newsletter=ENEWS)

    section = build_newsletter_section(newsletter_key=ETEATAJA)
    assert section.is_filtered
    assert [row.campaign_id for row in section.issues] == [1]
    assert section.figures
    assert any("kohale toimetatud" in figure.note.lower() for figure in section.figures)


def test_a_section_with_no_sends_has_nothing_to_show(viewer_client):
    """Subscriber readings no longer keep this section alive.

    While the audience rows were here, `has_any_data` was true the moment a
    single list had been read, so an account with three lists and no campaigns
    rendered a populated section listing sizes and nothing else. This section is
    about sends; with none, it says so.
    """
    read(DAY)

    section = build_newsletter_section()
    assert not section.has_any_data

    page = viewer_client.get(reverse("news"), NEWSLETTERS).content.decode()
    body = page[page.index("Uudiskirjade tulemused") :]
    assert "Saadetud uudiskirjad ilmuvad siia pärast esimest Smaily kogumist." in body


def test_the_page_does_not_print_the_coverage_note(viewer_client):
    """The sentence about list history was struck out on the board's print-out.

    It described the subscriber counts, which this section no longer carries at
    all, so it is now gone from the code as well as from the page — including
    from the empty state, which used to repeat it.
    `docs/newsletter-audience.md` carries the fact.
    """
    read(DAY)
    issue(1)
    page = viewer_client.get(reverse("news"), NEWSLETTERS).content.decode()

    assert "Varasemat ajalugu ei ole võimalik koguda" not in page


# -- the page ---------------------------------------------------------------


def test_the_page_renders_the_section(viewer_client):
    read(DAY)
    issue(1)

    page = viewer_client.get(reverse("news"), NEWSLETTERS).content.decode()
    assert "Uudiskirjade tulemused" in page
    # The column is `Avatud`; `Avamismäär` was the struck-out explanatory
    # paragraph's wording, not the table's.
    assert "Avatud" in page


def test_the_page_carries_the_filter_through(viewer_client):
    read(DAY)
    issue(1, newsletter=ETEATAJA)
    issue(2, newsletter=ENEWS)

    page = viewer_client.get(
        reverse("news"), NEWSLETTERS | {"uudiskiri": str(ENEWS)}
    ).content.decode()
    assert "Number 2" in page
    assert "Number 1" not in page


# -- the audience block is gone from this section ----------------------------


def test_the_section_carries_no_audience_and_the_band_still_does(viewer_client):
    """The list sizes were printed twice on one page; now they are printed once.

    They arrived here as three sparklines, and when the charts came off — two
    readings a day apart drawn as a trend — the rows underneath were still the
    same three numbers the `Uudiskirjad` card above prints. A figure that
    appears twice on a page is a figure the reader checks against itself, so
    this section dropped it.

    What must not happen is losing it altogether, which is why this asserts on
    both halves: absent below the section heading, present above it.
    """
    read(DAY)
    read(DAY + dt.timedelta(days=1), enews=31)
    issue(1)

    section = build_newsletter_section()
    assert not hasattr(section, "audience")
    assert not hasattr(section, "coverage_note")

    page = viewer_client.get(reverse("news"), NEWSLETTERS).content.decode()
    band, _, body = page.partition("Uudiskirjade tulemused")

    # `saajat` was the unit on the removed rows, and no other row on this page
    # uses it — the band prints each list as a bare labelled figure.
    assert "saajat" not in body
    # Still measured, still on the page: e-Teataja is 100 members + 200 others.
    assert "300" in band
    # And the sparklines are gone. The GA4 section is the only other user of
    # `trend_chart.html` and nothing has been collected for it here.
    assert "data-trend-chart" not in page
    assert "Trendi kuvamiseks on vaja vähemalt kahte lugemist." not in page


# -- searching the recent sends ---------------------------------------------


def test_the_section_searches_stored_subjects():
    read(DAY)
    issue(1, name="Kutse ärifoorumile")
    issue(2, name="Uudiskiri nr 400")

    found = build_newsletter_section(search="ärifoorum")
    assert [row.campaign_id for row in found.issues] == [1]
    assert found.is_searching
    assert found.total_sends == 1
    assert found.result_summary == "1 saadetud uudiskiri."


def test_the_search_and_the_newsletter_filter_combine():
    read(DAY)
    issue(1, name="Aastakoosolek", newsletter=ETEATAJA)
    issue(2, name="Aastakoosolek", newsletter=ENEWS)

    narrowed = build_newsletter_section(newsletter_key=ENEWS, search="aastakoosolek")
    assert [row.campaign_id for row in narrowed.issues] == [2]


def test_a_search_term_is_bounded():
    section = build_newsletter_section(search="x" * 500)
    assert len(section.search) <= 80


def test_a_search_matching_nothing_keeps_the_section_on_the_page(viewer_client):
    """The failure this test exists for.

    `has_any_data` used to be `has_audience or has_issues`. An account with no
    subscriber reading and a search matching nothing satisfied neither, so the
    section collapsed to `Andmed puuduvad` — taking with it the box holding the
    term and the link that would clear it. The reader was left on a page with
    no way back except editing the URL.
    """
    issue(1, name="Kutse ärifoorumile")

    section = build_newsletter_section(search="ei leidu midagi")
    assert not section.has_issues
    assert section.has_any_data

    page = viewer_client.get(
        reverse("news"), NEWSLETTERS | {"otsi": "ei leidu midagi"}
    ).content.decode()
    assert "Otsi uudiskirja" in page
    assert "Tühjenda otsing" in page
    assert "Ühtegi saadetud uudiskirja ei leitud." in page


def test_the_filter_chips_carry_the_search_and_clearing_keeps_the_newsletter():
    section = build_newsletter_section(newsletter_key=ETEATAJA, search="ärifoorum")

    assert all("otsi=%C3%A4rifoorum" in option.query for option in section.options)
    assert section.clear_query == f"uudiskiri={ETEATAJA}"


def test_the_archive_link_carries_both_rather_than_reopening_everything():
    """`Vaata kõiki` asks the archive the question this section is asking.

    It used to link to a bare `/nahtavus/uudiskirjad/` and print the unfiltered
    total beside it, so a reader looking at three e-Teataja matches was offered
    "see all 3 194" and landed on fourteen unfiltered years.
    """
    read(DAY)
    for campaign_id in range(1, 4):
        issue(campaign_id, name=f"Ärifoorum {campaign_id}", newsletter=ETEATAJA)
    issue(9, name="Midagi muud", newsletter=ENEWS)

    section = build_newsletter_section(newsletter_key=ETEATAJA, search="ärifoorum")
    assert section.total_sends == 3
    assert section.archive_query == f"uudiskiri={ETEATAJA}&otsi=%C3%A4rifoorum"


def test_the_page_reads_otsi_and_not_the_page_search(viewer_client):
    """`otsi` and `otsing` are two boxes on one page and must never be one.

    `otsi` matches campaign subjects, `otsing` matches news articles. Wiring the
    section to `otsing` would have looked correct on this page — the parameter
    exists and holds a string — and would have emptied the news archive on every
    newsletter search. The pair travelled together when the section moved: they
    named two different searches on Nähtavus and they name two different
    searches here.
    """
    read(DAY)
    issue(1, name="Kutse ärifoorumile")
    issue(2, name="Uudiskiri nr 400")

    page = viewer_client.get(reverse("news"), NEWSLETTERS | {"otsi": "ärifoorum"}).content.decode()
    assert "Kutse ärifoorumile" in page
    assert "Uudiskiri nr 400" not in page

    # The page search leaves the sends alone: both issues are still listed.
    other = viewer_client.get(
        reverse("news"), NEWSLETTERS | {"otsing": "ärifoorum"}
    ).content.decode()
    assert "Uudiskiri nr 400" in other
