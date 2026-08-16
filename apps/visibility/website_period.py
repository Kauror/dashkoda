"""The measurement window every Koduleht figure is read through, and how much
of it was actually measured.

Three objects, and the order they depend on each other in is the whole design:

- :class:`WebsitePeriod` is a **request**. "Ninety days ending at the newest
  collected day" is a pair of dates, and it is resolved against coverage rather
  than against today — a chart that ran to today would end in a flat gap the
  width of however late the collector is;
- :class:`WebsitePeriodCoverage` is what was **measured** inside that request. A
  thirty-day window with twenty-two collected days is not a thirty-day window,
  and the difference has to be a value the interface can read rather than an
  inference from a chart's shape;
- :class:`WebsiteComparison` is the **preceding equal-length window**, plus the
  one verdict that matters: whether the two are comparable at all.

## Why comparison needs its own verdict

The tempting bug is to derive a previous period, sum both, and print the
difference. That produces `−19%` for a month where the collector missed eight
days, and nothing on the page looks wrong. So a delta is offered only when both
windows are adequately covered **at the grain the delta is about** — site
figures need daily snapshots, a content delta needs page detail, a channel delta
needs channel detail, and a day can carry the first without the other two.

`Kõik` has no previous period at all. The window before "everything there is"
is the period before measurement began, which is not a quiet period — it is an
unmeasured one, and a comparison against it would be a comparison against
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Count, Q

from apps.core.query_state import parse_iso_date

from .ga4_selectors import Coverage, current_days

#: The query parameters the window carries.
PARAM_PERIOD = "periood"
PARAM_FROM = "alates"
PARAM_TO = "kuni"

#: The key a custom range travels under. It is a preset like any other as far as
#: the control is concerned; what makes it different is that its length comes
#: from two dates rather than from the registry.
CUSTOM_KEY = "kohandatud"

#: The shortest custom range worth drawing. One day is a reading, not a period,
#: and every comparison below would be a comparison of two single days.
MIN_CUSTOM_DAYS = 2


@dataclass(frozen=True)
class PeriodPreset:
    """One offered window. `days` is `None` for "everything there is", the only
    option whose length is a property of the data rather than of the choice."""

    key: str
    label: str
    days: int | None

    @property
    def is_all(self) -> bool:
        return self.days is None


#: Kept in step with `apps.visibility.traffic_page.PERIODS` deliberately: a
#: reader moving between the old address and this one must not find the window
#: they chose renamed. The custom range is appended rather than replacing any of
#: them.
PERIOD_PRESETS: tuple[PeriodPreset, ...] = (
    PeriodPreset(key="30", label="30 päeva", days=30),
    PeriodPreset(key="90", label="90 päeva", days=90),
    PeriodPreset(key="1a", label="1 aasta", days=365),
    PeriodPreset(key="3a", label="3 aastat", days=3 * 365),
    PeriodPreset(key="5a", label="5 aastat", days=5 * 365),
    PeriodPreset(key="koik", label="Kõik", days=None),
)

DEFAULT_PRESET = PERIOD_PRESETS[0]

_PRESETS_BY_KEY = {preset.key: preset for preset in PERIOD_PRESETS}


@dataclass(frozen=True)
class WebsitePeriod:
    """One resolved measurement window.

    `start` and `end` are always inside coverage, so no caller has to remember
    to clamp. `is_all` and `is_custom` are what the comparison and the controls
    branch on; nothing else needs to know which preset produced the dates.
    """

    key: str
    label: str
    start: date | None
    end: date | None
    is_all: bool = False
    is_custom: bool = False
    #: Set when a custom range was asked for and could not be honoured — the
    #: control says so instead of silently showing a different window.
    custom_note: str = ""

    @property
    def has_window(self) -> bool:
        return self.start is not None and self.end is not None

    @property
    def days(self) -> int:
        """Calendar days the window spans, ends included."""
        if not self.has_window:
            return 0
        return (self.end - self.start).days + 1

    @property
    def range_label(self) -> str:
        if not self.has_window:
            return ""
        return f"{self.start:%d.%m.%Y}–{self.end:%d.%m.%Y}"

    @property
    def window_label(self) -> str:
        """The window as a section says it: `30 päeva · 17.07.2026–15.08.2026`.

        Sections on a long page are read far from the period control at the top,
        and a ranking whose window a reader has to scroll up to check is a
        ranking they will assume covers something else. Composed here so every
        section that states it states it the same way.
        """
        if not self.has_window:
            return ""
        return f"{self.label} · {self.range_label}"


def parse_period(
    raw: str | None,
    coverage: Coverage,
    *,
    raw_from: str | None = None,
    raw_to: str | None = None,
) -> WebsitePeriod:
    """The window asked for, resolved against what was measured. Never raises.

    A hand-typed key, a reversed pair of dates, a range entirely before
    collection began — each resolves to something queryable, because a rotted
    bookmark should render a page rather than a stack trace.
    """
    key = (raw or "").strip()

    if key == CUSTOM_KEY:
        return _custom_period(raw_from, raw_to, coverage)

    preset = _PRESETS_BY_KEY.get(key, DEFAULT_PRESET)
    if not coverage.has_data:
        return WebsitePeriod(
            key=preset.key, label=preset.label, start=None, end=None, is_all=preset.is_all
        )

    end = coverage.latest
    if preset.is_all:
        return WebsitePeriod(
            key=preset.key, label=preset.label, start=coverage.earliest, end=end, is_all=True
        )

    start = max(end - timedelta(days=preset.days - 1), coverage.earliest)
    return WebsitePeriod(key=preset.key, label=preset.label, start=start, end=end)


def _custom_period(raw_from: str | None, raw_to: str | None, coverage: Coverage) -> WebsitePeriod:
    """A reader-supplied range, clamped to what exists.

    Four things can be wrong with two date fields and none of them is an error
    page: either may be blank, they may be reversed, they may sit wholly outside
    coverage, or they may name one day. Each resolves, and where the resolved
    window is not the one asked for the control says so in `custom_note` rather
    than showing a different period under the reader's own dates.
    """
    label = "Kohandatud"
    if not coverage.has_data:
        return WebsitePeriod(key=CUSTOM_KEY, label=label, start=None, end=None, is_custom=True)

    asked_from = parse_iso_date(raw_from)
    asked_to = parse_iso_date(raw_to)
    if asked_from is None and asked_to is None:
        # Nothing usable was submitted. The default window is a better answer
        # than an empty page, and the note says which window is being shown.
        fallback = parse_period(DEFAULT_PRESET.key, coverage)
        return WebsitePeriod(
            key=CUSTOM_KEY,
            label=label,
            start=fallback.start,
            end=fallback.end,
            is_custom=True,
            custom_note=f"Kuupäevi ei antud, kuvatud {DEFAULT_PRESET.label.lower()}.",
        )

    # A half-filled pair means "from here to the end of what we have", or "up to
    # here from the beginning of it".
    start = asked_from or coverage.earliest
    end = asked_to or coverage.latest

    note = ""
    if start > end:
        # Reversed rather than invalid: somebody filled the fields in the order
        # they read them. Swapping is what they meant.
        start, end = end, start
        note = "Kuupäevad olid vastupidi ja vahetati."

    clamped_start = max(start, coverage.earliest)
    clamped_end = min(end, coverage.latest)

    if clamped_start > clamped_end:
        # The whole range sits outside coverage. Nothing was measured there, and
        # padding it with zeros would state something no source said.
        fallback = parse_period(DEFAULT_PRESET.key, coverage)
        return WebsitePeriod(
            key=CUSTOM_KEY,
            label=label,
            start=fallback.start,
            end=fallback.end,
            is_custom=True,
            custom_note=(
                f"Valitud vahemikus mõõtmisandmed puuduvad, kuvatud {DEFAULT_PRESET.label.lower()}."
            ),
        )

    if (clamped_start, clamped_end) != (start, end):
        note = (
            f"Vahemik piiratud mõõdetud andmetega: {clamped_start:%d.%m.%Y}–{clamped_end:%d.%m.%Y}."
        )

    if (clamped_end - clamped_start).days + 1 < MIN_CUSTOM_DAYS:
        # One day is a reading, not a period. Widen by a day inside coverage so
        # the window has a shape, and say so.
        if clamped_end < coverage.latest:
            clamped_end = clamped_end + timedelta(days=1)
        elif clamped_start > coverage.earliest:
            clamped_start = clamped_start - timedelta(days=1)
        note = "Lühim vahemik on kaks päeva."

    return WebsitePeriod(
        key=CUSTOM_KEY,
        label=label,
        start=clamped_start,
        end=clamped_end,
        is_custom=True,
        custom_note=note,
    )


@dataclass(frozen=True)
class PeriodOption:
    """One period button: what it says, whether it is shown, whether history can
    fill it.

    An option history cannot fill is **disabled rather than removed**. A board
    member who looks for "5 aastat" and finds no such button learns nothing; one
    who finds it disabled learns the Chamber has been measuring for less than
    five years, which is the answer to the question they were asking.
    """

    key: str
    label: str
    is_active: bool
    is_offered: bool
    query: str = ""


@dataclass(frozen=True)
class WebsitePeriodCoverage:
    """How much of one requested interval was actually measured.

    Three completeness questions, not one, because a collected day can carry the
    site figures and neither detail set. A content ranking drawn over days that
    have no page rows is a ranking of the days that happen to have them.

    Every count is over **current revisions only**: a superseded reading of a day
    is provenance, not arithmetic.
    """

    start: date | None
    end: date | None
    expected_days: int
    snapshot_days: int
    days_with_page_detail: int
    days_with_channel_detail: int
    #: Per-metric completeness. A day may exist while one nullable figure is
    #: absent, and summing over it must not read that absence as a zero.
    days_with_sessions: int
    days_with_page_views: int
    days_with_engaged_sessions: int
    days_with_engagement_seconds: int
    missing_dates: tuple[date, ...] = ()

    @property
    def missing_count(self) -> int:
        return len(self.missing_dates)

    @property
    def has_days(self) -> bool:
        return self.snapshot_days > 0

    def _ratio(self, measured: int) -> float:
        if self.expected_days <= 0:
            return 0.0
        return measured / self.expected_days

    @property
    def site_ratio(self) -> float:
        return self._ratio(self.snapshot_days)

    @property
    def page_ratio(self) -> float:
        return self._ratio(self.days_with_page_detail)

    @property
    def channel_ratio(self) -> float:
        return self._ratio(self.days_with_channel_detail)

    @property
    def is_site_complete(self) -> bool:
        return self.expected_days > 0 and self.snapshot_days >= self.expected_days

    @property
    def is_page_complete(self) -> bool:
        return self.expected_days > 0 and self.days_with_page_detail >= self.expected_days

    @property
    def is_channel_complete(self) -> bool:
        return self.expected_days > 0 and self.days_with_channel_detail >= self.expected_days

    @property
    def is_engagement_complete(self) -> bool:
        """Whether every collected day carries an engagement-seconds reading.

        Its own question: the metric is nullable, and a period that sums it over
        the days that have it while dividing by every day's sessions would
        under-report the average without anything looking wrong.
        """
        return self.snapshot_days > 0 and self.days_with_engagement_seconds >= self.snapshot_days


def get_period_coverage(start: date | None, end: date | None) -> WebsitePeriodCoverage:
    """Coverage for one interval, in one aggregate query.

    `missing_dates` is computed from the same read rather than by a second
    query: the collected dates are already in hand, and the expected ones are
    arithmetic.
    """
    if start is None or end is None or start > end:
        return WebsitePeriodCoverage(
            start=start,
            end=end,
            expected_days=0,
            snapshot_days=0,
            days_with_page_detail=0,
            days_with_channel_detail=0,
            days_with_sessions=0,
            days_with_page_views=0,
            days_with_engaged_sessions=0,
            days_with_engagement_seconds=0,
        )

    expected = (end - start).days + 1
    rows = current_days().filter(report_date__gte=start, report_date__lte=end)
    counts = rows.aggregate(
        days=Count("id"),
        with_pages=Count("id", filter=Q(has_page_detail=True)),
        with_channels=Count("id", filter=Q(has_channel_detail=True)),
        with_sessions=Count("id", filter=Q(sessions__isnull=False)),
        with_views=Count("id", filter=Q(page_views__isnull=False)),
        with_engaged=Count("id", filter=Q(engaged_sessions__isnull=False)),
        with_seconds=Count("id", filter=Q(user_engagement_seconds__isnull=False)),
    )

    collected = set(rows.values_list("report_date", flat=True))
    missing = tuple(
        start + timedelta(days=offset)
        for offset in range(expected)
        if (start + timedelta(days=offset)) not in collected
    )

    return WebsitePeriodCoverage(
        start=start,
        end=end,
        expected_days=expected,
        snapshot_days=counts["days"] or 0,
        days_with_page_detail=counts["with_pages"] or 0,
        days_with_channel_detail=counts["with_channels"] or 0,
        days_with_sessions=counts["with_sessions"] or 0,
        days_with_page_views=counts["with_views"] or 0,
        days_with_engaged_sessions=counts["with_engaged"] or 0,
        days_with_engagement_seconds=counts["with_seconds"] or 0,
        missing_dates=missing,
    )


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

#: How much of a window must be measured before a delta drawn from it is
#: offered. Below this the two windows are describing different amounts of time
#: and their difference is mostly the collector's gaps.
MIN_COMPARISON_COVERAGE = 0.9

#: And how far apart the two windows' coverage may be. Two periods each missing
#: their own eight percent are comparable; a complete one against a
#: three-quarters-covered one is not, however well each scores alone.
MAX_COMPARISON_SKEW = 0.05


@dataclass(frozen=True)
class WebsiteComparison:
    """The window immediately before the chosen one, and whether it may be used.

    Equal length and non-overlapping by construction. `is_available` says a
    previous window exists inside coverage at all; the three `can_compare_*`
    verdicts say whether a delta at that grain would mean anything.
    """

    start: date | None
    end: date | None
    coverage: WebsitePeriodCoverage
    current_coverage: WebsitePeriodCoverage
    is_available: bool = False
    #: Why a comparison is not offered, for the interface to print instead of a
    #: delta. Empty when one is.
    unavailable_reason: str = ""

    @property
    def range_label(self) -> str:
        if self.start is None or self.end is None:
            return ""
        return f"{self.start:%d.%m.%Y}–{self.end:%d.%m.%Y}"

    def _comparable(self, current: float, previous: float) -> bool:
        if not self.is_available:
            return False
        if current < MIN_COMPARISON_COVERAGE or previous < MIN_COMPARISON_COVERAGE:
            return False
        return abs(current - previous) <= MAX_COMPARISON_SKEW

    @property
    def can_compare_site(self) -> bool:
        return self._comparable(self.current_coverage.site_ratio, self.coverage.site_ratio)

    @property
    def can_compare_pages(self) -> bool:
        return self._comparable(self.current_coverage.page_ratio, self.coverage.page_ratio)

    @property
    def can_compare_channels(self) -> bool:
        return self._comparable(self.current_coverage.channel_ratio, self.coverage.channel_ratio)


def build_comparison(
    period: WebsitePeriod, coverage: Coverage, current_coverage: WebsitePeriodCoverage
) -> WebsiteComparison:
    """The preceding equal-length window, with its own coverage read.

    Deliberately **not** shortened to fit. A previous period trimmed at the
    start of collection and still labelled "eelmine 30 päeva" is the exact
    comparison this module exists to refuse: fewer days measured fewer sessions,
    and the resulting fall is the collector's history rather than the Chamber's.
    """
    empty = WebsitePeriodCoverage(
        start=None,
        end=None,
        expected_days=0,
        snapshot_days=0,
        days_with_page_detail=0,
        days_with_channel_detail=0,
        days_with_sessions=0,
        days_with_page_views=0,
        days_with_engaged_sessions=0,
        days_with_engagement_seconds=0,
    )

    if not period.has_window:
        return WebsiteComparison(
            start=None,
            end=None,
            coverage=empty,
            current_coverage=current_coverage,
            unavailable_reason="Mõõtmisandmed puuduvad.",
        )

    if period.is_all:
        return WebsiteComparison(
            start=None,
            end=None,
            coverage=empty,
            current_coverage=current_coverage,
            unavailable_reason="Kogu ajaloole eelnevat perioodi ei ole.",
        )

    days = period.days
    previous_end = period.start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)

    if coverage.earliest is None or previous_start < coverage.earliest:
        return WebsiteComparison(
            start=previous_start,
            end=previous_end,
            coverage=empty,
            current_coverage=current_coverage,
            unavailable_reason="Eelnev võrdlusperiood jääb mõõtmise algusest varasemaks.",
        )

    return WebsiteComparison(
        start=previous_start,
        end=previous_end,
        coverage=get_period_coverage(previous_start, previous_end),
        current_coverage=current_coverage,
        is_available=True,
    )


__all__ = [
    "CUSTOM_KEY",
    "DEFAULT_PRESET",
    "MAX_COMPARISON_SKEW",
    "MIN_COMPARISON_COVERAGE",
    "MIN_CUSTOM_DAYS",
    "PARAM_FROM",
    "PARAM_PERIOD",
    "PARAM_TO",
    "PERIOD_PRESETS",
    "PeriodOption",
    "PeriodPreset",
    "WebsiteComparison",
    "WebsitePeriod",
    "WebsitePeriodCoverage",
    "build_comparison",
    "get_period_coverage",
    "parse_period",
]
