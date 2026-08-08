"""Read-only admin for durable identity and the opinion matcher's output.

Inspection informs the rules; it never overrides a row. There is no approve,
reject, edit, delete, manual relation, manual primary selection, force-match or
suppress-match action, and no button that re-runs matching for a single record.
A wrong decision is corrected by changing weights or thresholds in
`opinion_matching.py`, releasing a new matcher version, and re-running — which
is reviewable, testable and applies to every record at once.

What staff *can* see here is everything a reviewer needs to answer "why did this
link appear, and why did that obvious pair not?": the score, the runner-up, the
margin, the evidence codes, the contradictions and the exact snapshots involved.
That diagnostic detail is the whole point of this admin and is deliberately
absent from the viewer.
"""

from django.contrib import admin

from apps.core.admin import ReadOnlyAdmin

from .opinion_match_models import (
    LegalMatter,
    LegalMatterAlias,
    LegalOpinionDecision,
    LegalOpinionDocumentRelation,
    LegalOpinionMatchSnapshot,
    LegalOpinionPageRelation,
    OpinionResource,
)


@admin.register(LegalMatter)
class LegalMatterAdmin(ReadOnlyAdmin):
    """The durable identity. Not editable, by design and by constraint.

    An editable identity would let a person merge two legal matters by hand,
    which is exactly the failure the derivation exists to prevent.
    """

    list_display = (
        "last_known_topic",
        "received_date",
        "has_ambiguous_identity",
        "identity_version",
        "key_prefix",
        "first_seen_at",
    )
    list_filter = ("has_ambiguous_identity", "identity_version")
    search_fields = ("last_known_topic",)
    date_hierarchy = "first_seen_at"

    @admin.display(description="Identiteedivõti", ordering="matter_key")
    def key_prefix(self, obj):
        # A prefix identifies a matter in conversation without being a lookup
        # key, the same rule the document digests follow.
        return obj.matter_key[:12]

    def get_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields if f.name != "matter_key"] + ["key_prefix"]

    def get_readonly_fields(self, request, obj=None):
        return self.get_fields(request, obj)


@admin.register(LegalMatterAlias)
class LegalMatterAliasAdmin(ReadOnlyAdmin):
    """Provenance: what each snapshot called a matter.

    Recorded because an operator reading the spreadsheet sees `record_id` and
    will quote it. Nothing resolves a matter *by* these — that is the positional
    identifier the durable key replaced.
    """

    list_display = ("matter", "record_id", "source_year", "source_nr", "source_row", "snapshot")
    list_filter = ("source_year",)
    search_fields = ("record_id",)
    list_select_related = ("matter", "snapshot")


@admin.register(OpinionResource)
class OpinionResourceAdmin(ReadOnlyAdmin):
    list_display = ("public_id", "matter", "created_at")
    list_select_related = ("matter",)
    date_hierarchy = "created_at"


@admin.register(LegalOpinionMatchSnapshot)
class LegalOpinionMatchSnapshotAdmin(ReadOnlyAdmin):
    list_display = (
        "generated_at",
        "is_current",
        "matcher_version",
        "considered_item_count",
        "matched_count",
        "ambiguous_count",
        "unmatched_count",
        "legal_snapshot",
        "opinion_catalogue_snapshot",
    )
    list_filter = ("is_current", "matcher_version")
    date_hierarchy = "generated_at"
    ordering = ("-generated_at", "-id")
    list_select_related = ("legal_snapshot", "opinion_catalogue_snapshot")


class DecisionEvidenceFilter(admin.SimpleListFilter):
    """Whether the matcher had structured evidence or only text.

    The first question about any decision is which kind of evidence carried it,
    because a match resting on subject similarity alone is the shape a wrong
    link takes.
    """

    title = "Tõendi liik"
    parameter_name = "evidence_kind"

    def lookups(self, request, model_admin):
        return [
            ("dated", "Kuupäev kokku"),
            ("instrument", "Õigusakt tuvastatud"),
            ("blocked", "Vastuoluga"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "dated":
            return queryset.filter(evidence_codes__contains=["date-exact"])
        if value == "instrument":
            return queryset.filter(evidence_codes__contains=["identifier-match"])
        if value == "blocked":
            return queryset.exclude(contradiction_codes=[])
        return queryset


@admin.register(LegalOpinionDecision)
class LegalOpinionDecisionAdmin(ReadOnlyAdmin):
    list_display = (
        "legal_record",
        "decision",
        "score",
        "runner_up_score",
        "score_margin",
        "candidate_count",
        "evidence",
        "matcher_version",
    )
    list_filter = ("decision", DecisionEvidenceFilter, "snapshot__matcher_version")
    list_select_related = ("legal_item", "matter", "snapshot")
    ordering = ("-score", "legal_item_id")

    @admin.display(description="Õigusloome kirje", ordering="legal_item__record_id")
    def legal_record(self, obj):
        return f"{obj.legal_item.record_id}: {obj.legal_item.topic[:60]}"

    @admin.display(description="Tõendid")
    def evidence(self, obj):
        return ", ".join(obj.evidence_codes or []) or "—"

    @admin.display(description="Sobitaja", ordering="snapshot__matcher_version")
    def matcher_version(self, obj):
        return obj.snapshot.matcher_version


class RelationProvenanceFilter(admin.SimpleListFilter):
    """The review split staff actually need: which source supplied a link."""

    title = "Päritolu"
    parameter_name = "provenance"

    def lookups(self, request, model_admin):
        return [
            ("private_only", "Ainult privaatne"),
            ("public_only", "Ainult avalik"),
            ("both", "Privaatne ja avalik"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "private_only":
            return queryset.filter(entry__isnull=False, public_document__isnull=True)
        if value == "public_only":
            return queryset.filter(entry__isnull=True, public_document__isnull=False)
        if value == "both":
            return queryset.filter(entry__isnull=False, public_document__isnull=False)
        return queryset


@admin.register(LegalOpinionDocumentRelation)
class LegalOpinionDocumentRelationAdmin(ReadOnlyAdmin):
    list_display = ("decision", "document", "role", "is_primary", "provenance", "score")
    list_filter = ("role", "is_primary", RelationProvenanceFilter)
    list_select_related = ("decision", "blob", "entry", "public_document")

    @admin.display(description="Dokument")
    def document(self, obj):
        return obj.display_filename

    @admin.display(description="Päritolu")
    def provenance(self, obj):
        if obj.entry_id and obj.public_document_id:
            return "privaatne + avalik"
        if obj.public_document_id:
            return "avalik"
        return "privaatne"


@admin.register(LegalOpinionPageRelation)
class LegalOpinionPageRelationAdmin(ReadOnlyAdmin):
    """Article-only confirmations, for the review these links require."""

    list_display = ("decision", "page_title", "score", "evidence")
    list_select_related = ("decision", "page")
    ordering = ("-score",)

    @admin.display(description="Leht", ordering="page__title")
    def page_title(self, obj):
        return obj.page.title

    @admin.display(description="Tõendid")
    def evidence(self, obj):
        return ", ".join(obj.evidence_codes or []) or "—"
