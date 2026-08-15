"""What `build_mailings_page` computes, and the two rules it must never break.

These came from `tests/news/test_news_focus_views.py` with the builder they
exercise: the composition moved to `apps.visibility.mailings_page` when the
newsletters became `Otsepostitused`, and the arithmetic came with it unchanged.

The assertions are deliberately the same ones, against the same figures. That is
the point — the move was structural, so a rate that reads differently here from
the way it read under Uudised is a defect and not a new definition.

What is pinned:

- **rates are weighted, never averaged.** Summed opens over summed delivered. A
  mean of per-send percentages would weight a send to a hundred people the same
  as one to twenty thousand;
- **audiences are never totalled.** Three lists, three audiences, and nobody has
  measured the overlap;
- **an unmeasured send is not a zero.** A campaign whose statistics have never
  been read is absent from the drawing rather than drawn at the floor.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.visibility.mailings_page import build_mailings_page
from apps.visibility.smaily_segments import NEWSLETTERS

pytestmark = pytest.mark.django_db


@pytest.fixture
def newsletter_sends():
    """Completed sends with statistics, for one newsletter."""
    from apps.sources.services import build_import_run, register_external_reference
    from apps.visibility.bootstrap import ensure_smaily_source
    from apps.visibility.models import SmailyCampaign, SmailyCampaignStats

    state = {"n": 0}

    def _send(metric: str, *, delivered: int, opened: int, clicks: int, day: dt.date):
        from django.utils import timezone

        source = ensure_smaily_source()
        state["n"] += 1
        campaign = SmailyCampaign.objects.create(
            campaign_id=state["n"],
            name=f"Saadetis {state['n']}",
            template_name="tpl",
            newsletter=metric,
            audience="",
            status="COMPLETED",
            completed_at=timezone.make_aware(dt.datetime.combine(day, dt.time(9, 0))),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )
        artifact = register_external_reference(
            source=source,
            external_reference=f"synthetic:smaily:{state['n']}",
            original_name="s.json",
            mime_type="application/json",
            sha256=f"{state['n']:064d}",
            size_bytes=10,
        )
        run = build_import_run(
            artifact=artifact, importer_name="synthetic", schema_version="1.0", dry_run=False
        )
        SmailyCampaignStats.objects.create(
            campaign=campaign,
            artifact=artifact,
            import_run=run,
            observed_at=timezone.now(),
            checksum=f"{state['n']:064d}",
            revision=1,
            is_current=True,
            delivered_count=delivered,
            opened_count=opened,
            unique_click_count=clicks,
            unsubscribe_count=0,
        )
        return campaign

    return _send


def test_aggregate_rates_are_weighted_not_averaged(newsletter_sends):
    """The rule a mean would break.

    One send to 20 000 people opened by 40%, one to 100 opened by 90%. The
    weighted rate is 40,2%; the mean of the percentages is 65%, which describes
    no send and no audience.
    """
    metric = NEWSLETTERS[0].metric
    newsletter_sends(metric, delivered=20000, opened=8000, clicks=0, day=dt.date(2026, 6, 1))
    newsletter_sends(metric, delivered=100, opened=90, clicks=0, day=dt.date(2026, 6, 8))

    built = build_mailings_page(newsletter_key=metric)

    assert built.recent.delivered == 20100
    assert built.recent.open_rate == pytest.approx(8090 / 20100)
    # Not the mean of 40% and 90%.
    assert built.recent.open_rate < 0.5


def test_recent_sends_are_compared_with_the_block_before_them(newsletter_sends):
    metric = NEWSLETTERS[0].metric
    # 12 older sends at 20%, then 12 recent at 50%.
    for index in range(12):
        newsletter_sends(
            metric,
            delivered=1000,
            opened=200,
            clicks=10,
            day=dt.date(2026, 1, 1) + dt.timedelta(days=index),
        )
    for index in range(12):
        newsletter_sends(
            metric,
            delivered=1000,
            opened=500,
            clicks=10,
            day=dt.date(2026, 5, 1) + dt.timedelta(days=index),
        )

    built = build_mailings_page(newsletter_key=metric)

    assert built.recent.open_rate == pytest.approx(0.5)
    assert built.previous.open_rate == pytest.approx(0.2)
    labels = {row.label: row for row in built.changes}
    assert "pp" in labels["Avamismäär"].change


def test_newsletter_audiences_are_never_totalled(newsletter_sends):
    """Three lists whose overlap nobody measured do not add up to people."""
    for spec in NEWSLETTERS:
        newsletter_sends(
            spec.metric, delivered=1000, opened=400, clicks=40, day=dt.date(2026, 6, 1)
        )

    built = build_mailings_page(newsletter_key="")

    assert len(built.comparison) == len(NEWSLETTERS)
    # Each newsletter states its own figures; nothing sums them.
    assert not hasattr(built, "total_subscribers")
    assert "kokku" not in str(built.comparison).lower()


def test_the_landing_state_reads_only_the_comparison(newsletter_sends):
    """No newsletter chosen means nothing that would need one.

    The rate history, the block comparison and the rankings all describe a
    single newsletter. With none selected they are absent rather than built
    against a key no campaign carries.
    """
    for spec in NEWSLETTERS:
        newsletter_sends(
            spec.metric, delivered=1000, opened=400, clicks=40, day=dt.date(2026, 6, 1)
        )

    built = build_mailings_page(newsletter_key="")

    assert built.comparison
    assert not built.has_selection
    assert built.recent is None
    assert built.previous is None
    assert built.changes == ()
    assert built.sends is None
    assert not built.has_rankings


def test_a_send_without_statistics_is_not_drawn_as_zero(newsletter_sends):
    from django.utils import timezone

    from apps.visibility.models import SmailyCampaign

    metric = NEWSLETTERS[0].metric
    newsletter_sends(metric, delivered=1000, opened=400, clicks=40, day=dt.date(2026, 6, 1))
    # A send whose figures have never been read.
    SmailyCampaign.objects.create(
        campaign_id=9999,
        name="Mõõtmata saadetis",
        template_name="tpl",
        newsletter=metric,
        audience="",
        status="COMPLETED",
        completed_at=timezone.make_aware(dt.datetime(2026, 6, 15, 9, 0)),
        first_seen_at=timezone.now(),
        last_seen_at=timezone.now(),
    )

    built = build_mailings_page(newsletter_key=metric)

    subjects = [row[1] for row in built.sends.table_rows]
    assert "Mõõtmata saadetis" not in subjects
