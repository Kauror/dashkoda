"""The internal opinion resource page, and the one way to read a private PDF.

Every document in the managed store is private Chamber correspondence. There is
no static route to it, no media route, and no path on any served root — the only
way a byte of it reaches a browser is the view below, and that view answers a
question rather than a path.

The request carries an **opaque identifier**, never a filename and never a
storage key. Resolving it means: find a relation whose decision is current, on
the current legal snapshot, against the current catalogue, for an
opinion-eligible record, whose blob passed validation. Any link in that chain
missing means 404 — the same answer as "no such document", because telling an
unauthorised caller the difference is itself a disclosure.

Path traversal is not defended against by inspecting the identifier, because the
identifier never becomes a path. The digest does, through
`opinion_storage.resolve_within_store`, which compares the *resolved* target
against the store root.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from django.http import FileResponse, Http404
from django.shortcuts import render
from django.utils.encoding import iri_to_uri
from django.views.decorators.http import require_safe

from .opinion_eligibility import opinion_eligible_q
from .opinion_match_models import LegalOpinionDocumentRelation, OpinionResource
from .opinion_pdf import ValidationStatus
from .opinion_storage import StorageError, blob_path

PDF_CONTENT_TYPE = "application/pdf"


def _current_relations():
    """Relations a viewer may see, with every staleness check in the query.

    Expressed against the database rather than in Python so a stale snapshot
    cannot be filtered out "later" by code that forgets to.
    """
    return (
        LegalOpinionDocumentRelation.objects.filter(
            decision__snapshot__is_current=True,
            decision__legal_item__snapshot__is_current=True,
            entry__snapshot__is_current=True,
            entry__blob__validation_status=ValidationStatus.VALID,
        )
        .filter(opinion_eligible_q("decision__legal_item__"))
        .select_related("entry", "entry__blob", "entry__extraction", "decision")
    )


@require_safe
def opinion_resource(request, public_id):
    """The stable page a sent legal topic links to."""
    resource = OpinionResource.objects.filter(public_id=public_id).select_related("matter").first()
    if resource is None or resource.matter.has_ambiguous_identity:
        raise Http404

    relations = list(_current_relations().filter(decision__matter=resource.matter))
    primary = next((r for r in relations if r.is_primary), None)
    secondary = [r for r in relations if not r.is_primary]

    # A matter that has left the current workbook keeps a read-only heading from
    # its durable metadata rather than vanishing: the address was published and
    # a dead page is worse than a historical one.
    item = None
    decision = relations[0].decision if relations else None
    if decision is not None:
        item = decision.legal_item

    return render(
        request,
        "legal_work/opinion_resource.html",
        {
            "resource": resource,
            "matter": resource.matter,
            "item": item,
            "primary": primary,
            "secondary": secondary,
            "is_historical": item is None,
        },
    )


@require_safe
def opinion_document(request, public_id):
    """Serve one private PDF, or 404. Never anything in between."""
    relation = _current_relations().filter(entry__blob__public_id=public_id).first()
    if relation is None:
        raise Http404

    blob = relation.entry.blob
    try:
        path = blob_path(blob.sha256)
    except StorageError:
        raise Http404 from None

    if not path.is_file():
        # The row survives a missing file; the response does not invent one.
        raise Http404

    response = FileResponse(path.open("rb"), content_type=PDF_CONTENT_TYPE)
    response["X-Content-Type-Options"] = "nosniff"
    # Private correspondence: never stored by a shared cache, never revalidated
    # from one.
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Content-Disposition"] = _disposition(relation.entry.display_filename)
    # Nothing that could reconstruct a path or identify the file on disk.
    response["Content-Length"] = str(path.stat().st_size)
    return response


def _disposition(display_name: str) -> str:
    """A download header that cannot carry a path, a quote or a newline."""
    safe = Path(display_name or "dokument.pdf").name
    safe = "".join(c for c in safe if c.isprintable() and c not in '"\\/\r\n')[:120]
    if not safe.lower().endswith(".pdf"):
        safe = f"{safe}.pdf"
    ascii_fallback = safe.encode("ascii", "ignore").decode() or "dokument.pdf"
    return f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{iri_to_uri(safe)}"


# Registered so a mistyped content type cannot make a browser sniff a PDF.
mimetypes.add_type(PDF_CONTENT_TYPE, ".pdf")
