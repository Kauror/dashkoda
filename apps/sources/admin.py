from django import forms
from django.contrib import admin
from django.urls import path, reverse
from django.utils.html import format_html

from .models import DataSource, ImportRun, SourceArtifact
from .services import (
    create_data_source,
    register_artifact,
    register_external_reference,
    update_data_source,
)
from .views import DOWNLOAD_PERMISSION, artifact_download


@admin.register(DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    """Register, describe and deactivate sources. Referenced sources stay."""

    list_display = (
        "name",
        "slug",
        "source_type",
        "authority_tier",
        "authority_rank",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "source_type", "authority_tier", "expected_update_frequency")
    search_fields = ("name", "slug", "responsible_person", "description")
    ordering = ("authority_rank", "name")
    readonly_fields = ("created_at", "updated_at")
    prepopulated_fields = {"slug": ("name",)}
    actions = ["deactivate_selected"]

    def save_model(self, request, obj, form, change):
        """Route every write through the service so the audit trail is complete."""
        if change:
            stored = DataSource.objects.get(pk=obj.pk)
            changes = {name: form.cleaned_data[name] for name in form.changed_data}
            update_data_source(stored, actor=request.user, **changes)
            obj.pk = stored.pk
        else:
            editable = {
                field.name
                for field in DataSource._meta.fields
                if field.name not in {"id", "created_at", "updated_at"}
            }
            fields = {k: v for k, v in form.cleaned_data.items() if k in editable}
            created = create_data_source(actor=request.user, **fields)
            obj.pk = created.pk

    @admin.action(description="Deaktiveeri valitud andmeallikad")
    def deactivate_selected(self, request, queryset):
        count = 0
        for source in queryset.filter(is_active=True):
            update_data_source(source, actor=request.user, is_active=False)
            count += 1
        self.message_user(request, f"Deaktiveeritud: {count}")

    def has_delete_permission(self, request, obj=None):
        # A source that anything references is never physically deleted.
        if obj is not None and (obj.artifacts.exists() or obj.import_runs.exists()):
            return False
        return super().has_delete_permission(request, obj)


class SourceArtifactAdminForm(forms.ModelForm):
    """Registration form.

    The upload is a plain form field, not the model's file field, so the
    checksum, size and stored path are always produced by the service.
    """

    upload = forms.FileField(required=False, label="Fail")

    class Meta:
        model = SourceArtifact
        fields = ("source", "external_reference", "access_level")

    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get("upload")
        reference = (cleaned.get("external_reference") or "").strip()
        if bool(upload) == bool(reference):
            raise forms.ValidationError("Määra täpselt üks: kas fail või väline viide.")
        return cleaned

    def _post_clean(self):
        """Skip model validation of the half-built instance.

        The upload is not the model's file field, so the instance this form
        would construct always looks like it has neither a file nor a reference
        and would fail the XOR rule spuriously. The service builds the real
        instance and runs `full_clean` on it, which is what actually matters.
        """
        return


@admin.register(SourceArtifact)
class SourceArtifactAdmin(admin.ModelAdmin):
    """Register an original once, then inspect it. Never edit or delete it."""

    form = SourceArtifactAdminForm
    list_display = (
        "original_name",
        "source",
        "short_checksum",
        "size_bytes",
        "mime_type",
        "access_level",
        "uploaded_at",
    )
    list_filter = ("access_level", "source", "uploaded_at")
    search_fields = ("original_name", "sha256", "external_reference", "source__name")
    ordering = ("-uploaded_at", "-id")
    list_select_related = ("source",)

    @admin.display(description="SHA-256")
    def short_checksum(self, obj):
        return f"{obj.sha256[:12]}…" if obj.sha256 else "—"

    @admin.display(description="Allalaadimine")
    def download_link(self, obj):
        if obj.pk is None or obj.is_external:
            return "—"
        url = reverse("admin:sources_sourceartifact_download", args=[obj.pk])
        return format_html('<a href="{}">Laadi alla</a>', url)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        # Everything about a registered artifact is fixed.
        return (
            "source",
            "original_name",
            "mime_type",
            "size_bytes",
            "sha256",
            "external_reference",
            "access_level",
            "uploaded_at",
            "uploaded_by",
            "download_link",
        )

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("source", "upload", "external_reference", "access_level")
        return self.get_readonly_fields(request, obj)

    def save_model(self, request, obj, form, change):
        upload = form.cleaned_data.get("upload")
        if upload:
            created = register_artifact(
                source=form.cleaned_data["source"],
                upload=upload,
                original_name=upload.name,
                mime_type=getattr(upload, "content_type", "") or "",
                access_level=form.cleaned_data["access_level"],
                uploaded_by=request.user,
                actor=request.user,
            )
        else:
            created = register_external_reference(
                source=form.cleaned_data["source"],
                external_reference=form.cleaned_data["external_reference"],
                access_level=form.cleaned_data["access_level"],
                uploaded_by=request.user,
                actor=request.user,
            )
        # `obj` came from a form that skipped model construction, so copy the
        # registered row onto it for the admin's log entry and redirect.
        for field in SourceArtifact._meta.fields:
            setattr(obj, field.attname, getattr(created, field.attname))

    def get_urls(self):
        return [
            path(
                "<int:pk>/download/",
                self.admin_site.admin_view(artifact_download),
                name="sources_sourceartifact_download",
            ),
            *super().get_urls(),
        ]

    def has_change_permission(self, request, obj=None):
        # View-only detail page: a registered original is immutable.
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    """Inspection only.

    No importer exists yet. Runs are created by services, so this admin
    deliberately offers no way to start one.
    """

    list_display = (
        "importer_name",
        "schema_version",
        "source",
        "status",
        "dry_run",
        "rows_added",
        "rows_invalid",
        "created_at",
    )
    list_filter = ("status", "dry_run", "importer_name", "schema_version", "source")
    search_fields = ("importer_name", "import_key", "correlation_id", "source__name")
    ordering = ("-created_at", "-id")
    list_select_related = ("source", "artifact")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# Re-exported so the permission string has one definition in the codebase.
__all__ = ["DOWNLOAD_PERMISSION"]
