"""Every completed send is kept and shown, whatever kind it is.

The defect these exist for: the campaign list used to `exclude(newsletter="")`,
which hid 2 105 of the account's 3 194 completed campaigns — every event
calendar, invitation, Christmas card and export bulletin sent since 2012 —
behind a classifier that was only ever meant to *label* them.

The rule is collect first, classify second. A recognition failure must never
look like a campaign that never happened.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.visibility.campaign_history import PER_PAGE, build_campaign_history
from apps.visibility.models import SmailyCampaign, VisibilityMetric
from apps.visibility.newsletter_page import build_newsletter_section
from apps.visibility.smaily import CampaignRow
from apps.visibility.smaily_campaign_sync import synchronize_campaigns
from apps.visibility.smaily_campaigns import OTHER_KEY, OTHER_LABEL
from apps.visibility.smaily_selectors import campaign_queryset, get_campaign_performance

pytestmark = pytest.mark.django_db

ETEATAJA = VisibilityMetric.NEWSLETTER_ETEATAJA
ENEWS = VisibilityMetric.NEWSLETTER_ENEWS
EVESTNIK = VisibilityMetric.NEWSLETTER_EVESTNIK

PREVIEW = "https://example.sendsmaily.net/template/preview/id/4107/"


def row(campaign_id, *, template, subject, days_ago=1, status="COMPLETED", preview=PREVIEW):
    return CampaignRow(
        campaign_id=campaign_id,
        name=subject,
        template_name=template,
        template_external_id=str(campaign_id),
        preview_url=preview,
        status=status,
        created_at=timezone.now() - dt.timedelta(days=days_ago),
        completed_at=timezone.now() - dt.timedelta(days=days_ago),
    )


#: One of each kind the account actually sends.
EVERY_KIND = (
    row(1, template="e-Teataja 4.08 liikmed", subject="E-Teataja: uudised"),
    row(2, template="E-News 07.05.26", subject="Estonian business weekly"),
    row(3, template="e-Vestnik 25.06.26", subject="Вестник"),
    row(4, template="Ürituste kalender 04.08.26", subject="Sündmuste kalender"),
    row(5, template="EEN 16.06.26 tööstus", subject="Enterprise Europe Network"),
    row(6, template="Kutse Eesti-Islandi ärifoorumile", subject="Kutse ärifoorumile"),
    row(7, template="", subject="Ilma mallita saadetis"),
)


class FakeCollector:
    def __init__(self, campaigns):
        self.campaigns = tuple(campaigns)

    def collect_campaigns(self, *, limit=5000):
        return self.campaigns

    def collect_campaign_stats(self, campaign_id):
        from apps.visibility.smaily import CampaignStatsRow

        return CampaignStatsRow(
            campaign_id=campaign_id,
            total_count=1010,
            delivered_count=1000,
            opened_count=500,
            unique_click_count=50,
        )


def collect(campaigns=EVERY_KIND):
    return synchronize_campaigns(collector=FakeCollector(campaigns), stats_limit=50)


# -- collection keeps every kind --------------------------------------------


@pytest.mark.parametrize(
    ("campaign_id", "expected"),
    [
        (1, ETEATAJA),
        (2, ENEWS),
        (3, EVESTNIK),
        (4, ""),
        (5, ""),
        (6, ""),
        (7, ""),
    ],
)
def test_every_completed_campaign_is_collected(campaign_id, expected):
    """Each of the three newsletters, a known other family, and one with no
    template at all. All seven are stored; four carry no classification and are
    stored anyway."""
    collect()
    campaign = SmailyCampaign.objects.get(campaign_id=campaign_id)
    assert campaign.newsletter == expected


def test_an_unrecognised_campaign_is_stored_rather_than_dropped():
    collect()
    assert SmailyCampaign.objects.count() == len(EVERY_KIND)
    assert SmailyCampaign.objects.filter(newsletter="").count() == 4


def test_an_unclassified_campaign_appears_as_muu_not_as_nothing():
    collect()
    rows = {r.campaign_id: r for r in get_campaign_performance(limit=50)}
    assert rows[4].newsletter_label == OTHER_LABEL
    assert rows[7].newsletter_label == OTHER_LABEL


def test_the_default_list_contains_every_completed_type():
    collect()
    labels = {r.newsletter_label for r in get_campaign_performance(limit=50)}
    assert labels == {"e-Teataja", "eNews", "e-Vestnik", OTHER_LABEL}


def test_filtering_to_one_newsletter_does_not_delete_the_rest():
    """A filter narrows a view. It must not touch what is stored."""
    collect()
    before = SmailyCampaign.objects.count()

    assert [r.campaign_id for r in get_campaign_performance(metric=ETEATAJA, limit=50)] == [1]

    assert SmailyCampaign.objects.count() == before
    assert len(get_campaign_performance(limit=50)) == before


def test_the_muu_filter_selects_exactly_the_unclassified():
    collect()
    ids = {r.campaign_id for r in get_campaign_performance(metric=OTHER_KEY, limit=50)}
    assert ids == {4, 5, 6, 7}


# -- only completed sends ---------------------------------------------------


@pytest.mark.parametrize("status", ["DRAFT", "PENDING", "CANCELLED"])
def test_a_non_completed_status_is_never_asked_for(status):
    """The filter is Smaily's, and `collect_campaigns` asks for COMPLETED only.

    Verified against the account: the same call returns 3 194 completed beside
    331 drafts and 38 cancelled, so a draft cannot reach the catalogue by way of
    the list request.
    """
    from apps.visibility.smaily import SmailyApiClient, SmailyConfiguration

    class RecordingSession:
        def __init__(self):
            self.params = None
            self.auth = None

        def get(self, url, params=None, timeout=None, allow_redirects=None):
            self.params = params

            class Response:
                status_code = 200

                @staticmethod
                def json():
                    return []

            return Response()

    session = RecordingSession()
    client = SmailyApiClient(
        SmailyConfiguration(subdomain="example", username="u", password="p"), session=session
    )
    client.collect_campaigns(limit=10)
    assert session.params["status"] == "COMPLETED"
    assert status not in str(session.params)


def test_completed_is_what_reaches_the_catalogue():
    collect()
    assert set(SmailyCampaign.objects.values_list("status", flat=True)) == {"COMPLETED"}


# -- the section and the archive --------------------------------------------


def test_the_section_offers_muu_only_when_such_campaigns_exist():
    collect((EVERY_KIND[0],))
    assert OTHER_KEY not in {o.key for o in build_newsletter_section().options}

    collect(EVERY_KIND)
    assert OTHER_KEY in {o.key for o in build_newsletter_section().options}


def test_the_section_default_is_all_and_lists_every_kind():
    collect()
    section = build_newsletter_section()
    assert not section.is_filtered
    assert len(section.issues) == len(EVERY_KIND)


def test_the_archive_holds_the_whole_population():
    collect()
    history = build_campaign_history()
    assert history.total_rows == len(EVERY_KIND)
    assert len(history.rows) == len(EVERY_KIND)


def test_the_archive_paginates_rather_than_rendering_everything():
    many = tuple(
        row(100 + index, template="Ürituste kalender", subject=f"Saadetis {index}", days_ago=index)
        for index in range(PER_PAGE + 10)
    )
    collect(many)

    first = build_campaign_history()
    assert first.total_rows == PER_PAGE + 10
    assert len(first.rows) == PER_PAGE
    assert first.has_next and not first.has_previous

    second = build_campaign_history(page=2)
    assert len(second.rows) == 10
    assert second.has_previous and not second.has_next


def test_the_summary_span_covers_the_whole_filtered_set_not_the_page():
    """The count and the span are one sentence and must describe one thing.

    Taken from the rows on screen, "3 194 sends" read as though they had all
    happened in the three months the newest fifty covered.
    """
    many = tuple(
        row(200 + index, template="Ürituste kalender", subject=f"Vana {index}", days_ago=index * 30)
        for index in range(PER_PAGE + 5)
    )
    collect(many)

    history = build_campaign_history()
    oldest = min(r.completed_at for r in many).date()
    newest = max(r.completed_at for r in many).date()

    assert history.earliest == oldest
    assert history.latest == newest
    # The page holds only the newest fifty, so a span taken from it would start
    # far later than the oldest send.
    assert history.earliest < min(r.completed_at for r in history.rows)


def test_a_rotten_page_number_falls_back_rather_than_erroring():
    collect()
    for bad in ("0", "-4", "banana", "99999", None):
        assert build_campaign_history(page=bad).page_number >= 1


def test_the_archive_searches_stored_subjects():
    collect()
    found = build_campaign_history(search="ärifoorumile")
    assert [r.campaign_id for r in found.rows] == [6]

    assert build_campaign_history(search="ei leidu midagi").total_rows == 0


def test_search_and_type_filter_combine():
    collect()
    narrowed = build_campaign_history(newsletter_key=OTHER_KEY, search="kalender")
    assert [r.campaign_id for r in narrowed.rows] == [4]


def test_a_search_term_is_bounded():
    collect()
    history = build_campaign_history(search="x" * 500)
    assert len(history.search) <= 80


def test_the_backfill_imports_every_type_not_only_eteataja():
    outcome = collect()
    assert outcome.extra["campaigns_catalogued"] == len(EVERY_KIND)
    assert outcome.extra["campaigns_unclassified"] == 4
    assert campaign_queryset().count() == len(EVERY_KIND)


# -- the page ---------------------------------------------------------------


def test_the_page_shows_every_kind_and_links_to_the_archive(viewer_client):
    collect()
    page = viewer_client.get(reverse("mailings")).content.decode()

    assert "Viimased saadetud uudiskirjad" in page
    assert OTHER_LABEL in page
    assert "Kutse ärifoorumile" in page


def test_the_archive_page_renders(viewer_client):
    collect()
    page = viewer_client.get(reverse("mailings-history")).content.decode()

    assert "Saadetud uudiskirjad" in page
    assert "Kutse ärifoorumile" in page
    assert "Sündmuste kalender" in page


def test_the_archive_states_its_scope_once(viewer_client):
    """The subtitle went on 2026-08-16; the section header it duplicated stays.

    `Kõik Smailyst välja saadetud kampaaniad` sat directly above
    `Kõik saadetised`, which says the same thing about the same table. Only the
    duplicate left — a page that had dropped both would no longer tell the
    reader the table is the whole population rather than a recent slice.
    """
    collect()
    page = viewer_client.get(reverse("mailings-history")).content.decode()

    assert "Kõik saadetised" in page
    assert "Kõik Smailyst välja saadetud kampaaniad" not in page
