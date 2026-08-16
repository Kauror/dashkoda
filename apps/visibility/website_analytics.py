"""What the stored GA4 history says about how Koda.ee is used.

`ga4_selectors` answers "what is stored". This module answers the questions the
Koduleht dashboard asks of it: how much traffic, how deeply engaged, where from,
which content, what moved, and how much of each answer can be trusted.

Four rules govern everything below, and each of them is a bug that was easy to
write:

**Absent is not zero.** A `Sum` over no rows is `None` here and stays `None`
through the presenters into the template. A day that was not collected did not
measure zero sessions, and a page with no row was not measured at zero views.

**Denominators are matched to their numerators.** Engagement seconds are
nullable while page views are not, so dividing a partial `SUM(seconds)` by a
complete `SUM(page_views)` quietly under-reports every average. Every ratio here
sums its denominator over **the same rows** that produced its numerator, and the
`*_with_seconds` fields exist for no other reason.

**Distinct people are never added.** `active_users` appears in exactly one
aggregate below — a `Max` — and is labelled as the busiest single day wherever
it surfaces. See `apps.visibility.ga4_selectors` for the whole argument.

**A population question is asked of the population.** "Which pages grew" is not
"which of this period's top twenty grew": a page that went from rank 400 to rank
25 is the discovery the analysis exists to make. Every movement query below
groups over every measured path, in one round trip, with both windows expressed
as conditional sums.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from django.db.models import Case, Count, F, Max, Min, Q, Sum, Value, When
from django.db.models.functions import Coalesce, ExtractIsoWeekDay

from .content_ranking import LANGUAGES
from .content_sections import SECTION_EVENTS, SECTION_NEWS, SECTION_SERVICES, ContentSection
from .ga4_paths import canonical_path
from .ga4_selectors import current_channels, current_days, current_pages, only_rankable

# ---------------------------------------------------------------------------
# Eligibility thresholds
# ---------------------------------------------------------------------------

#: A page enters a movement or engagement ranking when it averaged at least this
#: many views a day across one of the windows being compared.
#:
#: Expressed as a **rate** rather than as a count, because the same dashboard
#: asks the question over thirty days and over three years, and a fixed count
#: would either admit every path in the long window or exclude every path in the
#: short one.
#:
#: Chosen by profiling the production history read-only on 2026-08-14, not
#: guessed. Over the thirty days to 2026-08-13 the property measured 14 029 page
#: views across 1 586 rankable paths, and the **median rankable path had one
#: view**: the distribution is a long tail of pages nobody read twice. One view a
#: day is what separates the pages a reader could reason about from that tail,
#: and it holds its shape at every offered window:
#:
#: | window | rankable paths | floor | eligible |
#: | --- | --- | --- | --- |
#: | 30 päeva | 1 586 | 30 | 57 |
#: | 90 päeva | 3 277 | 90 | 80 |
#: | 1 aasta | 8 482 | 365 | 140 |
#: | Kõik | 11 520 | 1 155 | 119 |
#:
#: A fixed count could not do that: 30 views admits 57 pages over a month and
#: essentially every page over three years.
#:
#: Every eligible page in all four windows carried an engagement reading, so the
#: opportunity matrix has both dimensions for its whole population rather than
#: for a subset of it.
MIN_PAGE_VIEW_RATE_PER_DAY = 1.0

#: The floor under that rate, so a two-day custom window still requires a page to
#: have been read more than once.
MIN_PAGE_VIEWS_FLOOR = 10

#: The same idea for acquisition channels, which are far fewer and much larger:
#: a channel is a group of sessions, not one address.
#:
#: Profiled the same way. Over the thirty days to 2026-08-13 the property
#: reported twelve channel groups, from Organic Search at 2 665 sessions down to
#: Paid Other and Organic Video at one each. Five sessions a day — 150 over that
#: window — admits the ten that could be said to have moved and excludes the two
#: that cannot: a channel going from one session to nine is +800% and is not news.
MIN_CHANNEL_SESSION_RATE_PER_DAY = 5.0
MIN_CHANNEL_SESSIONS_FLOOR = 25

#: How many pages the opportunity matrix draws. The medians are computed over the
#: **whole** eligible population; this bounds only the scatter, because three
#: thousand points is not a readable picture. The table beneath carries the rest.
MATRIX_DRAWN_LIMIT = 400

#: The shortest window a weekday pattern is drawn over. Below about eight weeks
#: each weekday has fewer than eight observations and the "pattern" is mostly
#: which Tuesday happened to carry a campaign.
WEEKDAY_PATTERN_MIN_DAYS = 56


def min_page_views_for(days: int) -> int:
    """The view floor a page must clear over a window of `days` to be ranked."""
    return max(MIN_PAGE_VIEWS_FLOOR, math.ceil(MIN_PAGE_VIEW_RATE_PER_DAY * max(days, 0)))


def min_channel_sessions_for(days: int) -> int:
    """The session floor a channel must clear over a window of `days`."""
    return max(
        MIN_CHANNEL_SESSIONS_FLOOR,
        math.ceil(MIN_CHANNEL_SESSION_RATE_PER_DAY * max(days, 0)),
    )


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    """A quotient, or `None` where one cannot honestly be formed.

    Both a missing numerator and a zero or missing denominator produce `None`
    rather than `0`: "no engagement was measured" and "engagement was measured at
    nothing" are different statements, and a division that turns the first into
    the second is how a gap becomes a figure.
    """
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _change(current: int | float | None, previous: int | float | None) -> float | None:
    """Relative change, or `None` when there is no base to change from.

    A rise from nothing is not "+100%" and not "+∞"; it is a page that was not
    measured before, and the interface says that in words instead.
    """
    if current is None or previous is None or not previous:
        return None
    return (current - previous) / previous


# ---------------------------------------------------------------------------
# Site-wide totals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebsiteTrafficSummary:
    """Every site-wide figure for one window, and the counts that qualify them.

    `sessions_with_seconds` and `sessions_with_engaged` are the matched
    denominators described in the module docstring. They are not the same as
    `sessions`: a day can report sessions and omit either derived count, and
    dividing by the wrong one is a silent under-report rather than an error.
    """

    start: date | None
    end: date | None
    days: int
    sessions: int | None = None
    page_views: int | None = None
    engaged_sessions: int | None = None
    engagement_seconds: int | None = None
    peak_active_users: int | None = None
    peak_active_users_on: date | None = None
    #: Sessions on the days that also reported engaged sessions.
    sessions_with_engaged: int | None = None
    #: Sessions on the days that also reported engagement seconds.
    sessions_with_seconds: int | None = None

    @property
    def engagement_rate(self) -> float | None:
        """`SUM(engaged_sessions) / SUM(sessions)` over the same days.

        Never an average of daily rates: a day with four sessions would then
        weigh as much as a day with four thousand.
        """
        return _ratio(self.engaged_sessions, self.sessions_with_engaged)

    @property
    def seconds_per_session(self) -> float | None:
        return _ratio(self.engagement_seconds, self.sessions_with_seconds)

    @property
    def views_per_session(self) -> float | None:
        return _ratio(self.page_views, self.sessions)

    @property
    def has_any(self) -> bool:
        return self.sessions is not None or self.page_views is not None


def get_traffic_summary(*, start: date | None, end: date | None) -> WebsiteTrafficSummary:
    """Every site-wide total for one window, in one aggregate query."""
    if start is None or end is None:
        return WebsiteTrafficSummary(start=start, end=end, days=0)

    rows = current_days().filter(report_date__gte=start, report_date__lte=end)
    # None of these aliases may be spelled like a field another aggregate in the
    # same call reads. Django resolves a later `Sum("sessions")` against an
    # earlier annotation named `sessions` and raises "Cannot compute
    # Sum('sessions'): 'sessions' is an aggregate" — which is a loud failure
    # here, and would have been a silent wrong denominator if it had resolved.
    totals = rows.aggregate(
        total_sessions=Sum("sessions"),
        total_page_views=Sum("page_views"),
        total_engaged=Sum("engaged_sessions"),
        total_seconds=Sum("user_engagement_seconds"),
        # `Max`, never `Sum`. Monday's users and Tuesday's are mostly the same
        # people, and no arithmetic over daily distinct counts produces a period
        # distinct count.
        busiest_active_users=Max("active_users"),
        sessions_when_engaged=Sum("sessions", filter=Q(engaged_sessions__isnull=False)),
        sessions_when_seconds=Sum("sessions", filter=Q(user_engagement_seconds__isnull=False)),
        day_count=Count("id"),
    )

    peak_day = None
    if totals["busiest_active_users"] is not None:
        peak_day = (
            rows.filter(active_users=totals["busiest_active_users"])
            .order_by("report_date")
            .values_list("report_date", flat=True)
            .first()
        )

    return WebsiteTrafficSummary(
        start=start,
        end=end,
        days=totals["day_count"] or 0,
        sessions=totals["total_sessions"],
        page_views=totals["total_page_views"],
        engaged_sessions=totals["total_engaged"],
        engagement_seconds=totals["total_seconds"],
        peak_active_users=totals["busiest_active_users"],
        peak_active_users_on=peak_day,
        sessions_with_engaged=totals["sessions_when_engaged"],
        sessions_with_seconds=totals["sessions_when_seconds"],
    )


@dataclass(frozen=True)
class WeekdayAverage:
    """One weekday's mean sessions across the observed days of a window."""

    #: ISO weekday, 1 = Monday.
    weekday: int
    mean_sessions: float
    observed_days: int


#: Estonian weekday names, indexed by ISO weekday minus one.
WEEKDAY_NAMES: tuple[str, ...] = (
    "esmaspäev",
    "teisipäev",
    "kolmapäev",
    "neljapäev",
    "reede",
    "laupäev",
    "pühapäev",
)


def get_weekday_pattern(*, start: date | None, end: date | None) -> tuple[WeekdayAverage, ...]:
    """Mean sessions per weekday, or nothing when the window is too short.

    Descriptive only. A higher Tuesday mean says Tuesdays measured more traffic;
    it does not say the day caused it, and nothing here is worded as though it
    did.
    """
    if start is None or end is None:
        return ()
    if (end - start).days + 1 < WEEKDAY_PATTERN_MIN_DAYS:
        return ()

    rows = (
        current_days()
        .filter(report_date__gte=start, report_date__lte=end, sessions__isnull=False)
        .annotate(weekday=ExtractIsoWeekDay("report_date"))
        .values("weekday")
        .annotate(total=Sum("sessions"), observed=Count("id"))
        .order_by("weekday")
    )
    return tuple(
        WeekdayAverage(
            weekday=row["weekday"],
            mean_sessions=row["total"] / row["observed"],
            observed_days=row["observed"],
        )
        for row in rows
        if row["observed"]
    )


# ---------------------------------------------------------------------------
# Acquisition channels
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebsiteChannelPerformance:
    """One acquisition channel across the chosen window and the one before it.

    `share` and `previous_share` are computed against the **whole-site** session
    total, never against the sum of the listed channels. Using the visible rows
    as the denominator makes every share add to 100% whatever is left out, which
    is a chart that cannot be wrong and therefore says nothing.
    """

    channel: str
    sessions: int
    engaged_sessions: int | None
    previous_sessions: int | None = None
    previous_engaged_sessions: int | None = None
    share: float | None = None
    previous_share: float | None = None

    @property
    def engagement_rate(self) -> float | None:
        return _ratio(self.engaged_sessions, self.sessions)

    @property
    def previous_engagement_rate(self) -> float | None:
        return _ratio(self.previous_engaged_sessions, self.previous_sessions)

    @property
    def session_change(self) -> int | None:
        if self.previous_sessions is None:
            return None
        return self.sessions - self.previous_sessions

    @property
    def relative_change(self) -> float | None:
        return _change(self.sessions, self.previous_sessions)

    @property
    def share_change_points(self) -> float | None:
        """Movement in **percentage points**, which is not percent.

        A share that went from 24,1% to 27,3% did not rise by 3,2 percent.
        """
        if self.share is None or self.previous_share is None:
            return None
        return (self.share - self.previous_share) * 100

    @property
    def engagement_change_points(self) -> float | None:
        current = self.engagement_rate
        previous = self.previous_engagement_rate
        if current is None or previous is None:
            return None
        return (current - previous) * 100


def get_channel_performance(
    *,
    start: date,
    end: date,
    previous_start: date | None = None,
    previous_end: date | None = None,
    site_sessions: int | None = None,
    previous_site_sessions: int | None = None,
) -> tuple[WebsiteChannelPerformance, ...]:
    """Every channel over both windows, in one grouped query.

    Both periods are expressed as conditional sums over the union of the two
    ranges, which are contiguous by construction. A second query per channel — or
    per period — is the shape this deliberately avoids.
    """
    lower = previous_start or start
    rows = current_channels().filter(report_date__gte=lower, report_date__lte=end)

    current_window = Q(report_date__gte=start, report_date__lte=end)
    # Aliases deliberately unlike the field names: see `get_traffic_summary`.
    aggregates = {
        "window_sessions": Sum("sessions", filter=current_window),
        "window_engaged": Sum("engaged_sessions", filter=current_window),
    }
    if previous_start is not None and previous_end is not None:
        previous_window = Q(report_date__gte=previous_start, report_date__lte=previous_end)
        aggregates["prior_sessions"] = Sum("sessions", filter=previous_window)
        aggregates["prior_engaged"] = Sum("engaged_sessions", filter=previous_window)

    grouped = (
        rows.values("channel")
        .annotate(**aggregates)
        .order_by(F("window_sessions").desc(nulls_last=True))
    )

    return tuple(
        WebsiteChannelPerformance(
            channel=row["channel"],
            sessions=row["window_sessions"] or 0,
            engaged_sessions=row["window_engaged"],
            previous_sessions=row.get("prior_sessions"),
            previous_engaged_sessions=row.get("prior_engaged"),
            share=_ratio(row["window_sessions"], site_sessions),
            previous_share=_ratio(row.get("prior_sessions"), previous_site_sessions),
        )
        for row in grouped
        if row["window_sessions"]
    )


@dataclass(frozen=True)
class ChannelMovement:
    """Channels that grew or fell, with the rule that admitted them attached."""

    rising: tuple[WebsiteChannelPerformance, ...]
    falling: tuple[WebsiteChannelPerformance, ...]
    minimum_sessions: int


def rank_channel_movement(
    channels: Sequence[WebsiteChannelPerformance], *, days: int, limit: int = 5
) -> ChannelMovement:
    """Split channels into risers and fallers, largest absolute move first.

    Ordered by absolute change rather than by a blended score, because the
    ranking has to be explainable in one sentence: this is how many more or
    fewer sessions the channel brought. The relative change travels beside it so
    a reader can see both, and neither is hidden inside the other.
    """
    floor = min_channel_sessions_for(days)
    eligible = [
        channel
        for channel in channels
        if channel.session_change is not None
        and max(channel.sessions, channel.previous_sessions or 0) >= floor
    ]
    rising = sorted(
        (channel for channel in eligible if channel.session_change > 0),
        key=lambda channel: channel.session_change,
        reverse=True,
    )
    falling = sorted(
        (channel for channel in eligible if channel.session_change < 0),
        key=lambda channel: channel.session_change,
    )
    return ChannelMovement(
        rising=tuple(rising[:limit]),
        falling=tuple(falling[:limit]),
        minimum_sessions=floor,
    )


# ---------------------------------------------------------------------------
# Content sections
# ---------------------------------------------------------------------------

#: The sections a measured page is filed under for the content mix, plus the
#: explicit remainder. `Muu` is every rankable page belonging to none of the
#: three registered sections — ordinary pages the Chamber publishes, not an
#: error bucket — and it is named so the denominator is complete.
MIX_SECTIONS: tuple[ContentSection, ...] = (SECTION_SERVICES, SECTION_NEWS, SECTION_EVENTS)
OTHER_SECTION_KEY = "muu"
OTHER_SECTION_LABEL = "Muu"


def _section_case(field_name: str = "path"):
    """A `CASE` filing each path under its section, whole-segment matched."""
    whens = []
    for section in MIX_SECTIONS:
        condition = Q()
        for prefix in section.prefixes:
            condition |= Q(**{field_name: prefix}) | Q(
                **{f"{field_name}__startswith": prefix + "/"}
            )
        whens.append(When(condition, then=Value(section.key)))
    return Case(*whens, default=Value(OTHER_SECTION_KEY))


@dataclass(frozen=True)
class ContentMixRow:
    """One section's measured share of the content that may be ranked."""

    key: str
    label: str
    page_views: int
    previous_page_views: int | None = None
    share: float | None = None
    previous_share: float | None = None

    @property
    def share_change_points(self) -> float | None:
        if self.share is None or self.previous_share is None:
            return None
        return (self.share - self.previous_share) * 100

    @property
    def relative_change(self) -> float | None:
        return _change(self.page_views, self.previous_page_views)


@dataclass(frozen=True)
class WebsiteContentMix:
    """The section mix, with its denominator stated rather than assumed.

    `total_page_views` is **rankable content only** — language homepages, the
    cart, internal search, Drupal node aliases and error documents are not
    pieces of content and are excluded from this list, exactly as they are from
    the ranking. That makes the denominator smaller than the site's page views,
    which is why every label for this figure says which of the two it is.
    """

    rows: tuple[ContentMixRow, ...]
    total_page_views: int | None
    previous_total_page_views: int | None = None

    @property
    def has_data(self) -> bool:
        return bool(self.rows) and bool(self.total_page_views)


def get_content_mix(
    *,
    start: date,
    end: date,
    previous_start: date | None = None,
    previous_end: date | None = None,
) -> WebsiteContentMix:
    """Section totals for both windows, in one grouped query over rankable pages."""
    lower = previous_start or start
    rows = only_rankable(current_pages().filter(report_date__gte=lower, report_date__lte=end))

    current_window = Q(report_date__gte=start, report_date__lte=end)
    aggregates = {"views": Sum("page_views", filter=current_window)}
    compare = previous_start is not None and previous_end is not None
    if compare:
        aggregates["previous_views"] = Sum(
            "page_views", filter=Q(report_date__gte=previous_start, report_date__lte=previous_end)
        )

    grouped = rows.annotate(section=_section_case()).values("section").annotate(**aggregates)
    by_key = {row["section"]: row for row in grouped}

    total = sum((row["views"] or 0) for row in by_key.values()) or None
    previous_total = None
    if compare:
        previous_total = sum((row.get("previous_views") or 0) for row in by_key.values()) or None

    ordered = [(section.key, section.label) for section in MIX_SECTIONS]
    ordered.append((OTHER_SECTION_KEY, OTHER_SECTION_LABEL))

    mix_rows = []
    for key, label in ordered:
        row = by_key.get(key)
        views = (row or {}).get("views") or 0
        previous_views = (row or {}).get("previous_views") if compare else None
        mix_rows.append(
            ContentMixRow(
                key=key,
                label=label,
                page_views=views,
                previous_page_views=previous_views,
                share=_ratio(views, total),
                previous_share=_ratio(previous_views, previous_total),
            )
        )

    return WebsiteContentMix(
        rows=tuple(mix_rows),
        total_page_views=total,
        previous_total_page_views=previous_total,
    )


# ---------------------------------------------------------------------------
# Page movement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebsitePageMovement:
    """One page's traffic in both windows.

    Both the absolute and the relative change are carried, because `5 000 → 5 500`
    and `50 → 100` are different kinds of growth and a single ordering would hide
    whichever one the reader was asking about. The list is ordered by the
    absolute figure and shows both; no blended score decides for them.
    """

    path: str
    page_views: int
    previous_page_views: int

    @property
    def change(self) -> int:
        return self.page_views - self.previous_page_views

    @property
    def relative_change(self) -> float | None:
        return _change(self.page_views, self.previous_page_views)

    @property
    def is_new(self) -> bool:
        """No measured traffic in the previous window.

        Not "grew by 100%" and not "grew infinitely": there was no base, and the
        interface says so in words.
        """
        return self.previous_page_views == 0 and self.page_views > 0


@dataclass(frozen=True)
class PageMovementResult:
    rising: tuple[WebsitePageMovement, ...]
    falling: tuple[WebsitePageMovement, ...]
    minimum_page_views: int


def get_page_movement(
    *,
    start: date,
    end: date,
    previous_start: date,
    previous_end: date,
    limit: int = 10,
) -> PageMovementResult:
    """Which pages gained and lost the most measured attention.

    **Population-wide, in one query.** Every measured path in the union of the
    two contiguous windows is grouped once, with each window as a conditional
    sum, and the volume floor is applied as a `HAVING` clause so the long tail
    never leaves PostgreSQL. Fetching this period's top twenty and comparing only
    those would answer a different question: a page that rose from rank 400 to
    rank 25 is precisely what this is for.
    """
    days = (end - start).days + 1
    floor = min_page_views_for(days)

    rows = only_rankable(
        current_pages().filter(report_date__gte=previous_start, report_date__lte=end)
    )
    grouped = (
        rows.values("path")
        .annotate(
            # `Coalesce` is load-bearing. A page with no rows at all in one
            # window sums to NULL there, and `views - NULL` is NULL, so the
            # ordering below silently dropped every page that appeared for the
            # first time — which is exactly the arrival this analysis exists to
            # find. Zero is the right value *here* because the window was
            # measured and the page was not in it; `is_new` is what keeps that
            # distinguishable from a page that fell to nothing.
            views=Coalesce(
                Sum("page_views", filter=Q(report_date__gte=start, report_date__lte=end)),
                Value(0),
            ),
            previous_views=Coalesce(
                Sum(
                    "page_views",
                    filter=Q(report_date__gte=previous_start, report_date__lte=previous_end),
                ),
                Value(0),
            ),
        )
        # The floor admits a page that cleared it in **either** window, so both a
        # page that grew into relevance and one that fell out of it are visible.
        .filter(Q(views__gte=floor) | Q(previous_views__gte=floor))
    )

    def _row(entry) -> WebsitePageMovement:
        return WebsitePageMovement(
            path=entry["path"],
            page_views=entry["views"] or 0,
            previous_page_views=entry["previous_views"] or 0,
        )

    delta = F("views") - F("previous_views")
    rising = tuple(
        _row(entry)
        for entry in grouped.annotate(delta=delta)
        .filter(delta__gt=0)
        .order_by("-delta", "path")[:limit]
    )
    falling = tuple(
        _row(entry)
        for entry in grouped.annotate(delta=delta)
        .filter(delta__lt=0)
        .order_by("delta", "path")[:limit]
    )
    return PageMovementResult(rising=rising, falling=falling, minimum_page_views=floor)


# ---------------------------------------------------------------------------
# Page engagement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebsitePageEngagement:
    """One page's attention and how long it held it.

    `seconds_per_view` divides the engagement seconds by the views of **the rows
    that reported seconds**, not by every view. The metric is nullable on a page
    row, and the unmatched division is a silent under-report.

    Deliberately not called "time on page": GA4 stores `userEngagementDuration`,
    which is not the same measurement, and a label that claimed it would be
    describing a metric the property does not hold.
    """

    path: str
    page_views: int
    engagement_seconds: int | None
    #: Views on the rows that also carried an engagement reading.
    views_with_seconds: int | None

    @property
    def seconds_per_view(self) -> float | None:
        return _ratio(self.engagement_seconds, self.views_with_seconds)


@dataclass(frozen=True)
class EngagementMatrix:
    """Pages placed against the two medians of their own population.

    The thresholds are the medians of the eligible pages themselves rather than
    numbers somebody chose, so the quadrants describe this site in this window
    and not a benchmark from elsewhere. Both are published to the interface, so
    the rule a page was filed under can be read rather than trusted.

    The quadrant names describe **measurements, not quality**. A page with many
    views and short engagement may be answering a question quickly, may be
    reached by the wrong audience, or may be thin; this data cannot tell which,
    and none of the labels pretend otherwise.
    """

    pages: tuple[WebsitePageEngagement, ...]
    median_page_views: float | None
    median_seconds_per_view: float | None
    minimum_page_views: int

    @property
    def has_data(self) -> bool:
        return bool(self.pages) and self.median_seconds_per_view is not None

    def quadrant_of(self, page: WebsitePageEngagement) -> str:
        """Which of the four groups a page falls in, or `""` when unmeasured."""
        if (
            self.median_page_views is None
            or self.median_seconds_per_view is None
            or page.seconds_per_view is None
        ):
            return ""
        many = page.page_views >= self.median_page_views
        deep = page.seconds_per_view >= self.median_seconds_per_view
        if many and deep:
            return "palju-sygav"
        if many:
            return "palju-lyhike"
        if deep:
            return "vahe-sygav"
        return "vahe-lyhike"

    def in_quadrant(self, quadrant: str) -> tuple[WebsitePageEngagement, ...]:
        return tuple(page for page in self.pages if self.quadrant_of(page) == quadrant)


#: What each quadrant is called on screen. Neutral by construction — no
#: `hea`, `halb`, `edukas` or `nõrk` — because this is an analytical grouping
#: and not a verdict on anybody's work.
QUADRANT_LABELS: dict[str, str] = {
    "palju-sygav": "Palju vaatamisi, pikem kaasatus",
    "palju-lyhike": "Palju vaatamisi, lühem kaasatus",
    "vahe-sygav": "Vähem vaatamisi, pikem kaasatus",
    "vahe-lyhike": "Vähem vaatamisi, lühem kaasatus",
}


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def get_engagement_matrix(*, start: date, end: date) -> EngagementMatrix:
    """Eligible pages with both dimensions, and the medians that split them.

    One grouped query. The volume floor runs as a `HAVING` clause, so what
    returns is the eligible population itself rather than a truncation of it —
    which matters, because a median taken over an arbitrary slice is not the
    median of anything.
    """
    days = (end - start).days + 1
    floor = min_page_views_for(days)

    grouped = (
        only_rankable(current_pages().filter(report_date__gte=start, report_date__lte=end))
        .values("path")
        .annotate(
            views=Sum("page_views"),
            seconds=Sum("user_engagement_seconds"),
            views_with_seconds=Sum("page_views", filter=Q(user_engagement_seconds__isnull=False)),
        )
        .filter(views__gte=floor)
        .order_by("-views", "path")
    )

    pages = tuple(
        WebsitePageEngagement(
            path=row["path"],
            page_views=row["views"] or 0,
            engagement_seconds=row["seconds"],
            views_with_seconds=row["views_with_seconds"],
        )
        for row in grouped
    )

    measured = tuple(page for page in pages if page.seconds_per_view is not None)
    return EngagementMatrix(
        pages=pages,
        median_page_views=_median([page.page_views for page in measured]),
        median_seconds_per_view=_median([page.seconds_per_view for page in measured]),
        minimum_page_views=floor,
    )


# ---------------------------------------------------------------------------
# Content language
# ---------------------------------------------------------------------------

OTHER_LANGUAGE_KEY = "muu"

#: What each language segment is called on screen.
LANGUAGE_LABELS: dict[str, str] = {
    "et": "Eesti",
    "en": "Inglise",
    "ru": "Vene",
    OTHER_LANGUAGE_KEY: "Määramata",
}


def _language_case(field_name: str = "path"):
    whens = [
        When(
            Q(**{field_name: f"/{language}"}) | Q(**{f"{field_name}__startswith": f"/{language}/"}),
            then=Value(language),
        )
        for language in LANGUAGES
    ]
    return Case(*whens, default=Value(OTHER_LANGUAGE_KEY))


@dataclass(frozen=True)
class LanguageShare:
    """One content language's measured page views.

    This is the language of **the page that was viewed**. It is not the visitor's
    nationality, country, browser language or preferred language — none of which
    GA4 reports to DashKoda and none of which could be inferred from a URL
    prefix. The methodology says so in the same words.
    """

    key: str
    label: str
    page_views: int
    previous_page_views: int | None = None
    share: float | None = None
    previous_share: float | None = None

    @property
    def share_change_points(self) -> float | None:
        if self.share is None or self.previous_share is None:
            return None
        return (self.share - self.previous_share) * 100


@dataclass(frozen=True)
class WebsiteLanguageMix:
    rows: tuple[LanguageShare, ...]
    total_page_views: int | None

    @property
    def has_data(self) -> bool:
        return bool(self.rows) and bool(self.total_page_views)


def get_language_mix(
    *,
    start: date,
    end: date,
    previous_start: date | None = None,
    previous_end: date | None = None,
) -> WebsiteLanguageMix:
    """Page views by the language of the page viewed, over **all** measured pages.

    A different denominator from the content mix on purpose: a language homepage
    is not a piece of content and is excluded from a ranking of content, but it
    is unambiguously an Estonian or an English page and belongs in a count of
    which language versions are read. Both denominators are stated where they
    are shown.
    """
    lower = previous_start or start
    rows = current_pages().filter(report_date__gte=lower, report_date__lte=end)

    aggregates = {
        "views": Sum("page_views", filter=Q(report_date__gte=start, report_date__lte=end))
    }
    compare = previous_start is not None and previous_end is not None
    if compare:
        aggregates["previous_views"] = Sum(
            "page_views", filter=Q(report_date__gte=previous_start, report_date__lte=previous_end)
        )

    grouped = rows.annotate(language=_language_case()).values("language").annotate(**aggregates)
    by_key = {row["language"]: row for row in grouped}

    total = sum((row["views"] or 0) for row in by_key.values()) or None
    previous_total = None
    if compare:
        previous_total = sum((row.get("previous_views") or 0) for row in by_key.values()) or None

    ordered = [*LANGUAGES, OTHER_LANGUAGE_KEY]
    shares = []
    for key in ordered:
        row = by_key.get(key)
        views = (row or {}).get("views") or 0
        if not views:
            continue
        previous_views = (row or {}).get("previous_views") if compare else None
        shares.append(
            LanguageShare(
                key=key,
                label=LANGUAGE_LABELS.get(key, key),
                page_views=views,
                previous_page_views=previous_views,
                share=_ratio(views, total),
                previous_share=_ratio(previous_views, previous_total),
            )
        )

    shares.sort(key=lambda share: share.page_views, reverse=True)
    return WebsiteLanguageMix(rows=tuple(shares), total_page_views=total)


# ---------------------------------------------------------------------------
# One page
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebsitePageDetail:
    """Everything measured about one page, for the explorer's detail view.

    `measured_total` is views inside GA4's coverage. For a page that existed
    before collection began it is **not** a lifetime figure, which is why the
    coverage start travels with it everywhere it is printed.
    """

    path: str
    page_views: int | None
    previous_page_views: int | None
    measured_total: int | None
    engagement_seconds: int | None
    views_with_seconds: int | None
    first_measured_on: date | None
    last_measured_on: date | None
    days_seen: int

    @property
    def seconds_per_view(self) -> float | None:
        return _ratio(self.engagement_seconds, self.views_with_seconds)

    @property
    def change(self) -> int | None:
        if self.page_views is None or self.previous_page_views is None:
            return None
        return self.page_views - self.previous_page_views

    @property
    def relative_change(self) -> float | None:
        return _change(self.page_views, self.previous_page_views)

    @property
    def has_data(self) -> bool:
        return self.measured_total is not None


def get_page_detail(
    *,
    path: str,
    start: date,
    end: date,
    previous_start: date | None = None,
    previous_end: date | None = None,
) -> WebsitePageDetail | None:
    """One page's figures, in one aggregate query over its own rows.

    `None` when the path has never been measured — absent rather than present
    with zeros, because nobody measuring a page is not a page nobody visited.
    """
    canonical = canonical_path(path)
    if not canonical:
        return None

    rows = current_pages().filter(path=canonical)
    window = Q(report_date__gte=start, report_date__lte=end)
    aggregates = {
        "views": Sum("page_views", filter=window),
        "total": Sum("page_views"),
        "seconds": Sum("user_engagement_seconds", filter=window),
        "views_with_seconds": Sum(
            "page_views", filter=window & Q(user_engagement_seconds__isnull=False)
        ),
        # The measured span of this page, across the whole of coverage rather
        # than the chosen window: it is what stops `measured_total` being read
        # as a lifetime figure for a page older than the collection.
        "first_seen": Min("report_date"),
        "last_seen": Max("report_date"),
        "days": Count("id", filter=window),
    }
    if previous_start is not None and previous_end is not None:
        aggregates["previous_views"] = Sum(
            "page_views", filter=Q(report_date__gte=previous_start, report_date__lte=previous_end)
        )

    totals = rows.aggregate(**aggregates)
    if totals["total"] is None:
        return None

    return WebsitePageDetail(
        path=canonical,
        page_views=totals["views"],
        previous_page_views=totals.get("previous_views"),
        measured_total=totals["total"],
        engagement_seconds=totals["seconds"],
        views_with_seconds=totals["views_with_seconds"],
        first_measured_on=totals["first_seen"],
        last_measured_on=totals["last_seen"],
        days_seen=totals["days"] or 0,
    )


__all__ = [
    "LANGUAGE_LABELS",
    "MATRIX_DRAWN_LIMIT",
    "MIN_CHANNEL_SESSIONS_FLOOR",
    "MIN_CHANNEL_SESSION_RATE_PER_DAY",
    "MIN_PAGE_VIEWS_FLOOR",
    "MIN_PAGE_VIEW_RATE_PER_DAY",
    "MIX_SECTIONS",
    "OTHER_LANGUAGE_KEY",
    "OTHER_SECTION_KEY",
    "OTHER_SECTION_LABEL",
    "QUADRANT_LABELS",
    "WEEKDAY_NAMES",
    "WEEKDAY_PATTERN_MIN_DAYS",
    "ChannelMovement",
    "ContentMixRow",
    "EngagementMatrix",
    "LanguageShare",
    "PageMovementResult",
    "WeekdayAverage",
    "WebsiteChannelPerformance",
    "WebsiteContentMix",
    "WebsiteLanguageMix",
    "WebsitePageDetail",
    "WebsitePageEngagement",
    "WebsitePageMovement",
    "WebsiteTrafficSummary",
    "get_channel_performance",
    "get_content_mix",
    "get_engagement_matrix",
    "get_language_mix",
    "get_page_detail",
    "get_page_movement",
    "get_traffic_summary",
    "get_weekday_pattern",
    "min_channel_sessions_for",
    "min_page_views_for",
    "rank_channel_movement",
]
