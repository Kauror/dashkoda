"""Cataloguing campaigns and publishing their aggregate statistics.

The transport is always faked. **No Smaily credential, subdomain or response
body exists anywhere in this file.**

What is pinned down here is the behaviour a year of campaign history depends on:
a campaign keeps its name after it scrolls out of Smaily's list, statistics that
are still moving are re-read and statistics that have settled are not, a
campaign that is not a newsletter never reaches a newsletter's figures, and a
failure loses nothing already catalogued.
"""

from __future__ import annotations

import datetime as dt
import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.core.feeds import FeedResult
from apps.sources.models import ImportRun, ImportStatus
from apps.visibility.models import (
    SmailyCampaign,
    SmailyCampaignStats,
    VisibilityMetric,
    VisibilityRecordImmutable,
)
from apps.visibility.smaily import CampaignRow, CampaignStatsRow, SmailyResponseError
from apps.visibility.smaily_campaign_sync import (
    STATS_RECONCILIATION_DAYS,
    synchronize_campaigns,
)

pytestmark = pytest.mark.django_db


def when(days_ago: int = 0):
    return timezone.now() - dt.timedelta(days=days_ago)


def campaign(
    campaign_id=4421, template="e-Teataja 4.08 mitteliikmed", days_ago=0, name="E-Teataja"
):
    return CampaignRow(
        campaign_id=campaign_id,
        name=name,
        template_name=template,
        status="COMPLETED",
        created_at=when(days_ago),
        completed_at=when(days_ago),
    )


def stats(campaign_id=4421, delivered=1000, opened=500, clicks=50):
    return CampaignStatsRow(
        campaign_id=campaign_id,
        total_count=delivered + 10,
        delivered_count=delivered,
        bounce_count=10,
        opened_count=opened,
        click_count=clicks * 2,
        unique_click_count=clicks,
        unsubscribe_count=1,
    )


class FakeCollector:
    """Answers a campaign list and per-campaign statistics from maps it holds."""

    def __init__(self, campaigns=(), statistics=None, *, error=None, stats_error=None):
        self.campaigns = tuple(campaigns)
        self.statistics = statistics or {}
        self.error = error
        self.stats_error = stats_error
        self.stats_asked = []

    def collect_campaigns(self, *, limit=200):
        if self.error is not None:
            raise self.error
        return self.campaigns

    def collect_campaign_stats(self, campaign_id):
        self.stats_asked.append(campaign_id)
        if self.stats_error is not None:
            raise self.stats_error
        return self.statistics.get(campaign_id, stats(campaign_id))


def sync(**kwargs):
    kwargs.setdefault("collector", FakeCollector((campaign(),)))
    return synchronize_campaigns(**kwargs)


# -- cataloguing ------------------------------------------------------------


def test_a_campaign_is_catalogued_with_its_classification():
    outcome = sync()

    assert outcome.result == FeedResult.IMPORTED
    row = SmailyCampaign.objects.get()
    assert row.campaign_id == 4421
    assert row.newsletter == VisibilityMetric.NEWSLETTER_ETEATAJA
    assert row.audience == "mitteliikmed"
    assert row.completed_at is not None


def test_a_campaign_that_is_not_a_newsletter_is_catalogued_unclassified():
    """It is kept — it is real history — but it reaches no newsletter figure."""
    outcome = sync(
        collector=FakeCollector(
            (
                campaign(
                    9001,
                    template="Ürituste kalender 04.08.26",
                    # The subject too: it is the fallback the classifier uses
                    # when a template was deleted, so leaving the default
                    # "E-Teataja" here would have classified this by accident.
                    name="Kaubanduskoja sündmuste kalender",
                ),
            )
        )
    )

    row = SmailyCampaign.objects.get()
    assert row.newsletter == ""
    assert row.is_newsletter is False
    assert outcome.extra["campaigns_unclassified"] == 1


def test_a_known_campaign_is_updated_rather_than_duplicated():
    sync()
    sync(collector=FakeCollector((campaign(name="E-Teataja parandatud pealkiri"),)))

    assert SmailyCampaign.objects.count() == 1
    assert SmailyCampaign.objects.get().name == "E-Teataja parandatud pealkiri"


def test_a_renamed_template_does_not_reclassify_history():
    """A template tidied up afterwards must not move last year's issues.

    Re-classification is possible but deliberate; a nightly run does not do it
    behind an operator's back.
    """
    sync()
    sync(collector=FakeCollector((campaign(template="Suvine kiri 04.08.26"),)))

    row = SmailyCampaign.objects.get()
    assert row.newsletter == VisibilityMetric.NEWSLETTER_ETEATAJA
    assert row.template_name == "Suvine kiri 04.08.26"


def test_a_campaign_keeps_its_name_after_it_leaves_smailys_list():
    """The whole reason the catalogue exists."""
    sync()
    sync(collector=FakeCollector(()))

    assert SmailyCampaign.objects.count() == 1
    assert SmailyCampaign.objects.get().name == "E-Teataja"


# -- statistics -------------------------------------------------------------


def test_statistics_are_published_and_the_import_run_completes():
    sync()

    row = SmailyCampaignStats.objects.get()
    assert row.is_current is True
    assert row.revision == 1
    assert row.delivered_count == 1000
    assert row.opened_count == 500
    assert ImportRun.objects.get().status == ImportStatus.SUCCEEDED


def test_rates_are_derived_with_the_denominator_they_name():
    sync()
    row = SmailyCampaignStats.objects.get()

    # Opens over *delivered*, which is what Smaily's own percentage means.
    assert row.open_rate == pytest.approx(500 / 1000)
    assert row.click_rate == pytest.approx(50 / 1000)
    # Clicks over *opens* is a different question with a different denominator.
    assert row.click_to_open_rate == pytest.approx(50 / 500)


def test_a_rate_is_none_rather_than_zero_when_there_is_nothing_to_divide():
    sync(
        collector=FakeCollector(
            (campaign(),),
            {4421: CampaignStatsRow(campaign_id=4421, total_count=5)},
        )
    )
    row = SmailyCampaignStats.objects.get()
    assert row.open_rate is None
    assert row.click_rate is None


def test_unchanged_statistics_publish_no_second_revision():
    sync()
    outcome = sync()

    assert SmailyCampaignStats.objects.count() == 1
    assert outcome.extra["stats_unchanged"] == 1


def test_moving_statistics_publish_a_revision_without_rewriting_the_first():
    sync()
    sync(collector=FakeCollector((campaign(),), {4421: stats(opened=600)}))

    revisions = SmailyCampaignStats.objects.order_by("revision")
    assert [r.revision for r in revisions] == [1, 2]
    assert [r.opened_count for r in revisions] == [500, 600]
    assert [r.is_current for r in revisions] == [False, True]
    assert revisions[1].supersedes_id == revisions[0].pk


def test_a_campaign_reporting_no_figures_publishes_no_row():
    """Absent is not a campaign with zero opens."""
    sync(
        collector=FakeCollector(
            (campaign(),),
            {4421: CampaignStatsRow(campaign_id=4421)},
        )
    )
    assert SmailyCampaignStats.objects.count() == 0
    assert SmailyCampaign.objects.count() == 1


# -- which campaigns get re-read --------------------------------------------


def test_a_settled_campaign_is_not_re_read():
    """Re-reading two hundred settled campaigns nightly would be two hundred
    requests to learn nothing."""
    old = campaign(4400, days_ago=STATS_RECONCILIATION_DAYS + 10)
    sync(collector=FakeCollector((old,)))
    assert SmailyCampaignStats.objects.count() == 1

    collector = FakeCollector((old,))
    sync(collector=collector)
    assert collector.stats_asked == []


def test_a_recent_campaign_is_re_read():
    recent = campaign(4421, days_ago=2)
    sync(collector=FakeCollector((recent,)))

    collector = FakeCollector((recent,))
    sync(collector=collector)
    assert collector.stats_asked == [4421]


def test_a_campaign_with_no_statistics_is_always_read_however_old():
    old = campaign(4400, days_ago=400)
    # Catalogue it without letting statistics through.
    sync(collector=FakeCollector((old,), {4400: CampaignStatsRow(campaign_id=4400)}))
    assert SmailyCampaignStats.objects.count() == 0

    collector = FakeCollector((old,))
    sync(collector=collector)
    assert collector.stats_asked == [4400]
    assert SmailyCampaignStats.objects.count() == 1


def test_the_run_is_bounded_by_the_statistics_limit():
    rows = tuple(campaign(4000 + index, days_ago=1) for index in range(10))
    collector = FakeCollector(rows)
    sync(collector=collector, stats_limit=3)

    assert len(collector.stats_asked) == 3


# -- failure ----------------------------------------------------------------


def test_a_listing_failure_catalogues_nothing_and_loses_nothing():
    sync()
    outcome = sync(collector=FakeCollector(error=SmailyResponseError("Smaily vastas ootamatult.")))

    assert outcome.result == FeedResult.FAILED
    assert SmailyCampaign.objects.count() == 1
    assert SmailyCampaignStats.objects.count() == 1


def test_a_statistics_failure_keeps_everything_already_catalogued():
    outcome = sync(
        collector=FakeCollector(
            (campaign(),), stats_error=SmailyResponseError("Smaily vastas ootamatult.")
        )
    )

    assert outcome.result == FeedResult.FAILED
    # The campaign was written down before its statistics were asked for.
    assert SmailyCampaign.objects.count() == 1
    assert SmailyCampaignStats.objects.count() == 0


def test_a_transport_failure_names_no_host_or_credential():
    import requests

    outcome = sync(
        collector=FakeCollector(
            error=requests.ConnectionError("could not reach example.sendsmaily.net")
        )
    )
    assert "sendsmaily" not in outcome.detail
    assert "example" not in outcome.detail


def test_a_campaign_failure_does_not_make_the_subscriber_figures_look_stale():
    """`SmailyFeedState` describes the audience reading.

    A campaign run that fails must not mark the newsletter feed failed when its
    figures were collected successfully minutes earlier.
    """
    from apps.visibility.models import SmailyFeedState

    sync(collector=FakeCollector(error=SmailyResponseError("Smaily vastas ootamatult.")))
    state = SmailyFeedState.objects.get()
    assert state.last_result != FeedResult.FAILED


# -- immutability and dry run -----------------------------------------------


def test_published_statistics_cannot_be_rewritten_or_deleted():
    sync()
    row = SmailyCampaignStats.objects.get()
    row.opened_count = 1
    with pytest.raises(VisibilityRecordImmutable):
        row.save()
    with pytest.raises(VisibilityRecordImmutable):
        row.delete()


def test_a_dry_run_writes_nothing():
    outcome = sync(dry_run=True)

    assert outcome.dry_run is True
    assert SmailyCampaign.objects.count() == 0
    assert SmailyCampaignStats.objects.count() == 0


def test_the_command_emits_json_carrying_no_campaign_name():
    stdout = StringIO()
    # No credential is configured in the test settings, so the run fails and the
    # command exits non-zero. That is the point: the JSON contract has to hold
    # on the failure path too, which is the path a misconfigured deployment sees.
    with pytest.raises(SystemExit):
        call_command("sync_smaily_campaigns", "--dry-run", "--json", stdout=stdout)
    payload = json.loads(stdout.getvalue())

    assert set(payload) == {
        "result",
        "detail",
        "dry_run",
        "campaigns_listed",
        "campaigns_catalogued",
        "campaigns_updated",
        "campaigns_unclassified",
        "stats_examined",
        "stats_imported",
        "stats_revised",
        "stats_unchanged",
        "api_requests",
        "api_retries",
    }
    body = stdout.getvalue()
    assert "sendsmaily" not in body
    assert "password" not in body.lower()
