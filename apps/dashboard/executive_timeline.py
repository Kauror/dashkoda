"""The next thirty days, from the two domains that genuinely have dates.

Legal deadlines and scheduled events are dated facts the Chamber holds. Nothing
else on this dashboard is: membership has no due dates, news has no schedule the
application knows, and a shop product is not owed by any particular day.
Inventing a deadline for those to fill out a timeline would be manufacturing
work that does not exist, so the timeline has exactly two lanes.

## A row links only to itself

An event links to its own public page when the matcher found one. A deadline
links to the Õigusloome list its topic sits in — and only because that list
genuinely contains that row.

What is deliberately not done: linking a deadline to a filtered view that does
not exist, or to the legal page in general on the grounds that the topic is
"somewhere on it". A row with no link is honest and costs the reader one extra
step; a row with a wrong link costs them their trust in every other link on the
page.

## Ordering is total

Date, then domain, then title. Without the last two, two items on the same day
would swap places between requests, and a reader refreshing the page would see
motion where nothing moved.
"""

from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

from apps.core.formatting import short_date
from apps.event_programme.executive import get_timeline_events
from apps.legal_work.executive import get_timeline_deadlines
from apps.legal_work.sections import SECTION_OPEN, anchor

from .executive_models import ExecutiveUpcomingItem

#: The horizon, in days. Thirty is the brief's own window and matches the
#: near-term horizon the Sündmused domain already counts against, so the pillar
#: fact and this list cannot describe different sets of events.
HORIZON_DAYS = 30

#: How many rows the section shows before it stops being a glance. Ten across
#: two domains; each domain is separately bounded at its own selector, so a busy
#: fortnight of deadlines cannot push every event off the list.
TIMELINE_LIMIT = 10

DOMAIN_LEGAL = "legal_work"
DOMAIN_EVENTS = "events"

LABEL_LEGAL = "Õigusloome"
#: Singular, because each row is one. `Sündmused` is the dashboard's name and
#: reads as a section heading when it sits on a line beside a single title.
LABEL_EVENTS = "Sündmus"


def build_timeline(*, legal_summary, events_executive) -> tuple[ExecutiveUpcomingItem, ...]:
    """Merge the two dated lanes into one chronological list.

    Both inputs arrive already read: the legal summary the page loaded for its
    freshness row, and the events executive whose bounded upcoming list this
    lane clips — so the timeline adds one deadline query and no event query.
    """
    rows = [
        *_deadline_rows(legal_summary),
        *_event_rows(events_executive),
    ]
    # Under-way rows last, then by date, then by domain and title. Sorting them
    # by their own start date would put a programme that opened in January above
    # a deadline falling tomorrow, which is the opposite of what this list is
    # read for.
    rows.sort(key=lambda item: (item.is_under_way, item.when, item.domain_key, item.title))
    return tuple(rows[:TIMELINE_LIMIT])


def _deadline_rows(summary) -> list[ExecutiveUpcomingItem]:
    """Open topics whose opinion deadline falls inside the horizon.

    `days_remaining` is stated in words beside the date rather than encoded as a
    colour, so urgency survives a reader who cannot distinguish the two warning
    tones — and survives being printed.
    """
    deadlines = get_timeline_deadlines(summary, within_days=HORIZON_DAYS)
    page = reverse("legal-work")
    rows = []
    for deadline in deadlines:
        item = deadline.item
        rows.append(
            ExecutiveUpcomingItem(
                when=item.deadline_date,
                domain_label=LABEL_LEGAL,
                domain_key=DOMAIN_LEGAL,
                title=item.topic,
                context=_remaining(deadline.days_remaining),
                # `Hetkel töös` lists every open topic, and this topic is open
                # by construction — `get_upcoming_deadlines` filters on it. So
                # the anchor genuinely contains this row.
                url=anchor(page, SECTION_OPEN),
            )
        )
    return rows


def _event_rows(executive) -> list[ExecutiveUpcomingItem]:
    """Scheduled events beginning inside the horizon, plus the ones under way.

    The public link is used when the matcher resolved one, and the row stays
    plain text otherwise. An event DashKoda cannot address is still worth
    knowing is coming.

    **An event that started before today is not dated in this list.** A
    year-long mentoring programme that opened on 1 January sorted to the top of
    `Järgmised 30 päeva` under the date `01.01`, which is not a thing happening
    in the next thirty days — it is a thing already happening. Those rows say
    `kestev` and carry no start date at all, because the date a reader would
    read off them is one they cannot act on.
    """
    today = timezone.localdate()
    rows = []
    for item in get_timeline_events(executive, within_days=HORIZON_DAYS):
        link = getattr(item, "public_link", None)
        url = link.url if link else ""
        started = item.start_date is not None and item.start_date < today
        rows.append(
            ExecutiveUpcomingItem(
                when=item.start_date,
                is_under_way=started,
                domain_label=LABEL_EVENTS,
                domain_key=DOMAIN_EVENTS,
                title=item.event_name,
                context=_event_context(item, under_way=started),
                url=url,
                is_external=bool(url),
            )
        )
    return rows


def _event_context(item, *, under_way: bool = False) -> str:
    """Delivery mode and end date, where the workbook classified them.

    An event already under way leads with `kestev`, which is what its missing
    start date means — the row is in this list because it has not finished, not
    because it is about to begin.

    Never attendance and never capacity: the programme records what was
    scheduled, not who came, and this application has no attendance figure at
    all.
    """
    parts = []
    if under_way:
        parts.append("kestev")
    mode = getattr(item, "delivery_mode", "")
    if mode:
        parts.append(mode)
    end = getattr(item, "end_date", None)
    if end and item.start_date and end != item.start_date:
        parts.append(f"kuni {short_date(end)}")
    return " · ".join(parts)


def _remaining(days: int) -> str:
    """How long is left, in words."""
    if days <= 0:
        return "tähtaeg täna"
    if days == 1:
        return "1 päev jäänud"
    return f"{days} päeva jäänud"


__all__ = [
    "HORIZON_DAYS",
    "TIMELINE_LIMIT",
    "build_timeline",
]
