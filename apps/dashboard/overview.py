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
- every figure carries the name of its source **and how often that source
  updates**. The public directory is recounted daily and the internal board
  report arrives monthly; two numbers of such different currency must never sit
  side by side unlabelled;
- a comparison states its own baseline. The activity strip is one fixed window
  so all four of its counts mean the same thing, and the member delta — whose
  baseline is the previous reading, not a fixed period — is shown with its own
  date beside the member figure instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.events.selectors import count_upcoming_within, get_upcoming_events
from apps.legal_work.selectors import (
    ACTIVITY_WINDOW_DAYS,
    Deadline,
    count_received_since,
    count_sent_since,
    get_latest_sent_items,
    get_newest_received_items,
    get_upcoming_deadlines,
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
from .sparkline import Sparkline, build_sparkline, meter_width

# How many rows each card previews before sending the reader to its own page.
PREVIEW_LIMIT = 4

# How much history the two membership trends draw.
TREND_DAYS = 365

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

    @property
    def source(self) -> str:
        return self.connection.label

    @property
    def cadence(self) -> str:
        return self.connection.cadence

    @property
    def freshness(self) -> str:
        return self.connection.state_variant

    @property
    def freshness_label(self) -> str:
        return self.connection.state_label


@dataclass(frozen=True)
class AttentionItem:
    """One thing the board is being asked to notice."""

    kind: str
    marker: str
    message: str
    variant: str


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
    """The two membership sources, side by side and never merged."""

    public: SourcedFigure
    internal: SourcedFigure
    change: MembershipChange
    paid_share_pct: Decimal | None = None

    @property
    def has_any_data(self) -> bool:
        return self.public.value is not None or self.internal.value is not None


@dataclass(frozen=True)
class OverviewPage:
    """Everything the overview template renders."""

    kpis: tuple[Kpi, ...]
    attention: tuple[AttentionItem, ...]
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

    @property
    def has_attention(self) -> bool:
        return bool(self.attention)


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
        attention=_build_attention(
            snapshot=snapshot,
            summaries=(
                (legal_work, SOURCE_LEGAL_WORKBOOK),
                (membership, SOURCE_PUBLIC_DIRECTORY),
                (events, SOURCE_EVENTS),
                (news, SOURCE_NEWS),
            ),
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
        secondary = f"{received} € / {budget} €"

    return Kpi(
        label=label,
        connection=connection,
        value=percentage,
        unit="%" if percentage is not None else "",
        comparison_period=(
            "raporteeritud" if reported is not None else "arvutatud" if computed else ""
        ),
        secondary=secondary,
        meter_pct=meter_width(percentage),
        as_of=internal_latest.observation_date,
    )


def _build_attention(*, snapshot, summaries) -> tuple[AttentionItem, ...]:
    """Approaching opinion deadlines, then any source showing older data.

    Deadlines lead because they are the only items here that expire. A stale
    source is important but it is a statement about the dashboard, not about
    something the board can miss.
    """
    items = [_deadline_item(deadline) for deadline in get_upcoming_deadlines(snapshot)]
    items.extend(
        AttentionItem(
            kind="Andmed",
            marker="allikas",
            message=(f"{label}: viimane kontroll ebaõnnestus, kuvatakse varasemat seisu."),
            variant="warning",
        )
        for summary, label in summaries
        if summary.is_stale_after_failure
    )
    return tuple(items)


def _deadline_item(deadline: Deadline) -> AttentionItem:
    return AttentionItem(
        kind="Tähtaeg",
        marker=deadline.remaining_label,
        message=(
            f"{deadline.item.topic} — arvamuse tähtaeg {deadline.item.deadline_date:%d.%m.%Y}"
        ),
        variant=deadline.variant,
    )


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
    """Two sources drawn together, each keeping its own identity.

    They are not two views of one number. The directory counts published member
    profiles and is recounted every day; the board report counts the Chamber's
    own membership and arrives once a month. Both trends are drawn, both are
    labelled with their cadence, and neither is ever continued into the other.
    """
    public_series = get_public_membership_history(days=TREND_DAYS)
    public = SourcedFigure(
        label="Liikmeid kokku",
        connection=public_connection,
        value=change.current.total_members if change.current else None,
        unit="liiget",
        as_of=change.current.observed_at if change.current else None,
        sparkline=build_sparkline(public_series),
        series=public_series,
        note="Avalikus kataloogis avaldatud liikmeprofiilid.",
    )

    internal_series: tuple = ()
    paid_share = None
    paid_value = None
    if internal_latest is not None:
        trend = get_internal_membership_trend(
            date_from=today - timedelta(days=TREND_DAYS),
        )
        internal_series = trend.series("paid_members")
        paid_share = internal_latest.paid_member_share_pct
        paid_value = internal_latest.value("paid_members")

    internal = SourcedFigure(
        label="Tasunud liikmeid",
        connection=internal_connection,
        value=paid_value,
        unit="liiget",
        as_of=internal_latest.observation_date if internal_latest else None,
        sparkline=build_sparkline(internal_series),
        series=internal_series,
        note="Koja enda aruande liikmeskonna määratlus.",
    )

    return MembershipCard(
        public=public,
        internal=internal,
        change=change,
        paid_share_pct=paid_share,
    )
