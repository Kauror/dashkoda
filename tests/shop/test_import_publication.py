"""Publishing the E-pood package: atomic, idempotent, and never rewriting.

Requires PostgreSQL. On a machine without one these are collected and skipped by
the database fixture; CI is where they run.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.audit.models import AuditEvent
from apps.shop.audit_actions import ShopAudit
from apps.shop.importing import ShopImportError, import_shop_package
from apps.shop.models import (
    MemberStatus,
    PageRole,
    PaymentClass,
    ProductType,
    ShopDailyFact,
    ShopProduct,
    ShopProductPage,
    ShopProductSnapshot,
    ShopSourceState,
)
from apps.sources.models import ImportRun, ImportStatus

from .package_factory import (
    DOCUMENT_PRODUCT_PAGE_ONLY,
    DOCUMENT_WITH_BOTH_PAGES,
    EVENT_PRODUCT,
    PHYSICAL_PRODUCT,
    build_package,
    default_daily_facts,
    default_manifest,
    default_products,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def package(tmp_path):
    return build_package(tmp_path)


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


def test_validate_only_writes_nothing(package):
    result = import_shop_package(package, dry_run=True)

    assert result.dry_run is True
    assert ShopProduct.objects.count() == 0
    assert ShopDailyFact.objects.count() == 0
    assert ShopSourceState.objects.count() == 0
    assert ImportRun.objects.filter(dry_run=True, status=ImportStatus.SUCCEEDED).exists()


def test_live_import_publishes_everything(package):
    result = import_shop_package(package, dry_run=False)

    assert result.unchanged is False
    assert ShopProduct.objects.count() == 4
    assert ShopProductSnapshot.objects.filter(is_current=True).count() == 4
    assert ShopDailyFact.objects.filter(is_current=True).count() == 7
    assert ShopProductPage.objects.filter(is_current=True).count() == 4

    state = ShopSourceState.objects.get(is_current=True)
    assert state.source_as_of == date(2026, 8, 11)
    assert state.coverage_start == date(2020, 10, 22)
    assert state.coverage_end == date(2026, 8, 11)
    assert state.member_semantics_verified is False
    assert state.public_listing_semantics_verified is False


def test_import_is_audited(package):
    import_shop_package(package, dry_run=False)

    event = AuditEvent.objects.get(action=ShopAudit.SNAPSHOT_IMPORTED)
    summary = event.change_summary
    assert summary["source_as_of"] == "2026-08-11"
    # Aggregate provenance only: no title, no price, no path.
    assert set(summary) == {
        "source",
        "content_checksum",
        "source_as_of",
        "coverage_start",
        "coverage_end",
        "counts",
    }


def test_dry_run_does_not_block_a_later_live_import(package):
    import_shop_package(package, dry_run=True)
    result = import_shop_package(package, dry_run=False)

    assert result.unchanged is False
    assert ShopDailyFact.objects.filter(is_current=True).count() == 7


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_identical_reimport_is_unchanged(package):
    import_shop_package(package, dry_run=False)
    result = import_shop_package(package, dry_run=False)

    assert result.unchanged is True
    assert ShopDailyFact.objects.count() == 7
    assert ShopSourceState.objects.count() == 1
    assert AuditEvent.objects.filter(action=ShopAudit.SNAPSHOT_UNCHANGED).exists()


def test_repackaged_identical_facts_are_unchanged(tmp_path):
    """A re-export with a new `exported_at` is not new information."""
    first = build_package(tmp_path, filename="a.zip")
    import_shop_package(first, dry_run=False)

    manifest = {**default_manifest(), "exported_at": "2026-08-12T09:30:00+03:00"}
    second = build_package(tmp_path, manifest=manifest, filename="b.zip")
    result = import_shop_package(second, dry_run=False)

    assert result.unchanged is True
    assert ShopDailyFact.objects.count() == 7


# ---------------------------------------------------------------------------
# Corrections supersede rather than rewrite
# ---------------------------------------------------------------------------


def test_corrected_fact_creates_a_new_revision(tmp_path):
    import_shop_package(build_package(tmp_path, filename="a.zip"), dry_run=False)

    rows = default_daily_facts()
    rows[1] = {**rows[1], "units": "9.00", "ordered_value_net": "135.0000"}
    import_shop_package(build_package(tmp_path, daily_facts=rows, filename="b.zip"), dry_run=False)

    cell = ShopDailyFact.objects.filter(
        report_date=date(2026, 3, 10),
        product__source_product_id=DOCUMENT_WITH_BOTH_PAGES,
        member_status=MemberStatus.MEMBER,
    )
    assert cell.count() == 2, "the old reading is kept, not overwritten"
    current = cell.get(is_current=True)
    superseded = cell.get(is_current=False)
    assert current.units == Decimal("9.00")
    assert superseded.units == Decimal("3.00")
    assert current.supersedes_id == superseded.pk


def test_unchanged_rows_are_not_revised(tmp_path):
    """Only what differs gets a new row."""
    import_shop_package(build_package(tmp_path, filename="a.zip"), dry_run=False)

    rows = default_daily_facts()
    rows[1] = {**rows[1], "units": "9.00", "ordered_value_net": "135.0000"}
    import_shop_package(build_package(tmp_path, daily_facts=rows, filename="b.zip"), dry_run=False)

    # Seven original rows plus exactly one revision.
    assert ShopDailyFact.objects.count() == 8


def test_a_published_fact_cannot_be_edited(package):
    import_shop_package(package, dry_run=False)
    fact = ShopDailyFact.objects.filter(is_current=True).first()

    fact.units = Decimal("99.00")
    with pytest.raises(Exception, match="immutable"):
        fact.save(update_fields=["units"])


# ---------------------------------------------------------------------------
# A failure never displaces good data
# ---------------------------------------------------------------------------


def test_failed_import_leaves_the_previous_dataset(tmp_path):
    import_shop_package(build_package(tmp_path, filename="a.zip"), dry_run=False)
    before = ShopDailyFact.objects.filter(is_current=True).count()

    rows = default_daily_facts()
    rows[0] = {**rows[0], "commerce_state": "draft"}
    with pytest.raises(ShopImportError):
        import_shop_package(
            build_package(tmp_path, daily_facts=rows, filename="b.zip"), dry_run=False
        )

    assert ShopDailyFact.objects.filter(is_current=True).count() == before
    assert ShopSourceState.objects.filter(is_current=True).count() == 1


def test_membership_rows_never_enter_the_shop(tmp_path):
    rows = default_products()
    rows[0] = {**rows[0], "product_type": "membership"}
    with pytest.raises(ShopImportError):
        import_shop_package(build_package(tmp_path, products=rows), dry_run=False)

    assert ShopProduct.objects.count() == 0


# ---------------------------------------------------------------------------
# Product identity
# ---------------------------------------------------------------------------


def test_a_renamed_product_is_the_same_product(tmp_path):
    import_shop_package(build_package(tmp_path, filename="a.zip"), dry_run=False)

    rows = default_products()
    rows[0] = {**rows[0], "title": "Hoopis teine pealkiri"}
    import_shop_package(build_package(tmp_path, products=rows, filename="b.zip"), dry_run=False)

    assert ShopProduct.objects.filter(source_product_id=DOCUMENT_WITH_BOTH_PAGES).count() == 1
    snapshots = ShopProductSnapshot.objects.filter(
        product__source_product_id=DOCUMENT_WITH_BOTH_PAGES
    )
    assert snapshots.count() == 2
    assert snapshots.get(is_current=True).title == "Hoopis teine pealkiri"


def test_a_reused_id_with_a_new_type_is_refused(tmp_path):
    """The same ID describing a different kind of product is not a rename."""
    import_shop_package(build_package(tmp_path, filename="a.zip"), dry_run=False)

    rows = default_products()
    rows[0] = {**rows[0], "product_type": "physical_product"}
    with pytest.raises(ShopImportError, match="liik muutus"):
        import_shop_package(build_package(tmp_path, products=rows, filename="b.zip"), dry_run=False)


def test_types_are_stored_as_given(package):
    import_shop_package(package, dry_run=False)
    by_id = {p.source_product_id: p.product_type for p in ShopProduct.objects.all()}
    assert by_id[DOCUMENT_WITH_BOTH_PAGES] == ProductType.DOCUMENT
    assert by_id[EVENT_PRODUCT] == ProductType.EVENT_REGISTRATION
    assert by_id[PHYSICAL_PRODUCT] == ProductType.PHYSICAL_PRODUCT


# ---------------------------------------------------------------------------
# Missing is not zero, all the way to the database
# ---------------------------------------------------------------------------


def test_absent_price_is_stored_as_null(package):
    import_shop_package(package, dry_run=False)
    snapshot = ShopProductSnapshot.objects.get(
        product__source_product_id=PHYSICAL_PRODUCT, is_current=True
    )
    assert snapshot.list_price_net is None
    assert snapshot.member_price_net is None


def test_explicit_zero_price_is_stored_as_zero(package):
    import_shop_package(package, dry_run=False)
    snapshot = ShopProductSnapshot.objects.get(
        product__source_product_id=DOCUMENT_PRODUCT_PAGE_ONLY, is_current=True
    )
    assert snapshot.list_price_net == Decimal("0.0000")


def test_published_and_publicly_listed_stay_separate(package):
    import_shop_package(package, dry_run=False)
    snapshot = ShopProductSnapshot.objects.get(
        product__source_product_id=DOCUMENT_WITH_BOTH_PAGES, is_current=True
    )
    assert snapshot.published is True
    assert snapshot.publicly_listed is None


def test_money_survives_as_exact_decimal(package):
    import_shop_package(package, dry_run=False)
    total = sum(
        (fact.ordered_value_net for fact in ShopDailyFact.objects.filter(is_current=True)),
        Decimal("0"),
    )
    assert total == Decimal("511.0000")


def test_unknown_member_status_is_kept_distinct(package):
    import_shop_package(package, dry_run=False)
    statuses = set(
        ShopDailyFact.objects.filter(is_current=True).values_list("member_status", flat=True)
    )
    assert MemberStatus.UNKNOWN in statuses
    assert MemberStatus.MEMBER in statuses
    assert MemberStatus.NON_MEMBER in statuses


def test_payment_classes_are_stored(package):
    import_shop_package(package, dry_run=False)
    classes = set(
        ShopDailyFact.objects.filter(is_current=True).values_list("payment_class", flat=True)
    )
    assert PaymentClass.INVOICE in classes
    assert PaymentClass.BANK_OR_CARD in classes


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_both_page_roles_are_stored_separately(package):
    import_shop_package(package, dry_run=False)
    roles = dict(
        ShopProductPage.objects.filter(
            product__source_product_id=DOCUMENT_WITH_BOTH_PAGES, is_current=True
        ).values_list("page_role", "path")
    )
    assert roles[PageRole.PRODUCT] == "/et/pood/lepingute-naidised/toosuhted/naidisleping"
    assert roles[PageRole.INFORMATION] == "/et/tooriistad/naidisleping"


def test_a_changed_path_retires_the_old_one_rather_than_deleting_it(tmp_path):
    """GA4 still holds traffic under the old address."""
    from .package_factory import default_product_paths

    import_shop_package(build_package(tmp_path, filename="a.zip"), dry_run=False)

    rows = default_product_paths()
    rows[0] = {**rows[0], "canonical_path": "/et/pood/lepingute-naidised/toosuhted/uus-tee"}
    import_shop_package(
        build_package(tmp_path, product_paths=rows, filename="b.zip"), dry_run=False
    )

    pages = ShopProductPage.objects.filter(
        product__source_product_id=DOCUMENT_WITH_BOTH_PAGES, page_role=PageRole.PRODUCT
    )
    assert pages.count() == 2
    assert pages.get(is_current=True).path.endswith("/uus-tee")
    assert pages.filter(is_current=False).exists()
