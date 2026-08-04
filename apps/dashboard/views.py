from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.events.selectors import get_event_summary
from apps.legal_work.selectors import get_legal_work_summary
from apps.membership.selectors import get_membership_summary
from apps.news.selectors import get_news_summary

from .freshness import current_freshness, freshness_from
from .navigation import NAVIGATION
from .overview import build_overview


@require_GET
def overview(request):
    """The board's landing page.

    Reads each module's summary, hands them to `build_overview` and renders. All
    four wired feeds plus the internal board report contribute where they have
    data; every other part of the page says plainly that it has no source yet.
    Nothing here reaches outside PostgreSQL.

    The shell freshness row is derived from the same four summaries the page
    content uses, so each summary is read exactly once per request.
    """
    legal_work = get_legal_work_summary()
    membership = get_membership_summary()
    news = get_news_summary()
    events = get_event_summary()
    context = {
        "navigation": NAVIGATION,
        "active_nav": "overview",
        "freshness": freshness_from((legal_work, membership, news, events)),
        "legal_work": legal_work,
        "membership": membership,
        "news": news,
        "events": events,
        "page": build_overview(
            legal_work=legal_work,
            membership=membership,
            news=news,
            events=events,
        ),
    }
    return render(request, "dashboard/overview.html", context)


@require_GET
def freshness_fragment(request):
    """Neutral HTMX fragment used to validate the partial-update pattern.

    It is an ordinary protected route: the viewer middleware guards it, and
    without JavaScript the same control falls back to reloading the overview.
    """
    return render(
        request,
        "dashboard/partials/freshness.html",
        {"freshness": current_freshness()},
    )
