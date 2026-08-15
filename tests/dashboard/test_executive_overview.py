"""What the executive overview may and may not claim.

Split deliberately into two halves. The first needs no database: signal
ordering, the deterministic meaning sentences and the presentation model are
pure functions over values, and testing them without Postgres keeps the rules
they encode readable as rules. The second half exercises the assembled page.

The rules under test are the ones that would be invisible if they broke — a
page that silently added two audiences, or printed a nought where nothing was
measured, still renders perfectly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.core.executive import DomainSignal, SignalPriority
from apps.dashboard.executive_models import (
    STATE_AVAILABLE,
    ExecutiveComparison,
    ExecutiveMetric,
    ExecutiveOverviewPage,
)
from apps.dashboard.executive_signals import (
    PER_DOMAIN_LIMIT,
    SIGNAL_LIMIT,
    collect_signals,
)
from apps.event_programme.executive import EventsExecutive
from apps.membership.executive import MembershipExecutive
from apps.shop.executive import NON_EVENT_TYPES
from apps.shop.models import ProductType

# ---------------------------------------------------------------------------
# Signals: collect, deduplicate, sort, limit — and nothing else
# ---------------------------------------------------------------------------


def signal(key: str, priority: SignalPriority = SignalPriority.NOTABLE) -> DomainSignal:
    return DomainSignal(key=key, headline=f"H {key}", evidence=f"E {key}", priority=priority)


def test_no_signals_is_a_state_and_not_an_empty_list_of_rows():
    assert collect_signals(()) == ()
    assert collect_signals((("shop", "E-pood", ()),)) == ()


def test_critical_outranks_attention_outranks_notable():
    collected = collect_signals(
        (
            ("shop", "E-pood", (signal("s", SignalPriority.NOTABLE),)),
            ("legal_work", "Õigusloome", (signal("l", SignalPriority.CRITICAL),)),
            ("website", "Koduleht", (signal("w", SignalPriority.ATTENTION),)),
        )
    )

    assert [entry.key for entry in collected] == ["l", "w", "s"]


def test_equal_priority_falls_back_to_page_order_not_to_arrival_order():
    """Two domains, same urgency. The page reads in pillar order.

    Arrival order is whatever the builder happened to call first, which is not a
    fact about the Chamber and must not decide what a reader sees at the top.
    """
    collected = collect_signals(
        (
            ("shop", "E-pood", (signal("s", SignalPriority.ATTENTION),)),
            ("membership", "Liikmeskond", (signal("m", SignalPriority.ATTENTION),)),
        )
    )

    assert [entry.key for entry in collected] == ["m", "s"]


def test_the_same_condition_is_never_printed_twice():
    collected = collect_signals(
        (
            ("shop", "E-pood", (signal("shop-units"),)),
            ("shop", "E-pood", (signal("shop-units"),)),
        )
    )

    assert len(collected) == 1


def test_one_busy_domain_cannot_fill_the_section():
    """A domain is capped, so five signals are never five from one source.

    Without the cap a fortnight of legal deadlines would push every other
    domain's signal off the page, and the section would stop being cross-domain
    exactly when the reader most needs it to be.
    """
    many = tuple(signal(f"l{index}", SignalPriority.CRITICAL) for index in range(5))
    collected = collect_signals(
        (
            ("legal_work", "Õigusloome", many),
            ("shop", "E-pood", (signal("s", SignalPriority.NOTABLE),)),
        )
    )

    assert len([one for one in collected if one.domain_key == "legal_work"]) == PER_DOMAIN_LIMIT
    assert "s" in [one.key for one in collected]


def test_the_section_is_bounded():
    crowd = [
        (f"d{index}", f"D{index}", (signal(f"k{index}", SignalPriority.CRITICAL),))
        for index in range(SIGNAL_LIMIT + 4)
    ]

    assert len(collect_signals(crowd)) == SIGNAL_LIMIT


def test_priority_is_a_word_before_it_is_a_colour():
    """Urgency must survive greyscale, a printer and colour blindness."""
    collected = collect_signals(
        (("legal_work", "Õigusloome", (signal("l", SignalPriority.CRITICAL),)),)
    )

    assert collected[0].priority_label == "Kiireloomuline"


def test_the_page_computes_no_cross_domain_score():
    """There is no aggregate verdict field anywhere on the page object.

    A weighted index over membership, opinions, sessions and acquisitions would
    need them to share a unit. The whole reason this page has five pillars is
    that they do not.
    """
    fields = set(ExecutiveOverviewPage.__dataclass_fields__)

    assert not fields & {"score", "health", "index", "total", "rating", "overall"}
    forbidden = ("skoor", "tervis", "kokku", "koondhinne")
    assert not any(word in name for name in fields for word in forbidden)


# ---------------------------------------------------------------------------
# Missing is not zero
# ---------------------------------------------------------------------------


def test_an_unmeasured_metric_is_unavailable_rather_than_nought():
    metric = ExecutiveMetric(label="Liikmeid kokku", period="viimane loend", source="X")

    assert metric.value is None
    assert not metric.is_available


def test_a_measured_zero_is_a_real_value_and_stays_distinguishable():
    metric = ExecutiveMetric(label="Arvamusi", period="YTD", source="X", value="0")

    assert metric.is_available


def test_a_pillar_with_no_baseline_says_nothing_about_direction():
    """No comparison means no sentence, not a reassuring one.

    `stabiilne` would be a claim, and nobody measured it.
    """
    summary = MembershipExecutive(total_members=3412, total_as_of=date(2026, 8, 14))

    assert summary.has_headline
    assert not summary.has_comparison
    assert summary.meaning == ""


def test_a_refused_comparison_prints_its_reason_where_the_delta_would_be():
    comparison = ExecutiveComparison(unavailable_note="Mõõtmisandmed on liiga ebaühtlased.")

    assert not comparison.is_available
    assert comparison.has_note


def test_a_reported_zero_share_is_a_value_and_not_a_gap():
    """A paid share of exactly 0% is a measurement, not a missing figure.

    January before anyone has paid is a real state of the world. The pillar
    must render it as `0%`, distinguishable from a report that carries no
    share at all — truthiness on the Decimal would erase exactly that line.
    """
    from apps.core.formatting import percent
    from apps.dashboard.executive import _membership_pillar

    summary = MembershipExecutive(
        total_members=3000,
        total_as_of=date(2026, 8, 1),
        paid_share_pct=Decimal("0"),
        fee_collection_pct=Decimal("0"),
        internal_as_of=date(2026, 7, 31),
    )

    pillar = _membership_pillar(summary)
    by_label = {fact.label: fact for fact in pillar.facts}

    assert by_label["Tasunud liikmete osakaal"].value == percent(Decimal("0"))
    assert by_label["Liikmemaksu laekumine"].value == percent(Decimal("0"))

    unmeasured = _membership_pillar(
        MembershipExecutive(total_members=3000, total_as_of=date(2026, 8, 1))
    )
    unmeasured_by_label = {fact.label: fact for fact in unmeasured.facts}

    assert unmeasured_by_label["Tasunud liikmete osakaal"].value is None
    assert unmeasured_by_label["Liikmemaksu laekumine"].value is None


# ---------------------------------------------------------------------------
# Deterministic meaning: generated from metrics, never written
# ---------------------------------------------------------------------------


def test_the_membership_sentence_states_the_measured_direction():
    summary = MembershipExecutive(
        total_members=3283,
        total_as_of=date(2026, 8, 14),
        change_absolute=-130,
        change_relative_pct=Decimal("-3.8"),
        baseline_as_of=date(2025, 8, 14),
    )

    assert summary.meaning == "Liikmeskond on 3,8% väiksem kui aasta tagasi."


def test_a_zero_baseline_yields_a_count_rather_than_an_infinite_percentage():
    summary = EventsExecutive(events_ytd=12, events_ytd_previous=0)

    assert "12" in summary.meaning
    assert "%" not in summary.meaning


def test_no_sentence_makes_a_causal_claim():
    """The sentences state movement, never why.

    A dashboard can say reading fell. It cannot say the content strategy failed,
    and no vocabulary here lets it try.
    """
    summary = MembershipExecutive(
        total_members=3283,
        change_absolute=-130,
        change_relative_pct=Decimal("-3.8"),
    )

    forbidden = ("sest", "tõttu", "põhjus", "ebaõnnestu", "halb", "hea", "suurepära")
    assert not any(word in summary.meaning.casefold() for word in forbidden)


# ---------------------------------------------------------------------------
# No double counting
# ---------------------------------------------------------------------------


def test_the_shop_pillar_excludes_event_registrations():
    """Kaasamine counts the programme; the shop figures count Commerce without it.

    The Digiteenused card is gone, but the shop still reaches this page — its
    interest panel, its signals and its `Andmete seis` row — and every one of
    those reads `NON_EVENT_TYPES`. If `EVENT_REGISTRATION` were in this tuple
    the same registrations would contribute to two sections at once, and the
    page would be presenting one set of rows as two separate contributions.
    """
    assert ProductType.EVENT_REGISTRATION not in NON_EVENT_TYPES
    assert ProductType.DOCUMENT in NON_EVENT_TYPES


def test_the_events_pillar_reads_no_commerce_at_all():
    """The other half of the same rule, asserted at the import boundary."""
    import apps.event_programme.executive as events_executive

    source = events_executive.__file__
    with open(source, encoding="utf-8") as handle:
        body = handle.read()

    assert "apps.shop" not in body
    assert "ShopDailyFact" not in body


def test_news_reading_is_a_share_of_site_reading_and_never_added_to_it():
    """A subset stated as a sum would count every article view twice."""
    from apps.news.analytics import NewsTrafficSummary

    summary = NewsTrafficSummary(news_views=400, site_views=1000)

    assert summary.share == pytest.approx(0.4)
    assert not hasattr(summary, "combined_views")
    assert not hasattr(summary, "total_views")


def test_no_audience_total_exists_anywhere_in_the_channel_summary():
    """Three lists and four social accounts overlap by unknown amounts."""
    from apps.visibility.selectors import NewsletterSummary

    fields = set(NewsletterSummary.__dataclass_fields__)
    attributes = set(dir(NewsletterSummary))

    assert "total" not in fields
    assert not any(name in attributes for name in ("total_audience", "combined", "reach"))


def test_the_page_never_subtracts_one_membership_definition_from_the_other():
    """The public directory is the headline; the report contributes ratios.

    The one arithmetic that must never appear is a difference between the two
    totals, so the executive summary has no field capable of holding one.
    """
    fields = set(MembershipExecutive.__dataclass_fields__)

    assert "total_members" in fields
    assert not any("difference" in name or "gap" in name for name in fields)


# ---------------------------------------------------------------------------
# Time context: every figure states its own period
# ---------------------------------------------------------------------------


def test_a_metric_cannot_be_built_without_a_period():
    """The type refuses it, so a future pillar cannot forget.

    A figure whose period is unknown is a figure nobody can check, and this page
    has no global period control to fall back on.
    """
    with pytest.raises(TypeError):
        ExecutiveMetric(label="Seansid", source="Google Analytics")  # no period


# ---------------------------------------------------------------------------
# The assembled page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_every_pillar_carries_a_period_and_a_source_in_its_data():
    """Provenance stays in the data even though the card no longer prints it.

    The board struck the question lines and the period/source rows off the
    cards on 2026-08-15, and the Digiteenused card with them — four pillars
    now, and the shop keeps its interest panel, its signals and its
    `Andmete seis` row. What must not follow the chrome out is the metadata:
    `Andmete seis` and the domain pages are built from these same objects, so
    a metric that lost its period would be a figure nobody can check anywhere.
    """
    from apps.dashboard.executive import build_executive_overview
    from apps.event_programme.selectors import get_event_programme_summary
    from apps.legal_work.selectors import get_legal_work_summary
    from apps.membership.selectors import get_membership_summary
    from apps.news.selectors import get_news_summary

    page = build_executive_overview(
        legal_work=get_legal_work_summary(),
        membership=get_membership_summary(),
        news=get_news_summary(),
        events=get_event_programme_summary(),
    )

    assert len(page.pillars) == 4
    assert [pillar.key for pillar in page.pillars] == [
        "membership",
        "legal_work",
        "events",
        "website",
    ]
    for pillar in page.pillars:
        assert pillar.links, "every pillar offers a next step"
        if pillar.headline is not None:
            assert pillar.headline.period
            assert pillar.headline.source


@pytest.mark.django_db
def test_the_data_status_section_speaks_per_source_not_per_collector():
    """Seven business sources, each with its own vocabulary.

    Membership appears twice on purpose: the daily public directory and the
    monthly board report are two sources with two cadences, and collapsing them
    into one row would force one freshness rule onto both.
    """
    from apps.dashboard.executive import build_executive_overview
    from apps.event_programme.selectors import get_event_programme_summary
    from apps.legal_work.selectors import get_legal_work_summary
    from apps.membership.selectors import get_membership_summary
    from apps.news.selectors import get_news_summary

    page = build_executive_overview(
        legal_work=get_legal_work_summary(),
        membership=get_membership_summary(),
        news=get_news_summary(),
        events=get_event_programme_summary(),
    )

    keys = [row.domain_key for row in page.data_status]

    assert "membership" in keys and "membership_internal" in keys
    for row in page.data_status:
        assert row.source_label
        assert row.state_label
    # With nothing imported, nothing may claim to be available.
    assert all(row.state != STATE_AVAILABLE for row in page.data_status)


@pytest.mark.django_db
def test_rendering_the_main_page_makes_no_external_request(
    client, authenticate_viewer, monkeypatch
):
    """A page render reads PostgreSQL and nothing else.

    Collection belongs to scheduled commands. A request that waited on Koda.ee,
    GA4, Smaily or Commerce would be a page that fails whenever a remote system
    does, and would leak the reader's timing to a third party.

    `requests` is patched rather than the socket layer: every collector in this
    repository goes through it, and refusing sockets outright would refuse
    PostgreSQL too and pass for the wrong reason.
    """
    import requests

    def refuse(*args, **kwargs):
        raise AssertionError("the overview attempted an outbound HTTP request")

    monkeypatch.setattr(requests.Session, "request", refuse)
    monkeypatch.setattr(requests, "request", refuse)

    authenticate_viewer(client)
    response = client.get("/")

    assert response.status_code == 200
