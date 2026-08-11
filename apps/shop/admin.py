"""Read-only admin for the imported E-pood dataset.

Inspection only, deliberately. Every row here is a published domain fact, and a
correction belongs to a new import rather than to a form: an editable admin
would be a second way to write history, with none of the provenance the importer
records.
"""

from django.contrib import admin

from apps.core.admin import ReadOnlyAdmin

from .models import (
    ShopDailyFact,
    ShopProduct,
    ShopProductPage,
    ShopProductSnapshot,
    ShopSourceState,
)


@admin.register(ShopProduct)
class ShopProductAdmin(ReadOnlyAdmin):
    list_display = ("source_product_id", "product_type", "first_seen_on", "last_seen_on")
    list_filter = ("product_type",)
    search_fields = ("source_product_id",)
    ordering = ("source_product_id",)


@admin.register(ShopProductSnapshot)
class ShopProductSnapshotAdmin(ReadOnlyAdmin):
    list_display = (
        "observed_on",
        "product",
        "title",
        "category_name",
        "published",
        "publicly_listed",
        "list_price_net",
        "member_price_net",
        "is_current",
    )
    list_filter = ("is_current", "published", "publicly_listed", "members_only", "category_name")
    search_fields = ("title", "product__source_product_id")
    date_hierarchy = "observed_on"
    ordering = ("-observed_on", "product_id")
    list_select_related = ("product", "import_run")


@admin.register(ShopProductPage)
class ShopProductPageAdmin(ReadOnlyAdmin):
    list_display = ("product", "page_role", "path", "is_current", "first_seen_on", "last_seen_on")
    list_filter = ("page_role", "is_current")
    search_fields = ("path", "product__source_product_id")
    ordering = ("product_id", "page_role", "path")
    list_select_related = ("product", "import_run")


@admin.register(ShopDailyFact)
class ShopDailyFactAdmin(ReadOnlyAdmin):
    list_display = (
        "report_date",
        "product",
        "member_status",
        "payment_class",
        "order_count",
        "units",
        "ordered_value_net",
        "currency",
        "is_current",
    )
    list_filter = ("is_current", "member_status", "payment_class", "currency")
    search_fields = ("product__source_product_id",)
    date_hierarchy = "report_date"
    ordering = ("-report_date", "product_id")
    list_select_related = ("product", "import_run")


@admin.register(ShopSourceState)
class ShopSourceStateAdmin(ReadOnlyAdmin):
    list_display = (
        "source_as_of",
        "coverage_start",
        "coverage_end",
        "schema_version",
        "member_semantics_verified",
        "public_listing_semantics_verified",
        "product_count",
        "fact_count",
        "is_current",
    )
    list_filter = (
        "is_current",
        "member_semantics_verified",
        "public_listing_semantics_verified",
    )
    ordering = ("-observed_at", "-id")
    list_select_related = ("source", "import_run")
