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
- a comparison states its own baseline. The activity strip is one fixed window
  so all four of its counts mean the same thing, and the member delta — whose
  baseline is the previous reading, not a fixed period — is shown with its own
  date beside the member figure instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.urls import reverse
from django.utils import timezone

from apps.events.selectors import count_upcoming_within, get_upcoming_events
from apps.legal_work.selectors import (
    ACTIVITY_WINDOW_DAYS,
    count_received_since,
    count_sent_since,
    get_latest_sent_items,
    get_newest_received_items,
)
from apps.membership.internal_selectors import (
    get_internal_membership_latest,
    get_internal_membership_trend,
)
from apps.membership.selectors import (
    MembershipChange,
    get_membership_change,
    get_public_membership_history,
)
from apps.news.selectors import count_published_since, get_latest_news
from apps.visibility.page import ChannelSlot, build_channel_band

from .connections import (
    CADENCE_DAILY,
    CADENCE_MONTHLY,
    Connection,
    ConnectionState,
    from_summary,
)
from .sparkline import (
    Sparkline,
    TrendChart,
    TrendSource,
    build_trend_chart,
    meter_width,
)

# How many rows each card previews before sending the reader to its own page.
PREVIEW_LIMIT = 4

# How much history the two membership trends draw.
TREND_DAYS = 365

# Estonian groups thousands with a non-breaking space, as Django's own `et`
# locale does. Written as an escape because the character is invisible in
# source and an ordinary space would let a figure wrap in the middle.
GROUP_SEPARATOR = "\N{NO-BREAK SPACE}"

SOURCE_PUBLIC_DIRECTORY = "Koda.ee liikmekataloog"
SOURCE_INTERNAL_REPORT = "Sisemine liikmeskonna aruanne"
SOURCE_LEGAL_WORKBOOK = "Õigusloome töövihik"
SOURCE_EVENTS = "Koda.ee kalender"
SOURCE_NEWS = "Koda.ee uudisvoog"


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


@dataclass(frozen=True)
class ChangeChip:
    """One count from the activity window."""

    value: str
    label: str


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
    sparkline: Sparkline | None = None
    series: tuple = ()
    note: str = ""


@dataclass(frozen=True)
class MembershipCard:
    """The two membership sources, drawn together and never merged.

    `internal_total` and `internal` are the board report's own total and paid
    counts. They share a definition, a report and a reading date, so `chart`
    puts them on one pair of axes and the gap between the lines *is* the paid
    share stated beside the figure.

    `public` is the koda.ee directory count. It is a different definition on a
    different cadence, so it appears as a figure under its own name and is never
    drawn against the other two, never summed with them and never continued into
    them.
    """

    public: SourcedFigure
    internal: SourcedFigure
    internal_total: SourcedFigure
    change: MembershipChange
    paid_share_pct: Decimal | None = None
    chart: TrendChart | None = None

    @property
    def has_any_data(self) -> bool:
        return self.public.value is not None or self.internal.value is not None


@dataclass(frozen=True)
class OverviewPage:
    """Everything the overview template renders, plus each module's connection.

    The three `Connection` fields are not rendered: the card footers show the
    as-of date alone. They are what the page knows about where its figures came
    from, and they are kept for the same reason as `Kpi.connection`.
    """

    kpis: tuple[Kpi, ...]
    activity: tuple[ChangeChip, ...]
    activity_window_days: int
    membership: MembershipCard
    legal_work: Connection
    legal_work_received: tuple
    legal_work_sent: tuple
    legal_work_open_count: int | None
    legal_work_received_recent: int | None
    legal_work_sent_recent: int | None
    events: Connection
    upcoming_events: tuple
    news: Connection
    latest_news: tuple
    channels: tuple[ChannelSlot, ...]


def build_overview(*, legal_work, membership, news, events) -> OverviewPage:
    """Read every connected module once and shape it for the page.

    The channel band is the one part assembled elsewhere: `apps.visibility` owns
    what those figures mean, how stale they are and how they must be worded, and
    restating any of that here would let the overview and the Nähtavus page drift
    apart about the same number.
    """
    today = timezone.localdate()
    window_start = today - timedelta(days=ACTIVITY_WINDOW_DAYS)
    snapshot = legal_work.snapshot

    legal_connection = from_summary(legal_work, label=SOURCE_LEGAL_WORKBOOK)
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

    change = get_membership_change()
    received_recent = count_received_since(snapshot, window_start) if snapshot else None
    sent_recent = count_sent_since(snapshot, window_start) if snapshot else None
    events_near_term = count_upcoming_within(events.snapshot) if events.has_data else None
    news_recent = (
        count_published_since(news.snapshot, _start_of_day(window_start)) if news.has_data else None
    )

    return OverviewPage(
        kpis=_build_kpis(
            legal_work=legal_work,
            legal_connection=legal_connection,
            public_connection=public_connection,
            events=events,
            events_connection=events_connection,
            events_near_term=events_near_term,
            internal_latest=internal_latest,
            internal_connection=internal_connection,
            change=change,
            received_recent=received_recent,
        ),
        activity=_build_activity(
            received_recent=received_recent,
            sent_recent=sent_recent,
            news_recent=news_recent,
            events_near_term=events_near_term,
        ),
        activity_window_days=ACTIVITY_WINDOW_DAYS,
        membership=_build_membership_card(
            change=change,
            public_connection=public_connection,
            internal_latest=internal_latest,
            internal_connection=internal_connection,
            today=today,
        ),
        legal_work=legal_connection,
        legal_work_received=(
            tuple(get_newest_received_items(snapshot, limit=PREVIEW_LIMIT)) if snapshot else ()
        ),
        legal_work_sent=(
            tuple(get_latest_sent_items(snapshot, limit=PREVIEW_LIMIT)) if snapshot else ()
        ),
        legal_work_open_count=legal_work.open_count if legal_work.has_data else None,
        legal_work_received_recent=received_recent,
        legal_work_sent_recent=sent_recent,
        events=events_connection,
        upcoming_events=tuple(get_upcoming_events(events.snapshot, limit=PREVIEW_LIMIT)),
        news=news_connection,
        latest_news=tuple(get_latest_news(news.snapshot, limit=PREVIEW_LIMIT)),
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
    internal_latest,
    internal_connection,
    change,
    received_recent,
) -> tuple[Kpi, ...]:
    """The four headline figures, in the order the board reads them."""
    return (
        Kpi(
            label="Liikmeid kokku",
            connection=public_connection,
            value=change.current.total_members if change.current else None,
            unit="liiget",
            # The baseline is the previous reading, so it is named here rather
            # than in the fixed-window activity strip below.
            change=change.label or "",
            change_direction=change.direction,
            comparison_period=(f"pärast {change.since:%d.%m.%Y}" if change.since else ""),
            as_of=change.current.observed_at if change.current else None,
        ),
        Kpi(
            label="Õigusloome teemasid töös",
            connection=legal_connection,
            value=legal_work.open_count if legal_work.has_data else None,
            change=f"+{received_recent}" if received_recent else "",
            change_direction="up" if received_recent else "",
            comparison_period=(
                f"uut viimase {ACTIVITY_WINDOW_DAYS} päeva jooksul" if received_recent else ""
            ),
            as_of=legal_work.reporting_date,
        ),
        Kpi(
            # Worded without the number so the label states no measurement of
            # its own; the exact window is stated once, in the activity strip.
            label="Sündmusi lähikuul",
            connection=events_connection,
            value=events_near_term,
            secondary=(f"Kalendris kokku {events.item_count}" if events.has_data else ""),
            as_of=events.observed_at,
        ),
        _fee_collection_kpi(internal_latest, internal_connection),
    )


def _fee_collection_kpi(internal_latest, connection) -> Kpi:
    """Membership-fee collection from the latest board report.

    The report may carry a stated percentage and the ingredients to compute one,
    and those two do not always agree. Neither is silently preferred: the cell
    names which one it is showing, and the Liikmeskond page shows both side by
    side without reconciling them.
    """
    label = "Liikmemaksude laekumine"
    if internal_latest is None:
        return Kpi(label=label, connection=connection)

    reported = internal_latest.value("membership_fee_collection_pct_reported")
    computed = internal_latest.computed_collection_pct
    percentage = reported if reported is not None else computed
    received = internal_latest.value("membership_fees_received_eur")
    budget = internal_latest.value("membership_fee_budget_eur")

    secondary = ""
    if received is not None and budget is not None:
        secondary = f"{_euros(received)} / {_euros(budget)}"

    return Kpi(
        label=label,
        connection=connection,
        value=_percentage(percentage),
        unit="%" if percentage is not None else "",
        comparison_period=(
            "raporteeritud" if reported is not None else "arvutatud" if computed else ""
        ),
        secondary=secondary,
        meter_pct=meter_width(percentage),
        as_of=internal_latest.observation_date,
    )


def _percentage(value: Decimal | None) -> Decimal | None:
    """A percentage at two decimals.

    The board report stores four, and `94,0400 %` reads as a precision the
    figure does not have. Rounding is presentational only: the stored value,
    which the Liikmeskond page shows beside the computed one, is untouched.
    """
    if value is None:
        return None
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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
    """A whole-euro amount, grouped so it can be read at a glance.

    Cents are noise beside a budget in the millions, and an ungrouped
    `1276101` has to be counted digit by digit. The separator is the
    non-breaking space Estonian uses, so a grouped figure never wraps mid-number.
    """
    whole = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{whole:,}".replace(",", GROUP_SEPARATOR) + f"{GROUP_SEPARATOR}€"


def _start_of_day(day: date) -> datetime:
    """Midnight local time, as the aware datetime a timestamp query needs."""
    return timezone.make_aware(datetime.combine(day, datetime.min.time()))


def _build_activity(
    *, received_recent, sent_recent, news_recent, events_near_term
) -> tuple[ChangeChip, ...]:
    """Counts over one fixed window, so every chip means the same period.

    A chip appears only for a connected source. An unconnected one contributes
    nothing rather than a zero, which would read as "nothing happened".
    """
    counts = (
        (received_recent, "uut õigusloome teemat"),
        (sent_recent, "esitatud arvamust"),
        (news_recent, "avaldatud uudist"),
        (events_near_term, "eelseisvat sündmust"),
    )
    return tuple(
        ChangeChip(value=str(count), label=label) for count, label in counts if count is not None
    )


def _build_membership_card(
    *, change, public_connection, internal_latest, internal_connection, today
) -> MembershipCard:
    """Three figures from two sources, and only one of them is charted.

    The two sources are not two views of one number. The directory counts
    published member profiles on koda.ee and is recounted every day; the board
    report counts the Chamber's own membership and arrives once a month.

    The **chart is the board report alone** — its total against its paid count.
    That pairing is a real comparison: both lines are the same definition of a
    member, read off the same report on the same day, and the share stated
    beside the paid figure is literally the gap between them.

    Drawing the directory's total against the report's paid count instead would
    put two definitions on one axis and invite exactly the subtraction that is
    forbidden. It also could not be drawn: an unchanged daily check writes no
    observation, so the directory series is a single point for weeks at a time
    and a single point is not a trend.

    The directory total keeps its place as a figure, under its own name and its
    own source, where it states what it is without being compared to anything.
    """
    public = SourcedFigure(
        # Not "Liikmeid kokku": the chart below carries a differently-sourced
        # total under that name, and two adjacent totals sharing one label is
        # the confusion this whole card is arranged to prevent.
        label="Liikmeid kataloogis",
        connection=public_connection,
        value=change.current.total_members if change.current else None,
        unit="liiget",
        as_of=change.current.observed_at if change.current else None,
        series=get_public_membership_history(days=TREND_DAYS),
        note="Avalikus kataloogis avaldatud liikmeprofiilid.",
    )

    total_series: tuple = ()
    paid_series: tuple = ()
    paid_share = None
    paid_value = None
    total_value = None
    if internal_latest is not None:
        trend = get_internal_membership_trend(
            date_from=today - timedelta(days=TREND_DAYS),
        )
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
        public=public,
        internal=internal,
        internal_total=internal_total,
        change=change,
        paid_share_pct=_whole_percent(paid_share),
        chart=build_trend_chart(
            (
                _trend_source(internal_total, style="solid"),
                _trend_source(internal, style="dashed"),
            )
        ),
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
