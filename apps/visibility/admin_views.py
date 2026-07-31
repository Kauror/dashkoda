"""Staff-only entry, correction and inspection of audience figures.

These views live inside the `/admin/` boundary and are wrapped in
`admin.site.admin_view`, so they require an active Django staff account. The
viewer PIN alone does not reach them: the PIN middleware guards `/admin/` as
well, which means a viewer must pass both gates and an ordinary viewer has no
staff account to pass the second with.

The flow is two-step and stateless, exactly like the membership report's. The
preview step re-renders the same form with everything the user typed still in it
and saves absolutely nothing; the confirm step is a second submission of the same
form. There is no draft record, no session copy and therefore nothing
half-written to clean up if the user closes the tab.

Publication is Post/Redirect/Get, and a repeated confirmation is recognised by
the submission's content hash — which is unique in the database, not merely
checked in a query — so pressing the button twice lands on the same batch rather
than creating a second one.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import VisibilityEntryForm, initial_from_batch
from .manual import VisibilityEntryError, build_preview, publish_submission
from .models import VisibilityEntryBatch
from .selectors import (
    get_batch_detail,
    get_manual_entry_defaults,
    get_visibility_entry_history,
)

ACTION_PREVIEW = "preview"
ACTION_CONFIRM = "confirm"


def _rows_with_latest(rows, latest):
    """Pair each form field with the value it would be replacing.

    Done here rather than in the template because a Django template cannot index
    a dict by a variable key, and done here rather than in the form because the
    form has no business querying the database.
    """
    return [(spec, field, latest.get(spec.key)) for spec, field in rows]


def _render_form(request, form, *, preview=None, correcting=None):
    # The latest stored reading for every metric, shown beside its own input so
    # the person typing sees what they are replacing *before* they submit rather
    # than after.
    latest = get_manual_entry_defaults()
    return render(
        request,
        "visibility/admin/entry_form.html",
        {
            **admin.site.each_context(request),
            "title": ("Paranda kanalite näitajaid" if correcting else "Lisa kanalite näitajad"),
            "form": form,
            "preview": preview,
            "correcting": correcting,
            "newsletter_rows": _rows_with_latest(form.newsletter_rows, latest),
            "social_rows": _rows_with_latest(form.social_rows, latest),
            "action_preview": ACTION_PREVIEW,
            "action_confirm": ACTION_CONFIRM,
        },
    )


def _handle(request, *, correcting: VisibilityEntryBatch | None = None):
    if request.method == "GET":
        initial = initial_from_batch(correcting) if correcting else {}
        return _render_form(request, VisibilityEntryForm(initial=initial), correcting=correcting)

    form = VisibilityEntryForm(request.POST)
    if not form.is_valid():
        return _render_form(request, form, correcting=correcting)

    submission = form.to_submission()
    preview = build_preview(submission)

    # The preview step is the default for anything that is not an explicit
    # confirmation, so a stray submit can never publish.
    if request.POST.get("action") != ACTION_CONFIRM or not preview.can_publish:
        return _render_form(request, form, preview=preview, correcting=correcting)

    try:
        batch = publish_submission(submission, actor=request.user)
    except VisibilityEntryError as error:
        messages.error(request, str(error))
        return _render_form(request, form, preview=preview, correcting=correcting)

    messages.success(
        request,
        f"Kanalite näitajad seisuga {batch.observation_date:%d.%m.%Y} on salvestatud.",
    )
    return redirect("visibility-admin-entry-detail", pk=batch.pk)


@require_http_methods(["GET", "POST"])
def entry_new(request):
    return _handle(request)


@require_http_methods(["GET", "POST"])
def entry_correct(request, pk: int):
    """Open the form prefilled from an existing submission.

    Correcting produces a *new* submission whose values supersede the previous
    current ones for the same date. Nothing on the original batch is edited, and
    the original observations keep their numbers.
    """
    batch = get_object_or_404(VisibilityEntryBatch.objects.prefetch_related("observations"), pk=pk)
    return _handle(request, correcting=batch)


@require_http_methods(["GET"])
def entry_detail(request, pk: int):
    """Read-only confirmation of exactly what was published.

    The landing page of the redirect after publication. It shows the stored
    record; nothing on it can change one.
    """
    row = get_batch_detail(pk)
    if row is None:
        raise Http404("Sisestust ei leitud.")
    return render(
        request,
        "visibility/admin/entry_detail.html",
        {
            **admin.site.each_context(request),
            "title": f"Kanalite näitajad {row.observation_date:%d.%m.%Y}",
            "row": row,
            "batch": row.batch,
        },
    )


@require_http_methods(["GET"])
def entry_list(request):
    """Read-only history of manual submissions, newest first."""
    return render(
        request,
        "visibility/admin/entry_list.html",
        {
            **admin.site.each_context(request),
            "title": "Kanalite statistika sisestused",
            "rows": get_visibility_entry_history(),
        },
    )
