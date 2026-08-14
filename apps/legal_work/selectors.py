"""Read paths for the legal-work dashboard.

Every query lives here rather than in a template or a view, so the definition
of "currently open" or "latest sent" has exactly one home.

All of them read the current snapshot only. When an older snapshot exists but
none is current, the answer is an empty state: showing retired data as if it
were live would be worse than showing nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from apps.core.feeds import FeedSummaryMixin

from .models import LegalWorkFeedState, LegalWorkItem, LegalWorkSnapshot, SentStatus

DEFAULT_RECENT_LIMIT = 15
# The open list is bounded so a workbook that grows cannot produce an
# unbounded page.
MAX_OPEN_ITEMS = 200

# The window the overview reports activity over. A month is short enough that a
# board recognises the period and long enough that a quiet fortnight does not
# read as a stalled department.
ACTIVITY_WINDOW_DAYS = 30

# How far ahead a deadline has to be before it stops being something the board
# needs to see on the front page.
DEADLINE_HORIZON_DAYS = 21
DEADLINE_PREVIEW_LIMIT = 5

# Thresholds for how a remaining deadline is described. Expressed in whole days
# because the workbook records a date and never a time.
DEADLINE_URGENT_DAYS = 3
DEADLINE_SOON_DAYS = 10


def get_current_snapshot() -> LegalWorkSnapshot | None:
    return (
        LegalWorkSnapshot.objects.filter(
            source__slug=settings.LEGAL_WORK_SOURCE_SLUG,
            is_current=True,
        )
        .select_related("source")
        .first()
    )


def _items(snapshot: LegalWorkSnapshot | None):
    if snapshot is None:
        return LegalWorkItem.objects.none()
    return LegalWorkItem.objects.filter(snapshot=snapshot)


def get_open_items(snapshot: LegalWorkSnapshot | None = None, limit: int | None = MAX_OPEN_ITEMS):
    """Topics still being worked on, most recently received first."""
    snapshot = snapshot or get_current_snapshot()
    # PostgreSQL puts NULLs first in a descending order, so `nulls_last` is
    # explicit: dated records lead and undated ones trail by topic.
    queryset = (
        _items(snapshot)
        .filter(is_open=True)
        .order_by(F("received_date").desc(nulls_last=True), "topic", "record_id")
    )
    return queryset[:limit] if limit else queryset


def get_open_items_by_deadline(
    snapshot: LegalWorkSnapshot | None = None, limit: int | None = MAX_OPEN_ITEMS
):
    """Topics still in work, the ones closest to going out first.

    Ordered by the opinion deadline rather than by arrival, because what the
    board wants off this list is what has to leave next. A topic whose deadline
    has already passed is *not* filtered out the way `get_upcoming_deadlines`
    filters it: there the block asks "what can you still act on", while here the
    question is "what is in work", and something overdue is in work most of all.

    A record with no deadline trails the dated ones rather than leading them. In
    a descending order PostgreSQL puts NULLs first, so `nulls_last` is spelled
    out; ascending needs it too, since an undated record is not the most urgent.
    """
    snapshot = snapshot or get_current_snapshot()
    queryset = (
        _items(snapshot)
        .filter(is_open=True)
        .order_by(F("deadline_date").asc(nulls_last=True), "topic", "record_id")
    )
    return queryset[:limit] if limit else queryset


def get_latest_sent_items(
    snapshot: LegalWorkSnapshot | None = None, limit: int = DEFAULT_RECENT_LIMIT
):
    """Most recently sent opinions.

    `not_sent` never appears here: a record that was explicitly not sent is not
    a recent send, and the model constraint already guarantees it carries no
    date to sort by.
    """
    snapshot = snapshot or get_current_snapshot()
    return (
        _items(snapshot)
        .filter(sent_status=SentStatus.SENT, sent_date__isnull=False)
        .order_by("-sent_date", "topic", "record_id")[:limit]
    )


def get_newest_received_items(
    snapshot: LegalWorkSnapshot | None = None, limit: int = DEFAULT_RECENT_LIMIT
):
    """Most recently received topics.

    A received date in the future is a known workbook data problem, flagged by
    the generator's `received_date_in_future` warning. Such a record would
    otherwise sit permanently at the top of this list, so it is excluded here
    while remaining fully present in the imported data.
    """
    snapshot = snapshot or get_current_snapshot()
    return (
        _items(snapshot)
        .filter(received_date__isnull=False, received_date__lte=_today())
        .order_by("-received_date", "topic", "record_id")[:limit]
    )


#: What a search may look at. Every one of these is a field a lawyer would
#: recognise from the workbook itself; nothing is matched against an internal
#: key the reader has never seen.
SEARCH_FIELDS = ("topic", "record_id", "act_type", "recipient", "stage", "next_step")

#: How long a search term may be. It reaches the ORM as a parameter and never as
#: SQL; the cap is here so a pathological query string cannot become a
#: pathological `LIKE`.
MAX_SEARCH_LENGTH = 120

#: The statuses a search can be narrowed to.
SEARCH_ALL = ""
SEARCH_OPEN = "toos"
SEARCH_SENT = "valjas"
SEARCH_STATUSES = (SEARCH_ALL, SEARCH_OPEN, SEARCH_SENT)

#: Deadline states the register can be narrowed to, measured from the snapshot's
#: own reporting date rather than from today.
#:
#: `DEADLINE_OVERDUE` deliberately means *passed and still unanswered*. A matter
#: whose opinion has already gone out and which remains open — waiting on a
#: committee, waiting to come into force — is not late, and a filter that swept
#: it up would manufacture a backlog out of ordinary process.
DEADLINE_ANY = ""
DEADLINE_OVERDUE = "moodas"
DEADLINE_WEEK = "7"
DEADLINE_FORTNIGHT = "14"
DEADLINE_THREE_WEEKS = "21"
DEADLINE_NONE = "puudub"
DEADLINE_STATES = (
    DEADLINE_ANY,
    DEADLINE_OVERDUE,
    DEADLINE_WEEK,
    DEADLINE_FORTNIGHT,
    DEADLINE_THREE_WEEKS,
    DEADLINE_NONE,
)
DEADLINE_HORIZONS = {DEADLINE_WEEK: 7, DEADLINE_FORTNIGHT: 14, DEADLINE_THREE_WEEKS: 21}

#: Member-feedback states. Three, not two, because the whole point is that an
#: untracked row and a row measured at zero are different facts: `FEEDBACK_ZERO`
#: is "somebody counted, and the answer was none", `FEEDBACK_UNTRACKED` is
#: "nobody counted". Collapsing them would be the same error as writing 0 into
#: an empty cell.
FEEDBACK_ANY = ""
FEEDBACK_PRESENT = "on"
FEEDBACK_ZERO = "null"
FEEDBACK_UNTRACKED = "puudub"
FEEDBACK_STATES = (FEEDBACK_ANY, FEEDBACK_PRESENT, FEEDBACK_ZERO, FEEDBACK_UNTRACKED)


def search_items(
    snapshot: LegalWorkSnapshot | None = None,
    *,
    query: str = "",
    status: str = SEARCH_ALL,
    source_year: int | None = None,
    stage_key: str = "",
    recipient: str = "",
    act_type: str = "",
    deadline: str = DEADLINE_ANY,
    feedback: str = FEEDBACK_ANY,
):
    """Every record in the current snapshot matching `query`.

    **The whole register, not the two lists the page draws.** `Hetkel töös` is
    eighteen open records and `Viimati välja läinud` is the fifteen most recent
    sends; the snapshot holds six hundred. A topic concluded last spring was
    therefore invisible on this page however well you knew its name, which is
    what this answers.

    Bounded the same way everything else here is: the current snapshot only, so
    a retired revision can never answer a search, and a term that reaches no
    field returns nothing rather than everything.
    """
    snapshot = snapshot or get_current_snapshot()
    queryset = _items(snapshot)

    if status == SEARCH_OPEN:
        queryset = queryset.filter(is_open=True)
    elif status == SEARCH_SENT:
        queryset = queryset.filter(sent_status=SentStatus.SENT, sent_date__isnull=False)

    query = (query or "").strip()
    if query:
        matching = Q()
        for field in SEARCH_FIELDS:
            matching |= Q(**{f"{field}__icontains": query})
        queryset = queryset.filter(matching)

    # Every value below has already been checked against a closed set, or
    # against the categories this snapshot actually contains, before it gets
    # here. Nothing arrives straight from the query string.
    if source_year is not None:
        queryset = queryset.filter(source_year=source_year)
    if stage_key:
        queryset = queryset.filter(stage_key=stage_key)
    if recipient:
        queryset = queryset.filter(recipient=recipient)
    if act_type:
        queryset = queryset.filter(act_type=act_type)

    queryset = _apply_deadline_filter(queryset, snapshot, deadline)

    if feedback == FEEDBACK_PRESENT:
        queryset = queryset.filter(feedback_member_count__gt=0)
    elif feedback == FEEDBACK_ZERO:
        queryset = queryset.filter(feedback_member_count=0)
    elif feedback == FEEDBACK_UNTRACKED:
        queryset = queryset.filter(feedback_member_count__isnull=True)

    # Newest arrival first, undated last, then a stable tie-break so two renders
    # of one search never disagree about the order.
    return queryset.order_by(F("received_date").desc(nulls_last=True), "topic", "record_id")


def _apply_deadline_filter(queryset, snapshot: LegalWorkSnapshot | None, deadline: str):
    """Narrow by how the opinion deadline sits against the reporting date.

    The reporting date, not today: a filter measured against the wall clock
    would put a record in a different band depending on when the page happened
    to be loaded, while the data underneath had not moved at all.
    """
    if deadline == DEADLINE_ANY or snapshot is None:
        return queryset

    reporting_date = snapshot.reporting_date

    if deadline == DEADLINE_NONE:
        return queryset.filter(deadline_date__isnull=True)

    if deadline == DEADLINE_OVERDUE:
        # Passed *and* still unanswered. A matter whose opinion already went out
        # is not late, however old its deadline.
        return queryset.filter(
            deadline_date__isnull=False,
            deadline_date__lt=reporting_date,
        ).exclude(sent_status=SentStatus.SENT)

    horizon = DEADLINE_HORIZONS.get(deadline)
    if horizon is None:
        return queryset
    return queryset.filter(
        deadline_date__isnull=False,
        deadline_date__gte=reporting_date,
        deadline_date__lte=reporting_date + timedelta(days=horizon),
    )


def count_received_since(snapshot: LegalWorkSnapshot | None, since: date) -> int:
    """Topics received between `since` and today, both inclusive.

    Bounded at both ends. The upper bound matters: the workbook is known to
    carry the occasional future received date, and counting those would make
    the window report more arrivals than actually arrived.
    """
    snapshot = snapshot or get_current_snapshot()
    return _items(snapshot).filter(received_date__gte=since, received_date__lte=_today()).count()


def count_sent_since(snapshot: LegalWorkSnapshot | None, since: date) -> int:
    """Opinions sent between `since` and today, both inclusive."""
    snapshot = snapshot or get_current_snapshot()
    return (
        _items(snapshot)
        .filter(sent_status=SentStatus.SENT, sent_date__gte=since, sent_date__lte=_today())
        .count()
    )


@dataclass(frozen=True)
class Deadline:
    """One approaching opinion deadline, and how close it is.

    The urgency is derived here rather than in the template so that "urgent"
    means the same thing everywhere it is drawn, and so the label exists as text
    beside the colour.
    """

    item: LegalWorkItem
    days_remaining: int

    @property
    def is_urgent(self) -> bool:
        return self.days_remaining <= DEADLINE_URGENT_DAYS

    @property
    def variant(self) -> str:
        if self.is_urgent:
            return "danger"
        return "warning" if self.days_remaining <= DEADLINE_SOON_DAYS else "info"

    @property
    def remaining_label(self) -> str:
        if self.days_remaining == 0:
            return "täna"
        if self.days_remaining == 1:
            return "1 päev"
        return f"{self.days_remaining} päeva"


def get_upcoming_deadlines(
    snapshot: LegalWorkSnapshot | None = None,
    *,
    within_days: int = DEADLINE_HORIZON_DAYS,
    limit: int = DEADLINE_PREVIEW_LIMIT,
) -> tuple[Deadline, ...]:
    """Open topics whose opinion deadline falls inside the horizon.

    Only open records: a deadline on something already concluded is history, not
    something the board can still act on. A deadline that has already passed is
    excluded too — the workbook is the place to correct it, and surfacing it
    here would read as an action that is still available.
    """
    snapshot = snapshot or get_current_snapshot()
    today = _today()
    items = (
        _items(snapshot)
        .filter(
            is_open=True,
            deadline_date__isnull=False,
            deadline_date__gte=today,
            deadline_date__lte=today + timedelta(days=within_days),
        )
        .order_by("deadline_date", "topic", "record_id")[:limit]
    )
    return tuple(
        Deadline(item=item, days_remaining=(item.deadline_date - today).days) for item in items
    )


def _today() -> date:
    return timezone.localdate()


@dataclass(frozen=True)
class LegalWorkSummary(FeedSummaryMixin):
    """Everything the dashboard needs to describe the data's state honestly."""

    snapshot: LegalWorkSnapshot | None
    feed_state: LegalWorkFeedState | None

    @property
    def has_data(self) -> bool:
        return self.snapshot is not None

    @property
    def open_count(self) -> int:
        return self.snapshot.open_record_count if self.snapshot else 0

    @property
    def total_count(self) -> int:
        return self.snapshot.total_record_count if self.snapshot else 0

    @property
    def reporting_date(self):
        return self.snapshot.reporting_date if self.snapshot else None

    @property
    def generated_at(self):
        return self.snapshot.workbook_generated_at if self.snapshot else None


def get_legal_work_summary() -> LegalWorkSummary:
    snapshot = get_current_snapshot()
    feed_state = (
        LegalWorkFeedState.objects.filter(source__slug=settings.LEGAL_WORK_SOURCE_SLUG)
        .select_related("source")
        .first()
    )
    return LegalWorkSummary(snapshot=snapshot, feed_state=feed_state)
