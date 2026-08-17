from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.event_programme.intelligence import build_coverage
from apps.event_programme.selectors import get_event_programme_summary
from apps.events.selectors import count_upcoming_within, get_event_summary
from apps.legal_work.analytics import data_quality
from apps.legal_work.selectors import get_legal_work_summary
from apps.membership.composition_selectors import get_current_composition_snapshot
from apps.membership.intelligence import build_quality_badge, build_source_stamps
from apps.membership.internal_selectors import (
    get_internal_membership_latest,
    get_internal_membership_observations,
    get_internal_membership_quality_summary,
)
from apps.membership.reconciliation import RECONCILIATION_LOOKBACK_YEARS, reconcile_history
from apps.membership.register_selectors import get_current_register_snapshot
from apps.membership.selectors import get_membership_summary
from apps.news.selectors import get_news_summary
from apps.visibility.ga4_selectors import get_coverage
from apps.visibility.website_period import get_period_coverage

from .executive import build_data_status, build_executive_overview
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

    ## What is here

    The dashboards carry data-quality warnings, source coverage, import status
    and provenance notes mixed in among the figures a board member came for.
    This is where that material collects, one dashboard at a time.

    **Andmete seis** — every business source's own state, coverage and
    limitation — left Koja töölaud on 2026-08-15 and is here now. The overview
    keeps only the header chip that counts what is worth disclosing, and that
    chip links here.

    **Sündmused' provenance block** came the same day. Its `Andmete kohta`
    left every events focus and arrives here whole — the export's schema and
    generator versions, the coverage denominators, the Commerce join and the
    public Koda.ee calendar's own connection state. Nothing was summarised on
    the way: a diagnostic that lost half its numbers in a move would be worse
    where it landed than where it started.

    The other two sections are still empty, and say so rather than counting to
    zero. `0 probleemi` beside a check nobody has moved yet would report the
    absence of the check as the absence of problems.

    This page still grants nothing and accepts no input. The events block reads
    the same selectors the events page read.
    """
    programme = get_event_programme_summary()
    public_calendar = get_event_summary()

    # Õigusloome's own summary read, the same function its own page reads —
    # its `Andmete seis` moved here whole on 2026-08-17.
    legal_work_summary = get_legal_work_summary()

    # Liikmeskond's own provenance read, moved here whole the same day. The
    # same functions and the same bounded reconciliation lookback its own
    # page used — see `apps/membership/views.py` before 2026-08-17.
    membership_latest = get_internal_membership_latest()
    membership_quality = get_internal_membership_quality_summary()
    membership_composition = get_current_composition_snapshot()
    membership_register = get_current_register_snapshot()
    if membership_latest is not None:
        membership_reconciliation_history = get_internal_membership_observations(
            date_from=membership_latest.observation_date.replace(
                year=membership_latest.observation_date.year - RECONCILIATION_LOOKBACK_YEARS
            ),
            date_to=membership_latest.observation_date,
        )
    else:
        membership_reconciliation_history = ()

    # Koduleht's coverage is reported over the **whole** collected history, not
    # over a default window. There is no period control on this page, and a
    # table quietly describing the last thirty days would be read as describing
    # the source. `get_period_coverage` needs a span, so it is given the one the
    # history actually has.
    website_coverage = get_coverage()
    website_period_coverage = (
        get_period_coverage(website_coverage.earliest, website_coverage.latest)
        if website_coverage.has_data
        else None
    )

    return render(
        request,
        "dashboard/admin.html",
        {
            "navigation": NAVIGATION,
            "active_nav": "admin",
            "data_status": build_data_status(),
            "events_quality": build_coverage(programme.snapshot),
            "public_calendar": public_calendar,
            "public_upcoming_count": (
                count_upcoming_within(public_calendar.snapshot)
                if public_calendar.has_data
                else None
            ),
            "website_coverage": website_coverage,
            "website_period_coverage": website_period_coverage,
            "legal_work_summary": legal_work_summary,
            "legal_work_quality": (
                data_quality(legal_work_summary.snapshot) if legal_work_summary.has_data else None
            ),
            "membership_source_stamps": build_source_stamps(
                latest=membership_latest,
                quality=membership_quality,
                composition_date=(
                    membership_composition.snapshot_date if membership_composition else None
                ),
                register_date=(membership_register.snapshot_date if membership_register else None),
            ),
            "membership_latest": membership_latest,
            "membership_quality": membership_quality,
            "membership_quality_badge": build_quality_badge(membership_quality),
            "membership_reconciliations": reconcile_history(membership_reconciliation_history),
            "can_add_data": request.user.is_authenticated and request.user.is_staff,
        },
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
