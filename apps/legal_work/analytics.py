"""Analytical read paths for the Õigusloome intelligence dashboard.

Everything here reads the **current** :class:`LegalWorkSnapshot` and groups its
rows by their own event dates. A snapshot is a complete revision of the register,
not one year's population, so historical totals are never assembled by adding
snapshots together: that would count the same legislative matter once per
revision. `selectors.py` owns "currently open" and "latest sent"; this module
owns the aggregates and never redefines either.

Four rules hold throughout, and each of them is a metric that would otherwise be
quietly wrong:

- **The cutoff is the snapshot's `reporting_date`, never today.** The workbook is
  regenerated in the morning and read all day; counting "this year" against the
  wall clock would move a published figure between two page loads and would count
  a year-to-date period the data does not cover.
- **A year-on-year comparison uses the same calendar cutoff in both years.** The
  current year is incomplete, so comparing it with a finished one measures the
  calendar rather than the work.
- **Missing is not zero.** An absent date contributes to no bucket and an absent
  count is `None`. A row is dropped from a statistic it cannot support and
  reported as missing coverage instead of being repaired.
- **Aggregation happens in PostgreSQL.** Medians read one bounded column into
  Python because PostgreSQL's percentile support is awkward through the ORM;
  nothing iterates rows to issue another query.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

from django.db.models import Count, F, Q, QuerySet, Sum
from django.db.models.functions import ExtractMonth, ExtractYear

from .models import LegalWorkItem, LegalWorkSnapshot, SentStatus

# --------------------------------------------------------------------------
# Definitions that the rest of the dashboard must not restate
# --------------------------------------------------------------------------

#: How a legislative matter is assigned to a year for *volume* questions.
#:
#: The operational workbook is organised as one sheet per year and `source_row`
#: is the row number inside that sheet, so `source_year` is literally the sheet a
#: matter belongs to — the register's own statement of which year's work it is.
#: A matter received in December 2025 and worked through 2026 sits on the 2026
#: sheet, so `source_year` and `received_date.year` genuinely disagree for a
#: minority of rows. That is a real property of the register, not a defect, and
#: it is why `Uued teemad kuude lõikes` uses `received_date` instead.
ANNUAL_MEMBERSHIP_FIELD = "source_year"

#: How a matter is assigned to a year for *response-window* questions.
#:
#: The interval measured is `deadline_date - received_date`, and both endpoints
#: come from the row's own dates, so the cohort year is the year the matter was
#: actually received. Grouping this by `source_year` instead would put a
#: consultation that opened in December 2025 into the 2026 cohort while the days
#: it measures were all spent in 2025.
RESPONSE_WINDOW_YEAR_FIELD = "received_date"

#: Shown instead of an empty stage. An active matter with no recorded stage is
#: still active work and must stay inside the total the stage chart reconciles
#: to, so it is named rather than dropped.
UNKNOWN_STAGE_LABEL = "Määramata"

#: Mutually exclusive deadline bands, in days remaining from the reporting date.
#: Cumulative bands ("within 7", "within 14") would double-count the same matter
#: in every wider band, so the upper bound of each is exclusive of the next.
DEADLINE_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("0–3 päeva", 0, 3),
    ("4–7 päeva", 4, 7),
    ("8–14 päeva", 8, 14),
    ("15–21 päeva", 15, 21),
    ("Hiljem", 22, None),
)

#: Age bands for open matters, in days since arrival. Chosen against the real
#: distribution: the register's median consultation window is around a fortnight
#: while EU files stay open for years, so the bands are fine at the short end and
#: coarse at the long one.
AGE_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("Alla 30 päeva", 0, 29),
    ("30–90 päeva", 30, 90),
    ("91–180 päeva", 91, 180),
    ("181–365 päeva", 181, 365),
    ("1–2 aastat", 366, 730),
    ("Üle 2 aasta", 731, None),
)

#: Response-window bands. The register's median sits at 15–16 days across every
#: profiled year with a tail beyond 90, so the first three bands split the bulk
#: of the distribution and the last two carry the tail.
RESPONSE_WINDOW_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("0–7 päeva", 0, 7),
    ("8–14 päeva", 8, 14),
    ("15–21 päeva", 15, 21),
    ("22–30 päeva", 22, 30),
    ("Üle 30 päeva", 31, None),
)

#: The neutral short-window threshold. Fourteen days is not a legal standard and
#: is not presented as one — it is the boundary the register's own distribution
#: puts just below its median, so "kuni 14 päeva" separates the shorter half of
#: consultations from the longer one. Nothing in the interface calls it too short.
SHORT_WINDOW_DAYS = 14

#: Below this many eligible matters a comparative statistic is withheld rather
#: than drawn. A median response window computed from three matters is not
#: comparable with one computed from ninety, and a ranking built out of such
#: figures invents differences between institutions.
MIN_COMPARISON_SAMPLE = 10

#: How many categories a ranking draws before the remainder is summarised.
TOP_CATEGORY_LIMIT = 10


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def median_days(values) -> float | None:
    """The one median in this domain.

    Every median the dashboard shows — response window, active age, feedback per
    topic — comes through here, so the annual chart, the recipient table and the
    feedback view cannot disagree about what a median is. The standard library
    is enough for cohorts this size; NumPy would be a dependency bought for one
    function.
    """
    ordered = [value for value in values if value is not None]
    if not ordered:
        return None
    return float(statistics.median(ordered))


def mean_days(values) -> float | None:
    ordered = [value for value in values if value is not None]
    if not ordered:
        return None
    return float(statistics.fmean(ordered))


def same_date_last_year(reporting_date: dt.date) -> dt.date:
    """The equivalent calendar cutoff one year earlier.

    29 February has no counterpart in an ordinary year. Rather than letting
    `replace()` raise — or silently sliding to 1 March, which would count a day
    the current year has not reached — the cutoff is pinned to 28 February, the
    last day the previous year actually shares with this one.
    """
    try:
        return reporting_date.replace(year=reporting_date.year - 1)
    except ValueError:
        return dt.date(reporting_date.year - 1, 2, 28)


def _items(snapshot: LegalWorkSnapshot | None) -> QuerySet[LegalWorkItem]:
    if snapshot is None:
        return LegalWorkItem.objects.none()
    return LegalWorkItem.objects.filter(snapshot=snapshot)


def _sent(snapshot: LegalWorkSnapshot | None) -> QuerySet[LegalWorkItem]:
    """Rows that count as an opinion Koda sent.

    The authoritative fact is the canonical status plus the date that proves it.
    A document existing in some catalogue is evidence *about* a matter and never
    the reason it counts here.
    """
    return _items(snapshot).filter(sent_status=SentStatus.SENT, sent_date__isnull=False)


def _band_for(value: int, bands) -> str:
    for label, low, high in bands:
        if value >= low and (high is None or value <= high):
            return label
    return bands[-1][0]


def _empty_band_counts(bands) -> dict[str, int]:
    return {label: 0 for label, _low, _high in bands}


# --------------------------------------------------------------------------
# Headline volumes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class YearOnYear:
    """A current-year figure beside the same calendar period a year earlier."""

    current: int
    previous: int
    current_cutoff: dt.date
    previous_cutoff: dt.date

    @property
    def absolute_change(self) -> int:
        return self.current - self.previous

    @property
    def percent_change(self) -> float | None:
        """`None` when the baseline is zero.

        A change from nothing is not an infinite percentage and not a hundred
        per cent; the absolute delta is the honest statement and the caller
        renders a dash instead of a ratio.
        """
        if self.previous == 0:
            return None
        return (self.current - self.previous) / self.previous * 100.0

    @property
    def direction(self) -> str:
        if self.absolute_change == 0:
            return "flat"
        return "up" if self.absolute_change > 0 else "down"


def count_topics_for_year(snapshot: LegalWorkSnapshot | None, year: int) -> int:
    """Matters belonging to `year` by the register's own annual grouping.

    This is a stock of work, not an arrival count: it answers "how much is the
    {year} file" and deliberately includes matters that arrived late in the
    previous December.
    """
    return _items(snapshot).filter(**{ANNUAL_MEMBERSHIP_FIELD: year}).count()


def count_sent_in_year(
    snapshot: LegalWorkSnapshot | None, year: int, *, cutoff: dt.date | None = None
) -> int:
    """Opinions sent during `year`, optionally clamped to a calendar cutoff.

    The clamp is what makes a year-to-date figure honest. Without it the current
    year would include a send dated after the reporting date — the workbook does
    carry the occasional future date — and the comparison year would silently
    cover twelve months against the current year's seven.
    """
    queryset = _sent(snapshot).filter(sent_date__year=year)
    if cutoff is not None:
        queryset = queryset.filter(sent_date__lte=cutoff)
    return queryset.count()


def sent_year_on_year(snapshot: LegalWorkSnapshot | None) -> YearOnYear | None:
    """`Arvamuste muutus võrreldes eelmise aastaga`, compared fairly.

    Both sides run from 1 January to the same day of the year. Comparing a
    part-year against a finished one is the single easiest way to make this
    dashboard lie, and it would understate the current year for eleven months
    of every twelve.
    """
    if snapshot is None:
        return None
    current_cutoff = snapshot.reporting_date
    previous_cutoff = same_date_last_year(current_cutoff)
    return YearOnYear(
        current=count_sent_in_year(snapshot, current_cutoff.year, cutoff=current_cutoff),
        previous=count_sent_in_year(snapshot, previous_cutoff.year, cutoff=previous_cutoff),
        current_cutoff=current_cutoff,
        previous_cutoff=previous_cutoff,
    )


def topics_year_on_year(snapshot: LegalWorkSnapshot | None) -> YearOnYear | None:
    """New arrivals to the same date last year, by `received_date`.

    Deliberately *not* a comparison of `source_year` populations: those are
    stocks that keep growing until the year closes, so a same-date clamp cannot
    be applied to them. Arrivals are datable events, so they can be compared
    fairly, and this is the only annual topic figure that may carry a delta.
    """
    if snapshot is None:
        return None
    current_cutoff = snapshot.reporting_date
    previous_cutoff = same_date_last_year(current_cutoff)
    received = _items(snapshot).filter(received_date__isnull=False)
    return YearOnYear(
        current=received.filter(
            received_date__year=current_cutoff.year, received_date__lte=current_cutoff
        ).count(),
        previous=received.filter(
            received_date__year=previous_cutoff.year, received_date__lte=previous_cutoff
        ).count(),
        current_cutoff=current_cutoff,
        previous_cutoff=previous_cutoff,
    )


# --------------------------------------------------------------------------
# Stage distribution
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StageCount:
    label: str
    stage_key: str
    count: int


@dataclass(frozen=True)
class StageBreakdown:
    """Where the currently open matters sit in the legislative process.

    `total` is the open count itself rather than the sum of the drawn bars, and
    a test asserts the two agree. If a row could ever fall out of the grouping,
    the chart would quietly describe fewer matters than the headline claims.
    """

    stages: tuple[StageCount, ...]
    total: int

    @property
    def largest_share(self) -> float | None:
        if not self.stages or self.total == 0:
            return None
        return self.stages[0].count / self.total * 100.0


def stage_breakdown(snapshot: LegalWorkSnapshot | None) -> StageBreakdown:
    """Open matters grouped by the workbook's normalised stage key.

    Grouping is on `stage_key` because that is the source's own normalised form;
    the label drawn is the `stage` text a lawyer actually wrote. Nothing here
    merges two stages because their strings look similar — the vocabulary is
    free text, it gains entries between one workbook and the next, and a fuzzy
    merge would silently combine two different points in the process.
    """
    items = _items(snapshot).filter(is_open=True)
    total = items.count()

    # One grouped query. The representative label is the most common `stage`
    # spelling inside each key, resolved in Python from the same rows rather
    # than by a second query per group.
    rows = (
        items.values("stage_key", "stage")
        .annotate(count=Count("id"))
        .order_by("stage_key", "-count", "stage")
    )

    by_key: dict[str, dict] = {}
    for row in rows:
        key = (row["stage_key"] or "").strip()
        label = (row["stage"] or "").strip()
        entry = by_key.setdefault(key, {"count": 0, "label": ""})
        entry["count"] += row["count"]
        # The first spelling seen for a key is the most common one, because the
        # queryset is ordered by descending count inside the key.
        if not entry["label"] and label:
            entry["label"] = label

    stages = tuple(
        sorted(
            (
                StageCount(
                    label=entry["label"] or UNKNOWN_STAGE_LABEL,
                    stage_key=key,
                    count=entry["count"],
                )
                for key, entry in by_key.items()
            ),
            # Count first, as the chart promises. The unknown bucket loses ties
            # so it does not sit between two named stages purely on alphabet —
            # but it is not pinned to the bottom either: if most active work
            # were unstaged, that is the first thing a reader should see.
            key=lambda stage: (-stage.count, stage.stage_key == "", stage.label),
        )
    )
    return StageBreakdown(stages=stages, total=total)


# --------------------------------------------------------------------------
# Monthly flow
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthlyFlow:
    """Twelve months of one measure, for one year.

    `counts` is dense over the months the year actually reached: a month with no
    activity is a measured zero and belongs on the chart, while a month the data
    has not arrived at yet is absent entirely. Drawing an empty December in
    August would read as a collapse.
    """

    year: int
    counts: tuple[int, ...]
    complete_through_month: int
    partial_month: int | None
    missing_date_count: int = 0

    @property
    def total(self) -> int:
        return sum(self.counts)


def _monthly_counts(
    queryset: QuerySet[LegalWorkItem], field_name: str, year: int, *, upper: dt.date | None
) -> dict[int, int]:
    queryset = queryset.filter(**{f"{field_name}__year": year, f"{field_name}__isnull": False})
    if upper is not None:
        queryset = queryset.filter(**{f"{field_name}__lte": upper})
    rows = (
        queryset.annotate(month=ExtractMonth(field_name))
        .values("month")
        .annotate(count=Count("id"))
        .order_by("month")
    )
    return {row["month"]: row["count"] for row in rows}


def monthly_new_topics(snapshot: LegalWorkSnapshot | None, year: int) -> MonthlyFlow:
    """`Uued teemad kuude lõikes`, by arrival date.

    A month is a question about when something happened, so it is answered by
    `received_date` and never by `source_year`. A row whose arrival date is
    missing therefore lands in no month at all; it is counted in
    `missing_date_count` and reported beside the chart rather than being
    assigned to January.
    """
    if snapshot is None:
        return MonthlyFlow(year=year, counts=(), complete_through_month=0, partial_month=None)

    reporting_date = snapshot.reporting_date
    upper = reporting_date if year == reporting_date.year else None
    counts = _monthly_counts(_items(snapshot), "received_date", year, upper=upper)

    missing = (
        _items(snapshot)
        .filter(**{ANNUAL_MEMBERSHIP_FIELD: year}, received_date__isnull=True)
        .count()
    )
    return _flow(year, counts, reporting_date, missing_date_count=missing)


def monthly_sent_opinions(snapshot: LegalWorkSnapshot | None, year: int) -> MonthlyFlow:
    """`Välja saadetud arvamused kuude lõikes`.

    The canonical contract requires a `sent_date` on every sent record, so these
    monthly buckets add up to the annual sent figure exactly. A test asserts it,
    because a silent disagreement between the two would mean one of them had
    stopped using the sent status.
    """
    if snapshot is None:
        return MonthlyFlow(year=year, counts=(), complete_through_month=0, partial_month=None)

    reporting_date = snapshot.reporting_date
    upper = reporting_date if year == reporting_date.year else None
    counts = _monthly_counts(_sent(snapshot), "sent_date", year, upper=upper)
    return _flow(year, counts, reporting_date)


def _flow(
    year: int,
    counts: dict[int, int],
    reporting_date: dt.date,
    *,
    missing_date_count: int = 0,
) -> MonthlyFlow:
    if year == reporting_date.year:
        last_month = reporting_date.month
        partial = last_month
        # A reporting date on the last day of its month describes a whole month.
        next_month_first = (
            dt.date(year + 1, 1, 1) if last_month == 12 else dt.date(year, last_month + 1, 1)
        )
        if reporting_date == next_month_first - dt.timedelta(days=1):
            partial = None
    else:
        last_month = 12
        partial = None

    return MonthlyFlow(
        year=year,
        counts=tuple(counts.get(month, 0) for month in range(1, last_month + 1)),
        complete_through_month=last_month if partial is None else last_month - 1,
        partial_month=partial,
        missing_date_count=missing_date_count,
    )


# --------------------------------------------------------------------------
# Annual series
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AnnualPoint:
    year: int
    count: int
    is_partial: bool


def annual_sent_opinions(snapshot: LegalWorkSnapshot | None) -> tuple[AnnualPoint, ...]:
    """`Välja saadetud arvamused aastate lõikes`.

    Grouped by the year each opinion was actually sent, from the current
    snapshot alone. The current year is marked partial so nothing invites a
    reader to compare seven months against twelve by eye.
    """
    if snapshot is None:
        return ()
    reporting_date = snapshot.reporting_date
    rows = (
        _sent(snapshot)
        .filter(sent_date__lte=reporting_date)
        .annotate(year=ExtractYear("sent_date"))
        .values("year")
        .annotate(count=Count("id"))
        .order_by("year")
    )
    return tuple(
        AnnualPoint(
            year=row["year"],
            count=row["count"],
            is_partial=row["year"] == reporting_date.year,
        )
        for row in rows
    )


def annual_topics(snapshot: LegalWorkSnapshot | None) -> tuple[AnnualPoint, ...]:
    """`Teemad aastate lõikes`, by the register's annual grouping."""
    if snapshot is None:
        return ()
    rows = (
        _items(snapshot)
        .values(ANNUAL_MEMBERSHIP_FIELD)
        .annotate(count=Count("id"))
        .order_by(ANNUAL_MEMBERSHIP_FIELD)
    )
    reporting_year = snapshot.reporting_date.year
    return tuple(
        AnnualPoint(
            year=row[ANNUAL_MEMBERSHIP_FIELD],
            count=row["count"],
            is_partial=row[ANNUAL_MEMBERSHIP_FIELD] == reporting_year,
        )
        for row in rows
    )


# --------------------------------------------------------------------------
# Response window
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ResponseWindowYear:
    """One year's consultation-window statistics, with its own denominator.

    `eligible` and `excluded_*` travel with the figures because an average from
    four matters and one from a hundred and eighty are not equally informative,
    and a reader cannot tell them apart from the line alone.
    """

    year: int
    eligible: int
    median: float | None
    mean: float | None
    missing_dates: int
    invalid_interval: int
    is_partial: bool


def _response_window_rows(snapshot: LegalWorkSnapshot | None):
    """Every row that can support a response-window measurement.

    Eligibility is explicit: both dates present and the deadline not before the
    arrival. A negative interval is a source-quality problem, so it is excluded
    and counted — never made positive with an absolute value, and never repaired
    to zero.
    """
    return (
        _items(snapshot)
        .filter(
            received_date__isnull=False,
            deadline_date__isnull=False,
            deadline_date__gte=F("received_date"),
        )
        .values_list(RESPONSE_WINDOW_YEAR_FIELD, "deadline_date")
    )


def response_window_by_year(
    snapshot: LegalWorkSnapshot | None,
) -> tuple[ResponseWindowYear, ...]:
    """`Arvamuse esitamiseks antud keskmine aeg`, as both median and mean.

    Both are kept because the distribution has a long right tail — a handful of
    three-month consultations pull the mean well above the typical matter — so
    the mean alone would overstate how much time a lawyer usually gets.
    """
    if snapshot is None:
        return ()

    by_year: dict[int, list[int]] = {}
    for received_date, deadline_date in _response_window_rows(snapshot):
        by_year.setdefault(received_date.year, []).append((deadline_date - received_date).days)

    # The two exclusion reasons, counted per cohort year in one query each so a
    # year's denominator can be stated rather than implied.
    missing = _year_counts(
        _items(snapshot).filter(
            Q(received_date__isnull=True) | Q(deadline_date__isnull=True),
        ),
        fallback_field=ANNUAL_MEMBERSHIP_FIELD,
    )
    invalid = _year_counts(
        _items(snapshot).filter(
            received_date__isnull=False,
            deadline_date__isnull=False,
            deadline_date__lt=F("received_date"),
        ),
        fallback_field=None,
    )

    reporting_year = snapshot.reporting_date.year
    years = sorted(set(by_year) | set(missing) | set(invalid))
    return tuple(
        ResponseWindowYear(
            year=year,
            eligible=len(by_year.get(year, ())),
            median=median_days(by_year.get(year, ())),
            mean=mean_days(by_year.get(year, ())),
            missing_dates=missing.get(year, 0),
            invalid_interval=invalid.get(year, 0),
            is_partial=year == reporting_year,
        )
        for year in years
    )


def _year_counts(
    queryset: QuerySet[LegalWorkItem], *, fallback_field: str | None
) -> dict[int, int]:
    """Count rows per cohort year.

    Rows with a received date are grouped by it, to match the cohort definition.
    Rows without one cannot be, so when a fallback field is named they are
    grouped by the register's annual field instead — that is the only year such
    a row has, and dropping it would understate the missing-data count for the
    very years it describes.
    """
    dated = (
        queryset.filter(received_date__isnull=False)
        .annotate(year=ExtractYear("received_date"))
        .values("year")
        .annotate(count=Count("id"))
    )
    counts: dict[int, int] = {row["year"]: row["count"] for row in dated}
    if fallback_field is not None:
        undated = (
            queryset.filter(received_date__isnull=True)
            .values(fallback_field)
            .annotate(count=Count("id"))
        )
        for row in undated:
            year = row[fallback_field]
            counts[year] = counts.get(year, 0) + row["count"]
    return counts


@dataclass(frozen=True)
class ResponseWindowDistribution:
    bands: tuple[tuple[str, int], ...]
    eligible: int
    short_window_count: int
    median: float | None

    @property
    def short_window_share(self) -> float | None:
        if self.eligible == 0:
            return None
        return self.short_window_count / self.eligible * 100.0


def response_window_distribution(
    snapshot: LegalWorkSnapshot | None, *, year: int | None = None
) -> ResponseWindowDistribution:
    """How often the Chamber gets a short consultation window.

    Answers a question the annual average cannot: an unchanged mean can hide a
    move from steady fortnights to a mix of three-day and two-month windows.
    """
    counts = _empty_band_counts(RESPONSE_WINDOW_BANDS)
    values: list[int] = []
    for received_date, deadline_date in _response_window_rows(snapshot):
        if year is not None and received_date.year != year:
            continue
        days = (deadline_date - received_date).days
        values.append(days)
        counts[_band_for(days, RESPONSE_WINDOW_BANDS)] += 1

    return ResponseWindowDistribution(
        bands=tuple((label, counts[label]) for label, _low, _high in RESPONSE_WINDOW_BANDS),
        eligible=len(values),
        short_window_count=sum(1 for value in values if value <= SHORT_WINDOW_DAYS),
        median=median_days(values),
    )


@dataclass(frozen=True)
class SentByDeadline:
    """Whether sent opinions carried a date on or before the stated deadline.

    Descriptive only. Deadlines are negotiated, dates are revised in the source
    afterwards, and some opinions are deliberately submitted late — so this is
    never presented as on-time performance or as a measure of anybody's work.
    """

    eligible: int
    on_or_before: int
    after: int
    median_days_before: float | None

    @property
    def share_on_or_before(self) -> float | None:
        if self.eligible == 0:
            return None
        return self.on_or_before / self.eligible * 100.0


def sent_by_deadline(snapshot: LegalWorkSnapshot | None) -> SentByDeadline:
    """Population: sent rows that state a deadline. Nothing else can be judged."""
    rows = (
        _sent(snapshot)
        .filter(deadline_date__isnull=False)
        .values_list("sent_date", "deadline_date")
    )
    margins = [(deadline - sent).days for sent, deadline in rows]
    on_or_before = sum(1 for margin in margins if margin >= 0)
    return SentByDeadline(
        eligible=len(margins),
        on_or_before=on_or_before,
        after=len(margins) - on_or_before,
        median_days_before=median_days(margins),
    )


# --------------------------------------------------------------------------
# Active work: age and deadline pressure
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ActiveAge:
    bands: tuple[tuple[str, int], ...]
    median: float | None
    measured: int
    missing_received_date: int
    future_received_date: int


def active_topic_age(snapshot: LegalWorkSnapshot | None) -> ActiveAge:
    """How long the open matters have been open, measured from the reporting date.

    Titled as age rather than as delay: a European file legitimately stays open
    for years, and calling that stagnation would misread the process. A received
    date after the reporting date is a known source anomaly — it would produce a
    negative age — so it is excluded and counted instead of being corrected.
    """
    if snapshot is None:
        return ActiveAge(
            bands=tuple((label, 0) for label, _low, _high in AGE_BANDS),
            median=None,
            measured=0,
            missing_received_date=0,
            future_received_date=0,
        )

    reporting_date = snapshot.reporting_date
    open_items = _items(snapshot).filter(is_open=True)

    counts = _empty_band_counts(AGE_BANDS)
    ages: list[int] = []
    for (received_date,) in open_items.filter(
        received_date__isnull=False, received_date__lte=reporting_date
    ).values_list("received_date"):
        age = (reporting_date - received_date).days
        ages.append(age)
        counts[_band_for(age, AGE_BANDS)] += 1

    return ActiveAge(
        bands=tuple((label, counts[label]) for label, _low, _high in AGE_BANDS),
        median=median_days(ages),
        measured=len(ages),
        missing_received_date=open_items.filter(received_date__isnull=True).count(),
        future_received_date=open_items.filter(received_date__gt=reporting_date).count(),
    )


@dataclass(frozen=True)
class DeadlinePressure:
    """Deadlines ahead, plus the two genuinely different kinds of passed one."""

    bands: tuple[tuple[str, int], ...]
    due_within_7: int
    overdue_pending: int
    overdue_already_sent: int
    without_deadline: int

    @property
    def upcoming_total(self) -> int:
        return sum(count for _label, count in self.bands)


def deadline_pressure(snapshot: LegalWorkSnapshot | None) -> DeadlinePressure:
    """Where deadline load is building among the open matters.

    A passed deadline is split in two, because they are not the same fact. A
    matter still awaiting its opinion is outstanding work; a matter whose opinion
    already went out and which remains open — waiting on a committee, waiting to
    come into force — is not late at all, and labelling it so would manufacture
    a backlog out of ordinary process.
    """
    if snapshot is None:
        return DeadlinePressure(
            bands=tuple((label, 0) for label, _low, _high in DEADLINE_BANDS),
            due_within_7=0,
            overdue_pending=0,
            overdue_already_sent=0,
            without_deadline=0,
        )

    reporting_date = snapshot.reporting_date
    open_items = _items(snapshot).filter(is_open=True)

    counts = _empty_band_counts(DEADLINE_BANDS)
    for (deadline_date,) in open_items.filter(
        deadline_date__isnull=False, deadline_date__gte=reporting_date
    ).values_list("deadline_date"):
        counts[_band_for((deadline_date - reporting_date).days, DEADLINE_BANDS)] += 1

    passed = open_items.filter(deadline_date__isnull=False, deadline_date__lt=reporting_date)
    # Derived from the band definitions rather than from their labels, so
    # rewording a band cannot silently change what "within a week" counts.
    within_7 = sum(
        counts[label] for label, _low, high in DEADLINE_BANDS if high is not None and high <= 7
    )
    return DeadlinePressure(
        bands=tuple((label, counts[label]) for label, _low, _high in DEADLINE_BANDS),
        due_within_7=within_7,
        overdue_pending=passed.exclude(sent_status=SentStatus.SENT).count(),
        overdue_already_sent=passed.filter(sent_status=SentStatus.SENT).count(),
        without_deadline=open_items.filter(deadline_date__isnull=True).count(),
    )


# --------------------------------------------------------------------------
# Member feedback (schema 1.2)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedbackSummary:
    """Member participation, counted per topic and never per person.

    The source records how many members answered on a matter and how many were
    asked directly. It does not record *who*, and this dashboard adds no field
    that could. Every figure here is therefore a count of topics or a sum of
    per-topic counts — never a number of distinct people, because one member
    answering on nine matters is nine here and one in reality.

    No response rate is offered. `with_feedback` is not a subset of
    `directly_asked`: members also answer through newsletters and general calls,
    and the register contains matters where more members answered than were
    asked directly. Dividing one by the other would produce ratios above 100%
    and would name them a response rate.
    """

    tracked_topics: int
    untracked_topics: int
    with_feedback: int
    measured_zero: int
    feedback_instances: int | None
    median_per_topic: float | None
    requested_tracked_topics: int
    requested_instances: int | None

    @property
    def has_coverage(self) -> bool:
        return self.tracked_topics > 0


def feedback_summary(
    snapshot: LegalWorkSnapshot | None, *, year: int | None = None
) -> FeedbackSummary:
    """Feedback figures for the whole register or one year.

    `None` and `0` are kept apart at every step. A topic whose count was never
    recorded is untracked; a topic recorded as zero is a measurement that nobody
    responded, and it belongs in the denominator.
    """
    items = _items(snapshot)
    if year is not None:
        items = items.filter(**{ANNUAL_MEMBERSHIP_FIELD: year})

    tracked = items.filter(feedback_member_count__isnull=False)
    aggregates = tracked.aggregate(
        total=Sum("feedback_member_count"),
        tracked_count=Count("id"),
        positive=Count("id", filter=Q(feedback_member_count__gt=0)),
        zero=Count("id", filter=Q(feedback_member_count=0)),
    )
    requested = items.filter(feedback_requested_member_count__isnull=False).aggregate(
        total=Sum("feedback_requested_member_count"),
        tracked_count=Count("id"),
    )

    tracked_count = aggregates["tracked_count"] or 0
    per_topic = list(
        tracked.filter(feedback_member_count__gt=0).values_list("feedback_member_count", flat=True)
    )

    return FeedbackSummary(
        tracked_topics=tracked_count,
        untracked_topics=items.filter(feedback_member_count__isnull=True).count(),
        with_feedback=aggregates["positive"] or 0,
        measured_zero=aggregates["zero"] or 0,
        # `None` rather than 0 when nothing is tracked: an absent sum is not a
        # measured total of zero responses.
        feedback_instances=aggregates["total"] if tracked_count else None,
        median_per_topic=median_days(per_topic),
        requested_tracked_topics=requested["tracked_count"] or 0,
        requested_instances=requested["total"] if requested["tracked_count"] else None,
    )


@dataclass(frozen=True)
class FeedbackCoverageYear:
    year: int
    total_topics: int
    tracked_topics: int
    with_feedback: int
    feedback_instances: int | None

    @property
    def coverage_share(self) -> float | None:
        if self.total_topics == 0:
            return None
        return self.tracked_topics / self.total_topics * 100.0


def feedback_coverage_by_year(
    snapshot: LegalWorkSnapshot | None,
) -> tuple[FeedbackCoverageYear, ...]:
    """How complete the feedback measurement is, year by year.

    This is the context that stops the feedback trend being misread. Tracking
    began partway through the register's history and is still partial, so a year
    with few recorded responses may simply be a year that was barely measured.
    Years before tracking are not drawn as zero anywhere.
    """
    if snapshot is None:
        return ()
    rows = (
        _items(snapshot)
        .values(ANNUAL_MEMBERSHIP_FIELD)
        .annotate(
            total=Count("id"),
            tracked=Count("id", filter=Q(feedback_member_count__isnull=False)),
            positive=Count("id", filter=Q(feedback_member_count__gt=0)),
            instances=Sum("feedback_member_count"),
        )
        .order_by(ANNUAL_MEMBERSHIP_FIELD)
    )
    return tuple(
        FeedbackCoverageYear(
            year=row[ANNUAL_MEMBERSHIP_FIELD],
            total_topics=row["total"],
            tracked_topics=row["tracked"],
            with_feedback=row["positive"],
            feedback_instances=row["instances"] if row["tracked"] else None,
        )
        for row in rows
    )


def first_tracked_feedback_year(snapshot: LegalWorkSnapshot | None) -> int | None:
    """The earliest year carrying any feedback measurement at all.

    Used to state where the series honestly begins instead of drawing a decade
    of zeroes before it.
    """
    row = (
        _items(snapshot)
        .filter(feedback_member_count__isnull=False)
        .order_by(ANNUAL_MEMBERSHIP_FIELD)
        .values_list(ANNUAL_MEMBERSHIP_FIELD, flat=True)
        .first()
    )
    return row


# --------------------------------------------------------------------------
# Category breakdowns
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryRow:
    label: str
    topics: int
    sent: int
    median_window: float | None
    has_enough_sample: bool


def _category_breakdown(
    snapshot: LegalWorkSnapshot | None,
    field_name: str,
    *,
    limit: int = TOP_CATEGORY_LIMIT,
) -> tuple[CategoryRow, ...]:
    """Top categories by volume, with a withheld median below the sample floor.

    The exact source string is the category. Spelling variants and renamed
    institutions are *not* merged: `MKM` and the ministry's full name look like
    the same body, but `Keskkonnaministeerium` becoming `Kliimaministeerium` was
    a genuine reorganisation with a changed remit, and no automatic rule can
    tell those two cases apart. Aggregating them would invent a continuity the
    register does not record.
    """
    if snapshot is None:
        return ()

    rows = (
        _items(snapshot)
        .exclude(**{field_name: ""})
        .values(field_name)
        .annotate(
            topics=Count("id"),
            sent=Count("id", filter=Q(sent_status=SentStatus.SENT)),
        )
        .order_by("-topics", field_name)[:limit]
    )
    labels = [row[field_name] for row in rows]

    # One query for every window value across the drawn categories, grouped in
    # Python. A median per category through the ORM would be one query each.
    windows: dict[str, list[int]] = {label: [] for label in labels}
    for label, received_date, deadline_date in (
        _items(snapshot)
        .filter(
            **{f"{field_name}__in": labels},
            received_date__isnull=False,
            deadline_date__isnull=False,
            deadline_date__gte=F("received_date"),
        )
        .values_list(field_name, "received_date", "deadline_date")
    ):
        windows[label].append((deadline_date - received_date).days)

    return tuple(
        CategoryRow(
            label=row[field_name],
            topics=row["topics"],
            sent=row["sent"],
            median_window=(
                median_days(windows[row[field_name]])
                if len(windows[row[field_name]]) >= MIN_COMPARISON_SAMPLE
                else None
            ),
            has_enough_sample=len(windows[row[field_name]]) >= MIN_COMPARISON_SAMPLE,
        )
        for row in rows
    )


@dataclass(frozen=True)
class FeedbackCategoryRow:
    """Member participation within one category.

    `tracked` is the denominator that makes the other two readable: a category
    with two feedback topics out of three measured is not the same as two out of
    ninety, and without it the ranking would simply follow category size.
    """

    label: str
    tracked: int
    with_feedback: int
    instances: int | None


def feedback_breakdown(
    snapshot: LegalWorkSnapshot | None,
    field_name: str,
    *,
    limit: int = TOP_CATEGORY_LIMIT,
) -> tuple[FeedbackCategoryRow, ...]:
    """Where member participation is concentrated, by act type or recipient.

    Descriptive only. That members engage more on one recipient's files does not
    mean the recipient caused it — the subject matter, the deadline and the
    Chamber's own outreach all sit between the two, and none of them is here.

    Ordered by how many topics actually drew feedback rather than by category
    size, and categories with nothing tracked are dropped instead of being drawn
    as a row of zeroes.
    """
    if snapshot is None:
        return ()
    rows = (
        _items(snapshot)
        .exclude(**{field_name: ""})
        .filter(feedback_member_count__isnull=False)
        .values(field_name)
        .annotate(
            tracked=Count("id"),
            with_feedback=Count("id", filter=Q(feedback_member_count__gt=0)),
            instances=Sum("feedback_member_count"),
        )
        .order_by("-with_feedback", "-tracked", field_name)[:limit]
    )
    return tuple(
        FeedbackCategoryRow(
            label=row[field_name],
            tracked=row["tracked"],
            with_feedback=row["with_feedback"],
            instances=row["instances"],
        )
        for row in rows
    )


@dataclass(frozen=True)
class FeedbackTopic:
    topic: str
    feedback_member_count: int
    requested_member_count: int | None
    source_year: int


def top_feedback_topics(
    snapshot: LegalWorkSnapshot | None, *, limit: int = TOP_CATEGORY_LIMIT
) -> tuple[FeedbackTopic, ...]:
    """The matters that drew the most member responses.

    Titled neutrally. A high count means broad engagement, not importance: a
    technical amendment affecting one sector can matter enormously and draw
    three replies, and nothing here ranks significance.
    """
    if snapshot is None:
        return ()
    rows = (
        _items(snapshot)
        .filter(feedback_member_count__gt=0)
        .order_by("-feedback_member_count", "topic")
        .values_list(
            "topic", "feedback_member_count", "feedback_requested_member_count", "source_year"
        )[:limit]
    )
    return tuple(
        FeedbackTopic(
            topic=topic,
            feedback_member_count=given,
            requested_member_count=asked,
            source_year=year,
        )
        for topic, given, asked, year in rows
    )


def recipient_breakdown(
    snapshot: LegalWorkSnapshot | None, *, limit: int = TOP_CATEGORY_LIMIT
) -> tuple[CategoryRow, ...]:
    """`Kellele arvamusi saadetakse?` — descriptive, never a ranking of institutions.

    A longer or shorter median consultation window describes the window, not the
    quality of the body that set it, so nothing here is labelled best or worst.
    """
    return _category_breakdown(snapshot, "recipient", limit=limit)


# --------------------------------------------------------------------------
# Data quality
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldCoverage:
    label: str
    present: int
    total: int

    @property
    def share(self) -> float | None:
        if self.total == 0:
            return None
        return self.present / self.total * 100.0


@dataclass(frozen=True)
class DataQuality:
    total: int
    coverage: tuple[FieldCoverage, ...]
    warning_records: int
    future_received: int
    future_sent: int
    negative_window: int


def data_quality(snapshot: LegalWorkSnapshot | None) -> DataQuality:
    """What the analytics could and could not measure, and why.

    Reported rather than repaired. Every anomaly counted here stays imported
    exactly as the workbook stated it; what changes is which statistics draw it.
    """
    if snapshot is None:
        return DataQuality(
            total=0,
            coverage=(),
            warning_records=0,
            future_received=0,
            future_sent=0,
            negative_window=0,
        )

    items = _items(snapshot)
    reporting_date = snapshot.reporting_date
    total = items.count()

    aggregates = items.aggregate(
        received=Count("id", filter=Q(received_date__isnull=False)),
        deadline=Count("id", filter=Q(deadline_date__isnull=False)),
        sent=Count("id", filter=Q(sent_date__isnull=False)),
        stage=Count("id", filter=~Q(stage_key="")),
        recipient=Count("id", filter=~Q(recipient="")),
        act_type=Count("id", filter=~Q(act_type="")),
        feedback=Count("id", filter=Q(feedback_member_count__isnull=False)),
        requested=Count("id", filter=Q(feedback_requested_member_count__isnull=False)),
        future_received=Count("id", filter=Q(received_date__gt=reporting_date)),
        future_sent=Count("id", filter=Q(sent_date__gt=reporting_date)),
        negative_window=Count(
            "id",
            filter=Q(
                received_date__isnull=False,
                deadline_date__isnull=False,
                deadline_date__lt=F("received_date"),
            ),
        ),
    )

    labels = (
        ("Saabumise kuupäev", "received"),
        ("Arvamuse tähtaeg", "deadline"),
        ("Väljasaatmise kuupäev", "sent"),
        ("Hetkeseis", "stage"),
        ("Saaja", "recipient"),
        ("Õigusakti liik", "act_type"),
        ("Liikmete tagasiside", "feedback"),
        ("Otsepöördumised", "requested"),
    )
    return DataQuality(
        total=total,
        coverage=tuple(
            FieldCoverage(label=label, present=aggregates[key], total=total)
            for label, key in labels
        ),
        warning_records=snapshot.warning_record_count,
        future_received=aggregates["future_received"],
        future_sent=aggregates["future_sent"],
        negative_window=aggregates["negative_window"],
    )


def warning_code_counts(snapshot: LegalWorkSnapshot | None) -> tuple[tuple[str, int], ...]:
    """How many records carry each warning code.

    Read from the stored JSON list in Python rather than through a JSON
    aggregate, because the column is a small list on a bounded number of rows
    and the query stays a single scan either way.
    """
    if snapshot is None:
        return ()
    counts: dict[str, int] = {}
    for (codes,) in _items(snapshot).exclude(warning_codes=[]).values_list("warning_codes"):
        for code in codes or ():
            counts[code] = counts.get(code, 0) + 1
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
