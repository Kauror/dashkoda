"""Read-only admin for imported legal-work data.

Everything here is inspection only. Snapshots and rows are written by the
importer and never edited by hand, so no add, change or delete action is
offered. The feed state shows a sanitized diagnostic and never a secret.

The current-topic catalogue and the match results are registered the same way,
and for them read-only is the *product*, not a precaution. The matcher runs in
shadow mode: staff read what it proposed, score it against reality outside the
application and calibrate the thresholds in code. There is deliberately no
approve, reject, override or link action, because a manual mapping workflow is
a different feature with different failure modes, and adding a button now would
quietly turn a measurement exercise into a data-entry one.
"""

from django.contrib import admin
from django.utils.html import format_html

from apps.core.admin import ReadOnlyAdmin

from .models import (
    CurrentTopicFeedState,
    CurrentTopicItem,
    CurrentTopicSnapshot,
    LegalCurrentTopicMatch,
    LegalCurrentTopicMatchSnapshot,
    LegalWorkFeedState,
    LegalWorkItem,
    LegalWorkSnapshot,
)


@admin.register(LegalWorkSnapshot)
class LegalWorkSnapshotAdmin(ReadOnlyAdmin):
    list_display = (
        "reporting_date",
        "is_current",
        "total_record_count",
        "open_record_count",
        "sent_record_count",
        "warning_record_count",
        "imported_at",
        "schema_version",
    )
    list_filter = ("is_current", "schema_version", "reporting_date")
    search_fields = ("schema_version",)
    date_hierarchy = "imported_at"
    ordering = ("-imported_at", "-id")
    list_select_related = ("source", "artifact", "import_run")


@admin.register(LegalWorkItem)
class LegalWorkItemAdmin(ReadOnlyAdmin):
    """No lawyer column exists here, because the model has no such field."""

    list_display = (
        "record_id",
        "topic",
        "act_type",
        "received_date",
        "deadline_date",
        "sent_date",
        "sent_status",
        "stage",
        "is_open",
    )
    list_filter = (
        "snapshot__is_current",
        "is_open",
        "sent_status",
        "source_year",
        "received_date",
        "sent_date",
    )
    search_fields = ("topic", "recipient", "record_id")
    ordering = ("-received_date", "topic", "record_id")
    list_select_related = ("snapshot",)


@admin.register(LegalWorkFeedState)
class LegalWorkFeedStateAdmin(ReadOnlyAdmin):
    list_display = (
        "source",
        "last_result",
        "last_checked_at",
        "last_successful_sync_at",
        "last_changed_at",
    )
    list_filter = ("last_result",)
    list_select_related = ("source", "current_snapshot")


@admin.register(CurrentTopicSnapshot)
class CurrentTopicSnapshotAdmin(ReadOnlyAdmin):
    list_display = ("observed_at", "is_current", "item_count", "source")
    list_filter = ("is_current",)
    date_hierarchy = "observed_at"
    ordering = ("-observed_at", "-id")
    list_select_related = ("source", "artifact", "import_run")


class DecisionScoreBandFilter(admin.SimpleListFilter):
    """Coarse score bands, which is the granularity calibration actually uses.

    A free numeric range would invite filtering to two decimal places on a
    number whose thresholds are still provisional. Bands answer the question
    that matters during shadow evaluation — how much of the field is near the
    automatic-match line — and stay meaningful when that line moves.
    """

    title = "Skoori vahemik"
    parameter_name = "score_band"

    BANDS = {
        "high": (62, 101, "Kõrge (62+)"),
        "mid": (38, 62, "Keskmine (38–62)"),
        "low": (0, 38, "Madal (alla 38)"),
    }

    def lookups(self, request, model_admin):
        return [(key, label) for key, (_low, _high, label) in self.BANDS.items()]

    def queryset(self, request, queryset):
        band = self.BANDS.get(self.value())
        if band is None:
            return queryset
        low, high, _label = band
        return queryset.filter(score__gte=low, score__lt=high)


class EvidenceCodeFilter(admin.SimpleListFilter):
    """The contradiction and confidence codes worth slicing the field by."""

    title = "Tõendikood"
    parameter_name = "evidence"

    CODES = (
        "deadline-exact",
        "deadline-conflict",
        "organization-match",
        "organization-conflict-unsupported",
        "generic-overlap-only",
        "impossible-chronology",
        "unique-token-hit",
        "narrow-margin",
        "no-plausible-candidate",
    )

    def lookups(self, request, model_admin):
        return [(code, code) for code in self.CODES]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(evidence_codes__contains=[value])


@admin.register(CurrentTopicItem)
class CurrentTopicItemAdmin(ReadOnlyAdmin):
    list_display = (
        "title",
        "canonical_url",
        "published_date",
        "feedback_deadline",
        "named_organization",
        "summary_excerpt",
        "snapshot",
    )
    list_filter = (
        "snapshot__is_current",
        "named_organization",
        "published_date",
        "feedback_deadline",
    )
    search_fields = ("title", "listing_summary", "named_organization", "canonical_url")
    ordering = ("snapshot", "source_order")
    list_select_related = ("snapshot",)

    @admin.display(description="Kokkuvõte")
    def summary_excerpt(self, obj) -> str:
        text = obj.listing_summary or obj.body_text
        return f"{text[:160]}…" if len(text) > 160 else text


@admin.register(CurrentTopicFeedState)
class CurrentTopicFeedStateAdmin(ReadOnlyAdmin):
    list_display = (
        "source",
        "last_result",
        "last_checked_at",
        "last_successful_sync_at",
        "last_changed_at",
    )
    list_filter = ("last_result",)
    list_select_related = ("source", "current_snapshot")


@admin.register(LegalCurrentTopicMatchSnapshot)
class LegalCurrentTopicMatchSnapshotAdmin(ReadOnlyAdmin):
    list_display = (
        "generated_at",
        "is_current",
        "matcher_version",
        "legal_item_count",
        "matched_count",
        "ambiguous_count",
        "unmatched_count",
        "legal_snapshot",
        "current_topic_snapshot",
    )
    list_filter = ("is_current", "matcher_version")
    date_hierarchy = "generated_at"
    ordering = ("-generated_at", "-id")
    list_select_related = ("legal_snapshot", "current_topic_snapshot")


@admin.register(LegalCurrentTopicMatch)
class LegalCurrentTopicMatchAdmin(ReadOnlyAdmin):
    """One row per open legal record, with everything needed to judge it.

    The candidate's address is rendered as a link so a reviewer can open the
    page and decide whether the proposal is right. That is a staff inspection
    tool behind `/admin/`; it is not the viewer-facing link, and nothing on
    `/oigusloome/` reads this model.
    """

    list_display = (
        "legal_record_id",
        "legal_topic",
        "decision",
        "score",
        "runner_up_score",
        "score_margin",
        "candidate_count",
        "candidate_title",
        "candidate_link",
        "evidence_summary",
        "matcher_version",
    )
    list_filter = (
        "decision",
        DecisionScoreBandFilter,
        EvidenceCodeFilter,
        "snapshot__is_current",
        "snapshot__matcher_version",
        "snapshot__legal_snapshot",
        "snapshot__current_topic_snapshot",
    )
    search_fields = (
        "legal_item__record_id",
        "legal_item__topic",
        "best_candidate__title",
        "best_candidate__canonical_url",
    )
    ordering = ("-score", "legal_item_id")
    list_select_related = (
        "snapshot",
        "legal_item",
        "best_candidate",
        "snapshot__legal_snapshot",
        "snapshot__current_topic_snapshot",
    )

    @admin.display(description="Kirje ID", ordering="legal_item__record_id")
    def legal_record_id(self, obj) -> str:
        return obj.legal_item.record_id

    @admin.display(description="Õigusloome teema", ordering="legal_item__topic")
    def legal_topic(self, obj) -> str:
        topic = obj.legal_item.topic
        return f"{topic[:120]}…" if len(topic) > 120 else topic

    @admin.display(description="Kandidaat")
    def candidate_title(self, obj) -> str:
        if obj.best_candidate is None:
            return "—"
        title = obj.best_candidate.title
        return f"{title[:120]}…" if len(title) > 120 else title

    @admin.display(description="Kandidaadi aadress")
    def candidate_link(self, obj):
        if obj.best_candidate is None:
            return "—"
        url = obj.best_candidate.canonical_url
        return format_html('<a href="{}" rel="noopener noreferrer">{}</a>', url, url)

    @admin.display(description="Tõendikoodid")
    def evidence_summary(self, obj) -> str:
        return ", ".join(obj.evidence_codes or []) or "—"

    @admin.display(description="Sobitaja", ordering="snapshot__matcher_version")
    def matcher_version(self, obj) -> str:
        return obj.snapshot.matcher_version
