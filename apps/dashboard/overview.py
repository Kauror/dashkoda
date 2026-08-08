"""Assemble the overview page from the module selectors.

The view stays a view: it renders. Everything about *what the board sees* — which
figure leads, what the change window is, when something is worth flagging — is
decided here, in one readable place, so the template holds layout and no
business rule.

Every query reaches PostgreSQL through a module's own `selectors.py`. Nothing
here talks to a source, opens a file or knows how a feed is collected.

Three rules run through the whole module:

- a figure whose source is not connected is `None`, and the card renders its
  empty state. It is never `0`, because nobody counted zero;
- every figure is built with the `Connection` it came from — its source name,
  its update cadence and its state. The card footers now show only the as-of
  date, but the two membership figures still name their sources beside their
  trends, because the public directory is recounted daily and the internal board
  report arrives monthly and two numbers of such different currency must never
  sit side by side unlabelled;
- a comparison states its own baseline. Every count on the headline strip is
  measured over the same fixed window, and each cell says so in its own words,
  so no reader has to hold a period in their head while moving between cells.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.urls import reverse
from django.utils import timezone

from apps.core.formatting import GROUP_SEPARATOR, percentage, whole_euros
from apps.event_programme.selectors import (
    NEAR_TERM_DAYS,
    count_events_started_within,
    count_events_starting_within,
    get_upcoming_programme_events,
)
from apps.legal_work.sections import (
    SECTION_OPEN,
    SECTION_RECEIVED,
    SECTION_SENT,
    anchor,
)
from apps.legal_work.selectors import (
    ACTIVITY_WINDOW_DAYS,
    count_received_since,
    count_sent_since,
    get_latest_sent_items,
    get_open_items_by_deadline,
)
from apps.legal_work.topic_links import present_topics, resolve_links_for
from apps.membership.internal_selectors import (
    get_internal_membership_latest,
    get_internal_membership_trend,
    get_internal_observation_span,
)
from apps.membership.ranges import (
    CARD_DEFAULT_MONTHS,
    DateWindow,
    offers_choice,
    resolve_window,
)
from apps.membership.selectors import (
    CHANGE_WINDOW_DAYS,
    MembershipChange,
    get_membership_change_over,
)
from apps.news.selectors import get_latest_news
from apps.visibility.page import ChannelSlot, build_channel_band

from .connections import (
    CADENCE_DAILY,
    CADENCE_MONTHLY,
    Connection,
    ConnectionState,
    from_summary,
)
from .sparkline import (
    TrendChart,
    TrendSource,
    build_trend_chart,
    meter_width,
)

# How many rows each card previews before sending the reader to its own page.
#
# One number per card rather than one shared default, because a grid row is as
# tall as its tallest card: a card listing fewer rows than the one beside it
# leaves space that is already being paid for. The cards are therefore tuned in
# pairs, by the row they sit in.
#
# Row one — the Õigusloome card shows one list at a time behind three tabs, so
# its rows cost a third of what an untabbed card's would, and it sits beside the
# Liikmeskond card's three figures and its chart.
LEGAL_PREVIEW_LIMIT = 7

#: Töös carries the whole active population now that arrivals are not a separate
#: tab. Twice the preview limit, because two tabs of seven could jointly surface
#: fourteen distinct active records and dropping one tab must not shrink what an
#: active record has to compete against to be seen. The full Õigusloome page
#: lists every open record; this is the summary of the most urgent of them.
LEGAL_ACTIVE_LIMIT = 14

# Row two — two list cards of the same shape, kept level with each other. Five is
# what the events card was asked for; the news card follows it so this row does
# not gain the gap row one just lost.
EVENTS_PREVIEW_LIMIT = 5
NEWS_PREVIEW_LIMIT = 5

SOURCE_PUBLIC_DIRECTORY = "Koda.ee liikmekataloog"
SOURCE_INTERNAL_REPORT = "Sisemine liikmeskonna aruanne"
SOURCE_LEGAL_WORKBOOK = "Õigusloome töövihik"
# The Chamber's own programme, not the public calendar. The board's event figures
# come from the canonical Excel export, so the label has to name that and not the
# website it used to be scraped from.
SOURCE_EVENTS = "Sündmuste programm"
SOURCE_NEWS = "Koda.ee uudisvoog"


@dataclass(frozen=True)
class KpiDetail:
    """One supporting count inside a headline cell.

    A module whose figures are of equal weight — topics in hand, arrived, sent —
    has no single number to promote, and promoting one anyway would read as the
    module's headline while the rest read as footnotes. Each row states its own
    period, because they are not all the same.

    `url` is where the rows behind the count are listed. It is optional: a count
    whose module has no section listing exactly those records stays plain text
    rather than linking somewhere approximate, because a link that lands on a
    different set of rows than the number describes is worse than no link.
    """

    label: str
    value: int | str
    url: str = ""


@dataclass(frozen=True)
class Kpi:
    """One cell of the headline strip.

    Field names match `dashboard/components/kpi_card.html` so the template
    passes them straight through rather than renaming them on the way.

    `connection` is the exception: the card footer prints only `as_of`, so
    nothing renders it today. It stays because a figure that does not know
    where it came from cannot be given its source back — by a footer, an
    export or a tooltip — without re-deriving it somewhere else.
    """

    label: str
    connection: Connection
    value: int | Decimal | str | None = None
    unit: str = ""
    change: str = ""
    change_direction: str = ""
    comparison_period: str = ""
    secondary: str = ""
    meter_pct: float | None = None
    as_of: date | datetime | None = None
    details: tuple[KpiDetail, ...] = ()

    @property
    def has_data(self) -> bool:
        return self.value is not None or bool(self.details)


@dataclass(frozen=True)
class SourcedFigure:
    """A number that always arrives with its provenance and its trend.

    Used where two differently-sourced figures appear together. Keeping the
    source and the cadence *inside* the figure means a template cannot render
    one without the other.
    """

    label: str
    connection: Connection
    value: int | Decimal | None = None
    unit: str = ""
    as_of: date | datetime | None = None
    series: tuple = ()
    note: str = ""


@dataclass(frozen=True)
class FeeCollection:
    """Membership-fee collection from the latest board report.

    The report may carry a stated percentage and the ingredients to compute one,
    and those two do not always agree. Neither is silently preferred: `basis`
    names which one is on screen, and the Liikmeskond page shows both side by
    side without reconciling them.
    """

    connection: Connection
    percentage: Decimal | None = None
    basis: str = ""
    amounts: str = ""
    meter_pct: float | None = None
    as_of: date | None = None

    @property
    def has_data(self) -> bool:
        return self.percentage is not None or bool(self.amounts)


@dataclass(frozen=True)
class MembershipCard:
    """Everything the board report says about the membership, in one card.

    `internal_total` and `internal` are the report's own total and paid counts.
    They share a definition, a report and a reading date, so `chart` puts them on
    one pair of axes and the gap between the lines *is* the paid share stated
    beside the figure. `fee` is the same report's fee collection, which used to
    sit in the headline strip away from the counts it is a ratio of.

    The koda.ee directory count is a different definition on a different cadence.
    It leads the headline strip and is not repeated here: one number, in one
    place, said once. Neither figure prints its source on the overview any more;
    the Liikmeskond page is where both are named in full.
    """

    internal: SourcedFigure
    internal_total: SourcedFigure
    fee: FeeCollection
    change: MembershipChange
    paid_share_pct: Decimal | None = None
    chart: TrendChart | None = None
    trend_window: DateWindow | None = None
    trend_earliest: date | None = None
    trend_latest: date | None = None

    @property
    def has_range_choice(self) -> bool:
        """Whether there is a choice worth offering.

        A history of one observation date has one drawable window; a pair of
        date fields over it could not change what is drawn, and a control that
        changes nothing reads as a control that is broken.
        """
        return offers_choice(earliest=self.trend_earliest, latest=self.trend_latest)

    @property
    def has_any_data(self) -> bool:
        return (
            self.internal_total.value is not None
            or self.internal.value is not None
            or self.fee.has_data
        )


@dataclass(frozen=True)
class OverviewPage:
    """Everything the overview template renders, plus each module's connection.

    The three `Connection` fields are not rendered: the card footers show the
    as-of date alone. They are what the page knows about where its figures came
    from, and they are kept for the same reason as `Kpi.connection`.
    """

    kpis: tuple[Kpi, ...]
    membership: MembershipCard
    legal_work: Connection
    # The three Õigusloome lists, in the order the card's tabs offer them: what
    # is in hand, what has just arrived, what has gone out. `Töös` leads because
    # it is the only one of the three that is a state rather than an event — a
    # board member opening the page asks what is on the table before asking what
    # moved.
    #
    # These hold `LegalTopicPresentation`, not `LegalWorkItem`: the imported row
    # plus whatever public address the read path resolved for it. The row itself
    # is reached through `.item` and is never modified.
    legal_work_open: tuple
    legal_work_sent: tuple
    events: Connection
    upcoming_events: tuple
    news: Connection
    latest_news: tuple
    channels: tuple[ChannelSlot, ...]


def build_overview(
    *,
    legal_work,
    membership,
    news,
    events,
    trend_from=None,
    trend_to=None,
    trend_range_key=None,
) -> OverviewPage:
    """Read every connected module once and shape it for the page.

    The channel band is the one part assembled elsewhere: `apps.visibility` owns
    what those figures mean, how stale they are and how they must be worded, and
    restating any of that here would let the overview and the Nähtavus page drift
    apart about the same number.

    `trend_from`, `trend_to` and the legacy `trend_range_key` are whatever
    arrived in the query string. None of them is trusted and none reaches a
    query: `apps.membership.ranges` folds them into a window the observation
    history can actually answer, or into the default.
    """
    today = timezone.localdate()
    window_start = today - timedelta(days=ACTIVITY_WINDOW_DAYS)
    snapshot = legal_work.snapshot

    legal_connection = from_summary(legal_work, label=SOURCE_LEGAL_WORKBOOK)

    # The card's three tabs list the same records the Õigusloome page lists, and
    # a record often appears in more than one tab. All three are read first and
    # their links resolved together in one query, so the same record cannot be
    # clickable under `Töös` and plain text under `Sisse tulnud`.
    #
    # The `if snapshot` guards stay: the selectors fall back to reading the
    # current snapshot themselves when handed `None`, which would be three
    # pointless queries on a page that already knows there is nothing published.
    legal_open_items = (
        list(get_open_items_by_deadline(snapshot, limit=LEGAL_ACTIVE_LIMIT)) if snapshot else []
    )
    legal_sent_items = (
        list(get_latest_sent_items(snapshot, limit=LEGAL_PREVIEW_LIMIT)) if snapshot else []
    )
    legal_links = resolve_links_for(legal_open_items, legal_sent_items)
    legal_open = present_topics(legal_open_items, legal_links)
    legal_sent = present_topics(legal_sent_items, legal_links)

    public_connection = from_summary(
        membership, label=SOURCE_PUBLIC_DIRECTORY, cadence=CADENCE_DAILY
    )
    events_connection = from_summary(events, label=SOURCE_EVENTS, cadence=CADENCE_DAILY)
    news_connection = from_summary(news, label=SOURCE_NEWS, cadence=CADENCE_DAILY)

    internal_latest = get_internal_membership_latest()
    internal_connection = Connection(
        label=SOURCE_INTERNAL_REPORT,
        state=(ConnectionState.CONNECTED if internal_latest else ConnectionState.NOT_CONNECTED),
        cadence=CADENCE_MONTHLY,
    )

    change = get_membership_change_over(days=CHANGE_WINDOW_DAYS)
    received_recent = count_received_since(snapshot, window_start) if snapshot else None
    sent_recent = count_sent_since(snapshot, window_start) if snapshot else None
    # Both windows are read from the one current workbook snapshot. The workbook
    # retains what already happened, so the backward window is a straight count
    # rather than an archaeology of older snapshots — and neither figure uses
    # `source_year`, which is the annual sheet a row sat on and not a date.
    events_near_term = count_events_starting_within(events.snapshot) if events.has_data else None
    events_last_month = count_events_started_within(events.snapshot) if events.has_data else None

    return OverviewPage(
        kpis=_build_kpis(
            legal_work=legal_work,
            legal_connection=legal_connection,
            public_connection=public_connection,
            events=events,
            events_connection=events_connection,
            events_near_term=events_near_term,
            events_last_month=events_last_month,
            change=change,
            received_recent=received_recent,
            sent_recent=sent_recent,
        ),
        membership=_build_membership_card(
            change=change,
            internal_latest=internal_latest,
            internal_connection=internal_connection,
            trend_from=trend_from,
            trend_to=trend_to,
            trend_range_key=trend_range_key,
        ),
        legal_work=legal_connection,
        legal_work_open=legal_open,
        legal_work_sent=legal_sent,
        events=events_connection,
        upcoming_events=tuple(
            get_upcoming_programme_events(events.snapshot, limit=EVENTS_PREVIEW_LIMIT)
        ),
        news=news_connection,
        latest_news=tuple(get_latest_news(news.snapshot, limit=NEWS_PREVIEW_LIMIT)),
        channels=build_channel_band(detail_url=reverse("visibility")),
    )


def _build_kpis(
    *,
    legal_work,
    legal_connection,
    public_connection,
    events,
    events_connection,
    events_near_term,
    events_last_month,
    change,
    received_recent,
    sent_recent,
) -> tuple[Kpi, ...]:
    """The three headline cells, in the order the board reads them.

    Each cell now carries its module's own counts instead of one figure with a
    separate strip of movements repeating the same numbers underneath. A module
    with several counts of equal weight lists them; only the member total has a
    single figure to lead with.

    A count links to the section that lists the records behind it. Each of the
    three Õigusloome counts has such a section and points at its own: `töös` at
    Hetkel töös, `uusi` at Uusimad sisse tulnud, `välja läinud` at Viimati välja
    läinud. The two Sündmused counts have no equivalent — the events page lists
    the programme, not the two windows this strip counts — so they stay plain
    text. A link that lands on a different set of rows than the number describes
    is worse than no link.
    """
    legal_page = reverse("legal-work")
    return (
        Kpi(
            label="Liikmeid kokku",
            connection=public_connection,
            value=change.current.total_members if change.current else None,
            unit="liiget",
            change=change.label or "",
            change_direction=change.direction if change.has_change else "",
            comparison_period=(
                f"viimase {CHANGE_WINDOW_DAYS} päeva jooksul" if change.has_change else ""
            ),
            # The source line is deliberately not repeated under the figure: the
            # strip states the reading date, and the board reads this cell as the
            # public count. `connection` still travels with the figure, so a
            # footer or an export can give it its source back without re-deriving
            # where the number came from.
            as_of=change.current.observed_at if change.current else None,
        ),
        Kpi(
            label="Õigusloome",
            connection=legal_connection,
            details=_counts(
                (
                    legal_work.open_count if legal_work.has_data else None,
                    "teemasid töös",
                    anchor(legal_page, SECTION_OPEN),
                ),
                (
                    received_recent,
                    f"uusi teemasid {ACTIVITY_WINDOW_DAYS} päevaga",
                    anchor(legal_page, SECTION_RECEIVED),
                ),
                (
                    sent_recent,
                    f"välja läinud teemasid {ACTIVITY_WINDOW_DAYS} päevaga",
                    anchor(legal_page, SECTION_SENT),
                ),
            ),
            as_of=legal_work.reporting_date,
        ),
        Kpi(
            label="Sündmused",
            connection=events_connection,
            details=_counts(
                (events_near_term, f"sündmusi järgmise {NEAR_TERM_DAYS} päeva jooksul", ""),
                (events_last_month, f"sündmusi eelmise {NEAR_TERM_DAYS} päeva jooksul", ""),
            ),
            # No `secondary` line: `kpi_card` renders one only beside a hero
            # value, and this cell is details-only. The predecessor here passed
            # "Kalendris kokku {n}" and it never reached a page either. The two
            # window counts are what this cell claims, and the programme's own
            # total is on the Sündmused page where it can be labelled properly.
            as_of=events.observed_at,
        ),
    )


def _counts(*rows) -> tuple[KpiDetail, ...]:
    """The rows whose count is known, as `(value, label, url)`.

    A missing count contributes no row at all. A zero written where nothing was
    counted reads as "none happened", which is a measurement nobody made.

    `url` is empty where the count has no section listing exactly the rows it
    describes; it is spelled out on every row rather than defaulted, so adding a
    count is a decision about where it points and not an omission.
    """
    return tuple(
        KpiDetail(label=label, value=value, url=url)
        for value, label, url in rows
        if value is not None
    )


def _build_fee_collection(internal_latest, connection) -> FeeCollection:
    """Fee collection, read off the same board report as the member counts."""
    if internal_latest is None:
        return FeeCollection(connection=connection)

    reported = internal_latest.value("membership_fee_collection_pct_reported")
    computed = internal_latest.computed_collection_pct
    percentage = reported if reported is not None else computed
    received = internal_latest.value("membership_fees_received_eur")
    budget = internal_latest.value("membership_fee_budget_eur")

    amounts = ""
    if received is not None and budget is not None:
        amounts = f"{_euros(received)} / {_euros(budget)}"
    elif received is not None:
        amounts = _euros(received)

    return FeeCollection(
        connection=connection,
        percentage=_percentage(percentage),
        basis=("raporteeritud" if reported is not None else "arvutatud" if computed else ""),
        amounts=amounts,
        meter_pct=meter_width(percentage),
        as_of=internal_latest.observation_date,
    )


def _percentage(value: Decimal | None) -> Decimal | None:
    """A percentage at two decimals, as `apps.core.formatting` defines it.

    Rounding is presentational only: the stored value, which the Liikmeskond
    page shows beside the computed one, is untouched.
    """
    return percentage(value)


def _whole_percent(value: Decimal | None) -> Decimal | None:
    """A supporting share, to the nearest whole percent.

    The paid share sits beside the paid figure as a ratio to be taken in at a
    glance, and `82,00 %` there offers a precision nobody reads. It is derived
    inside the board report rather than reported by it, so rounding the display
    states nothing the source did not.
    """
    if value is None:
        return None
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _euros(amount: Decimal) -> str:
    """A whole-euro amount with its symbol.

    The number itself is shaped by `apps.core.formatting`, so this card and the
    Liikmeskond page cannot end up writing the same amount two ways.
    """
    return whole_euros(amount) + f"{GROUP_SEPARATOR}€"


def _build_membership_card(
    *, change, internal_latest, internal_connection, trend_from, trend_to, trend_range_key
) -> MembershipCard:
    """The board report's own figures, and only two of them are charted.

    The **chart is the report's total against its paid count**. That pairing is a
    real comparison: both lines are the same definition of a member, read off the
    same report on the same day, and the share stated beside the paid figure is
    literally the gap between them.

    Drawing the koda.ee directory's total on those axes instead would put two
    definitions on one axis and invite exactly the subtraction that is forbidden.
    It also could not be drawn: an unchanged daily check writes no observation,
    so the directory series is a single point for weeks at a time and a single
    point is not a trend. That count leads the headline strip and is not
    repeated in this card.

    Fee collection is read off the same report as the two counts, so it sits with
    them rather than in a strip of unrelated headline figures.

    The three figures above the chart are the **latest** report and do not move
    with the range control. Narrowing the window changes how much history is
    drawn; it does not change what the most recent report said, and a headline
    figure that shifted when a reader asked for a longer line would be answering
    a question nobody put.
    """
    total_series: tuple = ()
    paid_series: tuple = ()
    paid_share = None
    paid_value = None
    total_value = None
    span = get_internal_observation_span()
    # The default window is anchored to the newest observation rather than to
    # today: a report that arrives late would otherwise shorten the window by
    # however late it was and quietly drop its oldest point.
    window = resolve_window(
        trend_from,
        trend_to,
        earliest=span.earliest,
        latest=span.latest,
        legacy_key=trend_range_key,
        default_months=CARD_DEFAULT_MONTHS,
    )
    if internal_latest is not None and window is not None:
        trend = get_internal_membership_trend(date_from=window.start, date_to=window.end)
        total_series = trend.series("total_members")
        paid_series = trend.series("paid_members")
        paid_share = internal_latest.paid_member_share_pct
        paid_value = internal_latest.value("paid_members")
        total_value = internal_latest.value("total_members")

    internal_total = SourcedFigure(
        label="Liikmeid kokku",
        connection=internal_connection,
        value=total_value,
        unit="liiget",
        as_of=internal_latest.observation_date if internal_latest else None,
        series=total_series,
        note="Koja enda aruande liikmeskonna määratlus.",
    )

    internal = SourcedFigure(
        label="Tasunud liikmeid",
        connection=internal_connection,
        value=paid_value,
        unit="liiget",
        as_of=internal_latest.observation_date if internal_latest else None,
        series=paid_series,
        note="Koja enda aruande liikmeskonna määratlus.",
    )

    return MembershipCard(
        internal=internal,
        internal_total=internal_total,
        fee=_build_fee_collection(internal_latest, internal_connection),
        change=change,
        paid_share_pct=_whole_percent(paid_share),
        chart=build_trend_chart(
            (
                _trend_source(internal_total, style="solid"),
                _trend_source(internal, style="dashed"),
            )
        ),
        trend_window=window,
        trend_earliest=span.earliest,
        trend_latest=span.latest,
    )


def _trend_source(figure: SourcedFigure, *, style: str) -> TrendSource:
    """A figure offered to the chart, carrying the name of where it came from.

    The source travels with the line rather than being written beside the
    drawing, so a legend cannot end up naming one series and describing the
    other.
    """
    return TrendSource(
        label=figure.label,
        style=style,
        source=(
            f"{figure.connection.label} · {figure.connection.cadence}"
            if figure.connection.cadence
            else figure.connection.label
        ),
        series=figure.series,
    )
