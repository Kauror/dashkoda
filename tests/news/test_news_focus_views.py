"""The impact, publishing and newsletter views.

These cover the rules that are easiest to break while the page still renders: a
ranking sliced before it is ordered, a partial period drawn as a finished one,
newsletter audiences added together, and rates averaged instead of weighted.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.news import page
from apps.news.categories import NewsCategory
from apps.news.measurement import resolve_reading
from apps.news.periods import resolve_period
from tests.news.conftest import COVERAGE_END, article

pytestmark = pytest.mark.django_db


def coverage():
    from apps.visibility.ga4_selectors import get_coverage

    return get_coverage()


# -- impact -------------------------------------------------------------------


def test_the_three_lenses_answer_three_different_questions(ga4):
    """One `Enim vaadatud` table cannot answer all three, which is why there are three.

    The old article wins `Loetakse praegu`; the new one wins the first-week
    ranking. A single ranking would have to pick one of these to be wrong about.
    """
    old = article("vana", published=dt.date(2026, 1, 5))
    recent_day = COVERAGE_END - dt.timedelta(days=20)
    fresh = article("varske", published=recent_day)
    ga4(
        views={
            old.path: {COVERAGE_END: 500},
            fresh.path: {recent_day: 300},
        }
    )
    reading = resolve_reading("30", coverage=coverage())

    now = page.build_impact(reading=reading, coverage=coverage(), lens=page.LENS_NOW)
    week = page.build_impact(reading=reading, coverage=coverage(), lens=page.LENS_WEEK)

    assert now["ranked"][0].title == old.title
    assert week["ranked"][0].title == fresh.title


def test_the_default_lens_is_the_age_normalised_one():
    """Comparing articles by total measured views compares their ages."""
    assert page.DEFAULT_LENS == page.LENS_MONTH
    assert page.parse_lens(None) == page.LENS_MONTH
    assert page.parse_lens("zzz") == page.LENS_MONTH


def test_the_first_month_ranking_orders_the_whole_population_before_slicing(ga4):
    """A ranking of a page is not a ranking.

    Thirty eligible articles, and the strongest is the one published earliest —
    so a ranking that took the newest thirty rows and sorted those in Python
    would put it nowhere near the top.
    """
    published_first = dt.date(2026, 2, 1)
    views = {}
    best = article("parim", published=published_first)
    views[best.path] = {published_first: 900}
    for index in range(30):
        item = article(f"muu-{index}", published=published_first + dt.timedelta(days=index + 1))
        views[item.path] = {published_first + dt.timedelta(days=index + 1): 10}
    ga4(views=views)

    built = page.build_impact(
        reading=resolve_reading("30", coverage=coverage()),
        coverage=coverage(),
        lens=page.LENS_MONTH,
    )

    assert built["ranked"][0].title == best.title


def test_the_distribution_describes_the_eligible_cohort(ga4):
    published = dt.date(2026, 2, 1)
    views = {}
    for index in range(20):
        item = article(f"lugu-{index}", published=published)
        views[item.path] = {published: index * 10}
    ga4(views=views)

    built = page.build_impact(
        reading=resolve_reading("30", coverage=coverage()),
        coverage=coverage(),
        lens=page.LENS_MONTH,
    )

    distribution = built["distribution"]
    assert distribution is not None
    assert distribution.has_data
    # Every eligible article lands in exactly one band.
    assert sum(int(row[1]) for row in distribution.table_rows) == 20


def test_category_performance_separates_output_from_fair_performance(ga4):
    """Koja out-publishes Sõprade, and that is not the same as out-performing it."""
    published = dt.date(2026, 2, 1)
    views = {}
    for index in range(12):
        chamber = article(f"koda-{index}", published=published, category=NewsCategory.CHAMBER)
        views[chamber.path] = {published: 20}
    for index in range(12):
        partner = article(f"sober-{index}", published=published, category=NewsCategory.PARTNER)
        views[partner.path] = {published: 20}
    # Three extra Chamber articles: more output, identical per-article result.
    for index in range(3):
        extra = article(f"koda-lisa-{index}", published=published, category=NewsCategory.CHAMBER)
        views[extra.path] = {published: 20}
    ga4(views=views)

    built = page.build_impact(
        reading=resolve_reading("30", coverage=coverage()),
        coverage=coverage(),
        lens=page.LENS_MONTH,
    )
    by_key = {row.key: row for row in built["categories"]}

    assert by_key[NewsCategory.CHAMBER].published == "15"
    assert by_key[NewsCategory.PARTNER].published == "12"
    # Same median: publishing more did not make each article better read.
    assert by_key[NewsCategory.CHAMBER].median == "20"
    assert by_key[NewsCategory.PARTNER].median == "20"


# -- publishing ---------------------------------------------------------------


def test_the_cadence_chart_never_draws_a_daily_grain(ga4):
    ga4()
    for index in range(40):
        article(f"lugu-{index}", published=COVERAGE_END - dt.timedelta(days=index * 4))

    weekly = page.build_publishing(
        period=resolve_period("90", today=COVERAGE_END), coverage=coverage()
    )
    monthly = page.build_publishing(
        period=resolve_period("1a", today=COVERAGE_END), coverage=coverage()
    )

    assert weekly["grain"] == "week"
    assert monthly["grain"] == "month"


def test_an_unfinished_period_is_labelled_partial(ga4):
    """A month that is a third over must not read as a collapse in output."""
    ga4()
    mid_month = dt.date(2026, 6, 10)
    for index in range(5):
        article(f"lugu-{index}", published=mid_month - dt.timedelta(days=index))

    built = page.build_publishing(
        period=resolve_period(
            "kohandatud",
            "2026-01-01",
            mid_month.isoformat(),
            today=mid_month,
        ),
        coverage=coverage(),
    )

    notes = [row[-1] for row in built["cadence"].table_rows]
    assert "osaline" in notes


def test_unclassified_articles_are_stacked_rather_than_dropped(ga4):
    ga4()
    day = dt.date(2026, 3, 1)
    article("koda", published=day, category=NewsCategory.CHAMBER)
    article("teadmata", published=day, category="")

    built = page.build_publishing(period=resolve_period("koik"), coverage=coverage())

    # The bar's height is the publication count, so the two must agree.
    assert built["counted"].total == 2
    assert sum(int(row[1]) for row in built["cadence"].table_rows) == 2


# -- newsletters --------------------------------------------------------------


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
    from apps.visibility.smaily_segments import NEWSLETTERS

    metric = NEWSLETTERS[0].metric
    newsletter_sends(metric, delivered=20000, opened=8000, clicks=0, day=dt.date(2026, 6, 1))
    newsletter_sends(metric, delivered=100, opened=90, clicks=0, day=dt.date(2026, 6, 8))

    built = page.build_newsletters(newsletter_key=metric)
    recent = built["newsletter_recent"]

    assert recent.delivered == 20100
    assert recent.open_rate == pytest.approx(8090 / 20100)
    # Not the mean of 40% and 90%.
    assert recent.open_rate < 0.5


def test_recent_sends_are_compared_with_the_block_before_them(newsletter_sends):
    from apps.visibility.smaily_segments import NEWSLETTERS

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

    built = page.build_newsletters(newsletter_key=metric)

    assert built["newsletter_recent"].open_rate == pytest.approx(0.5)
    assert built["newsletter_previous"].open_rate == pytest.approx(0.2)
    labels = {row.label: row for row in built["newsletter_changes"]}
    assert "pp" in labels["Avamismäär"].change


def test_newsletter_audiences_are_never_totalled(newsletter_sends):
    """Three lists whose overlap nobody measured do not add up to people."""
    from apps.visibility.smaily_segments import NEWSLETTERS

    for spec in NEWSLETTERS:
        newsletter_sends(
            spec.metric, delivered=1000, opened=400, clicks=40, day=dt.date(2026, 6, 1)
        )

    built = page.build_newsletters(newsletter_key="")

    assert len(built["newsletter_comparison"]) == len(NEWSLETTERS)
    # Each newsletter states its own figures; nothing sums them.
    assert not hasattr(built, "total_subscribers")
    assert "kokku" not in str(built["newsletter_comparison"]).lower()


def test_a_send_without_statistics_is_not_drawn_as_zero(newsletter_sends):
    from django.utils import timezone

    from apps.visibility.models import SmailyCampaign
    from apps.visibility.smaily_segments import NEWSLETTERS

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

    built = page.build_newsletters(newsletter_key=metric)

    subjects = [row[1] for row in built["newsletter_sends"].table_rows]
    assert "Mõõtmata saadetis" not in subjects
