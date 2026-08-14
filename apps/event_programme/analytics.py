"""What the event programme measures, computed in PostgreSQL.

The measurement object is one **canonical programme event** — one row of the
current snapshot's `DASH_EVENTS` table, one service code. It is not a session,
not an occurrence and not a calendar day. See `docs/event-programme-feed.md` for
why the workbook's occurrence sheet is not an analytical source.

Three rules run through everything here:

- **an undated event is a real event.** It never enters a month, a quarter, a
  year or a seasonal average, and it is never given an invented date. It stays
  in the population total, and every function that drops it says how many it
  dropped, so a reader can always reconcile a chart against the programme;
- **blank is its own category.** A missing delivery mode is `Määramata`, never
  `Kohapeal`; a missing type or tag is `Määramata`, never dropped from a share's
  denominator. Silently excluding blanks is how a share of 82% gets printed as
  100%;
- **every distribution is mutually exclusive and sums to its population.** One
  event carries exactly one `tag_key`, one `event_type_key` and one
  `delivery_mode`, so `Muu` really is the remainder and the reconciliation tests
  in `tests/event_programme/test_analytics_reconciliation.py` can assert it.

Every aggregate is one grouped query. Nothing here iterates events to count
them, because the programme is on the order of a thousand rows today and a page
that asks a question per row stops working long before that becomes ten
thousand.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date

from django.db.models import Case, Count, IntegerField, Q, QuerySet, Value, When
from django.utils import timezone

from apps.core.formatting import month_name

from .models import DeliveryMode, EventProgrammeItem, EventProgrammeSnapshot

#: What an unclassified value is called wherever one is shown. One word, used by
#: every dimension, so a reader learns it once.
UNKNOWN_LABEL = "Määramata"

#: The key a blank dimension value is grouped under. Not `""`: a template
#: iterating rows needs something to put in a URL, and an empty query value
#: means "not filtered" everywhere else on this page.
UNKNOWN_KEY = "_unknown"

#: How many categories a ranking shows before the rest become `Muu`. Ten leaves
#: a readable horizontal bar chart; the current programme carries 50 tags, and
#: 50 bars is a wall rather than a ranking.
TOP_CATEGORIES = 10

#: Duration bands, in whole calendar days from start to end inclusive. Chosen to
#: separate a seminar from a training programme from a mission, not to grade
#: anything: a one-day event is not a worse event than an eight-day one.
DURATION_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("1 päev", 1, 1),
    ("2–3 päeva", 2, 3),
    ("4–7 päeva", 4, 7),
    ("8+ päeva", 8, None),
)


@dataclass(frozen=True)
class Category:
    """One row of a distribution: a stable key, a visible label, a count.

    `share` is against the **whole** population the distribution describes,
    blanks included, so the rows of one distribution always sum to 100%.
    """

    key: str
    label: str
    count: int
    share: float = 0.0

    @property
    def is_unknown(self) -> bool:
        return self.key == UNKNOWN_KEY

    @property
    def share_pct(self) -> int:
        """The share as a whole number, for a bar's SVG geometry.

        An **integer**, because the design system requires geometry inside an
        attribute to be written with `stringformat` rather than `floatformat`:
        the dashboard renders in Estonian, `floatformat` is localised, and
        `12,34` in a `width` attribute is not one coordinate.
        """
        return int(round(self.share))


@dataclass(frozen=True)
class Distribution:
    """A complete, mutually exclusive breakdown of one population.

    `total` is the population, not the sum of the rows shown: a ranking cut to
    the top ten still knows what it is a share of. `remainder` carries whatever
    the cut left out, so `rows + remainder` reconciles with `total` exactly.
    """

    dimension: str
    rows: tuple[Category, ...]
    total: int
    remainder: Category | None = None

    @property
    def has_data(self) -> bool:
        return self.total > 0

    @property
    def all_rows(self) -> tuple[Category, ...]:
        return self.rows + ((self.remainder,) if self.remainder else ())

    @property
    def counted(self) -> int:
        """What the shown rows and the remainder actually account for."""
        return sum(row.count for row in self.all_rows)

    @property
    def unknown_count(self) -> int:
        return sum(row.count for row in self.all_rows if row.is_unknown)


@dataclass(frozen=True)
class YearVolume:
    """One year of the programme.

    `undated` is not part of `count`: an event with no readable date belongs to
    no year. It travels alongside so a chart can disclose what it cannot draw.
    """

    year: int
    count: int
    is_current: bool = False
    is_partial_history: bool = False


@dataclass(frozen=True)
class MonthVolume:
    month: int
    label: str
    count: int
    #: Split only for the current year, where the two are different questions.
    completed: int | None = None
    upcoming: int | None = None


@dataclass(frozen=True)
class SeasonalMonth:
    """A typical month, across complete years only."""

    month: int
    label: str
    median: float
    mean: float
    years: int


@dataclass(frozen=True)
class VolumeSummary:
    """Everything the Maht ja kalender focus draws, read in a handful of queries."""

    years: tuple[YearVolume, ...] = ()
    months: tuple[MonthVolume, ...] = ()
    quarters: tuple[Category, ...] = ()
    seasonality: tuple[SeasonalMonth, ...] = ()
    durations: Distribution | None = None
    complete_years: tuple[int, ...] = ()
    undated_count: int = 0
    dated_count: int = 0
    total_count: int = 0
    first_year_is_partial: bool = False
    earliest_start: date | None = None
    latest_start: date | None = None


def items_for(snapshot: EventProgrammeSnapshot | None) -> QuerySet[EventProgrammeItem]:
    if snapshot is None:
        return EventProgrammeItem.objects.none()
    return EventProgrammeItem.objects.filter(snapshot=snapshot)


def population(
    snapshot: EventProgrammeSnapshot | None, *, year: int | None = None
) -> QuerySet[EventProgrammeItem]:
    """The events one focus view is about.

    `year is None` means the whole programme. A year selects the **event
    cohort**: events whose own start date falls in that year. It never selects
    by when something was measured, viewed or transacted — those windows belong
    to the analyses that use them and are labelled there.
    """
    rows = items_for(snapshot)
    if year is not None:
        rows = rows.filter(event_year=year)
    return rows


# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------


def _shares(rows: list[Category], total: int) -> tuple[Category, ...]:
    if not total:
        return tuple(rows)
    return tuple(
        Category(key=row.key, label=row.label, count=row.count, share=row.count / total * 100)
        for row in rows
    )


def labelled_distribution(
    rows: QuerySet[EventProgrammeItem],
    *,
    key_field: str,
    label_field: str,
    dimension: str,
    top: int | None = TOP_CATEGORIES,
) -> Distribution:
    """A ranking of one hand-maintained vocabulary, blanks included.

    The vocabulary is **not** hard-coded: a key exists as a row because the
    snapshot contains it, so a tag the Chamber adds in `DASH_TAG_MAP` appears
    without a code change and cannot 500 the page.

    `Muu` is only produced when the dimension is genuinely one-value-per-event,
    which every dimension here is: the remainder of a complete, mutually
    exclusive classification is a real category. It is never a bucket for values
    that were dropped for another reason.
    """
    total = rows.count()
    counted = rows.values(key_field, label_field).annotate(n=Count("id")).order_by("-n", key_field)

    collected: dict[str, Category] = {}
    for row in counted:
        key = row[key_field] or UNKNOWN_KEY
        label = (row[label_field] or "").strip() or UNKNOWN_LABEL
        existing = collected.get(key)
        # Two labels for one key can only happen if the workbook relabelled a
        # value mid-history. The key is the identity; the first label wins.
        collected[key] = Category(
            key=key,
            label=existing.label if existing else label,
            count=(existing.count if existing else 0) + row["n"],
        )

    ordered = sorted(collected.values(), key=lambda row: (-row.count, row.label.casefold()))

    remainder = None
    if top is not None and len(ordered) > top:
        tail = ordered[top:]
        ordered = ordered[:top]
        remainder = Category(
            key="_other",
            label="Muu",
            count=sum(row.count for row in tail),
            share=(sum(row.count for row in tail) / total * 100) if total else 0.0,
        )

    return Distribution(
        dimension=dimension,
        rows=_shares(ordered, total),
        total=total,
        remainder=remainder,
    )


def delivery_mode_distribution(rows: QuerySet[EventProgrammeItem]) -> Distribution:
    """`Kohapeal`, `Veebis`, `Hübriid` and `Määramata`, in the model's own order.

    A blank delivery mode is its own row and never folded into `Kohapeal`. On
    the current programme it is 21% of events, so folding it would move a fifth
    of the history into a claim the source never made.
    """
    total = rows.count()
    counts = dict(
        rows.values_list("delivery_mode").annotate(n=Count("id")).values_list("delivery_mode", "n")
    )
    ordered = [
        Category(key=choice.value, label=choice.label, count=counts.get(choice.value, 0))
        for choice in DeliveryMode
        if counts.get(choice.value)
    ]
    unknown = counts.get("", 0)
    if unknown:
        ordered.append(Category(key=UNKNOWN_KEY, label=UNKNOWN_LABEL, count=unknown))
    return Distribution(dimension="delivery_mode", rows=_shares(ordered, total), total=total)


def delivery_mode_by_year(
    snapshot: EventProgrammeSnapshot | None, *, years: tuple[int, ...]
) -> dict[int, Distribution]:
    """One delivery-mode split per year, in a single grouped query.

    The whole point of the chart this feeds is the change from 2019 to 2021 to
    now, so asking the database once per year would be four queries on a normal
    page and ten on `Kõik aastad`.
    """
    if not years:
        return {}
    rows = (
        items_for(snapshot)
        .filter(event_year__in=years)
        .values("event_year", "delivery_mode")
        .annotate(n=Count("id"))
    )
    totals: dict[int, int] = {}
    cells: dict[int, dict[str, int]] = {}
    for row in rows:
        year = row["event_year"]
        cells.setdefault(year, {})[row["delivery_mode"]] = row["n"]
        totals[year] = totals.get(year, 0) + row["n"]

    result: dict[int, Distribution] = {}
    for year in years:
        counts = cells.get(year, {})
        total = totals.get(year, 0)
        ordered = [
            Category(key=choice.value, label=choice.label, count=counts.get(choice.value, 0))
            for choice in DeliveryMode
        ]
        ordered.append(Category(key=UNKNOWN_KEY, label=UNKNOWN_LABEL, count=counts.get("", 0)))
        result[year] = Distribution(
            dimension="delivery_mode",
            rows=_shares(ordered, total),
            total=total,
        )
    return result


def tag_mix_by_year(
    snapshot: EventProgrammeSnapshot | None,
    *,
    years: tuple[int, ...],
    keys: tuple[str, ...],
) -> dict[int, Distribution]:
    """How the named themes divide each year, everything else as `Muu`.

    `keys` is chosen by the caller from the whole-history ranking, so the same
    themes are tracked across every year and a theme that had one strong year
    does not appear and disappear from the legend.
    """
    if not years:
        return {}
    rows = (
        items_for(snapshot)
        .filter(event_year__in=years)
        .values("event_year", "tag_key", "tag_label")
        .annotate(n=Count("id"))
    )
    labels: dict[str, str] = {}
    cells: dict[int, dict[str, int]] = {}
    totals: dict[int, int] = {}
    for row in rows:
        year = row["event_year"]
        key = row["tag_key"] or UNKNOWN_KEY
        labels.setdefault(key, (row["tag_label"] or "").strip() or UNKNOWN_LABEL)
        bucket = key if key in keys else "_other"
        cells.setdefault(year, {})[bucket] = cells.setdefault(year, {}).get(bucket, 0) + row["n"]
        totals[year] = totals.get(year, 0) + row["n"]

    result: dict[int, Distribution] = {}
    for year in years:
        counts = cells.get(year, {})
        total = totals.get(year, 0)
        ordered = [
            Category(key=key, label=labels.get(key, key), count=counts.get(key, 0)) for key in keys
        ]
        if counts.get("_other"):
            ordered.append(Category(key="_other", label="Muu", count=counts["_other"]))
        result[year] = Distribution(dimension="tag", rows=_shares(ordered, total), total=total)
    return result


# ---------------------------------------------------------------------------
# Volume over time
# ---------------------------------------------------------------------------


def counts_by_year(snapshot: EventProgrammeSnapshot | None) -> dict[int, int]:
    """Dated events per event year. Undated events are in no year at all."""
    return {
        row["event_year"]: row["n"]
        for row in items_for(snapshot)
        .exclude(event_year=None)
        .values("event_year")
        .annotate(n=Count("id"))
        .order_by("event_year")
    }


def counts_by_month(
    snapshot: EventProgrammeSnapshot | None, *, year: int | None = None
) -> dict[tuple[int, int], int]:
    """`(year, month) -> count`, from the derived `YYYY-MM` key the importer stored."""
    rows = items_for(snapshot).exclude(event_month_key="")
    if year is not None:
        rows = rows.filter(event_year=year)
    counted: dict[tuple[int, int], int] = {}
    for row in rows.values("event_year", "event_month_key").annotate(n=Count("id")):
        tail = row["event_month_key"].split("-")[-1]
        if not tail.isdigit():
            continue
        counted[(row["event_year"], int(tail))] = row["n"]
    return counted


def _month_rows(
    snapshot: EventProgrammeSnapshot | None, *, year: int, today: date
) -> tuple[MonthVolume, ...]:
    """Twelve months of one year, future months included.

    This is a programme, not a year-to-date sales chart: an event scheduled for
    November is already decided in August and belongs on the drawing. The split
    between what has happened and what is still ahead is shown only for the
    current year, where the distinction is real.
    """
    counts = counts_by_month(snapshot, year=year)
    split: dict[int, tuple[int, int]] = {}
    if year == today.year:
        rows = (
            items_for(snapshot)
            .filter(event_year=year)
            .exclude(event_month_key="")
            .annotate(
                is_done=Case(
                    When(
                        Q(end_date__lt=today) | Q(end_date__isnull=True, start_date__lt=today),
                        then=Value(1),
                    ),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            )
            .values("event_month_key", "is_done")
            .annotate(n=Count("id"))
        )
        for row in rows:
            tail = row["event_month_key"].split("-")[-1]
            if not tail.isdigit():
                continue
            month = int(tail)
            done, ahead = split.get(month, (0, 0))
            if row["is_done"]:
                done += row["n"]
            else:
                ahead += row["n"]
            split[month] = (done, ahead)

    result = []
    for month in range(1, 13):
        count = counts.get((year, month), 0)
        done, ahead = split.get(month, (None, None))
        result.append(
            MonthVolume(
                month=month,
                label=month_name(month, short=True),
                count=count,
                completed=done,
                upcoming=ahead,
            )
        )
    return tuple(result)


def seasonality(
    snapshot: EventProgrammeSnapshot | None, *, complete_years: tuple[int, ...]
) -> tuple[SeasonalMonth, ...]:
    """A typical month, over complete years only.

    Median as well as mean, because one exceptional year moves a mean of eight
    observations a long way and the median is what "typical" means. A partial
    year is excluded entirely rather than scaled up: a year with four months of
    data does not describe a December.

    This is a description of what has happened. It is not a forecast, and
    nothing here extends it into a month that has not occurred.
    """
    if len(complete_years) < 2:
        return ()
    counts = counts_by_month(snapshot)
    result = []
    for month in range(1, 13):
        values = [counts.get((year, month), 0) for year in complete_years]
        result.append(
            SeasonalMonth(
                month=month,
                label=month_name(month, short=True),
                median=float(statistics.median(values)),
                mean=float(statistics.fmean(values)),
                years=len(complete_years),
            )
        )
    return tuple(result)


def quarter_distribution(rows: QuerySet[EventProgrammeItem]) -> tuple[Category, ...]:
    """Q1–Q4 for a population, undated events excluded and disclosed elsewhere."""
    counted = dict(
        rows.exclude(event_quarter="")
        .values_list("event_quarter")
        .annotate(n=Count("id"))
        .values_list("event_quarter", "n")
    )
    total = sum(counted.values())
    return _shares(
        [
            Category(key=quarter, label=quarter, count=counted.get(quarter, 0))
            for quarter in ("Q1", "Q2", "Q3", "Q4")
        ],
        total,
    )


def duration_distribution(rows: QuerySet[EventProgrammeItem]) -> Distribution:
    """How long events run, in whole calendar days.

    Calendar days from start to end inclusive — **not** training hours and not
    working days. A programme running from September to October is one event
    lasting many days, not many events, and this is the figure that separates a
    morning seminar from a mission without claiming anything about content.

    An event with a start date and no end date is a single-day event: that is
    what the workbook's own range parser means by leaving `end_date` empty.

    One query returning two date columns, banded in Python. `date - date` is an
    integer in PostgreSQL and an interval elsewhere, and a band boundary is not
    worth an expression whose type depends on the backend.
    """
    counted: dict[str, int] = {}
    total = 0
    for start, end in rows.exclude(start_date=None).values_list("start_date", "end_date"):
        total += 1
        days = ((end or start) - start).days + 1
        for label, low, high in DURATION_BANDS:
            if days >= low and (high is None or days <= high):
                counted[label] = counted.get(label, 0) + 1
                break
    if not total:
        return Distribution(dimension="duration", rows=(), total=0)

    ordered = [
        Category(key=label, label=label, count=counted.get(label, 0))
        for label, _low, _high in DURATION_BANDS
        if counted.get(label)
    ]
    return Distribution(dimension="duration", rows=_shares(ordered, total), total=total)


def complete_years_for(
    snapshot: EventProgrammeSnapshot | None, *, today: date | None = None
) -> tuple[int, ...]:
    """Years whose whole calendar has passed and that the programme covers.

    The current year is excluded because it is not finished. The **first** year
    is excluded when the programme's earliest event falls after January, because
    a history that starts in June would otherwise drag every seasonal median
    down for five months.
    """
    today = today or timezone.localdate()
    years = sorted(counts_by_year(snapshot))
    if not years:
        return ()
    complete = [year for year in years if year < today.year]
    if complete and first_year_is_partial(snapshot):
        complete = complete[1:]
    return tuple(complete)


def first_year_is_partial(snapshot: EventProgrammeSnapshot | None) -> bool:
    earliest = (
        items_for(snapshot)
        .exclude(start_date=None)
        .order_by("start_date")
        .values_list("start_date", flat=True)
        .first()
    )
    return earliest is not None and earliest.month > 1


def build_volume(
    snapshot: EventProgrammeSnapshot | None,
    *,
    year: int | None,
    today: date | None = None,
) -> VolumeSummary:
    """Everything the volume focus draws."""
    today = today or timezone.localdate()
    rows = items_for(snapshot)
    total = rows.count()
    if not total:
        return VolumeSummary()

    by_year = counts_by_year(snapshot)
    undated = rows.filter(start_date=None).count()
    partial = first_year_is_partial(snapshot)
    first_year = min(by_year) if by_year else None
    complete = complete_years_for(snapshot, today=today)

    span = rows.exclude(start_date=None).order_by("start_date").values_list("start_date", flat=True)
    earliest = span.first()
    latest = (
        rows.exclude(start_date=None)
        .order_by("-start_date")
        .values_list("start_date", flat=True)
        .first()
    )

    cohort = population(snapshot, year=year)
    return VolumeSummary(
        years=tuple(
            YearVolume(
                year=year_value,
                count=count,
                is_current=year_value == today.year,
                is_partial_history=partial and year_value == first_year,
            )
            for year_value, count in sorted(by_year.items())
        ),
        months=_month_rows(snapshot, year=year, today=today) if year is not None else (),
        quarters=quarter_distribution(cohort),
        seasonality=seasonality(snapshot, complete_years=complete),
        durations=duration_distribution(cohort),
        complete_years=complete,
        undated_count=undated,
        dated_count=total - undated,
        total_count=total,
        first_year_is_partial=partial,
        earliest_start=earliest,
        latest_start=latest,
    )


# ---------------------------------------------------------------------------
# Current state, derived from dates rather than from a stale snapshot field
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemporalState:
    """Where the programme sits in time **today**.

    `event_status` is computed by the generator when the export is produced, so
    a snapshot published a fortnight ago still calls a finished event
    `upcoming`. Everything that claims to describe now is derived from the dates
    and today's date instead. The stored status is kept for the register's
    filter, where it is the workbook's own statement rather than a claim about
    the present.
    """

    past: int = 0
    ongoing: int = 0
    upcoming: int = 0
    date_unknown: int = 0

    @property
    def total(self) -> int:
        return self.past + self.ongoing + self.upcoming + self.date_unknown


def temporal_state(
    rows: QuerySet[EventProgrammeItem], *, today: date | None = None
) -> TemporalState:
    today = today or timezone.localdate()
    ended = Q(end_date__lt=today) | Q(end_date__isnull=True, start_date__lt=today)
    started = Q(start_date__lte=today)
    counts = rows.aggregate(
        unknown=Count("id", filter=Q(start_date=None)),
        past=Count("id", filter=Q(start_date__isnull=False) & ended),
        ongoing=Count("id", filter=Q(start_date__isnull=False) & started & ~ended),
        upcoming=Count("id", filter=Q(start_date__gt=today)),
    )
    return TemporalState(
        past=counts["past"],
        ongoing=counts["ongoing"],
        upcoming=counts["upcoming"],
        date_unknown=counts["unknown"],
    )


def count_completed_in_year(
    snapshot: EventProgrammeSnapshot | None, *, year: int, today: date | None = None
) -> int:
    """Events of `year` that have already finished, by today's date."""
    today = today or timezone.localdate()
    return (
        population(snapshot, year=year)
        .filter(Q(end_date__lt=today) | Q(end_date__isnull=True, start_date__lt=today))
        .count()
    )


def count_year_to_date(snapshot: EventProgrammeSnapshot | None, *, year: int, today: date) -> int:
    """Events of `year` that started on or before the same day-of-year as `today`.

    The like-for-like half of the current-year comparison: 1 January to today,
    against 1 January to the same date a year earlier. Kept separate from the
    whole-programme comparison because the two answer different questions and
    mixing them is how a full-year schedule gets compared against eight months.

    29 February is clamped to 28 February in a non-leap comparison year, so the
    window never silently rolls into March.
    """
    day, month = today.day, today.month
    if month == 2 and day == 29:
        day = 28
    try:
        cutoff = date(year, month, day)
    except ValueError:  # pragma: no cover - guarded above
        return 0
    return (
        population(snapshot, year=year)
        .filter(start_date__gte=date(year, 1, 1), start_date__lte=cutoff)
        .count()
    )


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

#: How many events a category needs before its median is worth comparing. Below
#: this a "median" is one or two events wearing the authority of a statistic.
#: Chosen against the real programme: at eight, eight of thirteen event types
#: qualify and they cover 95% of the classified events, so the rule excludes the
#: genuinely thin categories without hollowing out the comparison.
MIN_SAMPLE = 8

#: Planning-lead bands, in days before the event.
PLANNING_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("Alla 14 päeva", 0, 13),
    ("14–29 päeva", 14, 29),
    ("30–59 päeva", 30, 59),
    ("60–89 päeva", 60, 89),
    ("90+ päeva", 90, None),
)


@dataclass(frozen=True)
class TypeStat:
    """One event type's median, with the sample it rests on."""

    key: str
    label: str
    median: float
    count: int


@dataclass(frozen=True)
class PlanningSummary:
    """How far ahead events enter the programme.

    `added_date` is when an event was **added to the Chamber's operational
    programme**. It is not when a public page appeared, and nothing here calls it
    a publication date: the two are different measurements and only the first
    has a source.

    `median_lead` is deliberately the headline and the mean is secondary. The
    real distribution runs from one day to four years, and a handful of events
    planned two years out drag a mean far away from what a typical event looks
    like.

    Events entered **after** they began carry a negative lead. They are a real
    property of how the programme is maintained, so they are counted and shown —
    and excluded from the statistics, because a negative planning lead is not a
    short planning lead, it is a different event altogether. Nothing clamps them
    to zero.
    """

    median_lead: float | None = None
    mean_lead: float | None = None
    bands: tuple[Category, ...] = ()
    by_type: tuple[TypeStat, ...] = ()
    by_year: tuple[tuple[int, float, int], ...] = ()
    measured: int = 0
    retroactive: int = 0
    missing: int = 0
    population: int = 0

    @property
    def has_data(self) -> bool:
        return self.measured > 0

    @property
    def coverage(self) -> float:
        return (self.measured / self.population * 100) if self.population else 0.0


def _median(values: list[int]) -> float | None:
    return float(statistics.median(values)) if values else None


def build_planning(
    snapshot: EventProgrammeSnapshot | None, *, year: int | None = None
) -> PlanningSummary:
    """Planning lead for one cohort, in one query over three columns."""
    rows = list(
        population(snapshot, year=year).values_list(
            "planning_lead_days", "event_type_key", "event_type_label", "event_year"
        )
    )
    if not rows:
        return PlanningSummary()

    leads = [row[0] for row in rows if row[0] is not None]
    usable = [value for value in leads if value >= 0]
    retroactive = len(leads) - len(usable)

    banded: dict[str, int] = {}
    for value in usable:
        for label, low, high in PLANNING_BANDS:
            if value >= low and (high is None or value <= high):
                banded[label] = banded.get(label, 0) + 1
                break

    by_type: dict[str, tuple[str, list[int]]] = {}
    by_year: dict[int, list[int]] = {}
    for lead, type_key, type_label, event_year in rows:
        if lead is None or lead < 0:
            continue
        key = type_key or UNKNOWN_KEY
        label = (type_label or "").strip() or UNKNOWN_LABEL
        by_type.setdefault(key, (label, []))[1].append(lead)
        if event_year is not None:
            by_year.setdefault(event_year, []).append(lead)

    typed = sorted(
        (
            TypeStat(
                key=key, label=label, median=float(statistics.median(values)), count=len(values)
            )
            for key, (label, values) in by_type.items()
            if len(values) >= MIN_SAMPLE
        ),
        key=lambda stat: stat.median,
        reverse=True,
    )

    return PlanningSummary(
        median_lead=_median(usable),
        mean_lead=float(statistics.fmean(usable)) if usable else None,
        bands=_shares(
            [
                Category(key=label, label=label, count=banded.get(label, 0))
                for label, _low, _high in PLANNING_BANDS
            ],
            len(usable),
        ),
        by_type=tuple(typed),
        by_year=tuple(
            (event_year, float(statistics.median(values)), len(values))
            for event_year, values in sorted(by_year.items())
            if len(values) >= MIN_SAMPLE
        ),
        measured=len(usable),
        retroactive=retroactive,
        missing=len(rows) - len(leads),
        population=len(rows),
    )


# ---------------------------------------------------------------------------
# Price structure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceSummary:
    """What the programme says an event costs — planned pricing, never a payment.

    This describes the **price list**. It is not what anybody was charged, not
    revenue, and not a transaction: the Chamber's Commerce data is where an
    actual acquisition lives, and the two are never added together.

    `status` is the authoritative classification and is the reason this is worth
    storing at all. A blank price with status `free` is a free event; a blank
    price with status `missing`, `tba` or `review` is an event whose price
    nobody has recorded. Those are opposite facts and are never merged.
    """

    status: Distribution | None = None
    member_by_type: tuple[TypeStat, ...] = ()
    nonmember_by_type: tuple[TypeStat, ...] = ()
    free_count: int = 0
    paid_count: int = 0
    unknown_count: int = 0
    priced_events: int = 0
    population: int = 0

    @property
    def has_data(self) -> bool:
        return bool(self.status and self.status.has_data)


#: Statuses that mean "the source could not say", kept apart from `free`.
UNKNOWN_PRICE_STATUSES = ("missing", "tba", "review", "")


def build_prices(
    snapshot: EventProgrammeSnapshot | None, *, year: int | None = None
) -> PriceSummary:
    """The cohort's price structure, and median prices by type where the sample allows."""
    from .models import PriceStatus

    rows = population(snapshot, year=year)
    total = rows.count()
    if not total:
        return PriceSummary()

    counted = dict(
        rows.values_list("price_status").annotate(n=Count("id")).values_list("price_status", "n")
    )
    known = [
        Category(key=choice.value, label=choice.label, count=counted.get(choice.value, 0))
        for choice in PriceStatus
        if counted.get(choice.value)
    ]
    # Anything the generator invents that this application has never heard of
    # still gets a row under its own key rather than disappearing from the
    # denominator.
    seen = {choice.value for choice in PriceStatus}
    for value, count in sorted(counted.items()):
        if value and value not in seen:
            known.append(Category(key=value, label=value, count=count))
    if counted.get(""):
        known.append(Category(key=UNKNOWN_KEY, label=UNKNOWN_LABEL, count=counted[""]))

    priced = [
        (member, nonmember, type_key, type_label)
        for member, nonmember, type_key, type_label in rows.exclude(
            member_price_eur=None, nonmember_price_eur=None
        ).values_list(
            "member_price_eur", "nonmember_price_eur", "event_type_key", "event_type_label"
        )
    ]

    def by_type(index: int) -> tuple[TypeStat, ...]:
        grouped: dict[str, tuple[str, list[float]]] = {}
        for row in priced:
            value = row[index]
            if value is None:
                continue
            key = row[2] or UNKNOWN_KEY
            label = (row[3] or "").strip() or UNKNOWN_LABEL
            grouped.setdefault(key, (label, []))[1].append(float(value))
        return tuple(
            sorted(
                (
                    TypeStat(
                        key=key,
                        label=label,
                        median=float(statistics.median(values)),
                        count=len(values),
                    )
                    for key, (label, values) in grouped.items()
                    if len(values) >= MIN_SAMPLE
                ),
                key=lambda stat: stat.median,
                reverse=True,
            )
        )

    return PriceSummary(
        status=Distribution(dimension="price_status", rows=_shares(known, total), total=total),
        member_by_type=by_type(0),
        nonmember_by_type=by_type(1),
        free_count=counted.get("free", 0),
        paid_count=counted.get("paid", 0) + counted.get("mixed", 0),
        unknown_count=sum(counted.get(value, 0) for value in UNKNOWN_PRICE_STATUSES),
        priced_events=len(priced),
        population=total,
    )


__all__ = [
    "DURATION_BANDS",
    "MIN_SAMPLE",
    "PLANNING_BANDS",
    "UNKNOWN_PRICE_STATUSES",
    "PlanningSummary",
    "PriceSummary",
    "TypeStat",
    "build_planning",
    "build_prices",
    "TOP_CATEGORIES",
    "UNKNOWN_KEY",
    "UNKNOWN_LABEL",
    "Category",
    "Distribution",
    "MonthVolume",
    "SeasonalMonth",
    "TemporalState",
    "VolumeSummary",
    "YearVolume",
    "build_volume",
    "complete_years_for",
    "count_completed_in_year",
    "count_year_to_date",
    "counts_by_month",
    "counts_by_year",
    "delivery_mode_by_year",
    "delivery_mode_distribution",
    "duration_distribution",
    "first_year_is_partial",
    "items_for",
    "labelled_distribution",
    "population",
    "quarter_distribution",
    "seasonality",
    "tag_mix_by_year",
    "temporal_state",
]
