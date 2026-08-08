"""The internal opinion resource page, and the one way to read a stored PDF.

Every document in the managed store is Chamber correspondence. There is no
static route to it, no media route, and no path on any served root — the only
way a byte of it reaches a browser is the view below, and that view answers a
question rather than a path.

The request carries an **opaque identifier**, never a filename and never a
storage key. Resolving it means: find a relation whose decision is current, on
the current legal snapshot, whose named provenances are current, for an
opinion-eligible record, whose blob passed validation. Any link in that chain
missing means 404 — the same answer as "no such document", because telling an
unauthorised caller the difference is itself a disclosure.

Since the public source exists, the page prefers a public destination for the
main action: the authoritative Koda.ee PDF when the matched document has one,
because linking a reader to the Chamber's own publication beats proxying it.
The protected DashKoda route stays for private-only documents and remains
valid for every stored blob, whatever its provenance — it is the fallback the
public URL's disappearance cannot take away. Both public addresses are
re-validated at render time; a stored URL that no longer passes the allowlist
renders the protected route instead.

Path traversal is not defended against by inspecting the identifier, because
the identifier never becomes a path. The digest does, through
`opinion_storage.resolve_within_store`, which compares the *resolved* target
against the store root.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils.encoding import iri_to_uri
from django.views.decorators.http import require_safe

from .opinion_eligibility import opinion_eligible_q
from .opinion_match_models import (
    LegalOpinionDocumentRelation,
    LegalOpinionPageRelation,
    OpinionResource,
)
from .opinion_pdf import ValidationStatus
from .opinion_storage import StorageError, blob_path
from .topic_links import (
    is_publishable_public_document_url,
    is_publishable_public_page_url,
)

PDF_CONTENT_TYPE = "application/pdf"


@dataclass(frozen=True)
class DocumentPresentation:
    """One document card: a label, one main action, one optional fallback.

    The template renders exactly what this says and decides nothing itself.
    `href` follows the fixed preference order — public Koda.ee PDF, then the
    protected DashKoda route — and `fallback_href` names the protected copy
    when the main action already went to Koda.ee, so public provenance never
    hides the private one.
    """

    label: str
    href: str
    is_external: bool
    page_count: int
    role_display: str = ""
    fallback_href: str = ""


def _current_relations():
    """Relations a viewer may see, with every staleness check in the query.

    Expressed against the database rather than in Python so a stale snapshot
    cannot be filtered out "later" by code that forgets to. A provenance the
    relation does not carry imposes no condition: a public-only document does
    not wait for a private catalogue, nor the other way round.
    """
    return (
        LegalOpinionDocumentRelation.objects.filter(
            Q(entry__isnull=True) | Q(entry__snapshot__is_current=True),
            Q(public_document__isnull=True) | Q(public_document__snapshot__is_current=True),
            decision__snapshot__is_current=True,
            decision__legal_item__snapshot__is_current=True,
            blob__validation_status=ValidationStatus.VALID,
        )
        .filter(opinion_eligible_q("decision__legal_item__"))
        .select_related(
            "blob",
            "entry",
            "public_document",
            "public_document__page",
            "decision",
            "decision__legal_item",
        )
    )


def _current_page_relations():
    """Article-only page evidence a viewer may see. Same discipline."""
    return (
        LegalOpinionPageRelation.objects.filter(
            decision__snapshot__is_current=True,
            decision__legal_item__snapshot__is_current=True,
            page__snapshot__is_current=True,
            page__is_present=True,
        )
        .filter(opinion_eligible_q("decision__legal_item__"))
        .select_related("page", "decision", "decision__legal_item")
    )


def _present(relation: LegalOpinionDocumentRelation) -> DocumentPresentation:
    """Apply the source preference to one relation."""
    protected = reverse("opinion-document", args=[relation.blob.public_id])
    public_document = relation.public_document
    public_url = ""
    if (
        public_document is not None
        and public_document.is_present
        and is_publishable_public_document_url(public_document.pdf_url)
    ):
        public_url = public_document.pdf_url

    return DocumentPresentation(
        label=relation.display_filename,
        href=public_url or protected,
        is_external=bool(public_url),
        page_count=relation.blob.page_count,
        role_display=relation.get_role_display(),
        fallback_href=protected if public_url else "",
    )


@require_safe
def opinion_resource(request, public_id):
    """The stable page a sent legal topic links to."""
    resource = OpinionResource.objects.filter(public_id=public_id).select_related("matter").first()
    if resource is None or resource.matter.has_ambiguous_identity:
        raise Http404

    relations = list(_current_relations().filter(decision__matter=resource.matter))
    primary_relation = next((r for r in relations if r.is_primary), None)
    primary = _present(primary_relation) if primary_relation else None
    secondary = [_present(r) for r in relations if not r.is_primary]

    # Public article coverage: the page the matched document was published on,
    # or a confident article-only confirmation when no document is known.
    # Labelled as an article in the template, never as the opinion PDF.
    article_url = ""
    for relation in relations:
        if (
            relation.public_document is not None
            and relation.public_document.is_present
            and relation.public_document.page.is_present
            and is_publishable_public_page_url(relation.public_document.page.canonical_url)
        ):
            article_url = relation.public_document.page.canonical_url
            break
    if not article_url:
        page_relation = (
            _current_page_relations()
            .filter(decision__matter=resource.matter)
            .order_by("-score")
            .first()
        )
        if page_relation is not None and is_publishable_public_page_url(
            page_relation.page.canonical_url
        ):
            article_url = page_relation.page.canonical_url

    # A matter that has left the current workbook keeps a read-only heading from
    # its durable metadata rather than vanishing: the address was published and
    # a dead page is worse than a historical one.
    item = None
    if relations:
        item = relations[0].decision.legal_item
    elif article_url:
        page_relation = _current_page_relations().filter(decision__matter=resource.matter).first()
        if page_relation is not None:
            item = page_relation.decision.legal_item

    return render(
        request,
        "legal_work/opinion_resource.html",
        {
            "resource": resource,
            "matter": resource.matter,
            "item": item,
            "primary": primary,
            "secondary": secondary,
            "article_url": article_url,
            "is_historical": item is None,
        },
    )


@require_safe
def opinion_document(request, public_id):
    """Serve one stored PDF, or 404. Never anything in between.

    Valid for every blob a current relation names, whatever its provenance:
    this is the protected fallback that outlives a public URL.
    """
    relation = _current_relations().filter(blob__public_id=public_id).first()
    if relation is None:
        raise Http404

    blob = relation.blob
    try:
        path = blob_path(blob.sha256)
    except StorageError:
        raise Http404 from None

    if not path.is_file():
        # The row survives a missing file; the response does not invent one.
        raise Http404

    response = FileResponse(path.open("rb"), content_type=PDF_CONTENT_TYPE)
    response["X-Content-Type-Options"] = "nosniff"
    # Chamber correspondence: never stored by a shared cache, never revalidated
    # from one.
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Content-Disposition"] = _disposition(relation.display_filename)
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
