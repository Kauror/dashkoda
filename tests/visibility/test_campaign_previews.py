"""Opening the newsletter: which preview addresses are rendered, and which are not.

A preview URL is the one piece of Smaily data that becomes an anchor a reader
clicks, so it is validated before it is stored and again by never being
constructed here. Smaily supplies `template.preview_url`; DashKoda uses that
address and never guesses one from a template ID.

What the account actually holds, read read-only on 2026-08-10: 3 127 of 3 194
completed campaigns carry a preview, all HTTPS on the account's own host, and
the remaining 67 have `template: "DELETED"`.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.visibility.models import SmailyCampaign
from apps.visibility.smaily import (
    TEMPLATE_DELETED,
    CampaignRow,
    _campaign_rows,
    safe_preview_url,
)
from apps.visibility.smaily_campaign_sync import synchronize_campaigns
from apps.visibility.smaily_selectors import get_campaign_performance

pytestmark = pytest.mark.django_db

HOST = "example.sendsmaily.net"
GOOD = f"https://{HOST}/template/preview/id/4107/"


# -- validation -------------------------------------------------------------


def test_a_valid_account_preview_is_accepted():
    assert safe_preview_url(GOOD, subdomain="example") == GOOD


@pytest.mark.parametrize(
    "hostile",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "  javascript:alert(1)  ",
        "vbscript:msgbox(1)",
        f"http://{HOST}/template/preview/id/1/",
        "https://evil.example/template/preview/id/1/",
        "https://sendsmaily.net.evil.example/x",
        f"https://user:secret@{HOST}/x",
        "not a url at all",
        "",
        None,
        123,
    ],
)
def test_an_unsafe_or_unexpected_preview_is_refused(hostile):
    assert safe_preview_url(hostile, subdomain="example") == ""


def test_another_smaily_account_is_refused_when_the_subdomain_is_known():
    """`.sendsmaily.net` is not enough. A preview claiming to live on somebody
    else's Smaily account is not this account's newsletter."""
    assert safe_preview_url("https://someoneelse.sendsmaily.net/t/1", subdomain="example") == ""


def test_an_over_long_url_is_refused():
    assert safe_preview_url(f"https://{HOST}/" + "x" * 600, subdomain="example") == ""


# -- parsing ----------------------------------------------------------------


def test_the_template_object_yields_name_id_and_preview():
    rows = _campaign_rows(
        [
            {
                "id": 1,
                "name": "Subject",
                "status": "COMPLETED",
                "template": {"id": 4107, "name": "e-Teataja 4.08", "preview_url": GOOD},
            }
        ],
        subdomain="example",
    )
    assert rows[0].template_name == "e-Teataja 4.08"
    assert rows[0].template_external_id == "4107"
    assert rows[0].preview_url == GOOD
    assert rows[0].has_preview


def test_a_deleted_template_yields_no_name_and_no_preview():
    """Smaily returns the literal string `DELETED`. It is not a template name."""
    rows = _campaign_rows(
        [{"id": 2, "name": "Vana saadetis", "status": "COMPLETED", "template": TEMPLATE_DELETED}],
        subdomain="example",
    )
    assert rows[0].template_name == ""
    assert rows[0].preview_url == ""
    assert not rows[0].has_preview
    # The campaign itself is intact.
    assert rows[0].campaign_id == 2
    assert rows[0].name == "Vana saadetis"


def test_a_hostile_preview_is_dropped_without_losing_the_campaign():
    rows = _campaign_rows(
        [
            {
                "id": 3,
                "name": "Saadetis",
                "status": "COMPLETED",
                "template": {"id": 9, "name": "Mall", "preview_url": "javascript:alert(1)"},
            }
        ],
        subdomain="example",
    )
    assert rows[0].preview_url == ""
    assert rows[0].name == "Saadetis"


# -- storage and identity ---------------------------------------------------


def campaign_row(campaign_id, *, preview=GOOD, template_id="4107", subject=None):
    return CampaignRow(
        campaign_id=campaign_id,
        name=subject or f"Saadetis {campaign_id}",
        template_name="e-Teataja",
        template_external_id=template_id,
        preview_url=preview,
        status="COMPLETED",
        created_at=timezone.now() - dt.timedelta(days=1),
        completed_at=timezone.now() - dt.timedelta(days=1),
    )


class FakeCollector:
    def __init__(self, campaigns):
        self.campaigns = tuple(campaigns)

    def collect_campaigns(self, *, limit=5000):
        return self.campaigns

    def collect_campaign_stats(self, campaign_id):
        from apps.visibility.smaily import CampaignStatsRow

        return CampaignStatsRow(
            campaign_id=campaign_id, total_count=10, delivered_count=10, opened_count=5
        )


def collect(campaigns):
    return synchronize_campaigns(collector=FakeCollector(campaigns), stats_limit=50)


def test_two_campaigns_sharing_a_template_stay_two_campaigns():
    """364 campaigns on this account share a template, one of them eleven ways.

    The preview is the *template's*, so it is not identity. The campaign ID is.
    """
    collect((campaign_row(11), campaign_row(12)))

    assert SmailyCampaign.objects.count() == 2
    assert set(SmailyCampaign.objects.values_list("campaign_id", flat=True)) == {11, 12}
    assert SmailyCampaign.objects.filter(preview_url=GOOD).count() == 2


def test_a_campaign_without_a_preview_still_stores_its_statistics():
    collect((campaign_row(13, preview="", template_id=""),))

    campaign = SmailyCampaign.objects.get(campaign_id=13)
    assert campaign.preview_url == ""
    assert not campaign.has_preview
    assert campaign.statistics.filter(is_current=True).exists()


def test_a_template_deleted_later_clears_the_dead_address():
    collect((campaign_row(14),))
    assert SmailyCampaign.objects.get(campaign_id=14).preview_url == GOOD

    collect((campaign_row(14, preview="", template_id=""),))
    campaign = SmailyCampaign.objects.get(campaign_id=14)
    assert campaign.preview_url == ""
    # Everything else about the campaign survives.
    assert campaign.name == "Saadetis 14"
    assert campaign.statistics.filter(is_current=True).exists()


# -- rendering --------------------------------------------------------------


def test_a_preview_renders_as_an_external_link(viewer_client):
    collect((campaign_row(15, subject="Avatav uudiskiri"),))
    page = viewer_client.get(reverse("mailings-history")).content.decode()

    assert GOOD in page
    assert 'target="_blank"' in page
    assert 'rel="noopener noreferrer"' in page
    assert "avaneb Smaily lehel uuel vahelehel" in page


def test_a_campaign_without_a_preview_renders_its_row_but_no_link(viewer_client):
    collect((campaign_row(16, preview="", template_id="", subject="Kustutatud malliga"),))
    page = viewer_client.get(reverse("mailings-history")).content.decode()

    assert "Kustutatud malliga" in page
    # The subject is present as plain text, and no anchor points at a preview.
    assert "/template/preview/" not in page


def test_the_row_carries_its_statistics_whether_or_not_it_has_a_preview():
    collect((campaign_row(17), campaign_row(18, preview="", template_id="")))
    rows = {r.campaign_id: r for r in get_campaign_performance(limit=50)}

    assert rows[17].has_preview
    assert not rows[18].has_preview
    for row in rows.values():
        assert row.delivered == 10
        assert row.open_rate_label.endswith("%")
