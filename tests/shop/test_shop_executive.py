"""When the shop executive summary may compare a period with the one before.

`derive_period_pair` is the E-pood page's one rule for "compared with what",
and it refuses a previous window that would reach before Commerce coverage
began. The executive summary must take its previous period from the same rule:
a full year compared against the few covered months that happen to precede it
would print a movement that is really the import's start date.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.shop.executive import get_shop_executive
from apps.shop.importing import import_shop_package

from .package_factory import (
    DOCUMENT_WITH_BOTH_PAGES,
    build_package,
    default_manifest,
)

pytestmark = pytest.mark.django_db


def fact(report_date: str, units: str, order_count: str) -> dict:
    return {
        "report_date": report_date,
        "source_product_id": str(DOCUMENT_WITH_BOTH_PAGES),
        "commerce_state": "completed",
        "member_status": "member",
        "payment_class": "invoice",
        "order_count": order_count,
        "units": units,
        "ordered_value_net": "30.0000",
        "currency": "EUR",
    }


def orders(report_date: str, count: str) -> list[dict]:
    return [
        {"report_date": report_date, "product_type": "", "distinct_order_count": count},
        {"report_date": report_date, "product_type": "document", "distinct_order_count": count},
    ]


def test_a_previous_window_outside_coverage_yields_no_comparison(tmp_path):
    """Coverage starts inside the would-be previous year: refused, not summed.

    The 2025-06-15 units sit in the covered tail of the previous window. An
    ungated sum would present them as the whole previous year and announce a
    fall; the page's rule refuses the pair, and the pillar must agree.
    """
    manifest = {**default_manifest(), "coverage_start": "2025-05-01"}
    facts = [fact("2025-06-15", "4.00", "4"), fact("2026-06-01", "1.00", "1")]
    import_shop_package(
        build_package(
            tmp_path,
            manifest=manifest,
            daily_facts=facts,
            daily_orders=orders("2025-06-15", "4") + orders("2026-06-01", "1"),
        ),
        dry_run=False,
    )

    executive = get_shop_executive()

    assert executive.units == Decimal("1.00")
    assert executive.previous_units is None
    assert executive.change_pct is None
    assert executive.signals == ()


def test_a_fully_covered_previous_window_still_compares(tmp_path):
    """With long coverage the same data yields the comparison and its signal."""
    facts = [fact("2025-06-15", "4.00", "4"), fact("2026-06-01", "1.00", "1")]
    import_shop_package(
        build_package(
            tmp_path,
            daily_facts=facts,
            daily_orders=orders("2025-06-15", "4") + orders("2026-06-01", "1"),
        ),
        dry_run=False,
    )

    executive = get_shop_executive()

    assert executive.units == Decimal("1.00")
    assert executive.previous_units == Decimal("4.00")
    assert executive.change_pct == pytest.approx(-75.0)
    assert [signal.key for signal in executive.signals] == ["shop-units"]
