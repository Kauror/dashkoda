"""The E-pood selectors against real rows: joins, coverage and query bounds.

Requires PostgreSQL; CI is where these run.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.shop.importing import import_shop_package
from apps.shop.models import MemberStatus
from apps.shop.selectors import (
    build_category_rows,
    build_product_rows,
    get_member_split,
    get_shop_coverage,
    get_totals,
    page_views_in_window,
    resolve_comparison,
)
from apps.visibility.models import Ga4DailySnapshot, Ga4PageDaily

from .package_factory import (
    DOCUMENT_PRODUCT_PAGE_ONLY,
    DOCUMENT_WITH_BOTH_PAGES,
    PHYSICAL_PRODUCT,
    build_package,
    default_manifest,
)

pytestmark = pytest.mark.django_db

PRODUCT_PATH = "/et/pood/lepingute-naidised/toosuhted/naidisleping"
INFORMATION_PATH = "/et/tooriistad/naidisleping"


@pytest.fixture
def ga4_day():
    """Publish one synthetic GA4 reporting day."""
    from apps.sources.services import build_import_run, register_external_reference
    from apps.visibility.bootstrap import ensure_ga4_source

    source = ensure_ga4_source()
    artifact = register_external_reference(
        source=source,
        external_reference="synthetic:shop-analytics",
        original_name="synthetic.json",
        mime_type="application/json",
        sha256="d" * 64,
        size_bytes=10,
    )
    run = build_import_run(
        artifact=artifact,
        importer_name="synthetic_shop_test",
        schema_version="2.0",
        dry_run=False,
    )
    counter = {"n": 0}

    def _day(report_date, *, pages=(), has_page_detail=True):
        counter["n"] += 1
        snapshot = Ga4DailySnapshot.objects.create(
            source=source,
            artifact=artifact,
            import_run=run,
            report_date=report_date,
            observed_at=timezone.now(),
            checksum=f"{counter['n']:064d}",
            is_current_for_date=True,
            has_page_detail=has_page_detail,
            sessions=1,
        )
        for path, views in pages:
            Ga4PageDaily.objects.create(
                snapshot=snapshot, report_date=report_date, path=path, page_views=views
            )
        return snapshot

    return _day


@pytest.fixture
def imported(tmp_path):
    import_shop_package(build_package(tmp_path), dry_run=False)
    return get_shop_coverage()


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------


def test_product_page_views_join_on_the_canonical_path(imported, ga4_day):
    ga4_day(dt.date(2026, 3, 10), pages=((PRODUCT_PATH, 400), (INFORMATION_PATH, 900)))

    window = resolve_comparison(start=None, end=None)
    figures = page_views_in_window([PRODUCT_PATH, INFORMATION_PATH], window=window)

    assert figures[PRODUCT_PATH].views == 400
    assert figures[INFORMATION_PATH].views == 900


def test_the_two_pages_are_never_added(imported, ga4_day):
    ga4_day(dt.date(2026, 3, 10), pages=((PRODUCT_PATH, 400), (INFORMATION_PATH, 900)))

    window = resolve_comparison(start=None, end=None)
    row = next(
        item
        for item in build_product_rows(window)
        if item.source_product_id == DOCUMENT_WITH_BOTH_PAGES
    )

    assert row.product_page_views.views == 400
    assert row.information_page_views.views == 900
    # The rate uses the product page alone: 1300 would be a different, wrong
    # denominator, and 400 is the page carrying the buy action.
    assert row.acquisitions_per_hundred == Decimal("1.0")  # 4 units / 400 views


def test_an_unmeasured_product_has_no_views_and_no_rate(imported, ga4_day):
    ga4_day(dt.date(2026, 3, 10), pages=((PRODUCT_PATH, 400),))

    window = resolve_comparison(start=None, end=None)
    row = next(
        item
        for item in build_product_rows(window)
        if item.source_product_id == DOCUMENT_PRODUCT_PAGE_ONLY
    )

    assert row.product_page_views is None or row.product_page_views.views is None
    assert row.acquisitions_per_hundred is None


def test_a_product_with_no_path_still_counts_in_commerce(imported, ga4_day):
    """The physical product has no mapping; it must not vanish from the totals."""
    ga4_day(dt.date(2026, 3, 10), pages=((PRODUCT_PATH, 400),))

    window = resolve_comparison(start=None, end=None)
    row = next(
        item for item in build_product_rows(window) if item.source_product_id == PHYSICAL_PRODUCT
    )

    assert row.units == Decimal("1.00")
    assert row.acquisitions_per_hundred is None


# ---------------------------------------------------------------------------
# Coverage: missing is not zero
# ---------------------------------------------------------------------------


def test_absent_path_is_unknown_when_page_detail_is_incomplete(imported, ga4_day):
    ga4_day(dt.date(2026, 3, 10), pages=((PRODUCT_PATH, 400),))
    ga4_day(dt.date(2026, 3, 11), pages=(), has_page_detail=False)

    window = resolve_comparison(start=dt.date(2026, 3, 10), end=dt.date(2026, 3, 11))
    figures = page_views_in_window([PRODUCT_PATH, "/et/pood/midagi-muud"], window=window)

    assert "/et/pood/midagi-muud" not in figures


def test_absent_path_is_a_measured_zero_when_coverage_is_complete(imported, ga4_day):
    ga4_day(dt.date(2026, 3, 10), pages=((PRODUCT_PATH, 400),), has_page_detail=True)

    window = resolve_comparison(start=dt.date(2026, 3, 10), end=dt.date(2026, 3, 10))
    figures = page_views_in_window([PRODUCT_PATH, "/et/pood/midagi-muud"], window=window)

    assert figures["/et/pood/midagi-muud"].views == 0
    assert figures["/et/pood/midagi-muud"].is_measured_zero is True


# ---------------------------------------------------------------------------
# The clamp, against real rows
# ---------------------------------------------------------------------------


def test_ga4_days_after_the_export_are_not_paired_with_zero_orders(tmp_path, ga4_day):
    """The stale-Commerce case, end to end.

    The export stops on 30 June; GA4 keeps measuring into July. A July period
    must yield no comparison at all rather than July traffic over no orders.
    """
    manifest = {
        **default_manifest(),
        "source_as_of": "2026-06-30",
        "coverage_end": "2026-06-30",
    }
    rows = [
        {
            "report_date": "2026-06-01",
            "source_product_id": str(DOCUMENT_WITH_BOTH_PAGES),
            "commerce_state": "completed",
            "member_status": "member",
            "payment_class": "invoice",
            "order_count": "1",
            "units": "1.00",
            "ordered_value_net": "30.0000",
            "currency": "EUR",
        }
    ]
    import_shop_package(build_package(tmp_path, manifest=manifest, daily_facts=rows), dry_run=False)
    ga4_day(dt.date(2026, 7, 15), pages=((PRODUCT_PATH, 5000),))

    window = resolve_comparison(start=dt.date(2026, 7, 1), end=dt.date(2026, 7, 31))

    assert window.has_commerce is False
    assert window.has_web is False


def test_pre_ga4_acquisitions_never_enter_the_numerator(imported, ga4_day):
    """2021 acquisitions exist; they must not inflate a 2026 rate."""
    ga4_day(dt.date(2026, 3, 10), pages=((PRODUCT_PATH, 100),))

    window = resolve_comparison(start=None, end=None)
    row = next(
        item
        for item in build_product_rows(window)
        if item.source_product_id == DOCUMENT_WITH_BOTH_PAGES
    )

    # Commerce over everything sees the 2021 cell as well as the 2026 ones.
    assert row.units == Decimal("7.00")
    # The numerator sees only the web window, which begins with GA4.
    assert row.conversion_units == Decimal("4.00")
    assert row.acquisitions_per_hundred == Decimal("4.0")


# ---------------------------------------------------------------------------
# Totals and splits
# ---------------------------------------------------------------------------


def test_totals_cover_the_whole_commerce_window(imported):
    window = resolve_comparison(start=None, end=None)
    totals = get_totals(window)

    assert totals.orders == 18
    assert totals.units == Decimal("21.00")
    assert totals.ordered_value_net == Decimal("511.0000")


def test_member_split_keeps_unknown_separate(imported):
    window = resolve_comparison(start=None, end=None)
    split = get_member_split(window)

    assert MemberStatus.MEMBER in split
    assert MemberStatus.NON_MEMBER in split
    assert MemberStatus.UNKNOWN in split


def test_member_gate_stays_shut_for_this_source(imported):
    assert imported.member_semantics_verified is False
    assert imported.public_listing_semantics_verified is False


def test_categories_roll_up_from_the_same_rows(imported, ga4_day):
    ga4_day(dt.date(2026, 3, 10), pages=((PRODUCT_PATH, 400),))
    window = resolve_comparison(start=None, end=None)
    rows = build_product_rows(window)
    categories = build_category_rows(rows)

    assert sum(row.units for row in categories) == sum(row.units for row in rows)
    assert sum(row.orders for row in categories) == sum(row.orders for row in rows)


# ---------------------------------------------------------------------------
# Query bounds
# ---------------------------------------------------------------------------


def test_ranking_query_count_does_not_grow_with_products(
    imported, ga4_day, django_assert_max_num_queries
):
    ga4_day(dt.date(2026, 3, 10), pages=((PRODUCT_PATH, 400), (INFORMATION_PATH, 900)))
    window = resolve_comparison(start=None, end=None)

    # Aggregation happens in PostgreSQL and page views arrive in one grouped
    # query; nothing here may run per product.
    with django_assert_max_num_queries(8):
        rows = build_product_rows(window)
        build_category_rows(rows)

    assert len(rows) == 4


def test_search_runs_over_the_whole_population(imported):
    window = resolve_comparison(start=None, end=None)

    by_title = build_product_rows(window, search="Näidisleping")
    by_path = build_product_rows(window, search="toosuhted/naidisleping")
    by_id = build_product_rows(window, search=str(DOCUMENT_WITH_BOTH_PAGES))

    assert {row.source_product_id for row in by_title} == {DOCUMENT_WITH_BOTH_PAGES}
    assert {row.source_product_id for row in by_path} == {DOCUMENT_WITH_BOTH_PAGES}
    assert {row.source_product_id for row in by_id} == {DOCUMENT_WITH_BOTH_PAGES}
