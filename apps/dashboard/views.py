from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.event_programme.selectors import get_event_programme_summary
from apps.legal_work.selectors import get_legal_work_summary
from apps.membership.ranges import LEGACY_PARAM, PARAM_FROM, PARAM_TO
from apps.membership.selectors import get_membership_summary
from apps.news.selectors import get_news_summary

from .freshness import current_freshness
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

    The one thing a reader can ask this page for is how much membership history
    the card draws — two dates, plus the retired button control's key so a stale
    bookmark keeps meaning what it meant. The parameters are passed on raw;
    `apps.membership.ranges` decides what they mean, and anything unreadable
    falls back to the default rather than erroring.
    """
    legal_work = get_legal_work_summary()
    membership = get_membership_summary()
    news = get_news_summary()
    # The event figures come from the canonical workbook programme. The public
    # Koda.ee calendar is collected separately and is named on the Sündmused
    # page; it contributes no count here.
    events = get_event_programme_summary()
    context = {
        "navigation": NAVIGATION,
        "active_nav": "overview",
        "freshness": current_freshness(legal_work, membership, news, events),
        "legal_work": legal_work,
        "membership": membership,
        "news": news,
        "events": events,
        "page": build_overview(
            legal_work=legal_work,
            membership=membership,
            news=news,
            events=events,
            trend_from=request.GET.get(PARAM_FROM),
            trend_to=request.GET.get(PARAM_TO),
            trend_range_key=request.GET.get(LEGACY_PARAM),
        ),
    }
    return render(request, "dashboard/overview.html", context)


@require_GET
def freshness_fragment(request):
    """Neutral HTMX fragment used to validate the partial-update pattern.

    It is an ordinary protected route: the viewer middleware guards it, and
    without JavaScript the same control falls back to reloading the overview.

    It has no page content to borrow a summary from, so it reads all four
    itself — which is the whole job of this endpoint.
    """
    return render(
        request,
        "dashboard/partials/freshness.html",
        {"freshness": current_freshness()},
    )
