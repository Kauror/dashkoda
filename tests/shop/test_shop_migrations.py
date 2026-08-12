"""Migration `0003` applied to a database that already holds shop facts.

`0003` adds three nullable columns and then a check constraint to
`shop_shopdailyfact`, a table that in production already holds 4 710 rows
imported under package schema 1.0. PostgreSQL validates a new check constraint
against every existing row, so "the constraint is obviously satisfied" is a
claim worth a test rather than an assumption — and AGENTS.md asks for one
precisely because a constraint added to a populated table is how `legal_work`
`0006` reached production.

The rows this seeds are the shape production actually has: a free/paid split
that was never stated, which the constraint permits as all-null.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

BEFORE = "0002_alter_shopdailyfact_order_count"
AFTER = "0003_shopdailysummary_shopdailyfact_free_units_and_more"


def seed_unclassified_facts(count: int):
    """Facts as schema 1.0 left them: units counted, nothing classified."""

    def seed(apps):
        DataSource = apps.get_model("sources", "DataSource")
        Product = apps.get_model("shop", "ShopProduct")
        Fact = apps.get_model("shop", "ShopDailyFact")

        DataSource.objects.create(
            slug="koda-commerce-shop",
            name="Sünteetiline",
            source_type="spreadsheet",
            authority_tier="unclassified",
            authority_rank=1,
            expected_update_frequency="irregular",
            is_active=True,
        )
        product = Product.objects.create(
            source_product_id=900001,
            product_type="document",
            first_seen_on=date(2026, 1, 1),
            last_seen_on=date(2026, 1, 1),
        )
        for index in range(count):
            Fact.objects.create(
                report_date=date(2026, 1, 1 + index),
                product=product,
                member_status="unknown",
                payment_class="invoice",
                currency="EUR",
                order_count=1,
                units=Decimal("1.00"),
                ordered_value_net=Decimal("30.0000"),
                is_current=True,
            )
        assert Fact.objects.count() == count

    return seed


def test_the_split_constraint_accepts_facts_that_predate_it(populated_migration):
    """Existing rows have no split at all, which is a permitted third state."""
    apps = populated_migration("shop", before=BEFORE, after=AFTER, seed=seed_unclassified_facts(6))

    Fact = apps.get_model("shop", "ShopDailyFact")
    assert Fact.objects.count() == 6
    # All three null together: not stated, which the constraint allows and which
    # the interface must never render as "0 tasuta".
    for fact in Fact.objects.all():
        assert fact.free_units is None
        assert fact.paid_units is None
        assert fact.unknown_units is None


def test_the_summary_table_starts_empty_beside_existing_facts(populated_migration):
    """A dataset imported before distinct orders existed simply has none."""
    apps = populated_migration("shop", before=BEFORE, after=AFTER, seed=seed_unclassified_facts(3))

    assert apps.get_model("shop", "ShopDailySummary").objects.count() == 0
