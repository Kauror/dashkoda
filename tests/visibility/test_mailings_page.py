"""What `build_mailings_page` computes, and the rules it must never break.

`period_key="koik"` throughout: the real default period is a rolling window
ending at today's real date, and these fixtures seed fixed 2026 dates — asking
for `Kõik` is what keeps a test's outcome independent of when it happens to run.

What is pinned:

- **rates are weighted, never averaged.** Summed opens over summed delivered. A
  mean of per-send percentages would weight a send to a hundred people the same
  as one to twenty thousand;
- **the comparison never claims one person count.** No field sums the three
  newsletters' subscriber counts — see `apps/visibility/mailings_page.py`'s own
  docstring for why even a caveated total was rejected;
- **an unmeasured send is not a zero.** A campaign whose statistics have never
  been read is absent from the rankings rather than ranked at the floor;
- **the landing state has a newsletter, since 2026-08-18.** The mockup this
  round rebuilt the page to shows the chart and the rankings without a click
  first, so no newsletter chosen means the first one in the registry, not none.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.visibility.mailings_page import DEFAULT_NEWSLETTER, build_mailings_page
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

    built = build_mailings_page(newsletter_key=metric, period_key="koik")

    row = next(row for row in built.comparison if row.metric == metric)
    # Weighted: (8000 + 90) / (20000 + 100) ≈ 40,2%, not the mean of 40% and 90%.
    assert row.open_rate.startswith("40")


def test_the_comparison_never_claims_one_person_count(newsletter_sends):
    """Three lists whose overlap nobody measured do not add up to people.

    No field on `MailingsPage` sums the three subscriber counts — see
    `apps/visibility/mailings_page.py`'s own docstring for why even a caveated
    total was rejected.
    """
    for spec in NEWSLETTERS:
        newsletter_sends(
            spec.metric, delivered=1000, opened=400, clicks=40, day=dt.date(2026, 6, 1)
        )

    built = build_mailings_page(newsletter_key="", period_key="koik")

    assert len(built.comparison) == len(NEWSLETTERS)
    assert not hasattr(built, "subscribers_total")


def test_the_landing_state_defaults_to_the_first_newsletter(newsletter_sends):
    """No newsletter chosen is the default newsletter now, not nothing.

    The mockup shows the chart and the rankings without a click first, so
    `build_mailings_page(newsletter_key="")` must read as though the reader
    had picked `DEFAULT_NEWSLETTER` themselves.
    """
    for index in range(3):
        newsletter_sends(
            DEFAULT_NEWSLETTER,
            delivered=1000,
            opened=400,
            clicks=40,
            day=dt.date(2026, 6, 1) + dt.timedelta(days=index),
        )

    built = build_mailings_page(newsletter_key="", period_key="koik")

    assert built.comparison
    assert built.has_selection
    assert built.selected_newsletter == DEFAULT_NEWSLETTER
    assert built.has_rankings


def test_a_chosen_newsletter_governs_the_chart_and_rankings(newsletter_sends):
    other = NEWSLETTERS[1].metric
    for index in range(3):
        newsletter_sends(
            other,
            delivered=1000,
            opened=400,
            clicks=40,
            day=dt.date(2026, 6, 1) + dt.timedelta(days=index),
        )

    built = build_mailings_page(newsletter_key=other, period_key="koik")

    assert built.selected_newsletter == other
    assert built.chart is not None
    assert built.has_rankings


def test_a_send_without_statistics_is_not_ranked_as_zero(newsletter_sends):
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

    built = build_mailings_page(newsletter_key=metric, period_key="koik")

    names = [send.name for sends in built.rankings.values() for send in sends]
    assert "Mõõtmata saadetis" not in names


def test_the_period_governs_the_comparison_but_not_the_click_benchmark(newsletter_sends):
    """The benchmark's own trailing-12-month window is independent of the page's
    period picker — see `apps/visibility/mailings_page.py`."""
    metric = NEWSLETTERS[0].metric
    today = dt.date(2026, 6, 30)
    newsletter_sends(
        metric, delivered=1000, opened=400, clicks=100, day=today - dt.timedelta(days=10)
    )

    built = build_mailings_page(newsletter_key=metric, period_key="30", today=today)

    assert metric in built.click_benchmarks
    assert built.click_benchmarks[metric] == pytest.approx(0.1)
