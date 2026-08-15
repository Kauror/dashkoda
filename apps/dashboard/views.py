from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.event_programme.selectors import get_event_programme_summary
from apps.legal_work.selectors import get_legal_work_summary
from apps.membership.selectors import get_membership_summary
from apps.news.selectors import get_news_summary

from .executive import build_executive_overview
from .freshness import current_freshness
from .navigation import NAVIGATION


@require_GET
def overview(request):
    """`Koja töölaud` — the executive overview.

    The board's landing page, and deliberately the only page in DashKoda that
    spans every domain. It answers where the Chamber stands, what needs
    attention, what is coming in the next thirty days, what audiences are using
    and whether the data can be trusted — then links to the dashboard that
    explains each of those.

    It is an orientation layer, not an analytical one. Nothing here paginates,
    filters, sorts or exports, and no section reproduces a domain dashboard: the
    question this page answers is "which one should I open".

    ## No period control, on purpose

    The four summaries below are read once and handed both to the shell
    freshness row and to `build_executive_overview`, so each costs its two
    indexed queries once per request rather than three times.

    There is no query parameter. The previous overview accepted a membership
    trend range, and that control moved to the Liikmeskond page with the chart it
    governed. A single period control across this page would be worse than no
    control: the domains have genuinely different time semantics — a latest
    observation, a year-to-date cutoff, thirty measured days, a Commerce export's
    own window — and one selector over them would imply a comparability that does
    not exist. Every figure states its own period instead.

    Nothing here reaches outside PostgreSQL. No page render contacts Koda.ee,
    GA4, Smaily, OneDrive or Commerce; those are scheduled commands, and a
    request that waited on one of them would be a page that fails when a remote
    system does.
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
        "page": build_executive_overview(
            legal_work=legal_work,
            membership=membership,
            news=news,
            events=events,
        ),
    }
    return render(request, "dashboard/overview.html", context)


@require_GET
def admin_area(request):
    """`Admin` at `/haldus/` — where DashKoda's technical material will live.

    An ordinary dashboard page behind the same viewer PIN as every other one. It
    is **not** an administration interface: it grants nothing, changes nothing
    and accepts no input. Django's admin site and the staff data-entry
    workflows keep `/admin/` and their own `is_staff` requirement, untouched.

    ## Why it exists before it has content

    The dashboards carry data-quality warnings, source coverage, import status
    and provenance notes mixed in among the figures a board member came for.
    That material has to go somewhere before it can be taken out of those pages,
    and moving it needs a destination that already exists, is already routed and
    is already in the shell. This is that destination.

    It is deliberately empty. Each section states what it is for and shows
    nothing else — no invented count, no fabricated warning and above all no
    "0 probleemi", which would be this page reporting its own emptiness as a
    clean bill of health for checks that are not running yet. The material
    arrives one diagnostic at a time in later work; until then a quiet page is
    the honest one.
    """
    return render(
        request,
        "dashboard/admin.html",
        {"navigation": NAVIGATION, "active_nav": "admin"},
    )


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
