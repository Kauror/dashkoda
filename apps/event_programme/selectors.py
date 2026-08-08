"""Read paths for the event programme. Reads PostgreSQL and the current snapshot.

This is the dashboard's authoritative event source. One successful import writes
one complete snapshot that already carries the whole available history, so every
figure and every table row on this page comes from **one** snapshot. Nothing here
unions several snapshots to reconstruct a past: doing that would let two exports
of different vintages contribute to the same total.

`apps.events` is a different thing and is never consulted here. It collects the
public Koda.ee calendar, it answers "what did we announce", and it may not
supply, extend, correct or fill a gap in anything below.

Every filter reads the event's own dates. `source_year` is the annual sheet the
operational workbook happened to hold the row on, not when the event ran, so it
appears in no period figure and in no period filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import F, Q, QuerySet
from django.utils import timezone

from apps.core.feeds import FeedSummaryMixin

from .models import (
    DeliveryMode,
    EventProgrammeFeedState,
    EventProgrammeItem,
    EventProgrammeSnapshot,
    EventStatus,
)
from .public_links import matched_event_ids

# The near-term window the overview counts, in both directions. Matches the
# legal-work activity window so the KPI cells describe the same length of time.
NEAR_TERM_DAYS = 30

# Rows per page of the programme table. Server-side, because the table holds the
# whole history and sending all of it to the browser would be a page-weight
# problem as well as a memory one.
PAGE_SIZE = 50

# Stable query values for the two filters whose vocabulary is a property of the
# dashboard rather than of the workbook.
LINK_ALL = "all"
LINK_LINKED = "linked"
LINK_UNLINKED = "unlinked"
LINK_VALUES = (LINK_ALL, LINK_LINKED, LINK_UNLINKED)

REVIEW_ALL = "all"
REVIEW_REQUIRED = "required"
REVIEW_CLEAR = "clear"
REVIEW_VALUES = (REVIEW_ALL, REVIEW_REQUIRED, REVIEW_CLEAR)

# `year=all` lifts the period restriction entirely. A separate token rather than
# an empty value, so "no year chosen" and "every year chosen" stay distinct: the
# first gets the default period, the second gets the whole history.
YEAR_ALL = "all"


@dataclass(frozen=True)
class Option:
    """One selectable filter value: a stable key and its visible label."""

    value: str
    label: str


@dataclass(frozen=True)
class FilterOptions:
    """Everything the filter controls may offer, derived from the snapshot.

    Nothing here is hard-coded. A year, tag, type or delivery mode exists as an
    option because the current snapshot actually contains it, so a vocabulary the
    Chamber grows in `DASH_TAG_MAP` appears without a code change and a year
    nobody ran events in never appears at all.
    """

    years: tuple[int, ...] = ()
    months: tuple[Option, ...] = ()
    quarters: tuple[Option, ...] = ()
    tags: tuple[Option, ...] = ()
    event_types: tuple[Option, ...] = ()
    delivery_modes: tuple[Option, ...] = ()
    statuses: tuple[Option, ...] = ()

    @property
    def latest_year(self) -> int | None:
        return self.years[0] if self.years else None

    def default_year(self, today=None) -> int | None:
        """The current calendar year when the snapshot has it, else the latest.

        A dashboard opened in January of a year the Chamber has not yet run an
        event in would otherwise default to an empty table.
        """
        if not self.years:
            return None
        current = (today or timezone.localdate()).year
        return current if current in self.years else self.years[0]


@dataclass(frozen=True)
class ProgrammeFilters:
    """One validated filter state. Every value has already been checked.

    `year is None` means no period restriction. Every other field uses `""` for
    "not filtered", so a blank select and an absent parameter mean the same
    thing.
    """

    q: str = ""
    year: int | None = None
    month: str = ""
    quarter: str = ""
    tag: str = ""
    event_type: str = ""
    delivery_mode: str = ""
    status: str = ""
    public_link: str = LINK_ALL
    review: str = REVIEW_ALL

    @property
    def is_active(self) -> bool:
        """Whether anything narrows the table beyond the default period."""
        return bool(
            self.q
            or self.month
            or self.quarter
            or self.tag
            or self.event_type
            or self.delivery_mode
            or self.status
            or self.public_link != LINK_ALL
            or self.review != REVIEW_ALL
        )


def get_current_event_programme_snapshot() -> EventProgrammeSnapshot | None:
    return (
        EventProgrammeSnapshot.objects.filter(
            source__slug=settings.EVENT_PROGRAMME_SOURCE_SLUG, is_current=True
        )
        .select_related("source")
        .first()
    )


def get_event_programme_feed_state() -> EventProgrammeFeedState | None:
    return (
        EventProgrammeFeedState.objects.filter(source__slug=settings.EVENT_PROGRAMME_SOURCE_SLUG)
        .select_related("source")
        .first()
    )


@dataclass(frozen=True)
class EventProgrammeSummary(FeedSummaryMixin):
    """The programme's freshness and size, in the shared feed vocabulary.

    `observed_at` is the workbook's own `export_refreshed_at`: the moment the
    Chamber's generator produced the export, which is what the figures describe.
    The import time is a separate fact and is not what a reader means by "as of".

    Stale-after-failure comes from `FeedSummaryMixin` unchanged, so a failed
    morning check leaves the previous snapshot on screen and says so, exactly as
    every other feed does.
    """

    snapshot: EventProgrammeSnapshot | None
    feed_state: EventProgrammeFeedState | None

    @property
    def has_data(self) -> bool:
        return self.snapshot is not None

    @property
    def item_count(self) -> int:
        return self.snapshot.canonical_event_count if self.snapshot else 0

    @property
    def observed_at(self):
        return self.snapshot.export_refreshed_at if self.snapshot else None

    @property
    def imported_at(self):
        return self.snapshot.imported_at if self.snapshot else None


def get_event_programme_summary() -> EventProgrammeSummary:
    return EventProgrammeSummary(
        snapshot=get_current_event_programme_snapshot(),
        feed_state=get_event_programme_feed_state(),
    )


def _items(snapshot: EventProgrammeSnapshot | None) -> QuerySet[EventProgrammeItem]:
    if snapshot is None:
        return EventProgrammeItem.objects.none()
    return EventProgrammeItem.objects.filter(snapshot=snapshot)


def get_event_programme_filter_options(
    snapshot: EventProgrammeSnapshot | None = None,
) -> FilterOptions:
    """Every filter value the current snapshot actually contains.

    One small query per dimension rather than one distinct query over every
    combination: the cross product of tag, type, month and status approaches the
    row count, so asking for it would be slower than asking seven questions.

    Every query below sets its **own** ordering, and that is load-bearing rather
    than tidiness. The model orders by start date, name and id; a `DISTINCT` query
    that inherited that ordering would have those three columns appended to its
    select list — Django does it silently — and would then return one row per
    event instead of one per value. The database, not Python, does the collapsing.
    """
    if snapshot is None:
        return FilterOptions()
    rows = _items(snapshot)

    years = tuple(
        year
        for year in rows.exclude(event_year=None)
        .order_by("-event_year")
        .values_list("event_year", flat=True)
        .distinct()
    )

    # `event_month_key` is `YYYY-MM`, so the month number is its tail. The label
    # is the generator's own Estonian month name; the same month across several
    # years is one option.
    months: dict[str, str] = {}
    for key, label in (
        rows.exclude(event_month_key="")
        .order_by("event_month_key", "event_month_label")
        .values_list("event_month_key", "event_month_label")
        .distinct()
    ):
        number = key.split("-")[-1]
        if number and number not in months:
            months[number] = label or number
    month_options = tuple(Option(value=number, label=months[number]) for number in sorted(months))

    return FilterOptions(
        years=years,
        months=month_options,
        quarters=_plain_options(rows, "event_quarter"),
        tags=_labelled_options(rows, "tag_key", "tag_label"),
        event_types=_labelled_options(rows, "event_type_key", "event_type_label"),
        delivery_modes=_choice_options(rows, "delivery_mode", DeliveryMode),
        statuses=_choice_options(rows, "event_status", EventStatus),
    )


def _plain_options(rows, field: str) -> tuple[Option, ...]:
    values = rows.exclude(**{field: ""}).order_by(field).values_list(field, flat=True).distinct()
    return tuple(Option(value=value, label=value) for value in values)


def _labelled_options(rows, key_field: str, label_field: str) -> tuple[Option, ...]:
    """Distinct keys with the label the workbook gave them.

    The key is the query value and the label is what a reader sees, so a
    reclassified label never changes a bookmarked URL. Sorted by label, because
    that is the order the reader is looking at.
    """
    seen: dict[str, str] = {}
    for key, label in (
        rows.exclude(**{key_field: ""})
        .order_by(key_field, label_field)
        .values_list(key_field, label_field)
        .distinct()
    ):
        seen.setdefault(key, label or key)
    return tuple(
        Option(value=key, label=label)
        for key, label in sorted(seen.items(), key=lambda pair: pair[1].casefold())
    )


def _choice_options(rows, field: str, choices) -> tuple[Option, ...]:
    """Distinct values of a controlled vocabulary, in the model's own order."""
    present = set(
        rows.exclude(**{field: ""}).order_by(field).values_list(field, flat=True).distinct()
    )
    return tuple(
        Option(value=choice.value, label=choice.label)
        for choice in choices
        if choice.value in present
    )


def get_filtered_event_programme_items(
    snapshot: EventProgrammeSnapshot | None = None,
    *,
    filters: ProgrammeFilters | None = None,
) -> QuerySet[EventProgrammeItem]:
    """The programme table's rows, filtered and deterministically ordered.

    Ordering: known dates newest first, undated events after every dated one, and
    a name plus service-code tie-break so two events on the same day never swap
    places between two requests or between two pages of the same result.

    Undated events sort last rather than being dropped. They are real events the
    Chamber ran whose operational row held date text nobody could parse, and the
    page discloses how many there are.
    """
    filters = filters or ProgrammeFilters()
    rows = _items(snapshot)

    if filters.q:
        rows = rows.filter(
            Q(event_name__icontains=filters.q) | Q(service_code__icontains=filters.q)
        )
    if filters.year is not None:
        rows = rows.filter(event_year=filters.year)
    if filters.month:
        # The derived `YYYY-MM` key the importer stored, matched on its month
        # tail so the filter means "March" rather than "March 2024".
        rows = rows.filter(event_month_key__endswith=f"-{filters.month}")
    if filters.quarter:
        rows = rows.filter(event_quarter=filters.quarter)
    if filters.tag:
        rows = rows.filter(tag_key=filters.tag)
    if filters.event_type:
        rows = rows.filter(event_type_key=filters.event_type)
    if filters.delivery_mode:
        rows = rows.filter(delivery_mode=filters.delivery_mode)
    if filters.status:
        rows = rows.filter(event_status=filters.status)
    if filters.public_link == LINK_LINKED:
        rows = rows.filter(_has_effective_link())
    elif filters.public_link == LINK_UNLINKED:
        rows = rows.exclude(_has_effective_link())
    if filters.review == REVIEW_REQUIRED:
        rows = rows.filter(review_required=True)
    elif filters.review == REVIEW_CLEAR:
        rows = rows.filter(review_required=False)

    return rows.order_by(F("start_date").desc(nulls_last=True), "event_name", "service_code")


def count_events_starting_within(
    snapshot: EventProgrammeSnapshot | None = None, *, days: int = NEAR_TERM_DAYS
) -> int:
    """How many events start inside the next `days` days, today included.

    Counted by start date, not by overlap: an event that began earlier and is
    still running is under way, not something starting in the window.
    """
    today = timezone.localdate()
    return (
        _items(snapshot)
        .filter(start_date__gte=today, start_date__lte=today + timedelta(days=days))
        .count()
    )


def count_events_started_within(
    snapshot: EventProgrammeSnapshot | None = None, *, days: int = NEAR_TERM_DAYS
) -> int:
    """How many events started inside the previous `days` days, excluding today.

    Today belongs to the forward window, so the two never count the same event.
    Unlike the public calendar, the workbook retains what already happened, so
    this is a straight count of the current snapshot rather than an archaeology
    of older ones.
    """
    today = timezone.localdate()
    return (
        _items(snapshot)
        .filter(start_date__gte=today - timedelta(days=days), start_date__lt=today)
        .count()
    )


def count_events_for_year(snapshot: EventProgrammeSnapshot | None, year: int | None) -> int:
    """Events whose own start date falls in `year`; the whole snapshot for None."""
    rows = _items(snapshot)
    if year is not None:
        rows = rows.filter(event_year=year)
    return rows.count()


def count_unknown_date_events(snapshot: EventProgrammeSnapshot | None = None) -> int:
    return _items(snapshot).filter(start_date=None).count()


def _has_effective_link() -> Q:
    """Rows a reader would see as a link: the workbook's own, or a matched page.

    The filter and the counts have to ask the same question the table answers,
    or the page contradicts itself — "0 linked" above a column full of links.
    """
    return Q(public_url__gt="") | Q(event_id__in=matched_event_ids())


def count_linked_events(snapshot: EventProgrammeSnapshot | None = None) -> int:
    """Events that show a link, from either source."""
    return _items(snapshot).filter(_has_effective_link()).count()


def count_workbook_linked_events(snapshot: EventProgrammeSnapshot | None = None) -> int:
    """Events the Chamber linked by hand. The workbook's own coverage, unchanged."""
    return _items(snapshot).exclude(public_url="").count()


def count_review_required_events(snapshot: EventProgrammeSnapshot | None = None) -> int:
    return _items(snapshot).filter(review_required=True).count()


def get_upcoming_programme_events(
    snapshot: EventProgrammeSnapshot | None = None, *, limit: int = 4
):
    """The next events the Chamber has scheduled, soonest first.

    Filtered by date at read time as well as trusting `event_status`, because a
    snapshot published a fortnight ago still calls a since-finished event
    `upcoming`. An event whose end date is today is still under way.
    """
    today = timezone.localdate()
    return (
        _items(snapshot)
        .filter(Q(end_date__isnull=True, start_date__gte=today) | Q(end_date__gte=today))
        .order_by("start_date", "event_name", "service_code")[:limit]
    )
