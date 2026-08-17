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
    ExecutiveDomainCard,
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
    need them to share a unit. The whole reason this page has six domain cards
    is that they do not.
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


def test_a_card_with_no_baseline_says_nothing_about_direction():
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

    January before anyone has paid is a real state of the world. The card
    must render it as `0%`, distinguishable from a report that carries no
    share at all — truthiness on the Decimal would erase exactly that line.
    """
    from apps.core.formatting import percent
    from apps.dashboard.executive import _membership_card

    summary = MembershipExecutive(
        total_members=3000,
        total_as_of=date(2026, 8, 1),
        paid_share_pct=Decimal("0"),
        fee_collection_pct=Decimal("0"),
        internal_as_of=date(2026, 7, 31),
    )

    card = _membership_card(summary)
    by_label = {fact.label: fact for fact in card.facts}

    assert by_label["Tasunud liikmete osakaal"].value == percent(Decimal("0"))
    assert by_label["Liikmemaksu laekumine"].value == percent(Decimal("0"))

    unmeasured = _membership_card(
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


def test_the_shop_card_excludes_event_registrations():
    """Sündmused counts the programme; E-pood counts Commerce without it.

    Both cards are on the page again since 2026-08-17, so the rule is load
    bearing in its original form: if `EVENT_REGISTRATION` were in this tuple the
    same registrations would contribute to two cards at once, and a reader
    adding them — which the page never invites, but readers add things — would
    double count.
    """
    assert ProductType.EVENT_REGISTRATION not in NON_EVENT_TYPES
    assert ProductType.DOCUMENT in NON_EVENT_TYPES


def test_the_events_card_reads_no_commerce_at_all():
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

#: The six domain cards, in the order the page reads them — the sidebar's own
#: order. Written out rather than derived, because the point of the assertion is
#: that somebody has to change this list deliberately.
CARD_KEYS = ["membership", "legal_work", "events", "website", "mailings", "shop"]

CARD_LABELS = [
    "Liikmeskond",
    "Õigusloome",
    "Sündmused",
    "Koduleht ja uudised",
    "Otsepostitused",
    "E-pood",
]


def _overview():
    from apps.dashboard.executive import build_executive_overview
    from apps.event_programme.selectors import get_event_programme_summary
    from apps.legal_work.selectors import get_legal_work_summary
    from apps.membership.selectors import get_membership_summary
    from apps.news.selectors import get_news_summary

    return build_executive_overview(
        legal_work=get_legal_work_summary(),
        membership=get_membership_summary(),
        news=get_news_summary(),
        events=get_event_programme_summary(),
    )


@pytest.mark.django_db
def test_the_page_carries_one_card_per_domain_dashboard():
    """Six cards, one per sidebar entry, and every one offers a way out.

    The strip has been four, then five, then two. Each of those was a subset
    chosen by hand, which meant the front page silently decided that some of the
    Chamber's activities did not need reporting. Six is not a taste: it is the
    set of dashboards DashKoda has.
    """
    page = _overview()

    assert [card.key for card in page.cards] == CARD_KEYS
    assert [card.label for card in page.cards] == CARD_LABELS
    for card in page.cards:
        assert card.links, "every card offers a next step"


@pytest.mark.django_db
def test_every_card_carries_a_period_and_a_source_in_its_data():
    """Provenance stays in the data even though the card no longer prints it.

    The board struck the question lines and the period/source rows off the cards
    on 2026-08-15, and the compact cards that replaced the pillars print one
    period line instead. What must not follow the chrome out is the metadata:
    `Andmete seis` and the domain pages are built from these same objects, so a
    metric that lost its period would be a figure nobody can check anywhere.
    """
    page = _overview()

    for card in page.cards:
        if card.headline is not None:
            assert card.headline.period
            assert card.headline.source


def test_the_legal_card_leads_with_the_stock_of_open_matters():
    """`X teemat töös`, and no comparison against a year that has none.

    A stock has no year-to-date pair: the workbook holds one snapshot, so
    "open matters a year ago" is not a figure anything here can produce. The
    metric contract says so, and a card inventing one would be the page holding
    a definition of its own.
    """
    from apps.dashboard.executive import _legal_card
    from apps.legal_work.analytics import YearOnYear
    from apps.legal_work.executive import LegalWorkExecutive

    summary = LegalWorkExecutive(
        sent=YearOnYear(
            current=165,
            previous=130,
            current_cutoff=date(2026, 8, 14),
            previous_cutoff=date(2025, 8, 14),
        ),
        open_topics=42,
        due_within_7=3,
        reporting_date=date(2026, 8, 14),
    )

    card = _legal_card(summary)

    assert card.headline.value == "42"
    assert card.headline.unit == "teemat töös"
    assert card.headline.comparison is None
    labels = [fact.label for fact in card.available_facts]
    assert "Tähtaegu 7 päeva jooksul" in labels
    assert "Arvamusi saadetud tänavu" in labels


def test_a_legal_snapshot_without_an_open_count_is_unavailable_and_not_nought():
    """Missing is not zero, on the one figure the card leads with."""
    from apps.dashboard.executive import _legal_card
    from apps.legal_work.analytics import YearOnYear
    from apps.legal_work.executive import LegalWorkExecutive

    card = _legal_card(
        LegalWorkExecutive(
            sent=YearOnYear(
                current=165,
                previous=130,
                current_cutoff=date(2026, 8, 14),
                previous_cutoff=date(2025, 8, 14),
            ),
            open_topics=None,
        )
    )

    assert not card.is_available
    assert card.unavailable_note
    assert card.links, "an unavailable card still offers the dashboard behind it"


def test_the_events_card_leads_with_the_near_term_horizon():
    """`X sündmust järgmise 30 päeva jooksul`, from the domain's own horizon.

    The same constant the shared timeline clips to, so the headline and the
    thirty-day list below it cannot describe different sets of events. The
    year-to-date pair is a supporting fact with its own like-for-like basis.
    """
    from apps.dashboard.executive import _events_card
    from apps.dashboard.executive_timeline import HORIZON_DAYS
    from apps.event_programme.executive import NEAR_TERM_DAYS

    card = _events_card(
        EventsExecutive(events_ytd=85, events_ytd_previous=70, starting_soon=6, completed_ytd=79)
    )

    assert NEAR_TERM_DAYS == HORIZON_DAYS
    assert card.headline.value == "6"
    assert card.headline.unit == f"sündmust järgmise {NEAR_TERM_DAYS} päeva jooksul"
    labels = [fact.label for fact in card.available_facts]
    assert "Sündmusi tänavu" in labels
    assert "Sama ajaks eelmisel aastal" in labels


def test_no_card_claims_an_attendance_figure():
    """DashKoda does not hold one, so no wording may imply it does.

    The programme workbook records what was scheduled. Registrations are
    Commerce and are gated off; attendance is not in this application at all.
    """
    from apps.dashboard.executive import _events_card

    card = _events_card(
        EventsExecutive(events_ytd=85, events_ytd_previous=70, starting_soon=6, completed_ytd=79)
    )
    words = " ".join(
        [card.headline.unit, card.period_line] + [fact.label for fact in card.facts]
    ).casefold()

    for forbidden in ("osaleja", "osalej", "kohalolij", "registreeri"):
        assert forbidden not in words


def test_the_website_card_never_calls_a_page_view_a_visit():
    """GA4 sessions are `külastused`; GA4 page views are `vaatamised`.

    Two different measures of two different things, and the commonest way to
    overstate a website is to spell the larger one with the smaller one's word.
    """
    from apps.dashboard.executive import _website_card
    from apps.news.executive import NewsExecutive
    from apps.visibility.executive import WebsiteExecutive

    card = _website_card(
        WebsiteExecutive(
            sessions=4210,
            engagement_rate=0.62,
            page_views=9100,
            start=date(2026, 7, 16),
            end=date(2026, 8, 14),
            days=30,
        ),
        NewsExecutive(news_views=2100, site_share=0.23, published=11, end=date(2026, 8, 14)),
    )

    assert card.headline.unit == "külastust"
    labels = [fact.label for fact in card.facts]
    assert "Uudiste vaatamised" in labels
    assert "Uudiste osa kodulehe vaatamistest" in labels
    # No label may spell a page view as a visit.
    for label in labels:
        folded = label.casefold()
        assert not ("vaatamis" in folded and "külastus" in folded)
    # And none may name the newsletter: the e-Teataja rate is the Otsepostitused
    # card's headline since 2026-08-17.
    assert not any("teataja" in label.casefold() for label in labels)


def test_the_shop_card_never_calls_ordered_value_revenue():
    """Order-time value net of VAT is not revenue, turnover or cash received.

    An order can be cancelled, refunded or never paid, and none of that reaches
    this dataset.
    """
    from apps.dashboard.executive import _shop_card
    from apps.shop.executive import ShopExecutive

    card = _shop_card(
        ShopExecutive(
            units=Decimal("412"),
            previous_units=Decimal("380"),
            ordered_value_net=Decimal("7420.50"),
            free_share=Decimal("74"),
            period_start=date(2026, 7, 3),
            period_end=date(2026, 8, 1),
            period_label="viimased 30 päeva",
            source_as_of=date(2026, 8, 1),
        )
    )

    assert card.headline.unit == "ühikut ostetud"
    labels = [fact.label for fact in card.facts]
    assert "Tellitud väärtus (KM-ta)" in labels
    words = " ".join(labels + [card.headline.unit]).casefold()
    for forbidden in ("tulu", "käive", "laekumine"):
        assert forbidden not in words


def test_the_mailings_card_carries_rates_and_never_an_audience():
    """Three lists whose overlap nobody measured do not add up to people.

    The card has no field capable of holding a subscriber count, let alone a sum
    across the three. The list sizes are the `Auditooriumid` strip's job, one
    per list.
    """
    from apps.dashboard.executive import _mailings_card
    from apps.visibility.mailings_executive import MailingsExecutive, NewsletterRates

    summary = MailingsExecutive(
        flagship=NewsletterRates(
            metric="newsletter_eteataja",
            label="e-Teataja",
            campaigns=12,
            open_rate=0.482,
            click_rate=0.091,
        ),
        flagship_previous_open_rate=0.441,
        others=(
            NewsletterRates(metric="newsletter_enews", label="eNews", campaigns=4, open_rate=0.37),
            NewsletterRates(
                metric="newsletter_evestnik", label="e-Vestnik", campaigns=3, open_rate=0.29
            ),
        ),
        issues=12,
    )

    card = _mailings_card(summary)

    assert card.headline.value == "48,2%"
    assert "avamismäär" in card.headline.unit
    # The movement is in percentage points, not percent: two rates differ by
    # points, and `+9%` of a percentage overstates it by an order of magnitude.
    assert "pp" in card.headline.comparison.text
    labels = [fact.label for fact in card.available_facts]
    assert labels == ["e-Teataja klikimäär", "eNews avamismäär", "e-Vestnik avamismäär"]
    # Nothing on this card is an audience, and nothing is a sum.
    words = " ".join(labels + [card.headline.unit, card.period_line]).casefold()
    for forbidden in ("tellija", "auditoorium", "kokku", "nimekirja suurus"):
        assert forbidden not in words


def test_an_uncollected_newsletter_is_unavailable_rather_than_a_zero_rate():
    from apps.dashboard.executive import _mailings_card
    from apps.visibility.mailings_executive import MailingsExecutive

    card = _mailings_card(MailingsExecutive())

    assert not card.is_available
    assert card.unavailable_note
    assert card.links


@pytest.mark.django_db
def test_the_data_status_section_speaks_per_source_not_per_collector():
    """Seven business sources, each with its own vocabulary.

    Membership appears twice on purpose: the daily public directory and the
    monthly board report are two sources with two cadences, and collapsing them
    into one row would force one freshness rule onto both.
    """
    page = _overview()

    keys = [row.domain_key for row in page.data_status]

    assert "membership" in keys and "membership_internal" in keys
    for row in page.data_status:
        assert row.source_label
        assert row.state_label
    # With nothing imported, nothing may claim to be available.
    assert all(row.state != STATE_AVAILABLE for row in page.data_status)


@pytest.mark.django_db
def test_the_interest_strip_is_website_news_and_shop_only():
    """Three columns, and the next event is deliberately not one of them.

    This section answers "what are people paying attention to". A scheduled date
    is not an answer to it, and events already hold a card above and the whole
    of `Järgmised 30 päeva` between.
    """
    page = _overview()

    assert [item.domain_key for item in page.interest] == ["website", "news", "shop"]


@pytest.mark.django_db
def test_the_audience_strip_never_repeats_the_website():
    """Sessions are the `Koduleht ja uudised` headline and appear once.

    The website slot was removed from `build_channel_band` outright rather than
    filtered out here: the overview was its only consumer, so a slot the band
    still built would be a query nobody renders. Asserted on the label the slot
    used to carry, because that is what a reintroduction would bring back.
    """
    page = _overview()

    assert page.channels, "the strip still names the audiences"
    assert all("külastused" not in slot.label.casefold() for slot in page.channels)


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


# ---------------------------------------------------------------------------
# The card's delta row
# ---------------------------------------------------------------------------


def _card_markup(card) -> str:
    from django.template.loader import render_to_string

    return render_to_string("dashboard/components/executive_domain_card.html", {"card": card})


def _delta_card(**comparison_kwargs) -> ExecutiveDomainCard:
    return ExecutiveDomainCard(
        key="shop",
        label="E-pood",
        headline=ExecutiveMetric(
            label="Soetatud ühikud",
            period="viimased 30 päeva",
            source="Koda.ee e-poe väljavõte",
            value="412",
            unit="ühikut ostetud",
            comparison=ExecutiveComparison(**comparison_kwargs),
        ),
    )


def test_the_card_draws_its_delta_and_names_the_basis():
    markup = _card_markup(
        _delta_card(text="+26,9%", basis="vs eelmine sama pikk periood", direction="up")
    )

    assert "+26,9%" in markup
    assert "vs eelmine sama pikk periood" in markup


def test_the_delta_is_read_off_the_metric_and_not_off_the_card():
    """The bug the pillar's delta row spent its whole first life in.

    `has_comparison` is a property of `ExecutiveMetric`. The old template asked
    `pillar.has_comparison`, Django resolved the missing attribute to falsy, and
    the row silently drew nothing for months — no test failed, because nothing
    asserted the row existed.

    So this pins both halves: the card genuinely does not carry the property,
    and the row renders anyway. A template that reaches through the card again
    fails here instead of going quiet.
    """
    card = _delta_card(text="+26,9%", basis="vs eelmine sama pikk periood", direction="up")

    assert not hasattr(card, "has_comparison"), (
        "if the card ever grows this property, this test stops proving anything"
    )
    assert card.headline.has_comparison
    assert "+26,9%" in _card_markup(card)


def test_the_delta_carries_direction_in_the_sign_not_only_in_colour():
    """A change distinguished only by hue does not exist for some readers."""
    up = _card_markup(_delta_card(text="+26,9%", basis="b", direction="up"))
    down = _card_markup(_delta_card(text="−12,0%", basis="b", direction="down"))

    assert "+26,9%" in up and "text-success" in up
    assert "−12,0%" in down and "text-danger" in down


def test_a_refused_comparison_still_prints_its_reason_instead_of_a_delta():
    markup = _card_markup(_delta_card(unavailable_note="Mõõtmisandmed on liiga ebaühtlased."))

    assert "Mõõtmisandmed on liiga ebaühtlased." in markup
    assert "text-success" not in markup


def test_a_card_without_a_comparison_draws_neither_delta_nor_note():
    card = ExecutiveDomainCard(
        key="events",
        label="Sündmused",
        headline=ExecutiveMetric(label="", period="2026", source="Sündmuste programm", value="85"),
    )
    markup = _card_markup(card)

    assert "85" in markup
    assert "text-success" not in markup
    assert "text-danger" not in markup


def test_the_card_draws_no_sparkline_and_no_meaning_sentence():
    """Six cards fit two rows only because none of them is tall.

    Both were on the pillar card and both restated something else: the sentence
    the comparison above it, the sparkline the two dates the comparison names.
    """
    card = ExecutiveDomainCard(
        key="membership",
        label="Liikmeskond",
        headline=ExecutiveMetric(
            label="Liikmeid kokku",
            period="viimane loend",
            source="X",
            value="3 412",
            unit="liiget",
        ),
        period_line="kataloog 14.08.26",
    )
    markup = _card_markup(card)

    assert "<svg" not in markup
    assert "polyline" not in markup
    fields = set(ExecutiveDomainCard.__dataclass_fields__)
    assert not fields & {"trend", "trend_label", "meaning"}


def test_an_unavailable_card_prints_no_period_line_beside_no_figure():
    """A period beside no figure describes nothing, so the line stays empty."""
    from apps.dashboard.executive import NO_SOURCE_NOTE

    card = ExecutiveDomainCard(key="shop", label="E-pood", unavailable_note=NO_SOURCE_NOTE)
    markup = _card_markup(card)

    assert NO_SOURCE_NOTE in markup
    assert card.period_line == ""
