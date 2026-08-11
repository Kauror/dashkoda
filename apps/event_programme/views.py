"""The Sündmused page.

An ordinary protected page that reads PostgreSQL only. It never downloads the
workbook and never calls Koda.ee: collection is the scheduled
`sync_event_programme` command, and the public calendar has its own scheduled
collector.

The page's subject is the Chamber's own event programme — the whole available
history from the canonical Excel export. The public Koda.ee calendar appears
once, at the foot, as a named secondary connection. It is read here only to say
whether it is collecting and how many publicly announced upcoming events it
currently lists; it supplies no figure the programme states, no link and no row.
"""

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.dashboard.freshness import current_freshness
from apps.dashboard.live_search import push_url, search_fragment
from apps.dashboard.navigation import NAVIGATION

# The one deliberate cross-domain read on this page. `apps.events` is a separate
# feed with separate snapshots; nothing below merges it into the programme.
from apps.events.selectors import count_upcoming_within, get_event_summary

from .page import build_programme_page
from .selectors import get_event_programme_summary


@require_GET
def event_programme_overview(request):
    summary = get_event_programme_summary()
    public_calendar = get_event_summary()
    return render(
        request,
        "event_programme/overview.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "events",
            # The shell row reuses the summary this page already read, so the
            # programme is loaded exactly once per request.
            "freshness": current_freshness(summary),
            "summary": summary,
            "page": build_programme_page(summary, request.GET),
            "public_calendar": public_calendar,
            "public_upcoming_count": (
                count_upcoming_within(public_calendar.snapshot)
                if public_calendar.has_data
                else None
            ),
        },
    )


#: The filter form's own fields. `sort` is among them: the ordering is chosen by
#: links, so the form carries it as a hidden input to keep a filter change from
#: silently resetting the order.
PROGRAMME_FIELDS = (
    "q",
    "year",
    "month",
    "quarter",
    "tag",
    "status",
    "public_link",
    "review",
    "sort",
)

#: Everything this page understands, `page` included. Only these reach a pushed
#: URL, because that value ends up in somebody's address bar.
PROGRAMME_PARAMS = (*PROGRAMME_FIELDS, "page")


@require_GET
def programme_search_fragment(request):
    """The programme rows alone, for a reader typing or choosing a filter.

    Page one, always. A reader on page 6 who narrows the filters is asking a
    new question, and carrying the page number into it would answer "no events
    match" for every filter matching fewer rows than that page. `page` is
    stripped from the parameters before they reach the builder and dropped from
    the pushed URL to match.

    The public calendar at the foot of the page is not read here: it is a
    separate feed, it is outside the swapped region, and no filter changes it.
    """
    params = request.GET.copy()
    params.pop("page", None)
    return search_fragment(
        request,
        "event_programme/partials/_programme_results.html",
        {"page": build_programme_page(get_event_programme_summary(), params)},
        pushed=push_url(
            request,
            path=reverse("events"),
            allowed=PROGRAMME_PARAMS,
            # Every field the form carries, taken from the form rather than from
            # the reader's current URL: unlike the other four searches, this one
            # can *clear* a filter, and a key absent from the submission has to
            # disappear from the address bar rather than survive in it.
            updates={key: params.get(key, "") for key in PROGRAMME_FIELDS} | {"page": ""},
        ),
    )
