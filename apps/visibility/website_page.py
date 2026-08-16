"""The Koduleht page: what a reader sees, and which questions each view answers.

The selectors say what is stored, `website_analytics` derives what it means, and
this module decides what is shown, in what order, and — the part that matters
most — **what is not shown because it could not be trusted**.

## Five views, one measurement window

`fookus` chooses the view and `periood` chooses the window, and the two are
independent: changing what you are looking at never silently changes the period
you are looking at it over. Every control is an ordinary GET link carrying the
whole state, so a view is bookmarkable, shareable and reload-safe, and nothing
here needs JavaScript to work.

Each view builds only its own analysis. The overview does not run the movement
query and the page explorer does not build the channel mix, because a dashboard
that computes everything on every visit is a dashboard that gets slower every
time somebody adds a question to it.

## The rule that shapes every readout

A figure is shown when it was measured, and a **comparison** is shown when both
windows were measured well enough to be compared at the grain the comparison is
about. Those are two separate permissions, and `WebsiteComparison` holds the
second one. A thirty-day period against a previous one with twenty-two collected
days produces no delta here — not a delta with a footnote, no delta — because a
number on a dashboard is read long before its footnote is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from apps.core.formatting import (
    integer,
    long_date,
    percent,
    percentage_points,
    signed_integer,
    signed_percent,
)
from apps.core.query_state import parse_page as core_parse_page
from apps.core.query_state import parse_search as core_parse_search

from .content_performance import ContentPerformanceRow, describe_pages, paths_for_title
from .content_sections import PARAM_CONTENT, ContentSection, all_index_paths, parse_section
from .ga4_selectors import (
    Coverage,
    PageTotal,
    get_coverage,
    get_page_series,
    get_top_pages,
    get_traffic_series,
    search_pages,
)
from .period_users import get_period_users
from .website_analytics import (
    MATRIX_DRAWN_LIMIT,
    QUADRANT_LABELS,
    WEEKDAY_NAMES,
    EngagementMatrix,
    PageMovementResult,
    PeakDay,
    TrafficConcentration,
    WebsiteChannelPerformance,
    WebsiteContentMix,
    WebsiteLanguageMix,
    WebsitePageDetail,
    WebsitePageMovement,
    WebsiteQualitySignal,
    WebsiteTrafficSummary,
    get_channel_performance,
    get_concentration,
    get_content_mix,
    get_engagement_matrix,
    get_language_mix,
    get_page_detail,
    get_page_movement,
    get_peak_day,
    get_quality_signals,
    get_traffic_summary,
    get_weekday_pattern,
    rank_channel_movement,
)

# Aliased on the way in. Several of these build a chart that is stored on a
# presenter field of the same name, and two identifiers spelled alike — one a
# function, one an attribute — is a line that reads correctly and is understood
# wrongly by the next person to change it.
from .website_charts import (
    DEFAULT_TRAFFIC_METRIC,
    TRAFFIC_METRICS,
    WebsiteChart,
    parse_traffic_metric,
)
from .website_charts import channel_engagement_chart as build_channel_engagement_chart
from .website_charts import channel_sessions_chart as build_channel_sessions_chart
from .website_charts import content_mix_chart as build_content_mix_chart
from .website_charts import engagement_matrix_chart as build_engagement_matrix_chart
from .website_charts import language_chart as build_language_chart
from .website_charts import top_pages_chart as build_top_pages_chart
from .website_charts import traffic_trend_chart as build_traffic_trend_chart
from .website_charts import weekday_chart as build_weekday_chart
from .website_period import (
    CUSTOM_KEY,
    PARAM_FROM,
    PARAM_PERIOD,
    PARAM_TO,
    PERIOD_PRESETS,
    PeriodOption,
    WebsiteComparison,
    WebsitePeriod,
    WebsitePeriodCoverage,
    build_comparison,
    get_period_coverage,
    parse_period,
)

#: The product name. The Django app stays `apps.visibility` and the route name
#: stays `visibility`: renaming an established app, its migration namespace and
#: its model labels is not justified by a change of what the page is called.
PRODUCT_NAME = "Koduleht"

#: The query parameters this page understands.
PARAM_FOCUS = "fookus"
PARAM_METRIC = "naitaja"
PARAM_SEARCH = "otsing"
PARAM_PAGE = "lk"
PARAM_DETAIL = "leht"

MAX_SEARCH_LENGTH = 120
SEARCH_PER_PAGE = 25

#: How many rows each surface shows. The overview previews; the focus views rank.
OVERVIEW_TOP_PAGES = 5
OVERVIEW_CHANNELS = 6
CONTENT_TOP_PAGES = 15
MOVEMENT_ROWS = 10


# ---------------------------------------------------------------------------
# Focus navigation
# ---------------------------------------------------------------------------

FOCUS_OVERVIEW = "ulevaade"
FOCUS_TRAFFIC = "liiklus"
FOCUS_CONTENT = "sisu"
FOCUS_CHANNELS = "kanalid"
FOCUS_PAGES = "lehed"


@dataclass(frozen=True)
class Focus:
    key: str
    label: str
    #: What this view answers, for the section landmark and the page description.
    question: str


FOCUSES: tuple[Focus, ...] = (
    Focus(key=FOCUS_OVERVIEW, label="Ülevaade", question="Kuidas Koda.ee-l läheb?"),
    Focus(key=FOCUS_TRAFFIC, label="Liiklus", question="Kuidas kodulehe kasutus muutub?"),
    Focus(
        key=FOCUS_CONTENT,
        label="Sisu",
        question="Millised osad ja lehed tähelepanu saavad?",
    ),
    Focus(
        key=FOCUS_CHANNELS,
        label="Kanalid",
        question="Kust liiklus tuleb ja kui kaasatud see on?",
    ),
    Focus(key=FOCUS_PAGES, label="Lehed", question="Kuidas läks ühel kindlal lehel?"),
)

DEFAULT_FOCUS = FOCUSES[0]

_FOCUS_BY_KEY = {focus.key: focus for focus in FOCUSES}


def parse_focus(raw: str | None) -> Focus:
    """The view asked for, or the overview. Never raises.

    An unknown value is a rotted bookmark rather than an error, and the overview
    is the view that answers the most without being asked.
    """
    return _FOCUS_BY_KEY.get((raw or "").strip(), DEFAULT_FOCUS)


@dataclass(frozen=True)
class FocusOption:
    """One tab of the focus navigation, with its link already built.

    Built here rather than in the template because a link carries the whole
    query state, and a template that assembled one would be the second place the
    page's URL grammar lived.
    """

    focus: Focus
    is_active: bool
    query: str

    @property
    def key(self) -> str:
        return self.focus.key

    @property
    def label(self) -> str:
        return self.focus.label


# ---------------------------------------------------------------------------
# Query state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebsiteQuery:
    """Everything the address bar carries, validated.

    One place builds every link on the page. A reader who chose ninety days, the
    content view and a search term keeps all three when they touch any one of
    them; dropping the others silently is how an analytical tool makes somebody
    start over.

    Nothing here is echoed from `request.GET`. Each field was resolved against a
    closed registry or bounded, and the links are rebuilt from the resolved
    values, so a hand-typed parameter cannot be reflected into an href.
    """

    focus: str = FOCUS_OVERVIEW
    period: str = PERIOD_PRESETS[0].key
    date_from: str = ""
    date_to: str = ""
    metric: str = DEFAULT_TRAFFIC_METRIC
    section: str = ""
    search: str = ""
    page: int = 1
    detail: str = ""

    def build(self, **overrides) -> str:
        """This state with `overrides` applied, as a query string.

        A parameter at its default is omitted, so an ordinary link stays short
        and a shared URL says only what the reader actually chose.
        """
        state = {
            PARAM_FOCUS: overrides.get("focus", self.focus),
            PARAM_PERIOD: overrides.get("period", self.period),
            PARAM_FROM: overrides.get("date_from", self.date_from),
            PARAM_TO: overrides.get("date_to", self.date_to),
            PARAM_METRIC: overrides.get("metric", self.metric),
            PARAM_CONTENT: overrides.get("section", self.section),
            PARAM_SEARCH: overrides.get("search", self.search),
            PARAM_DETAIL: overrides.get("detail", self.detail),
        }
        page = overrides.get("page", self.page)

        parts = [f"{PARAM_FOCUS}={quote(state[PARAM_FOCUS])}"]
        if state[PARAM_PERIOD]:
            parts.append(f"{PARAM_PERIOD}={quote(state[PARAM_PERIOD])}")
        if state[PARAM_PERIOD] == CUSTOM_KEY:
            if state[PARAM_FROM]:
                parts.append(f"{PARAM_FROM}={quote(state[PARAM_FROM])}")
            if state[PARAM_TO]:
                parts.append(f"{PARAM_TO}={quote(state[PARAM_TO])}")
        if state[PARAM_METRIC] and state[PARAM_METRIC] != DEFAULT_TRAFFIC_METRIC:
            parts.append(f"{PARAM_METRIC}={quote(state[PARAM_METRIC])}")
        if state[PARAM_CONTENT]:
            parts.append(f"{PARAM_CONTENT}={quote(state[PARAM_CONTENT])}")
        if state[PARAM_SEARCH]:
            parts.append(f"{PARAM_SEARCH}={quote(state[PARAM_SEARCH])}")
        if state[PARAM_DETAIL]:
            parts.append(f"{PARAM_DETAIL}={quote(state[PARAM_DETAIL])}")
        if page and int(page) > 1:
            parts.append(f"{PARAM_PAGE}={int(page)}")
        return "&".join(parts)


# ---------------------------------------------------------------------------
# Headline figures
# ---------------------------------------------------------------------------


def duration_label(seconds: float | None) -> str:
    """`1 min 42 s`, or `48 s`. Empty when nothing was measured.

    Written out rather than left as a decimal count of seconds, because "102" is
    a number a reader has to convert and "1 min 42 s" is one they can read.
    """
    if seconds is None:
        return ""
    total = int(round(seconds))
    minutes, rest = divmod(total, 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


@dataclass(frozen=True)
class WebsiteHeadline:
    """One primary figure, already spelled, with its comparison or without it.

    `change` is empty whenever the comparison could not be trusted. That is the
    whole point of the object: a headline with no delta is a headline whose delta
    was refused, and the reason travels in `note` rather than being left to a
    reader to notice.
    """

    key: str
    label: str
    value: str
    change: str = ""
    change_label: str = ""
    direction: str = ""
    note: str = ""
    comparison_period: str = ""

    @property
    def has_value(self) -> bool:
        return bool(self.value)

    @property
    def has_change(self) -> bool:
        return bool(self.change)


def _direction(value: float | int | None) -> str:
    """The non-colour signal beside a change.

    Direction only. A fall in traffic is not an error and a rise is not a
    success, so nothing here calls one good: the glyph says which way, and the
    reader decides what it means.
    """
    if value is None or value == 0:
        return "flat"
    return "up" if value > 0 else "down"


def _count_headline(
    *,
    key: str,
    label: str,
    current: int | None,
    previous: int | None,
    can_compare: bool,
    comparison_period: str,
    unavailable_note: str,
) -> WebsiteHeadline:
    """A count, with a relative change when one may be drawn."""
    if current is None:
        return WebsiteHeadline(key=key, label=label, value="", note="Ei ole mõõdetud.")

    if not can_compare or previous is None or not previous:
        return WebsiteHeadline(
            key=key,
            label=label,
            value=integer(current),
            note=unavailable_note,
        )

    relative = (current - previous) / previous * 100
    return WebsiteHeadline(
        key=key,
        label=label,
        value=integer(current),
        change=signed_percent(relative),
        change_label=f"{signed_integer(current - previous)} võrreldes eelneva perioodiga",
        direction=_direction(relative),
        comparison_period=comparison_period,
    )


def _users_headline(
    *,
    current: int | None,
    previous: int | None,
    can_compare: bool,
    comparison_period: str,
    unavailable_note: str,
    is_custom: bool,
) -> WebsiteHeadline:
    """Distinct people over the window — the figure GA4's own dashboard leads with.

    Unlike every other headline this one is not computed from the stored daily
    rows, because it cannot be: users are distinct people and days do not add.
    It is a cached answer to a separate GA4 query whose date range *is* this
    period, and a window nobody asked that query for has **no** value here.

    A hand-picked range is the ordinary case of that, and it says so. The
    alternative — quietly showing the nearest preset's number, or summing the
    daily counts — would put a figure under this label that answers a different
    question, which is the one failure this metric is most likely to produce and
    the hardest to notice.
    """
    label = "Kasutajad"
    if current is None:
        return WebsiteHeadline(
            key="kasutajad",
            label=label,
            value="",
            note=(
                "Valitud vahemiku kohta ei ole kasutajate arvu päritud."
                if is_custom
                else "Ei ole veel päritud."
            ),
        )

    if not can_compare or previous is None or not previous:
        return WebsiteHeadline(
            key="kasutajad",
            label=label,
            value=integer(current),
            note=unavailable_note,
        )

    relative = (current - previous) / previous * 100
    return WebsiteHeadline(
        key="kasutajad",
        label=label,
        value=integer(current),
        change=signed_percent(relative),
        change_label=f"{signed_integer(current - previous)} võrreldes eelneva perioodiga",
        direction=_direction(relative),
        comparison_period=comparison_period,
    )


def _rate_headline(
    *,
    key: str,
    label: str,
    current: float | None,
    previous: float | None,
    can_compare: bool,
    comparison_period: str,
    unavailable_note: str,
) -> WebsiteHeadline:
    """A proportion, whose movement is stated in **percentage points**.

    A rate that went from 61,3% to 63,4% did not rise by 2,1 percent, and
    reporting the percentage change of a percentage is how a two-point move gets
    published as a three-and-a-half-percent one.
    """
    if current is None:
        return WebsiteHeadline(key=key, label=label, value="", note="Ei ole mõõdetud.")

    if not can_compare or previous is None:
        return WebsiteHeadline(
            key=key, label=label, value=percent(current * 100), note=unavailable_note
        )

    points = (current - previous) * 100
    return WebsiteHeadline(
        key=key,
        label=label,
        value=percent(current * 100),
        change=percentage_points(points),
        change_label=f"{percentage_points(points)} võrreldes eelneva perioodiga",
        direction=_direction(points),
        comparison_period=comparison_period,
    )


def _duration_headline(
    *,
    current: float | None,
    previous: float | None,
    can_compare: bool,
    comparison_period: str,
    unavailable_note: str,
) -> WebsiteHeadline:
    label = "Keskmine kaasatuse aeg / külastus"
    if current is None:
        return WebsiteHeadline(key="kaasatuse_aeg", label=label, value="")

    if not can_compare or previous is None or not previous:
        return WebsiteHeadline(
            key="kaasatuse_aeg",
            label=label,
            value=duration_label(current),
            note=unavailable_note,
        )

    relative = (current - previous) / previous * 100
    return WebsiteHeadline(
        key="kaasatuse_aeg",
        label=label,
        value=duration_label(current),
        change=signed_percent(relative),
        change_label=(f"{duration_label(previous)} eelmisel perioodil"),
        direction=_direction(relative),
        comparison_period=comparison_period,
    )


def build_headlines(
    summary: WebsiteTrafficSummary,
    previous: WebsiteTrafficSummary | None,
    comparison: WebsiteComparison,
    *,
    is_custom_period: bool = False,
) -> tuple[WebsiteHeadline, ...]:
    """The primary figures, in the order a manager reads them.

    `Kasutajad` leads, which is the order Google Analytics' own dashboard uses
    and the order the Chamber reads them in. It is the one card here whose value
    is fetched rather than derived — see `period_users` — so it is also the one
    that can be blank while the rest of the strip is full.

    Four when engagement time was measured, three when it was not — the layout
    tolerates both, and a card for a metric this property does not carry would be
    an empty box claiming a measurement exists.

    `Kaasatud külastuste osakaal` left this strip on 2026-08-16. It was not
    dropped from the page: it is still one of the comparisons in `Perioodi
    muutus`, and its definition is still in `Andmete kohta` on `/haldus/`.
    """
    can_compare = comparison.can_compare_site
    window = comparison.range_label
    note = _comparison_note(comparison)
    previous = previous or WebsiteTrafficSummary(start=None, end=None, days=0)

    headlines = [
        _users_headline(
            current=get_period_users(summary.start, summary.end),
            previous=get_period_users(comparison.start, comparison.end),
            can_compare=can_compare,
            comparison_period=window,
            unavailable_note=note,
            is_custom=is_custom_period,
        ),
        _count_headline(
            key="seansid",
            label="Külastused",
            current=summary.sessions,
            previous=previous.sessions,
            can_compare=can_compare,
            comparison_period=window,
            unavailable_note=note,
        ),
        _count_headline(
            key="lehevaatamised",
            label="Lehevaatamised",
            current=summary.page_views,
            previous=previous.page_views,
            can_compare=can_compare,
            comparison_period=window,
            unavailable_note=note,
        ),
    ]

    if summary.seconds_per_session is not None:
        headlines.append(
            _duration_headline(
                current=summary.seconds_per_session,
                previous=previous.seconds_per_session,
                can_compare=can_compare,
                comparison_period=window,
                unavailable_note=note,
            )
        )
    return tuple(headlines)


def build_unstripped_measures(
    summary: WebsiteTrafficSummary,
    previous: WebsiteTrafficSummary | None,
    comparison: WebsiteComparison,
) -> tuple[WebsiteHeadline, ...]:
    """Measures that are still computed but no longer carry a card.

    `Kaasatud külastuste osakaal` left the KPI strip on 2026-08-16 and did not
    leave the page: it is still one of the movements in `Perioodi muutus`, and
    its definition is still in `Andmete kohta`. It is built here rather than in
    `build_headlines` so that nothing renders it as a card by accident.
    """
    previous = previous or WebsiteTrafficSummary(start=None, end=None, days=0)
    rate = _rate_headline(
        key="kaasatuse_maar",
        label="Kaasatud külastuste osakaal",
        current=summary.engagement_rate,
        previous=previous.engagement_rate,
        can_compare=comparison.can_compare_site,
        comparison_period=comparison.range_label,
        unavailable_note=_comparison_note(comparison),
    )
    return (rate,) if rate is not None else ()


def _comparison_note(comparison: WebsiteComparison) -> str:
    """Why a delta is missing, in words, so the gap is a statement."""
    if comparison.unavailable_reason:
        return comparison.unavailable_reason
    if not comparison.can_compare_site:
        return "Võrdlus on jäetud näitamata: perioodide mõõdetud päevade hulk erineb."
    return ""


@dataclass(frozen=True)
class SecondaryReadout:
    """A supporting figure, deliberately not given a card of its own."""

    label: str
    value: str
    note: str = ""


@dataclass(frozen=True)
class TableRow:
    """One row of a small analytical table, already spelled.

    Every cell is a finished string. A template that decided how to write a
    share, a percentage-point movement or a signed count would be the second
    place those decisions lived, and the two would drift the first time one of
    them changed — which is the same rule the chart tooltips follow.

    `direction` is the non-colour signal for whichever cell carries a change,
    and `values` is positional so one template renders every table on the page.
    """

    label: str
    values: tuple[str, ...]
    href: str = ""
    badge: str = ""
    note: str = ""
    direction: str = ""


@dataclass(frozen=True)
class TableView:
    """One small table, carrying its own caption, headings and rows.

    The same reasoning as a chart payload: a table that described itself in the
    template would put its column headings in one file and the values under them
    in another, and the two would drift the first time a column moved.
    """

    caption: str
    headers: tuple[str, ...]
    rows: tuple[TableRow, ...] = ()
    empty_message: str = ""

    @property
    def has_rows(self) -> bool:
        return bool(self.rows)


def _share(value: float | None) -> str:
    """A stored fraction as a percentage a reader can read, or a dash."""
    return percent(value * 100) if value is not None else "–"


def _points(value: float | None) -> str:
    return percentage_points(value) if value is not None else "–"


def _relative(value: float | None) -> str:
    return signed_percent(value * 100) if value is not None else "–"


def build_mix_table(mix: WebsiteContentMix) -> TableView:
    """Section mix, spelled. Share movement is in percentage points, not percent."""
    rows = tuple(
        TableRow(
            label=row.label,
            values=(integer(row.page_views), _share(row.share), _points(row.share_change_points)),
            direction=_direction(row.share_change_points),
        )
        for row in mix.rows
    )
    return TableView(
        caption="Vaadatud sisu jaotus osade kaupa",
        headers=("Osa", "Lehevaatamised", "Osakaal", "Muutus"),
        rows=rows,
    )


def build_language_table(mix: WebsiteLanguageMix) -> TableView:
    rows = tuple(
        TableRow(
            label=row.label,
            values=(integer(row.page_views), _share(row.share), _points(row.share_change_points)),
            direction=_direction(row.share_change_points),
        )
        for row in mix.rows
    )
    return TableView(
        caption="Lehevaatamised sisukeele järgi",
        headers=("Keel", "Lehevaatamised", "Osakaal", "Muutus"),
        rows=rows,
    )


def build_channel_table(channels: tuple[WebsiteChannelPerformance, ...]) -> TableView:
    """One row per channel: volume, share, quality, and both kinds of movement."""
    rows = tuple(
        TableRow(
            label=channel.channel,
            values=(
                integer(channel.sessions),
                _share(channel.share),
                integer(channel.engaged_sessions) if channel.engaged_sessions is not None else "–",
                _share(channel.engagement_rate),
                signed_integer(channel.session_change)
                if channel.session_change is not None
                else "–",
                _relative(channel.relative_change),
                _points(channel.share_change_points),
            ),
            direction=_direction(channel.session_change),
        )
        for channel in channels
    )
    return TableView(
        caption="Kanalid",
        headers=(
            "Kanal",
            "Külastused",
            "Osakaal",
            "Kaasatud külastused",
            "Kaasatuse määr",
            "Muutus",
            "Suhteline",
            "Osakaalu muutus",
        ),
        rows=rows,
    )


def build_movement_table(
    movement: Sequence[WebsitePageMovement], titles: dict[str, str], *, caption: str
) -> TableView:
    """Page movement, with both the absolute and the relative change spelled.

    A page with no measured traffic in the previous window says so in words. It
    did not grow by a hundred percent and it did not grow infinitely: there was
    no base, and printing one would invent it.
    """
    rows = tuple(
        TableRow(
            label=titles.get(row.path, row.path),
            values=(
                integer(row.previous_page_views),
                integer(row.page_views),
                signed_integer(row.change),
                "uus mõõdetud liiklus" if row.is_new else _relative(row.relative_change),
            ),
            note=row.path,
            direction=_direction(row.change),
        )
        for row in movement
    )
    return TableView(
        caption=caption,
        headers=("Leht", "Eelmine periood", "Valitud periood", "Muutus", "Suhteline"),
        rows=rows,
    )


def build_concentration_readouts(
    concentration: TrafficConcentration,
) -> tuple[SecondaryReadout, ...]:
    return (
        SecondaryReadout(label="Top 5 osakaal", value=_share(concentration.top_5_share)),
        SecondaryReadout(label="Top 10 osakaal", value=_share(concentration.top_10_share)),
        SecondaryReadout(label="Järjestatud lehti", value=integer(concentration.ranked_pages)),
    )


def build_search_table(results: PageSearchResults, query: WebsiteQuery) -> TableView:
    """Search results, each row linking to that page's own analysis.

    Two figures per row and they answer different questions: what the page did
    inside the chosen window, and everything measured for it across the whole of
    coverage. Collapsing them into one column would hide whichever the reader was
    actually asking about.
    """
    rows = tuple(
        TableRow(
            label=row.label,
            values=(
                integer(row.page_views),
                integer(row.total_views) if row.total_views is not None else "–",
            ),
            href=query.build(detail=row.path, page=1),
            badge=row.type_label,
            note=row.path,
        )
        for row in results.rows
    )
    return TableView(
        caption=f"Otsingutulemused: {results.term}",
        headers=("Leht", "Valitud perioodil", "Kokku mõõdetud"),
        rows=rows,
        empty_message="Ühtegi lehte ei leitud.",
    )


def build_detail_readouts(detail: WebsitePageDetail) -> tuple[SecondaryReadout, ...]:
    """One page's figures, spelled, with the honesty about coverage attached."""
    readouts = [
        SecondaryReadout(
            label="Valitud perioodil",
            value=integer(detail.page_views) if detail.page_views is not None else "–",
        )
    ]
    if detail.previous_page_views is not None:
        readouts.append(
            SecondaryReadout(
                label="Eelmisel perioodil",
                value=integer(detail.previous_page_views),
                note=(
                    f"Muutus {signed_integer(detail.change)} ({_relative(detail.relative_change)})"
                    if detail.change is not None
                    else ""
                ),
            )
        )
    readouts.append(
        SecondaryReadout(
            label="Kokku mõõdetud",
            value=integer(detail.measured_total) if detail.measured_total is not None else "–",
            # Not a lifetime figure for a page older than the collection, which
            # is why the first measured day is printed beside it rather than
            # left to be assumed.
            note=(
                f"Mõõdetud alates {long_date(detail.first_measured_on)}."
                if detail.first_measured_on
                else ""
            ),
        )
    )
    if detail.seconds_per_view is not None:
        readouts.append(
            SecondaryReadout(
                label="Kaasatuse aeg / lehevaatamine",
                value=duration_label(detail.seconds_per_view),
                note="GA4 kaasatuse kestus, mitte lehel viibitud aeg.",
            )
        )
    readouts.append(
        SecondaryReadout(label="Mõõdetud päevi perioodil", value=integer(detail.days_seen))
    )
    return tuple(readouts)


def build_quality_table(signals: tuple[WebsiteQualitySignal, ...]) -> TableView:
    rows = tuple(
        TableRow(
            label=signal.label,
            values=(
                integer(signal.page_views) if signal.page_views is not None else "–",
                _share(signal.share_of_page_views),
                _relative(signal.relative_change),
            ),
            direction=_direction(signal.relative_change),
        )
        for signal in signals
    )
    return TableView(
        caption="Tehnilised signaalid",
        headers=("Signaal", "Lehevaatamised", "Osakaal kõigist", "Muutus"),
        rows=rows,
    )


def build_secondary_readouts(summary: WebsiteTrafficSummary) -> tuple[SecondaryReadout, ...]:
    """The facts worth stating without competing with the primary four.

    `Uued kasutajad` is **not** among them. GA4 stores `newUsers` per day and the
    field is collected, but whether adding those days produces the period
    quantity a reader would assume has not been demonstrated against this
    property — and this codebase's rule is that a metric's meaning is verified
    before it is published, not argued from its name. It stays collected and
    unshown; the methodology says so.
    """
    readouts: list[SecondaryReadout] = []
    if summary.views_per_session is not None:
        readouts.append(
            SecondaryReadout(
                label="Lehevaatamisi külastuse kohta",
                value=f"{summary.views_per_session:.1f}".replace(".", ","),
            )
        )
    if summary.peak_active_users is not None:
        readouts.append(
            SecondaryReadout(
                label="Kõige aktiivsem päev",
                # The unit rides with the figure here. The label no longer says
                # `kasutajad`, and this readout now sits under a top-row card
                # that *is* a period user count — an unlabelled 443 beside it
                # would read as a second, smaller answer to the same question.
                value=f"{integer(summary.peak_active_users)} kasutajat",
                note=(
                    f"{long_date(summary.peak_active_users_on)}. "
                    if summary.peak_active_users_on
                    else ""
                )
                + "Perioodi kasutajate arv ei ole päevade summa.",
            )
        )
    return tuple(readouts)


# ---------------------------------------------------------------------------
# Mis muutus?
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebsiteInsight:
    """One deterministic statement about what moved.

    Built from the comparisons already computed, by rules written in Python and
    covered by tests. Nothing here calls a language model, and there is no
    composite index: a reader can trace every line back to two measured numbers.
    """

    label: str
    value: str
    direction: str = ""
    detail: str = ""


def build_insights(
    headlines: tuple[WebsiteHeadline, ...],
    channels: tuple[WebsiteChannelPerformance, ...],
    mix: WebsiteContentMix | None,
    *,
    unstripped: tuple[WebsiteHeadline, ...] = (),
) -> tuple[WebsiteInsight, ...]:
    """Three or four movements worth stating, largest first.

    Only signals whose comparison survived the coverage check reach here, because
    a headline with no delta has nothing to say about change.

    `unstripped` is for measures that are still measured and still worth stating
    as a movement, but no longer have a card in the KPI strip. The engagement
    rate is the first of them: it left the strip on 2026-08-16, and because this
    function derives its labels *from* the strip, dropping the card would
    otherwise have deleted the movement too — silently, since nothing else
    mentions it.
    """
    # Insertion order decided which movements survived the cap below, which is
    # how adding `Kasutajad` to the strip silently truncated the engagement rate
    # out of this section: five measures, four places, and the rate appended
    # last. The order is stated instead. Average engagement time is deliberately
    # last of the five — it moves least and explains least — so the four that
    # get printed are the counts, the users and the rate.
    priority = ("kasutajad", "seansid", "lehevaatamised", "kaasatuse_maar", "kaasatuse_aeg")
    measures = sorted(
        (headline for headline in (*headlines, *unstripped) if headline.has_change),
        key=lambda headline: (
            priority.index(headline.key) if headline.key in priority else len(priority)
        ),
    )
    insights: list[WebsiteInsight] = [
        WebsiteInsight(
            label=headline.label,
            value=headline.change,
            direction=headline.direction,
            detail=headline.change_label,
        )
        for headline in measures
    ]

    leader = next((channel for channel in channels if channel.share is not None), None)
    if leader is not None and leader.share_change_points is not None:
        insights.append(
            WebsiteInsight(
                label=f"Suurima kanali osakaal — {leader.channel}",
                value=percentage_points(leader.share_change_points),
                direction=_direction(leader.share_change_points),
                detail=f"{percent(leader.share * 100)} kõigist külastustest.",
            )
        )

    if mix is not None:
        moved = [row for row in mix.rows if row.share_change_points is not None]
        if moved:
            largest = max(moved, key=lambda row: abs(row.share_change_points))
            insights.append(
                WebsiteInsight(
                    label=f"Suurim sisunihe — {largest.label}",
                    value=percentage_points(largest.share_change_points),
                    direction=_direction(largest.share_change_points),
                    detail=(
                        f"{percent(largest.share * 100)} vaadatud sisust."
                        if largest.share is not None
                        else ""
                    ),
                )
            )

    return tuple(insights[:4])


# ---------------------------------------------------------------------------
# Võimalused
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebsiteOpportunity:
    """One evidence-backed thing worth looking into.

    Every field is a measurement or a formatted measurement. There is no verdict
    and no instruction — the dashboard states what it saw and the reader decides
    whether it merits action, because nothing in this data establishes *why* a
    figure moved.
    """

    kind: str
    label: str
    subject: str
    evidence: str
    path: str = ""
    query: str = ""


def build_opportunities(
    *,
    movement: PageMovementResult | None,
    matrix: EngagementMatrix | None,
    channels: tuple[WebsiteChannelPerformance, ...],
    titles: dict[str, str],
    query: WebsiteQuery,
    limit: int = 4,
) -> tuple[WebsiteOpportunity, ...]:
    """The deterministic opportunity rules, each stated with its own evidence.

    The rules and their thresholds live here and in `website_analytics`, both in
    Python, both tested. None of them is hidden in frontend JavaScript and none
    of them is a score.
    """
    found: list[WebsiteOpportunity] = []

    def _name(path: str) -> str:
        return titles.get(path, path)

    if movement and movement.rising:
        top = movement.rising[0]
        relative = top.relative_change
        evidence = f"{integer(top.previous_page_views)} → {integer(top.page_views)} lehevaatamist"
        if relative is not None:
            evidence += f" ({signed_percent(relative * 100)})"
        elif top.is_new:
            evidence += " (eelmisel perioodil mõõdetud vaatamisi ei olnud)"
        found.append(
            WebsiteOpportunity(
                kind="kasvav-leht",
                label="Kiiresti kasvav leht",
                subject=_name(top.path),
                evidence=evidence,
                path=top.path,
                query=query.build(focus=FOCUS_PAGES, detail=top.path, search="", page=1),
            )
        )

    if matrix and matrix.has_data:
        deeper = matrix.in_quadrant("vahe-sygav")
        if deeper:
            best = max(deeper, key=lambda page: page.seconds_per_view or 0)
            found.append(
                WebsiteOpportunity(
                    kind="vahem-leitud",
                    label="Vähem leitud, kuid sügavamalt kasutatud",
                    subject=_name(best.path),
                    evidence=(
                        f"{integer(best.page_views)} lehevaatamist, "
                        f"{duration_label(best.seconds_per_view)} vaatamise kohta — "
                        f"kaasatus üle perioodi mediaani, vaatamisi alla selle."
                    ),
                    path=best.path,
                    query=query.build(focus=FOCUS_PAGES, detail=best.path, search="", page=1),
                )
            )

        shallow = matrix.in_quadrant("palju-lyhike")
        if shallow:
            busiest = max(shallow, key=lambda page: page.page_views)
            found.append(
                WebsiteOpportunity(
                    kind="palju-liiklust",
                    label="Palju liiklust, lühem kaasatus",
                    subject=_name(busiest.path),
                    evidence=(
                        f"{integer(busiest.page_views)} lehevaatamist, "
                        f"{duration_label(busiest.seconds_per_view)} vaatamise kohta — "
                        f"vaatamisi üle perioodi mediaani, kaasatust alla selle."
                    ),
                    path=busiest.path,
                    query=query.build(focus=FOCUS_PAGES, detail=busiest.path, search="", page=1),
                )
            )

    rising_channels = [
        channel
        for channel in channels
        if channel.share_change_points is not None
        and channel.share_change_points > 0
        # A channel whose engagement fell while its share rose is a mixed
        # signal, not an opportunity, and is left out rather than described as
        # one.
        and (channel.engagement_change_points is None or channel.engagement_change_points >= 0)
    ]
    if rising_channels:
        best = max(rising_channels, key=lambda channel: channel.share_change_points)
        evidence = f"Osakaal {percentage_points(best.share_change_points)}"
        if best.engagement_rate is not None:
            evidence += f", kaasatuse määr {percent(best.engagement_rate * 100)}"
        found.append(
            WebsiteOpportunity(
                kind="kasvav-kanal",
                label="Kasvava osakaaluga kanal",
                subject=best.channel,
                evidence=evidence + ".",
                query=query.build(focus=FOCUS_CHANNELS, detail="", search="", page=1),
            )
        )

    return tuple(found[:limit])


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageSearchResults:
    term: str
    rows: tuple[ContentPerformanceRow, ...]
    total: int
    page_number: int
    total_pages: int

    @property
    def has_results(self) -> bool:
        return bool(self.rows)

    @property
    def summary(self) -> str:
        if not self.total:
            return "Ühtegi lehte ei leitud."
        return f"{self.total} lehte."

    @property
    def has_previous(self) -> bool:
        return self.page_number > 1

    @property
    def has_next(self) -> bool:
        return self.page_number < self.total_pages


@dataclass(frozen=True)
class WebsiteIntelligencePage:
    """Everything the Koduleht template renders.

    A view builds only its own analysis, so most of these are `None` on most
    focus views. The template asks whether a thing is present rather than which
    focus is active, which keeps the layout decisions in one place and the
    "which questions does this view answer" decision in this module.
    """

    query: WebsiteQuery
    focus: Focus
    focuses: tuple[FocusOption, ...]
    period: WebsitePeriod
    period_options: tuple[PeriodOption, ...]
    coverage: Coverage
    period_coverage: WebsitePeriodCoverage
    comparison: WebsiteComparison
    today: date

    summary: WebsiteTrafficSummary | None = None
    previous_summary: WebsiteTrafficSummary | None = None
    headlines: tuple[WebsiteHeadline, ...] = ()
    secondary: tuple[SecondaryReadout, ...] = ()
    insights: tuple[WebsiteInsight, ...] = ()
    opportunities: tuple[WebsiteOpportunity, ...] = ()

    trend: WebsiteChart | None = None
    metric_options: tuple[tuple[str, str, bool, str], ...] = ()
    weekday: WebsiteChart | None = None
    peak_day: PeakDay | None = None

    content_mix: WebsiteContentMix | None = None
    content_mix_table: TableView | None = None
    content_mix_chart: WebsiteChart | None = None
    top_pages: tuple[ContentPerformanceRow, ...] = ()
    top_pages_chart: WebsiteChart | None = None
    movement: PageMovementResult | None = None
    rising_table: TableView | None = None
    falling_table: TableView | None = None
    matrix: EngagementMatrix | None = None
    matrix_chart: WebsiteChart | None = None
    concentration: TrafficConcentration | None = None
    concentration_readouts: tuple[SecondaryReadout, ...] = ()
    language: WebsiteLanguageMix | None = None
    language_table: TableView | None = None
    language_chart: WebsiteChart | None = None

    channels: tuple[WebsiteChannelPerformance, ...] = ()
    channel_table: TableView | None = None
    channel_chart: WebsiteChart | None = None
    channel_engagement_chart: WebsiteChart | None = None
    rising_channels: tuple[WebsiteChannelPerformance, ...] = ()
    falling_channels: tuple[WebsiteChannelPerformance, ...] = ()
    channel_minimum_sessions: int = 0

    search: PageSearchResults | None = None
    search_table: TableView | None = None
    detail: WebsitePageDetail | None = None
    detail_title: str = ""
    detail_readouts: tuple[SecondaryReadout, ...] = ()
    detail_chart: WebsiteChart | None = None

    quality: tuple[WebsiteQualitySignal, ...] = ()
    quality_table: TableView | None = None
    section: ContentSection | None = None

    @property
    def title(self) -> str:
        return PRODUCT_NAME

    @property
    def has_data(self) -> bool:
        return self.coverage.has_data

    @property
    def charts(self) -> tuple[WebsiteChart, ...]:
        """Every chart on the current view, for the bundle gate in `extra_head`.

        The chart JavaScript loads only when there is something to draw, and this
        is the single expression the template asks — a second, differently-named
        context key is how a page once shipped every section and no chart script
        at all.
        """
        candidates = (
            self.trend,
            self.weekday,
            self.content_mix_chart,
            self.top_pages_chart,
            self.matrix_chart,
            self.language_chart,
            self.channel_chart,
            self.channel_engagement_chart,
            self.detail_chart,
        )
        return tuple(chart for chart in candidates if chart is not None)

    @property
    def freshness_note(self) -> str:
        """The quiet line under the title: how current the source is."""
        if not self.coverage.has_data:
            return "Google Analyticsi andmeid ei ole veel kogutud."
        return (
            f"Google Analytics kuni {long_date(self.coverage.latest)} · "
            f"ajalugu alates {long_date(self.coverage.earliest)}"
        )

    @property
    def search_previous_query(self) -> str:
        return self.query.build(page=max(self.search.page_number - 1, 1)) if self.search else ""

    @property
    def search_next_query(self) -> str:
        if not self.search:
            return ""
        return self.query.build(page=min(self.search.page_number + 1, self.search.total_pages))

    @property
    def clear_search_query(self) -> str:
        """Back to an empty explorer, keeping the window and the section.

        Clearing a search is not starting again: the reader still wants ninety
        days and Uudised, they have simply finished looking for one page.
        """
        return self.query.build(search="", detail="", page=1)

    @property
    def clear_detail_query(self) -> str:
        """Back to the results, keeping the term and the result page."""
        return self.query.build(detail="")

    @property
    def link_to_traffic(self) -> str:
        return self.query.build(focus=FOCUS_TRAFFIC, page=1)

    @property
    def link_to_content(self) -> str:
        return self.query.build(focus=FOCUS_CONTENT, page=1)

    @property
    def link_to_channels(self) -> str:
        return self.query.build(focus=FOCUS_CHANNELS, page=1)

    @property
    def link_to_pages(self) -> str:
        """The explorer, cleared of any page a previous view had selected.

        A "look at the pages" link is an invitation to search, not a return to
        whatever single page the reader last opened from an opportunity card.
        """
        return self.query.build(focus=FOCUS_PAGES, detail="", search="", page=1)

    @property
    def coverage_status(self) -> tuple[str, str]:
        """A short state and its variant, for the overview's quiet indicator."""
        gaps = self.period_coverage.missing_count
        if not self.coverage.has_data:
            return "Andmed puuduvad", "danger"
        if gaps:
            return f"{gaps} päeva andmeid puudub", "warning"
        if not self.period_coverage.is_page_complete:
            missing = (
                self.period_coverage.expected_days - self.period_coverage.days_with_page_detail
            )
            return f"{missing} päeva lehekaupa andmeid puudub", "warning"
        return "Andmed korras", "success"


def _period_options(period: WebsitePeriod, coverage: Coverage, query: WebsiteQuery):
    span = coverage.span_days
    return tuple(
        PeriodOption(
            key=preset.key,
            label=preset.label,
            is_active=preset.key == period.key,
            # Offered when the history can fill more than about a third of it.
            # Below that the chart is mostly the empty space before collection
            # started, and an option that cannot be filled is more informative
            # disabled than absent.
            is_offered=preset.is_all or (span > 0 and span * 3 >= preset.days),
            query=query.build(period=preset.key, date_from="", date_to="", page=1),
        )
        for preset in PERIOD_PRESETS
    )


def _titles_for(paths) -> dict[str, str]:
    """Authoritative titles for a bounded set of paths, or the path itself.

    Nothing is derived from a slug: a page DashKoda cannot name shows its path,
    which is the honest answer rather than a sentence nobody wrote.
    """
    rows = describe_pages(tuple(PageTotal(path=path, page_views=0, days_seen=0) for path in paths))
    return {row.path: row.label for row in rows}


def build_website_page(
    *,
    focus_key: str | None = None,
    period_key: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    metric_key: str | None = None,
    section_key: str | None = None,
    search: str | None = None,
    page: str | int | None = None,
    detail_path: str | None = None,
    today: date | None = None,
) -> WebsiteIntelligencePage:
    """Read the stored history once and shape it for the requested view."""
    coverage = get_coverage()
    focus = parse_focus(focus_key)
    period = parse_period(period_key, coverage, raw_from=date_from, raw_to=date_to)
    metric = parse_traffic_metric(metric_key)
    section = parse_section(section_key)
    term = core_parse_search(search, limit=MAX_SEARCH_LENGTH)
    number = core_parse_page(page)

    query = WebsiteQuery(
        focus=focus.key,
        period=period.key,
        date_from=(date_from or "").strip() if period.is_custom else "",
        date_to=(date_to or "").strip() if period.is_custom else "",
        metric=metric,
        section=section.key if section_key else "",
        search=term,
        page=number,
        detail=(detail_path or "").strip(),
    )

    period_coverage = get_period_coverage(period.start, period.end)
    comparison = build_comparison(period, coverage, period_coverage)

    base = {
        "query": query,
        "focus": focus,
        "focuses": tuple(
            FocusOption(
                focus=option,
                is_active=option.key == focus.key,
                # A view change keeps the window and the section, and resets the
                # result page: page four of a search is not a position that means
                # anything in another view.
                query=query.build(focus=option.key, page=1),
            )
            for option in FOCUSES
        ),
        "period": period,
        "period_options": _period_options(period, coverage, query),
        "coverage": coverage,
        "period_coverage": period_coverage,
        "comparison": comparison,
        "today": today or coverage.latest or date.today(),
        "section": section,
    }

    if not period.has_window:
        return WebsiteIntelligencePage(**base)

    start, end = period.start, period.end
    previous_start = comparison.start if comparison.is_available else None
    previous_end = comparison.end if comparison.is_available else None

    if focus.key == FOCUS_PAGES:
        return _pages_view(
            base, query, start, end, previous_start, previous_end, section, term, number
        )
    if focus.key == FOCUS_CHANNELS:
        return _channels_view(base, start, end, previous_start, previous_end, comparison)
    if focus.key == FOCUS_CONTENT:
        return _content_view(base, query, start, end, previous_start, previous_end, comparison)
    if focus.key == FOCUS_TRAFFIC:
        return _traffic_view(base, start, end, previous_start, previous_end, comparison, metric)
    return _overview(base, query, start, end, previous_start, previous_end, comparison, metric)


def _summaries(start, end, previous_start, previous_end):
    summary = get_traffic_summary(start=start, end=end)
    previous = (
        get_traffic_summary(start=previous_start, end=previous_end)
        if previous_start and previous_end
        else None
    )
    return summary, previous


def _metric_options(query: WebsiteQuery, active: str):
    return tuple(
        (key, label, key == active, query.build(metric=key))
        for key, label, _attribute in TRAFFIC_METRICS
    )


def _overview(base, query, start, end, previous_start, previous_end, comparison, metric):
    """The first screen: answers before the reader interacts with anything."""
    summary, previous = _summaries(start, end, previous_start, previous_end)
    headlines = build_headlines(
        summary, previous, comparison, is_custom_period=base["period"].is_custom
    )

    series = get_traffic_series(start=start, end=end)
    channels = get_channel_performance(
        start=start,
        end=end,
        previous_start=previous_start if comparison.can_compare_channels else None,
        previous_end=previous_end if comparison.can_compare_channels else None,
        site_sessions=summary.sessions,
        previous_site_sessions=previous.sessions if previous else None,
    )
    top_channels = channels[:OVERVIEW_CHANNELS]

    compare_pages = comparison.can_compare_pages
    mix = get_content_mix(
        start=start,
        end=end,
        previous_start=previous_start if compare_pages else None,
        previous_end=previous_end if compare_pages else None,
    )

    top = describe_pages(
        get_top_pages(start=start, end=end, limit=OVERVIEW_TOP_PAGES),
        section=base["section"],
    )

    # A ranking and a matrix describe the days that were collected, and say so
    # when some were not. A **movement** compares two windows, so it is built
    # only when both were covered well enough for the difference to be about the
    # website rather than about the collector.
    matrix = get_engagement_matrix(start=start, end=end)
    movement = None
    if compare_pages and previous_start and previous_end:
        movement = get_page_movement(
            start=start,
            end=end,
            previous_start=previous_start,
            previous_end=previous_end,
            limit=MOVEMENT_ROWS,
        )

    opportunity_paths = set()
    if movement:
        opportunity_paths.update(row.path for row in movement.rising[:1])
    if matrix and matrix.has_data:
        opportunity_paths.update(
            page.path
            for quadrant in ("vahe-sygav", "palju-lyhike")
            for page in matrix.in_quadrant(quadrant)[:20]
        )
    titles = _titles_for(opportunity_paths) if opportunity_paths else {}

    return WebsiteIntelligencePage(
        **base,
        summary=summary,
        previous_summary=previous,
        headlines=headlines,
        secondary=build_secondary_readouts(summary),
        insights=build_insights(
            headlines,
            top_channels,
            mix,
            unstripped=build_unstripped_measures(summary, previous, comparison),
        ),
        opportunities=build_opportunities(
            movement=movement, matrix=matrix, channels=top_channels, titles=titles, query=query
        ),
        trend=build_traffic_trend_chart(series, metric=metric),
        metric_options=_metric_options(query, metric),
        channels=top_channels,
        channel_table=build_channel_table(top_channels),
        channel_chart=(
            build_channel_sessions_chart(top_channels, site_sessions=summary.sessions)
            if top_channels
            else None
        ),
        top_pages=top,
    )


def _traffic_view(base, start, end, previous_start, previous_end, comparison, metric):
    """How overall use of the website is changing."""
    summary, previous = _summaries(start, end, previous_start, previous_end)
    series = get_traffic_series(start=start, end=end)
    pattern = get_weekday_pattern(start=start, end=end)
    quality = get_quality_signals(
        start=start,
        end=end,
        previous_start=previous_start,
        previous_end=previous_end,
        total_page_views=summary.page_views,
    )

    return WebsiteIntelligencePage(
        **base,
        summary=summary,
        previous_summary=previous,
        headlines=build_headlines(
            summary, previous, comparison, is_custom_period=base["period"].is_custom
        ),
        secondary=build_secondary_readouts(summary),
        trend=build_traffic_trend_chart(series, metric=metric),
        metric_options=_metric_options(base["query"], metric),
        weekday=build_weekday_chart(pattern, names=WEEKDAY_NAMES) if pattern else None,
        peak_day=get_peak_day(start=start, end=end),
        quality=quality,
        quality_table=build_quality_table(quality),
    )


def _content_view(base, query, start, end, previous_start, previous_end, comparison):
    """Which parts of the site hold attention, and which pages are changing."""
    compare_pages = comparison.can_compare_pages
    mix = get_content_mix(
        start=start,
        end=end,
        previous_start=previous_start if compare_pages else None,
        previous_end=previous_end if compare_pages else None,
    )
    language = get_language_mix(
        start=start,
        end=end,
        previous_start=previous_start if compare_pages else None,
        previous_end=previous_end if compare_pages else None,
    )
    concentration = get_concentration(start=start, end=end)
    section = base["section"]
    top = describe_pages(
        get_top_pages(
            start=start,
            end=end,
            limit=CONTENT_TOP_PAGES,
            prefix=section.prefixes,
            # Inside a section, its own listing page; across everything, every
            # section's — so a ranking of content is never topped by the page
            # that merely lists it.
            exclude=(all_index_paths() if section.is_everything else section.index_paths),
        ),
        section=section,
    )
    matrix = get_engagement_matrix(start=start, end=end)

    movement = None
    if compare_pages and previous_start and previous_end:
        movement = get_page_movement(
            start=start,
            end=end,
            previous_start=previous_start,
            previous_end=previous_end,
            limit=MOVEMENT_ROWS,
        )

    named_paths = set()
    if movement:
        named_paths.update(row.path for row in (*movement.rising, *movement.falling))
    named_paths.update(page.path for page in matrix.pages[:MATRIX_DRAWN_LIMIT])
    titles = _titles_for(named_paths) if named_paths else {}

    return WebsiteIntelligencePage(
        **base,
        content_mix=mix,
        content_mix_table=build_mix_table(mix),
        content_mix_chart=build_content_mix_chart(mix) if mix.has_data else None,
        top_pages=top,
        top_pages_chart=(
            build_top_pages_chart(top, total_page_views=mix.total_page_views) if top else None
        ),
        movement=movement,
        rising_table=(
            build_movement_table(movement.rising, titles, caption="Kasvavad lehed")
            if movement
            else None
        ),
        falling_table=(
            build_movement_table(movement.falling, titles, caption="Vähenenud tähelepanu")
            if movement
            else None
        ),
        matrix=matrix,
        matrix_chart=build_engagement_matrix_chart(matrix, labels=titles, limit=MATRIX_DRAWN_LIMIT)
        if matrix.has_data
        else None,
        concentration=concentration,
        concentration_readouts=build_concentration_readouts(concentration),
        language=language,
        language_table=build_language_table(language),
        language_chart=build_language_chart(language) if language.has_data else None,
        opportunities=build_opportunities(
            movement=movement, matrix=matrix, channels=(), titles=titles, query=query
        ),
    )


def _channels_view(base, start, end, previous_start, previous_end, comparison):
    """Where traffic comes from, and how engaged it is."""
    summary, previous = _summaries(start, end, previous_start, previous_end)
    compare = comparison.can_compare_channels
    channels = get_channel_performance(
        start=start,
        end=end,
        previous_start=previous_start if compare else None,
        previous_end=previous_end if compare else None,
        site_sessions=summary.sessions,
        previous_site_sessions=previous.sessions if previous else None,
    )
    movement = rank_channel_movement(channels, days=(end - start).days + 1)

    return WebsiteIntelligencePage(
        **base,
        summary=summary,
        previous_summary=previous,
        channels=channels,
        channel_table=build_channel_table(channels),
        channel_chart=(
            build_channel_sessions_chart(channels, site_sessions=summary.sessions)
            if channels
            else None
        ),
        channel_engagement_chart=build_channel_engagement_chart(channels) if channels else None,
        rising_channels=movement.rising,
        falling_channels=movement.falling,
        channel_minimum_sessions=movement.minimum_sessions,
    )


def _pages_view(base, query, start, end, previous_start, previous_end, section, term, number):
    """The deep page explorer: search the whole population, then read one page."""
    search_results = None
    if term:
        matches, total = search_pages(
            term=term,
            start=start,
            end=end,
            # The catalogues turn a title into paths; nothing here guesses one
            # from a slug, so a page DashKoda cannot name is still found by path.
            extra_paths=paths_for_title(term),
            prefix=section.prefixes,
            limit=SEARCH_PER_PAGE,
            offset=(number - 1) * SEARCH_PER_PAGE,
        )
        total_pages = max((total + SEARCH_PER_PAGE - 1) // SEARCH_PER_PAGE, 1)
        search_results = PageSearchResults(
            term=term,
            rows=describe_pages(matches, section=section),
            total=total,
            page_number=min(number, total_pages),
            total_pages=total_pages,
        )

    detail = None
    detail_title = ""
    detail_chart = None
    detail_readouts: tuple[SecondaryReadout, ...] = ()
    if query.detail:
        detail = get_page_detail(
            path=query.detail,
            start=start,
            end=end,
            previous_start=previous_start,
            previous_end=previous_end,
        )
        if detail is not None:
            detail_title = _titles_for((detail.path,)).get(detail.path, detail.path)
            series = get_page_series(path=detail.path, start=start, end=end)
            detail_chart = build_traffic_trend_chart(series, metric="lehevaatamised")
            detail_readouts = build_detail_readouts(detail)

    return WebsiteIntelligencePage(
        **base,
        search=search_results,
        search_table=build_search_table(search_results, query) if search_results else None,
        detail=detail,
        detail_title=detail_title,
        detail_readouts=detail_readouts,
        detail_chart=detail_chart,
    )


__all__ = [
    "DEFAULT_FOCUS",
    "FOCUSES",
    "FOCUS_CHANNELS",
    "FOCUS_CONTENT",
    "FOCUS_OVERVIEW",
    "FOCUS_PAGES",
    "FOCUS_TRAFFIC",
    "MAX_SEARCH_LENGTH",
    "PARAM_DETAIL",
    "PARAM_FOCUS",
    "PARAM_METRIC",
    "PARAM_PAGE",
    "PARAM_SEARCH",
    "PRODUCT_NAME",
    "QUADRANT_LABELS",
    "Focus",
    "FocusOption",
    "PageSearchResults",
    "SecondaryReadout",
    "TableRow",
    "TableView",
    "WebsiteHeadline",
    "WebsiteInsight",
    "WebsiteIntelligencePage",
    "WebsiteOpportunity",
    "WebsiteQuery",
    "build_channel_table",
    "build_concentration_readouts",
    "build_detail_readouts",
    "build_headlines",
    "build_language_table",
    "build_mix_table",
    "build_movement_table",
    "build_insights",
    "build_opportunities",
    "build_quality_table",
    "build_search_table",
    "build_secondary_readouts",
    "build_website_page",
    "duration_label",
    "parse_focus",
]
