"""Assemble the executive overview from the seven domain summaries.

The main dashboard is the decision and navigation layer of DashKoda, not another
analytical page. It answers, in this order: what state the Chamber's activity
domains are in, what needs management attention, what is coming in the next
thirty days, and how large the audiences it owns are. Everything past that is a
link to the dashboard that explains it.

The cards come first as of 2026-08-18 and `Tähelepanu` follows them. A section
of exceptions means nothing until the ordinary state is on screen, and it is
frequently empty — a page that opens with an empty section reads as a broken
one. `Praegu enim huvi` left the same day: which single page, article and
product happened to lead is a browsing question, and the three domain cards
already carry the volumes those leaders are a slice of.

## What this module is allowed to do

Read each domain's compact executive summary **once**, and turn it into the
presentation objects in `executive_models`. Format a number. Choose a label.
Decide which domain card a figure belongs to.

## What it is not allowed to do

Compute one. There is no ORM query in this file and no arithmetic on a domain's
figures beyond turning a comparison the domain already made into the string that
prints it. Every threshold, every window, every share and every signal arrives
decided.

## Six domain cards, one per dashboard

```text
Liikmeskond        → public Koda.ee directory count
Õigusloome         → open matters now
Sündmused          → events starting inside the near-term horizon
Koduleht ja uudised → website sessions, with news reading beside them
Otsepostitused     → e-Teataja's weighted open rate
E-pood             → non-event units acquired
```

That is the same set as the sidebar, deliberately: a reader who wants more of
what a card says opens the dashboard named on it, and a domain that is worth its
own page is worth its own card. The strip has been four, then five, then two, and
each of those was a subset chosen by hand — which meant the front page silently
decided that two of the Chamber's activities did not need reporting.

**The disjointness rule survives every one of those reorganisations**, because it
governs what a card may *count* rather than how many cards there are:

- the Sündmused card reads the programme workbook and no Commerce at all;
- the E-pood card reads Commerce minus `EVENT_REGISTRATION`;
- `Koduleht ja uudised` holds two sources that overlap by construction — news
  views are a subset of site views — and states the subset as a **share** rather
  than adding them;
- the Otsepostitused card carries rates and no audience, and the audience strip
  at the foot carries list sizes and never a total across them.

Nothing on this page sums across domains, and there is no total, no index and no
score. Six numbers in different units do not have a sum, and a weighted one would
hide exactly the trade-offs a manager opens this page to see.

## What a card does not carry

The question lines, the period · source · seis rows and the per-fact source
captions came off in the 2026-08-15 declutter and have not returned. Neither has
the meaning sentence or the sparkline: six cards fit two rows only because each
one is a label, a figure, a comparison, a few facts and a date. The
`ExecutiveMetric` objects keep `period`, `source` and `as_of` — `Andmete seis`
and the domain pages still need them — so the removal is presentation, and a
figure's provenance is one link away rather than under every number.
"""

from __future__ import annotations

from django.urls import reverse

from apps.core.formatting import (
    euros,
    integer,
    long_date,
    percent,
    percentage_points,
    short_date,
    signed_integer,
    signed_percent,
)
from apps.event_programme.executive import NEAR_TERM_DAYS, get_events_executive
from apps.event_programme.selectors import get_event_programme_summary
from apps.legal_work.executive import get_legal_work_executive
from apps.legal_work.selectors import get_legal_work_summary
from apps.membership.executive import get_membership_executive
from apps.membership.selectors import get_membership_summary
from apps.news.executive import get_news_executive
from apps.news.selectors import get_news_summary
from apps.shop.executive import get_shop_executive
from apps.visibility.executive import get_website_executive
from apps.visibility.mailings_executive import get_mailings_executive
from apps.visibility.page import build_audience_rows

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
    ExecutiveDomainCard,
    ExecutiveFact,
    ExecutiveLink,
    ExecutiveMetric,
    ExecutiveOverviewPage,
)
from .executive_signals import collect_signals
from .executive_timeline import build_timeline
from .freshness import latest_import_at

SOURCE_PUBLIC_DIRECTORY = "Koda.ee liikmekataloog"
SOURCE_INTERNAL_REPORT = "Koja sisemine liikmeskonna aruanne"
SOURCE_LEGAL_WORKBOOK = "Õigusloome töövihik"
SOURCE_EVENTS = "Sündmuste programm"
SOURCE_GA4 = "Google Analytics"
SOURCE_NEWS = "Koda.ee uudisvoog"
SOURCE_COMMERCE = "Koda.ee e-poe väljavõte"
SOURCE_SMAILY = "Smaily"

NO_SOURCE_NOTE = "Andmeallikas ei ole ühendatud."

#: The card covering the website and the news that sits on it.
#:
#: The brief that first built this page proposed `Nähtavus ja teavitamine`. It is
#: not used, because `Nähtavus` is a **retired product name**: the website
#: surface became `Koduleht` when that dashboard was rebuilt, `/nahtavus/`
#: survives only as a redirect, and `tests/visibility/test_pages.py` holds the
#: front page to never showing the old word again. Reintroducing it here as a
#: card heading would put a name on the main page that exists nowhere else in the
#: product, and a reader following it would look for a `Nähtavus` dashboard that
#: is gone.
#:
#: The two live product names say the same thing and match the card's own two
#: drill links.
WEBSITE_CARD_LABEL = "Koduleht ja uudised"


def build_executive_overview(*, legal_work, membership, news, events) -> ExecutiveOverviewPage:
    """Read every domain once and shape the whole page.

    The four feed summaries arrive from the view, which also hands them to the
    shell freshness row — so each is read exactly once per request. The seven
    executive summaries are read here, once each, and every section below is
    built from those same objects rather than from fresh queries: the cards,
    the signals, the timeline and the data status all share one read.
    """
    membership_exec = get_membership_executive()
    legal_exec = get_legal_work_executive(legal_work)
    events_exec = get_events_executive(events)
    website_exec = get_website_executive()
    news_exec = get_news_executive(news)
    mailings_exec = get_mailings_executive()
    shop_exec = get_shop_executive()

    return ExecutiveOverviewPage(
        cards=(
            _membership_card(membership_exec),
            _legal_card(legal_exec),
            _events_card(events_exec),
            _website_card(website_exec, news_exec),
            _mailings_card(mailings_exec),
            _shop_card(shop_exec),
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
        upcoming=build_timeline(legal_summary=legal_work, events_executive=events_exec),
        # One row per audience, largest first. Audiences only: the band had a
        # website slot until 2026-08-17 and it was removed there, because
        # sessions are the `Koduleht ja uudised` card's headline and one measure
        # under two labels on one page invites a reconciliation nobody can
        # perform. Sessions are visits, not an audience.
        audiences=build_audience_rows(),
        data_status=_data_status(
            legal_work=legal_work,
            membership=membership,
            membership_exec=membership_exec,
            news=news,
            events=events,
            website_exec=website_exec,
            shop_exec=shop_exec,
        ),
        updated_at=latest_import_at(),
    )


# ---------------------------------------------------------------------------
# Põhinäitajad — six compact domain cards
# ---------------------------------------------------------------------------


def _membership_card(summary) -> ExecutiveDomainCard:
    """Liikmeskond. The public directory count leads; the report supports.

    The two sources never share a figure. The headline is the koda.ee directory;
    the paid share, the fee collection and the joined/removed pair are ratios and
    movements **inside** the board report. AGENTS.md forbids two unlabelled
    member *totals* side by side, and this card shows exactly one total.

    The distinction is nevertheless visible without filling the card with
    provenance chrome: `period_line` names both currencies once — the catalogue's
    own reading date and the report's — so a reader can see that one figure is
    recounted whenever it changes and the others were reported once a month.
    Each fact still carries its `source` in the data, and `Andmete seis` at
    `/haldus/` states which source is which with the warning that the two must
    never be compared.
    """
    links = (ExecutiveLink(label="Vaata liikmeskonda", url=reverse("membership")),)
    if not summary.has_headline:
        return ExecutiveDomainCard(
            key="membership",
            label="Liikmeskond",
            unavailable_note=NO_SOURCE_NOTE,
            links=links,
        )

    return ExecutiveDomainCard(
        key="membership",
        label="Liikmeskond",
        headline=ExecutiveMetric(
            label="Liikmeid kokku",
            period="viimane loend",
            source=SOURCE_PUBLIC_DIRECTORY,
            value=integer(summary.total_members),
            unit="liiget",
            as_of=summary.total_as_of,
            comparison=_membership_comparison(summary),
        ),
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


def _legal_card(summary) -> ExecutiveDomainCard:
    """Õigusloome. The stock of open matters leads, not the year's output.

    `Arvamusi välja saadetud tänavu` led this card until 2026-08-17 and was the
    wrong headline for a management cockpit: it is a cumulative record of work
    already done, it can only rise, and by December it says nothing about what
    the Chamber is holding. `open_topics` — `X teemat töös` — is the state of
    play, and it is the figure that changes when somebody acts.

    Output is not impact, and neither figure on this card is ever called `mõju`:
    the workbook records opinions sent, not provisions changed.

    **Neither deadline figure is here.** `overdue_pending` and `due_within_7`
    are both `Tähelepanu`'s, where they arrive as the domain's own critical and
    attention signals with a link to the list the rows live in. `due_within_7`
    was a card fact until 2026-08-18 and was the page saying the same thing
    twice, quietly in one place and urgently in another — and the quiet copy is
    the one a reader meets first.
    """
    links = (ExecutiveLink(label="Vaata õigusloomet", url=reverse("legal-work")),)
    # `has_headline` says a snapshot exists; the headline itself is the open
    # count, so a snapshot that somehow carries no count renders unavailable
    # rather than as a nought nobody measured.
    if not summary.has_headline or summary.open_topics is None:
        return ExecutiveDomainCard(
            key="legal_work",
            label="Õigusloome",
            unavailable_note=NO_SOURCE_NOTE,
            links=links,
        )

    sent = summary.sent
    return ExecutiveDomainCard(
        key="legal_work",
        label="Õigusloome",
        headline=ExecutiveMetric(
            label="Teemasid töös",
            period="töövihiku seisu kuupäeval",
            source=SOURCE_LEGAL_WORKBOOK,
            value=integer(summary.open_topics),
            unit="teemat töös",
            as_of=summary.reporting_date,
            # No comparison. A stock has no year-to-date pair: the workbook
            # holds one snapshot, and "open matters a year ago" is not a figure
            # anything here can produce. The metric contract says so too.
            comparison=None,
        ),
        facts=(
            ExecutiveFact(
                label="Arvamusi saadetud tänavu",
                value=integer(sent.current) if sent is not None else None,
                source=SOURCE_LEGAL_WORKBOOK,
                as_of=summary.reporting_date,
            ),
            ExecutiveFact(
                # The baseline of the fact above, drawn quieter than it: both
                # sides stop on the same calendar day, and the like-for-like
                # cutoff is the whole reason the pair is worth printing.
                label="Sama ajaks eelmisel aastal",
                value=(
                    integer(sent.previous)
                    if sent is not None and sent.previous is not None
                    else None
                ),
                source=SOURCE_LEGAL_WORKBOOK,
                as_of=summary.reporting_date,
                is_secondary=True,
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


def _events_card(summary) -> ExecutiveDomainCard:
    """Sündmused. What is starting soon leads; the year's programme supports.

    The grain is named in the wording because the workbook also holds an
    occurrence sheet counting something else entirely: one row is one programme
    event, never an occurrence and never a calendar day.

    No attendance figure appears here or anywhere on this page, because DashKoda
    does not have one. The programme records what was scheduled.
    """
    links = (ExecutiveLink(label="Vaata sündmusi", url=reverse("events")),)
    if not summary.has_headline or summary.starting_soon is None:
        return ExecutiveDomainCard(
            key="events",
            label="Sündmused",
            unavailable_note=NO_SOURCE_NOTE,
            links=links,
        )

    return ExecutiveDomainCard(
        key="events",
        label="Sündmused",
        headline=ExecutiveMetric(
            label=f"Algab {NEAR_TERM_DAYS} päeva jooksul",
            period=f"järgmised {NEAR_TERM_DAYS} päeva",
            source=SOURCE_EVENTS,
            value=integer(summary.starting_soon),
            # Self-describing, because the card prints no period row: the same
            # horizon the timeline below uses, so the two cannot describe
            # different sets of events.
            unit=f"järgmise {NEAR_TERM_DAYS} päeva jooksul",
            as_of=summary.observed_at,
            # No comparison: "events starting in the next thirty days a year
            # ago" is not a figure the programme holds. The year-to-date pair is
            # below, where it belongs, with its own like-for-like basis.
            comparison=None,
        ),
        facts=(
            ExecutiveFact(
                label="Sündmusi tänavu",
                value=integer(summary.events_ytd) if summary.events_ytd is not None else None,
                source=SOURCE_EVENTS,
                as_of=summary.observed_at,
            ),
            ExecutiveFact(
                label="Sama ajaks eelmisel aastal",
                value=(
                    integer(summary.events_ytd_previous)
                    if summary.events_ytd_previous is not None
                    else None
                ),
                source=SOURCE_EVENTS,
                as_of=summary.observed_at,
                is_secondary=True,
            ),
        ),
        links=links,
    )


def _website_card(website, news) -> ExecutiveDomainCard:
    """Koduleht ja uudised. Two sources, side by side, never summed.

    Sessions lead because they are the closest the Chamber has to "how much is
    the website being used". News reading sits beside them as a **share** of site
    page views, which is the only honest relation between the two — news views
    are a subset, and adding them would count every article view twice.

    **The vocabulary is load-bearing.** A GA4 session is a `külastus` and a GA4
    page view is a `vaatamine`. They are different measures of different things
    and this card never calls a page view a `külastus`; neither is ever worded as
    a count of people, because a session is a visit and two visits by one person
    are two sessions.

    The e-Teataja open rate used to be the fifth fact here. It is the
    Otsepostitused card's headline since 2026-08-17: the newsletters have their
    own dashboard, and a rate about email was the one figure on this card that
    was not about the website.
    """
    links = (
        ExecutiveLink(label="Vaata kodulehte", url=reverse("visibility")),
        ExecutiveLink(label="Vaata uudiseid", url=reverse("news")),
    )
    if not website.has_headline:
        return ExecutiveDomainCard(
            key="website",
            label=WEBSITE_CARD_LABEL,
            unavailable_note=NO_SOURCE_NOTE,
            links=links,
        )

    return ExecutiveDomainCard(
        key="website",
        label=WEBSITE_CARD_LABEL,
        headline=ExecutiveMetric(
            label="Kodulehe külastused",
            period=f"viimased {website.days} mõõdetud päeva",
            source=SOURCE_GA4,
            value=integer(website.sessions),
            unit=f"külastust · {website.days} p",
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
        facts=(
            ExecutiveFact(
                label="Uudiste vaatamised",
                value=integer(news.news_views) if news.news_views is not None else None,
                source=SOURCE_GA4,
                as_of=news.end,
                url=reverse("news"),
            ),
            ExecutiveFact(
                # `vaatamistest`, never `külastustest`. Both sides of this share
                # are GA4 page views over the same days; spelling the
                # denominator as visits would make the card claim a ratio
                # between two different measures.
                label="Uudiste osa vaatamistest",
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
        ),
        period_line=(
            f"{short_date(website.start)} – {short_date(website.end)}"
            if website.start and website.end
            else ""
        ),
        links=links,
    )


def _mailings_card(summary) -> ExecutiveDomainCard:
    """Otsepostitused. e-Teataja's open rate leads; the others support.

    Rates only. **There is no audience figure on this card at all**, and there
    cannot be a total: the three lists overlap by an unmeasured amount, so a sum
    would silently claim the overlap is zero. The list sizes are in
    `Auditooriumid` at the foot of the page, one per list.

    Every rate is summed opens over summed delivered across the domain's own
    block of recent sends — never the mean of per-issue percentages, which would
    weight a send to 755 people the same as one to 20 616. The domain computes
    all of them; this function formats.
    """
    links = (ExecutiveLink(label="Vaata otsepostitusi", url=reverse("mailings")),)
    if not summary.has_headline:
        return ExecutiveDomainCard(
            key="mailings",
            label="Otsepostitused",
            unavailable_note=NO_SOURCE_NOTE,
            links=links,
        )

    flagship = summary.flagship
    movement = summary.open_rate_change_points
    return ExecutiveDomainCard(
        key="mailings",
        label="Otsepostitused",
        headline=ExecutiveMetric(
            label=f"{flagship.label} avamismäär",
            period=f"viimased {summary.issues} saadetist",
            source=SOURCE_SMAILY,
            value=percent(flagship.open_rate * 100),
            unit=f"{flagship.label} avamismäär",
            comparison=(
                ExecutiveComparison(
                    # Percentage **points**, not percent: two rates differ by
                    # points, and `+8%` of a percentage is the commonest way to
                    # overstate a newsletter by an order of magnitude.
                    text=percentage_points(movement),
                    basis=f"vs eelmised {summary.issues} saadetist",
                    direction=_direction(movement),
                )
                if movement is not None
                else None
            ),
        ),
        facts=(
            ExecutiveFact(
                label=f"{flagship.label} klikimäär",
                value=(
                    percent(flagship.click_rate * 100) if flagship.click_rate is not None else None
                ),
                source=SOURCE_SMAILY,
            ),
            ExecutiveFact(
                # How much went out, which is the one thing the rates cannot
                # say: a month with no letters and a month with four both have
                # an open rate, and only one of them is a month of work.
                label=f"Uudiskirju saadetud viimased {summary.cadence_days} päeva",
                value=_sends_value(summary),
                source=SOURCE_SMAILY,
            ),
        ),
        period_line=f"kaalutud viimase {summary.issues} saadetise peale",
        links=links,
    )


def _shop_card(summary) -> ExecutiveDomainCard:
    """E-pood. Acquired units over the Commerce export's own period.

    **Ordered value is not revenue.** `ordered_value_net` is what the orders were
    worth at order time excluding VAT; it is not recognised revenue, not cash
    received and not reconciled to any ledger, because an order can be cancelled,
    refunded or never paid and none of that reaches this dataset. The label says
    `tellitud väärtus` and never `tulu`, `käive` or `laekumine`.

    Event registrations are excluded at the query, which is what keeps this card
    and the Sündmused card from presenting one set of rows twice.
    """
    links = (ExecutiveLink(label="Vaata e-poodi", url=reverse("shop")),)
    if not summary.has_headline:
        return ExecutiveDomainCard(
            key="shop",
            label="E-pood",
            unavailable_note=NO_SOURCE_NOTE,
            links=links,
        )

    change = summary.change_pct
    return ExecutiveDomainCard(
        key="shop",
        label="E-pood",
        headline=ExecutiveMetric(
            label="Soetatud ühikud",
            period=summary.period_label,
            source=SOURCE_COMMERCE,
            value=integer(summary.units),
            # The period's own words, from the domain. The card cannot say
            # `viimase 30 päeva jooksul` unless the export's own window is
            # thirty days, and `resolve_period` decides that rather than this.
            unit=summary.period_label.lower(),
            as_of=summary.source_as_of,
            comparison=(
                ExecutiveComparison(
                    text=signed_percent(change),
                    basis="vs eelmine sama pikk periood",
                    direction=_direction(change),
                )
                if change is not None
                else None
            ),
        ),
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
        ),
        period_line=(
            f"{short_date(summary.period_start)} – {short_date(summary.period_end)}"
            if summary.period_start and summary.period_end
            else summary.period_label
        ),
        links=links,
    )


def _sends_value(summary) -> str | None:
    """Letters posted in the window, with the movement against the one before.

    `None` when nothing was collected — never `0`, which would say the Chamber
    sent nothing in a month it may simply never have been asked about. A genuine
    nought is a real answer and prints, which is why the test is `is not None`.
    """
    if summary.sends_recent is None:
        return None
    change = summary.sends_change
    if change is None or change == 0:
        return integer(summary.sends_recent)
    return f"{integer(summary.sends_recent)} ({signed_integer(change)})"


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
# Andmete seis
# ---------------------------------------------------------------------------


def build_data_status() -> tuple[ExecutiveDataStatus, ...]:
    """Every source row, read on its own.

    `/haldus/` renders this section since 2026-08-15 and has no page-wide read to
    borrow from, so it asks for the rows directly. The overview keeps building
    them inside `build_executive_overview` off the reads it already has, and both
    go through the same `_data_status`, so the two can never disagree about what
    a source's state is.
    """
    legal_work = get_legal_work_summary()
    membership = get_membership_summary()
    news = get_news_summary()
    events = get_event_programme_summary()
    return _data_status(
        legal_work=legal_work,
        membership=membership,
        membership_exec=get_membership_executive(),
        news=news,
        events=events,
        website_exec=get_website_executive(),
        shop_exec=get_shop_executive(),
    )


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
    the card shows no delta and the reason appears here.
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


__all__ = ["build_data_status", "build_executive_overview"]
