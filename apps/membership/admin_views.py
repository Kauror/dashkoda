"""Staff-only entry and correction of internal membership reports.

These views live inside the `/admin/` boundary and are wrapped in
`admin.site.admin_view`, so they require an active Django staff account. The
viewer PIN alone does not reach them: the PIN middleware guards `/admin/` as
well, which means a viewer must pass both gates and an ordinary viewer has no
staff account to pass the second with.

The flow is two-step and stateless. The preview step re-renders the same form
with everything the user typed still in it and saves absolutely nothing; the
confirm step is a second submission of that same form. There is no draft record,
no session copy of the report and therefore nothing half-written to clean up if
the user closes the tab.

Publication is Post/Redirect/Get, and a repeated confirmation is recognised by
the report's content hash, so pressing the button twice lands on the same
observation rather than creating a second one.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .bootstrap import ensure_internal_membership_source
from .forms import InternalMembershipReportForm, initial_from_observation
from .internal_selectors import get_manual_entry_defaults, get_observation_detail
from .manual import ManualEntryError, build_preview, publish_manual_report
from .models import InternalMembershipObservation, QualityStatus

ACTION_PREVIEW = "preview"
ACTION_CONFIRM = "confirm"


def _render_form(request, form, *, preview=None, correcting=None, defaults=None):
    return render(
        request,
        "membership/admin/manual_report_form.html",
        {
            **admin.site.each_context(request),
            "title": ("Loo parandatud versioon" if correcting else "Lisa liikmeskonna aruanne"),
            "form": form,
            "preview": preview,
            "correcting": correcting,
            "defaults": defaults or {},
            "action_preview": ACTION_PREVIEW,
            "action_confirm": ACTION_CONFIRM,
        },
    )


def _handle(request, *, correcting: InternalMembershipObservation | None = None):
    source = ensure_internal_membership_source()

    if request.method == "GET":
        initial = initial_from_observation(correcting) if correcting else {}
        form = InternalMembershipReportForm(source=source, initial=initial)
        year = correcting.observation_date.year if correcting else _current_year()
        return _render_form(
            request,
            form,
            correcting=correcting,
            defaults=get_manual_entry_defaults(year),
        )

    form = InternalMembershipReportForm(request.POST, source=source)
    if not form.is_valid():
        return _render_form(request, form, correcting=correcting)

    report = form.to_report()
    preview = build_preview(report, source=source)
    defaults = get_manual_entry_defaults(report.monthly_year or report.observation_date.year)

    # The preview step is the default for anything that is not an explicit
    # confirmation, so a stray submit can never publish.
    if request.POST.get("action") != ACTION_CONFIRM or not preview.can_publish:
        return _render_form(
            request, form, preview=preview, correcting=correcting, defaults=defaults
        )

    try:
        observation = publish_manual_report(report, actor=request.user)
    except ManualEntryError as error:
        messages.error(request, str(error))
        return _render_form(
            request, form, preview=preview, correcting=correcting, defaults=defaults
        )

    messages.success(
        request,
        f"Liikmeskonna aruanne {observation.observation_date:%d.%m.%Y} on salvestatud.",
    )
    return redirect("membership-admin-report-detail", pk=observation.pk)


def _current_year() -> int:
    return timezone.localdate().year


@require_http_methods(["GET", "POST"])
def manual_report_new(request):
    return _handle(request)


@require_http_methods(["GET", "POST"])
def manual_report_correct(request, pk: int):
    """Open the form prefilled from an existing observation.

    A superseded row cannot be corrected again: the correction that replaced it
    is the current record, and correcting the retired one would produce two
    competing revisions of the same date.
    """
    observation = get_object_or_404(
        InternalMembershipObservation.objects.prefetch_related("size_movements", "removal_reasons"),
        pk=pk,
        source__slug=ensure_internal_membership_source().slug,
    )
    if observation.quality_status == QualityStatus.SUPERSEDED:
        raise Http404("Asendatud vaatlust ei saa uuesti parandada.")
    return _handle(request, correcting=observation)


@require_http_methods(["GET"])
def manual_report_detail(request, pk: int):
    """Read-only confirmation of what was saved.

    The landing page of the redirect after publication, and the page the admin
    links to. It shows the stored record; nothing on it can change one.
    """
    point = get_observation_detail(pk)
    if point is None:
        raise Http404("Vaatlust ei leitud.")
    return render(
        request,
        "membership/admin/manual_report_detail.html",
        {
            **admin.site.each_context(request),
            "title": f"Liikmeskonna aruanne {point.observation_date:%d.%m.%Y}",
            "point": point,
            "observation": point.observation,
            "admin_change_url": reverse(
                "admin:membership_internalmembershipobservation_change",
                args=[point.observation.pk],
            ),
        },
    )
