"""Assemble the executive overview from the six domain summaries.

The main dashboard is the decision and navigation layer of DashKoda, not another
analytical page. It answers, in this order: where the Chamber stands, what needs
attention, what is coming, what audiences are using, and whether the data can be
trusted. Everything past that is a link to the dashboard that explains it.

## What this module is allowed to do

Read each domain's compact executive summary **once**, and turn it into the
presentation objects in `executive_models`. Format a number. Choose a label.
Decide which of five pillars a figure belongs to.

## What it is not allowed to do

Compute one. There is no ORM query in this file and no arithmetic on a domain's
figures beyond turning a comparison the domain already made into the string that
prints it. Every threshold, every window, every share and every signal arrives
decided.

## The five pillars, and the rule that keeps them disjoint

```text
Liikmeskond   → public Koda.ee directory count
Huvikaitse    → opinions sent YTD, from the workbook
Kaasamine     → the events programme
Nähtavus      → website sessions + news reading + newsletter engagement
Digiteenused  → Commerce, minus event registrations
```

Kaasamine and Digiteenused are the pair that could double count, and they do not
share a row: the events pillar reads the programme workbook and no Commerce at
all, and the shop pillar excludes `EVENT_REGISTRATION` at the query. Nähtavus
holds two sources that overlap by construction — news views are a subset of site
views — and states the subset as a **share** rather than adding them.

Nothing on this page sums across pillars, and there is no total, no index and no
score. Five numbers in different units do not have a sum, and a weighted one
would hide exactly the trade-offs a manager opens this page to see.
"""

from __future__ import annotations

from django.urls import reverse

from apps.core.formatting import (
    euros,
    integer,
    long_date,
    percent,
    short_date,
    signed_integer,
    signed_percent,
)
from apps.event_programme.executive import NEAR_TERM_DAYS, get_events_executive
from apps.legal_work.executive import URGENT_DAYS, get_legal_work_executive
from apps.membership.executive import get_membership_executive
from apps.news.executive import get_news_executive
from apps.shop.executive import get_shop_executive
from apps.visibility.executive import get_website_executive
from apps.visibility.page import build_channel_band

from .executive_models import (
    STATE_AVAILABLE,
    STATE_LABELS,
    STATE_MANUAL,
    STATE_NOT_CONNECTED,
    STATE_PARTIAL,
    STATE_STALE,
    STATE_VARIANTS,
    ExecutiveComparison,
    ExecutiveDataStatus,
    ExecutiveFact,
    ExecutiveInterestItem,
    ExecutiveLink,
    ExecutiveMetric,
    ExecutiveOverviewPage,
    ExecutivePillar,
)
from .executive_signals import collect_signals
from .executive_timeline import build_timeline
from .sparkline import build_sparkline

SOURCE_PUBLIC_DIRECTORY = "Koda.ee liikmekataloog"
SOURCE_INTERNAL_REPORT = "Koja sisemine liikmeskonna aruanne"
SOURCE_LEGAL_WORKBOOK = "Õigusloome töövihik"
SOURCE_EVENTS = "Sündmuste programm"
SOURCE_GA4 = "Google Analytics"
SOURCE_NEWS = "Koda.ee uudisvoog"
SOURCE_COMMERCE = "Koda.ee e-poe väljavõte"
SOURCE_SMAILY = "Smaily"

NO_SOURCE_NOTE = "Andmeallikas ei ole ühendatud."

#: The strategic area covering the website, the news and the newsletters.
#:
#: The brief proposed `Nähtavus ja teavitamine`. It is not used, because
#: `Nähtavus` is a **retired product name**: the website surface became
#: `Koduleht` when that dashboard was rebuilt, `/nahtavus/` survives only as a
#: redirect, and `tests/visibility/test_pages.py` holds the front page to never
#: showing the old word again. Reintroducing it here as a pillar heading would
#: put a name on the main page that exists nowhere else in the product, and a
#: reader following it would look for a `Nähtavus` dashboard that is gone.
#:
#: The two live product names say the same thing and match the card's own two
#: drill links.
VISIBILITY_PILLAR_LABEL = "Koduleht ja uudised"


def build_executive_overview(*, legal_work, membership, news, events) -> ExecutiveOverviewPage:
    """Read every domain once and shape the whole page.

    The four feed summaries arrive from the view, which also hands them to the
    shell freshness row — so each is read exactly once per request. The six
    executive summaries are read here, once each, and every section below is
    built from those same objects rather than from fresh queries: the pillars,
    the signals, the timeline, the interest panels and the data status all share
    one read.
    """
    membership_exec = get_membership_executive()
    legal_exec = get_legal_work_executive(legal_work)
    events_exec = get_events_executive(events)
    website_exec = get_website_executive()
    news_exec = get_news_executive(news)
    shop_exec = get_shop_executive()

    return ExecutiveOverviewPage(
        pillars=(
            _membership_pillar(membership_exec),
            _legal_pillar(legal_exec),
            _events_pillar(events_exec),
            _visibility_pillar(website_exec, news_exec),
            _shop_pillar(shop_exec),
        ),
        signals=collect_signals(
            (
                ("membership", "Liikmeskond", membership_exec.signals),
                ("legal_work", "Õigusloome", legal_exec.signals),
                ("events", "Sündmused", events_exec.signals),
                ("website", "Koduleht", website_exec.signals),
                ("news", "Uudised", news_exec.signals),
                ("shop", "E-pood", shop_exec.signals),
            )
        ),
        upcoming=build_timeline(legal_summary=legal_work, events_summary=events),
        interest=_interest_panels(website_exec, news_exec, events_exec, shop_exec),
        channels=build_channel_band(),
        data_status=_data_status(
            legal_work=legal_work,
            membership=membership,
            membership_exec=membership_exec,
            news=news,
            events=events,
            website_exec=website_exec,
            shop_exec=shop_exec,
        ),
    )


# ---------------------------------------------------------------------------
# Pillars
# ---------------------------------------------------------------------------


def _membership_pillar(summary) -> ExecutivePillar:
    """Liikmeskond. The public directory count leads; the report supports.

    The two sources never share a figure. The headline is the koda.ee directory,
    the paid share and the fee collection are ratios inside the board report, and
    each fact names which source it came from — because AGENTS.md forbids two
    unlabelled member totals sitting side by side, and this pillar is the one
    place on the dashboard where both sources appear at once.
    """
    links = (ExecutiveLink(label="Vaata liikmeskonda", url=reverse("membership")),)
    if not summary.has_headline:
        return ExecutivePillar(
            key="membership",
            label="Liikmeskond",
            question="Kui tugev on liikmeskond?",
            unavailable_note=NO_SOURCE_NOTE,
            links=links,
        )

    return ExecutivePillar(
        key="membership",
        label="Liikmeskond",
        question="Kui tugev on liikmeskond?",
        headline=ExecutiveMetric(
            label="Liikmeid kokku",
            period="viimane loend",
            source=SOURCE_PUBLIC_DIRECTORY,
            value=integer(summary.total_members),
            unit="liiget",
            as_of=summary.total_as_of,
            comparison=_membership_comparison(summary),
        ),
        meaning=summary.meaning,
        facts=(
            ExecutiveFact(
                label="Tasunud liikmete osakaal",
                # `is not None`, not truthiness: a reported share of exactly 0%
                # is a measured value and must render, not vanish as missing.
                value=(
                    percent(summary.paid_share_pct) if summary.paid_share_pct is not None else None
                ),
                source=SOURCE_INTERNAL_REPORT,
                as_of=summary.internal_as_of,
            ),
            ExecutiveFact(
                label="Liikmemaksu laekumine",
                value=(
                    percent(summary.fee_collection_pct)
                    if summary.fee_collection_pct is not None
                    else None
                ),
                source=SOURCE_INTERNAL_REPORT,
                as_of=summary.internal_as_of,
            ),
            ExecutiveFact(
                label="Liitunud / välja arvatud sel aastal",
                value=(
                    f"{integer(summary.joined_ytd)} / {integer(summary.removed_ytd)}"
                    if summary.joined_ytd is not None and summary.removed_ytd is not None
                    else None
                ),
                source=SOURCE_INTERNAL_REPORT,
                as_of=summary.internal_as_of,
            ),
        ),
        trend=build_sparkline(summary.series),
        trend_label="Koda.ee liikmekataloogi loend",
        links=links,
    )


def _membership_comparison(summary) -> ExecutiveComparison | None:
    if not summary.has_comparison:
        return None
    text = (
        signed_percent(summary.change_relative_pct)
        if summary.change_relative_pct is not None
        else signed_integer(summary.change_absolute)
    )
    basis = (
        f"vs {long_date(summary.baseline_as_of)}" if summary.baseline_as_of else "vs aasta tagasi"
    )
    return ExecutiveComparison(
        text=text, basis=basis, direction=_direction(summary.change_absolute)
    )


def _legal_pillar(summary) -> ExecutivePillar:
    """Huvikaitse. Output, compared to the same calendar day a year earlier."""
    links = (ExecutiveLink(label="Vaata õigusloomet", url=reverse("legal-work")),)
    if not summary.has_headline:
        return ExecutivePillar(
            key="legal_work",
            label="Huvikaitse",
            question="Kui palju poliitikakujundamise tööd Koda kannab?",
            unavailable_note=NO_SOURCE_NOTE,
            links=links,
        )

    sent = summary.sent
    return ExecutivePillar(
        key="legal_work",
        label="Huvikaitse",
        question="Kui palju poliitikakujundamise tööd Koda kannab?",
        headline=ExecutiveMetric(
            label="Arvamusi välja saadetud tänavu",
            period=f"1. jaanuar – {short_date(sent.current_cutoff)}",
            source=SOURCE_LEGAL_WORKBOOK,
            value=integer(sent.current),
            unit="arvamust",
            as_of=summary.reporting_date,
            comparison=ExecutiveComparison(
                text=(
                    signed_percent(sent.percent_change)
                    if sent.percent_change is not None
                    else signed_integer(sent.absolute_change)
                ),
                basis=f"vs 1. jaanuar – {short_date(sent.previous_cutoff)}",
                direction=sent.direction,
            ),
        ),
        meaning=summary.meaning,
        facts=(
            ExecutiveFact(
                label="Teemasid töös",
                value=integer(summary.open_topics) if summary.open_topics is not None else None,
                source=SOURCE_LEGAL_WORKBOOK,
                as_of=summary.reporting_date,
                url=reverse("legal-work"),
            ),
            ExecutiveFact(
                label=f"Tähtaegu {URGENT_DAYS} päeva jooksul",
                value=integer(summary.due_within_7) if summary.due_within_7 is not None else None,
                source=SOURCE_LEGAL_WORKBOOK,
                as_of=summary.reporting_date,
            ),
            ExecutiveFact(
                label="Tänavusi teemasid",
                value=(
                    integer(summary.topics_this_year.current)
                    if summary.topics_this_year is not None
                    else None
                ),
                source=SOURCE_LEGAL_WORKBOOK,
                as_of=summary.reporting_date,
            ),
        ),
        links=links,
    )


def _events_pillar(summary) -> ExecutivePillar:
    """Kaasamine. Programme events — the grain is in the label, deliberately."""
    links = (ExecutiveLink(label="Vaata sündmusi", url=reverse("events")),)
    if not summary.has_headline:
        return ExecutivePillar(
            key="events",
            label="Kaasamine",
            question="Kui palju osalust Koda sündmustega loob?",
            unavailable_note=NO_SOURCE_NOTE,
            links=links,
        )

    return ExecutivePillar(
        key="events",
        label="Kaasamine",
        question="Kui palju osalust Koda sündmustega loob?",
        headline=ExecutiveMetric(
            # The grain is named in the label because the workbook also holds an
            # occurrence sheet counting something else entirely.
            label="Sündmusi tänavu",
            period="1. jaanuar – täna",
            source=SOURCE_EVENTS,
            value=integer(summary.events_ytd),
            unit="sündmust",
            as_of=summary.observed_at,
            comparison=ExecutiveComparison(
                text=(
                    signed_percent(summary.change_pct)
                    if summary.change_pct is not None
                    else signed_integer(summary.change)
                ),
                basis="vs sama ajaks eelmisel aastal",
                direction=_direction(summary.change),
            )
            if summary.change is not None
            else None,
        ),
        meaning=summary.meaning,
        facts=(
            ExecutiveFact(
                label=f"Algab {NEAR_TERM_DAYS} päeva jooksul",
                value=integer(summary.starting_soon) if summary.starting_soon is not None else None,
                source=SOURCE_EVENTS,
                as_of=summary.observed_at,
            ),
            ExecutiveFact(
                label="Toimunud sel aastal",
                value=integer(summary.completed_ytd) if summary.completed_ytd is not None else None,
                source=SOURCE_EVENTS,
                as_of=summary.observed_at,
            ),
            ExecutiveFact(
                label="Enim kasutatud vorm",
                value=(
                    f"{summary.top_delivery_mode} · {percent(summary.top_delivery_share_pct)}"
                    if summary.top_delivery_mode
                    else None
                ),
                source=SOURCE_EVENTS,
                as_of=summary.observed_at,
            ),
        ),
        links=links,
    )


def _visibility_pillar(website, news) -> ExecutivePillar:
    """Nähtavus ja teavitamine. Three sources, side by side, never summed.

    Sessions lead because they are the closest the Chamber has to "how much is
    the website being used". News reading sits beside them as a **share** of
    site page views, which is the only honest relation between the two — news
    views are a subset, and adding them would count every article view twice.
    The newsletter figure is a rate, not an audience, so it cannot be added to
    anything either.
    """
    links = (
        ExecutiveLink(label="Vaata kodulehte", url=reverse("visibility")),
        ExecutiveLink(label="Vaata uudiseid", url=reverse("news")),
    )
    if not website.has_headline:
        return ExecutivePillar(
            key="website",
            label=VISIBILITY_PILLAR_LABEL,
            question="Kui hästi Koda oma auditooriumideni jõuab?",
            unavailable_note=NO_SOURCE_NOTE,
            links=links,
        )

    period = f"viimased {website.days} mõõdetud päeva"
    return ExecutivePillar(
        key="website",
        label=VISIBILITY_PILLAR_LABEL,
        question="Kui hästi Koda oma auditooriumideni jõuab?",
        headline=ExecutiveMetric(
            label="Kodulehe seansid",
            period=period,
            source=SOURCE_GA4,
            value=integer(website.sessions),
            unit="seanssi",
            as_of=website.end,
            comparison=(
                ExecutiveComparison(
                    text=signed_percent(website.change_pct),
                    basis="vs eelmine sama pikk periood",
                    direction=_direction(website.change_pct),
                )
                if website.change_pct is not None
                else ExecutiveComparison(unavailable_note=website.comparison_note)
                if website.comparison_note
                else None
            ),
        ),
        meaning=website.meaning,
        facts=(
            ExecutiveFact(
                label="Kaasatuse määr",
                value=(
                    percent(website.engagement_rate * 100)
                    if website.engagement_rate is not None
                    else None
                ),
                source=SOURCE_GA4,
                as_of=website.end,
            ),
            ExecutiveFact(
                label="Uudiste vaatamised",
                value=integer(news.news_views) if news.news_views is not None else None,
                source=SOURCE_GA4,
                as_of=news.end,
                url=reverse("news"),
            ),
            ExecutiveFact(
                label="Uudiste osa kodulehe vaatamistest",
                value=percent(news.site_share * 100) if news.site_share is not None else None,
                source=SOURCE_GA4,
                as_of=news.end,
            ),
            ExecutiveFact(
                label="Avaldatud uudiseid",
                value=integer(news.published) if news.published is not None else None,
                source=SOURCE_NEWS,
                as_of=news.end,
            ),
            ExecutiveFact(
                label="e-Teataja avamismäär",
                value=(
                    percent(website.newsletter_open_rate * 100)
                    if website.newsletter_open_rate is not None
                    else None
                ),
                source=SOURCE_SMAILY,
            ),
        ),
        links=links,
    )


def _shop_pillar(summary) -> ExecutivePillar:
    """Digiteenused. Commerce without event registrations — see the shop module."""
    links = (ExecutiveLink(label="Vaata e-poodi", url=reverse("shop")),)
    if not summary.has_headline:
        return ExecutivePillar(
            key="shop",
            label="Digiteenused",
            question="Milliseid praktilisi digiteenuseid kasutatakse?",
            unavailable_note=NO_SOURCE_NOTE,
            links=links,
        )

    period = (
        f"{short_date(summary.period_start)} – {short_date(summary.period_end)}"
        if summary.period_start
        else summary.period_label
    )
    return ExecutivePillar(
        key="shop",
        label="Digiteenused",
        question="Milliseid praktilisi digiteenuseid kasutatakse?",
        headline=ExecutiveMetric(
            # "Mitte-sündmuse" is in the label because the exclusion is the
            # figure's definition, not a footnote to it.
            label="Mitte-sündmuse tooteid soetatud",
            period=period,
            source=SOURCE_COMMERCE,
            value=integer(summary.units),
            unit="ühikut",
            as_of=summary.source_as_of,
            comparison=(
                ExecutiveComparison(
                    text=signed_percent(summary.change_pct),
                    basis="vs eelmine sama pikk periood",
                    direction=_direction(summary.change_pct),
                )
                if summary.change_pct is not None
                else None
            ),
        ),
        meaning=summary.meaning,
        facts=(
            ExecutiveFact(
                label="Tellitud väärtus (KM-ta)",
                value=(
                    euros(summary.ordered_value_net)
                    if summary.ordered_value_net is not None
                    else None
                ),
                source=SOURCE_COMMERCE,
                as_of=summary.source_as_of,
            ),
            ExecutiveFact(
                label="Tasuta osakaal",
                value=percent(summary.free_share) if summary.free_share is not None else None,
                source=SOURCE_COMMERCE,
                as_of=summary.source_as_of,
            ),
            ExecutiveFact(
                label="Enim soetatud toode",
                value=summary.top_product.title if summary.top_product else None,
                source=SOURCE_COMMERCE,
                as_of=summary.source_as_of,
            ),
        ),
        links=links,
    )


def _direction(value) -> str:
    """`up`, `down` or `flat` from a signed number. `none` when unknown."""
    if value is None:
        return "none"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


# ---------------------------------------------------------------------------
# Praegu huvi pakkuv
# ---------------------------------------------------------------------------


def _interest_panels(website, news, events, shop) -> tuple[ExecutiveInterestItem, ...]:
    """Four panels, four metrics, four periods, no shared axis.

    Page views, article views, an event's scheduled date and acquired units are
    four different things. They are shown side by side because a reader wants to
    know what is being used *now*, not because the four numbers can be compared
    — which is why none of them is drawn as a bar against the others.
    """
    return (
        _website_panel(website),
        _news_panel(news),
        _event_panel(events),
        _shop_panel(shop),
    )


def _website_panel(website) -> ExecutiveInterestItem:
    """The leading page that is neither a news article nor an event page."""
    page = website.top_page
    if page is None:
        return ExecutiveInterestItem(
            domain_label="Koduleht",
            domain_key="website",
            title="",
            unavailable_note="Mõõdetud lehtede andmed puuduvad.",
        )
    return ExecutiveInterestItem(
        domain_label="Koduleht",
        domain_key="website",
        # `label` is the resolved title where DashKoda's own catalogues knew it
        # and the decoded path where they did not. Never a title invented from
        # a slug.
        title=page.label,
        metric_value=integer(page.page_views),
        metric_label="lehevaatamist",
        period=f"viimased {website.days} mõõdetud päeva",
        context=page.type_label,
        url=page.url,
        is_external=True,
    )


def _news_panel(news) -> ExecutiveInterestItem:
    """The most-read article in the window, whenever it was published."""
    article = news.top_article
    if article is None:
        return ExecutiveInterestItem(
            domain_label="Uudised",
            domain_key="news",
            title="",
            unavailable_note="Mõõdetud uudiste andmed puuduvad.",
        )
    published = getattr(article, "published_at", None)
    return ExecutiveInterestItem(
        domain_label="Uudised",
        domain_key="news",
        title=getattr(article, "title", "") or article.path,
        metric_value=integer(news.top_article_views),
        metric_label="vaatamist perioodil",
        period=f"{short_date(news.start)} – {short_date(news.end)}",
        # Publication date beside the figure rather than folded into it: an old
        # article leading the panel is a real and interesting result, and the
        # reader has to be able to see that is what happened.
        context=f"avaldatud {short_date(published)}" if published else "",
        url=article.canonical_url,
        is_external=True,
    )


def _event_panel(events) -> ExecutiveInterestItem:
    """The next scheduled event.

    Deliberately the *next* one rather than the most-viewed: a completed event
    cannot occupy a panel about what is coming, and the programme's own order is
    the honest answer to "what is next". Page views are not shown here, because
    an upcoming event's views belong to a window this panel does not state.
    """
    upcoming = getattr(events, "next_event", None)
    if upcoming is None:
        return ExecutiveInterestItem(
            domain_label="Sündmused",
            domain_key="events",
            title="",
            unavailable_note="Tulemas sündmusi ei ole.",
        )
    return ExecutiveInterestItem(
        domain_label="Sündmused",
        domain_key="events",
        title=upcoming.event_name,
        metric_value=short_date(upcoming.start_date),
        metric_label="algab",
        period="programmi järgi",
        context=getattr(upcoming, "delivery_mode", "") or "",
        url=upcoming.public_link.url if getattr(upcoming, "public_link", None) else "",
        is_external=True,
    )


def _shop_panel(shop) -> ExecutiveInterestItem:
    """The most-acquired non-event product in the Commerce period."""
    product = shop.top_product
    if product is None:
        return ExecutiveInterestItem(
            domain_label="E-pood",
            domain_key="shop",
            title="",
            unavailable_note="E-poe andmed puuduvad.",
        )
    return ExecutiveInterestItem(
        domain_label="E-pood",
        domain_key="shop",
        title=product.title,
        metric_value=integer(product.units),
        metric_label="ühikut soetatud",
        period=(
            f"{short_date(shop.period_start)} – {short_date(shop.period_end)}"
            if shop.period_start
            else shop.period_label
        ),
        context=product.product_type_label,
        url=reverse("shop"),
    )


# ---------------------------------------------------------------------------
# Andmete seis
# ---------------------------------------------------------------------------


def _data_status(
    *, legal_work, membership, membership_exec, news, events, website_exec, shop_exec
) -> tuple[ExecutiveDataStatus, ...]:
    """One row per business source, in that source's own vocabulary.

    Not one freshness rule applied seven times. `current_freshness` counts wired
    feeds for the shell row and keeps doing exactly that; this section answers a
    different question — whether the *business figures above* can be trusted —
    and a monthly board report or a dated Commerce export answers it differently
    from a daily collector.
    """
    return (
        _feed_row(
            "Liikmeskond",
            "membership",
            SOURCE_PUBLIC_DIRECTORY,
            membership,
            as_of=membership_exec.total_as_of,
            coverage="Loend kirjutatakse ainult siis, kui arv muutub.",
        ),
        ExecutiveDataStatus(
            domain_label="Liikmeskond",
            domain_key="membership_internal",
            source_label=SOURCE_INTERNAL_REPORT,
            state=STATE_MANUAL if membership_exec.internal_as_of else STATE_NOT_CONNECTED,
            state_label=STATE_LABELS[
                STATE_MANUAL if membership_exec.internal_as_of else STATE_NOT_CONNECTED
            ],
            state_variant=STATE_VARIANTS[
                STATE_MANUAL if membership_exec.internal_as_of else STATE_NOT_CONNECTED
            ],
            as_of=membership_exec.internal_as_of,
            coverage="Kord kuus, koja enda aruandest.",
            limitation=(
                "Loeb liikmeks muud kui avalik kataloog; kahte arvu ei tohi kõrvutada ega lahutada."
            ),
        ),
        _feed_row(
            "Õigusloome",
            "legal_work",
            SOURCE_LEGAL_WORKBOOK,
            legal_work,
            as_of=legal_work.reporting_date,
            coverage="Kõik näitajad lõpevad töövihiku seisu kuupäeval.",
        ),
        _feed_row(
            "Sündmused",
            "events",
            SOURCE_EVENTS,
            events,
            as_of=events.observed_at,
            coverage="Üks kirje = üks programmi sündmus, mitte toimumiskord.",
            limitation="Registreerimisi ega osalejaid see allikas ei sisalda.",
        ),
        _website_row(website_exec),
        _feed_row(
            "Uudised",
            "news",
            SOURCE_NEWS,
            news,
            as_of=news.observed_at,
            coverage="Artiklite kataloog; lugemine tuleb Google Analyticsist.",
        ),
        _shop_row(shop_exec),
    )


def _feed_row(
    label: str,
    key: str,
    source_label: str,
    summary,
    *,
    as_of=None,
    coverage: str = "",
    limitation: str = "",
) -> ExecutiveDataStatus:
    """A wired feed's row, using the summary's own connected/stale verdict.

    `has_data` and `is_stale_after_failure` come from `FeedSummaryMixin`, so this
    row and the module's own page cannot disagree about whether a source is
    publishing or showing older data after a failed check.
    """
    if not summary.has_data:
        state = STATE_NOT_CONNECTED
    elif summary.is_stale_after_failure:
        state = STATE_STALE
    else:
        state = STATE_AVAILABLE
    return ExecutiveDataStatus(
        domain_label=label,
        domain_key=key,
        source_label=source_label,
        state=state,
        state_label=STATE_LABELS[state],
        state_variant=STATE_VARIANTS[state],
        as_of=as_of,
        coverage=coverage,
        limitation=limitation,
    )


def _website_row(website) -> ExecutiveDataStatus:
    """GA4's row, which is partial whenever the comparison was refused.

    This is the one place a data-quality fact is allowed to change what the
    business figures claim: when coverage is too uneven to subtract two windows,
    the pillar shows no delta and the reason appears here.
    """
    if not website.has_headline:
        state = STATE_NOT_CONNECTED
    elif website.comparison_note:
        state = STATE_PARTIAL
    else:
        state = STATE_AVAILABLE
    return ExecutiveDataStatus(
        domain_label="Koduleht",
        domain_key="website",
        source_label=SOURCE_GA4,
        state=state,
        state_label=STATE_LABELS[state],
        state_variant=STATE_VARIANTS[state],
        as_of=website.end,
        coverage=(
            f"{short_date(website.start)} – {short_date(website.end)}" if website.start else ""
        ),
        limitation=website.comparison_note,
    )


def _shop_row(shop) -> ExecutiveDataStatus:
    """Commerce's row. A dated manual export, which is not a failed feed."""
    state = STATE_MANUAL if shop.has_headline else STATE_NOT_CONNECTED
    return ExecutiveDataStatus(
        domain_label="E-pood",
        domain_key="shop",
        source_label=SOURCE_COMMERCE,
        state=state,
        state_label=STATE_LABELS[state],
        state_variant=STATE_VARIANTS[state],
        as_of=shop.source_as_of,
        coverage=(
            f"{short_date(shop.period_start)} – {short_date(shop.period_end)}"
            if shop.period_start
            else ""
        ),
        limitation="Käsitsi tehtud väljavõte; perioodid on ankurdatud väljavõtte lõppkuupäevale.",
    )


__all__ = ["build_executive_overview"]
