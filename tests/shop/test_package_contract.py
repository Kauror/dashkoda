"""The E-pood package contract, exercised without a database.

`apps.shop.package` deliberately holds no Django import, so every rule below can
be checked on a machine with no PostgreSQL — which is where the real export gets
validated before anyone trusts it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.shop.package import (
    DAILY_FACTS_NAME,
    PRODUCT_PATHS_NAME,
    PRODUCTS_NAME,
    REQUIRED_HEADERS,
    PackageContractError,
    content_checksum,
    read_package,
)

from .package_factory import (
    DOCUMENT_PRODUCT_PAGE_ONLY,
    DOCUMENT_WITH_BOTH_PAGES,
    PHYSICAL_PRODUCT,
    build_package,
    default_daily_facts,
    default_manifest,
    default_product_paths,
    default_products,
)


def test_valid_package_parses(tmp_path):
    parsed = read_package(build_package(tmp_path))
    assert parsed.row_counts["products"] == 4
    assert parsed.row_counts["daily_facts"] == 7
    assert parsed.row_counts["product_paths"] == 4
    assert parsed.manifest.money_basis == "net_ex_vat"
    assert parsed.manifest.order_state_filter == "completed"


# ---------------------------------------------------------------------------
# The privacy boundary
# ---------------------------------------------------------------------------


def test_unknown_column_is_refused(tmp_path):
    """The rule that keeps a customer export out of the database."""
    header = (*REQUIRED_HEADERS[DAILY_FACTS_NAME], "customer_note")
    rows = [{**row, "customer_note": "irrelevant"} for row in default_daily_facts()]
    package = build_package(
        tmp_path,
        headers={DAILY_FACTS_NAME: header},
        extra_values={DAILY_FACTS_NAME: rows},
    )
    with pytest.raises(PackageContractError, match="lubamatuid veerge"):
        read_package(package)


def test_email_column_is_named_in_the_error(tmp_path):
    """A mistaken export must fail loudly, naming the offending column."""
    header = (*REQUIRED_HEADERS[PRODUCTS_NAME], "email")
    rows = [{**row, "email": "someone@example.org"} for row in default_products()]
    package = build_package(
        tmp_path,
        headers={PRODUCTS_NAME: header},
        extra_values={PRODUCTS_NAME: rows},
    )
    with pytest.raises(PackageContractError, match="email"):
        read_package(package)


def test_missing_column_is_refused(tmp_path):
    header = tuple(name for name in REQUIRED_HEADERS[PRODUCTS_NAME] if name != "category_name")
    rows = [{k: v for k, v in row.items() if k != "category_name"} for row in default_products()]
    package = build_package(
        tmp_path, headers={PRODUCTS_NAME: header}, extra_values={PRODUCTS_NAME: rows}
    )
    with pytest.raises(PackageContractError, match="päis"):
        read_package(package)


# ---------------------------------------------------------------------------
# Commerce semantics
# ---------------------------------------------------------------------------


def test_non_completed_state_is_refused(tmp_path):
    """`field_order_completed` is not a sales state and must never be imported."""
    rows = default_daily_facts()
    rows[0] = {**rows[0], "commerce_state": "draft"}
    package = build_package(tmp_path, daily_facts=rows)
    with pytest.raises(PackageContractError, match="commerce_state"):
        read_package(package)


def test_manifest_must_declare_completed_state_filter(tmp_path):
    manifest = {**default_manifest(), "order_state_filter": "field_order_completed"}
    package = build_package(tmp_path, manifest=manifest)
    with pytest.raises(PackageContractError, match="order_state_filter"):
        read_package(package)


def test_manifest_must_declare_net_money(tmp_path):
    manifest = {**default_manifest(), "money_basis": "gross_incl_vat"}
    package = build_package(tmp_path, manifest=manifest)
    with pytest.raises(PackageContractError, match="money_basis"):
        read_package(package)


def test_manifest_must_declare_tallinn_timezone(tmp_path):
    manifest = {**default_manifest(), "timezone": "UTC"}
    package = build_package(tmp_path, manifest=manifest)
    with pytest.raises(PackageContractError, match="timezone"):
        read_package(package)


def test_membership_product_type_is_refused(tmp_path):
    """Commerce processes membership orders; membership is not E-pood analytics."""
    rows = default_products()
    rows[0] = {**rows[0], "product_type": "membership"}
    package = build_package(tmp_path, products=rows)
    with pytest.raises(PackageContractError, match="ei ole e-poe analüütika osa"):
        read_package(package)


# ---------------------------------------------------------------------------
# Missing is not zero
# ---------------------------------------------------------------------------


def test_absent_price_stays_none(tmp_path):
    parsed = read_package(build_package(tmp_path))
    physical = next(row for row in parsed.products if row.source_product_id == PHYSICAL_PRODUCT)
    assert physical.list_price_current_net is None
    assert physical.member_price_current_net is None


def test_explicit_zero_price_stays_zero(tmp_path):
    parsed = read_package(build_package(tmp_path))
    free = next(
        row for row in parsed.products if row.source_product_id == DOCUMENT_PRODUCT_PAGE_ONLY
    )
    assert free.list_price_current_net == Decimal("0.0000")
    assert free.member_price_current_net == Decimal("0.0000")


def test_absent_publicly_listed_stays_none(tmp_path):
    """The 273-vs-144 discrepancy is unresolved; unknown must stay unknown."""
    parsed = read_package(build_package(tmp_path))
    row = next(
        item for item in parsed.products if item.source_product_id == DOCUMENT_WITH_BOTH_PAGES
    )
    assert row.published is True
    assert row.publicly_listed is None


def test_money_is_decimal_and_exact(tmp_path):
    parsed = read_package(build_package(tmp_path))
    total = sum((row.ordered_value_net for row in parsed.daily_facts), Decimal("0"))
    assert isinstance(total, Decimal)
    assert total == Decimal("511.0000")


def test_float_shaped_money_is_refused(tmp_path):
    rows = default_daily_facts()
    rows[0] = {**rows[0], "ordered_value_net": "3.0e1"}
    package = build_package(tmp_path, daily_facts=rows)
    with pytest.raises(PackageContractError, match="eksponendita"):
        read_package(package)


# ---------------------------------------------------------------------------
# Keys, references and dates
# ---------------------------------------------------------------------------


def test_duplicate_daily_fact_is_refused(tmp_path):
    rows = default_daily_facts()
    rows.append(dict(rows[1]))
    package = build_package(tmp_path, daily_facts=rows)
    with pytest.raises(PackageContractError, match="kordub"):
        read_package(package)


def test_duplicate_product_is_refused(tmp_path):
    rows = default_products()
    rows.append(dict(rows[0]))
    package = build_package(tmp_path, products=rows)
    with pytest.raises(PackageContractError, match="kordub"):
        read_package(package)


def test_fact_for_unknown_product_is_refused(tmp_path):
    rows = default_daily_facts()
    rows[0] = {**rows[0], "source_product_id": "999999"}
    package = build_package(tmp_path, daily_facts=rows)
    with pytest.raises(PackageContractError, match="tundmatule tootele"):
        read_package(package)


def test_path_for_unknown_product_is_refused(tmp_path):
    rows = default_product_paths()
    rows[0] = {**rows[0], "source_product_id": "999999"}
    package = build_package(tmp_path, product_paths=rows)
    with pytest.raises(PackageContractError, match="tundmatule tootele"):
        read_package(package)


def test_two_paths_for_one_role_are_refused(tmp_path):
    rows = default_product_paths()
    rows.append(
        {
            "source_product_id": str(DOCUMENT_WITH_BOTH_PAGES),
            "page_role": "product",
            "canonical_path": "/et/pood/lepingute-naidised/toosuhted/teine",
            "observed_on": "2026-08-11",
        }
    )
    package = build_package(tmp_path, product_paths=rows)
    with pytest.raises(PackageContractError, match="mitu teed"):
        read_package(package)


def test_fact_after_coverage_end_is_refused(tmp_path):
    rows = default_daily_facts()
    rows[0] = {**rows[0], "report_date": "2026-09-01"}
    package = build_package(tmp_path, daily_facts=rows)
    with pytest.raises(PackageContractError, match="coverage_end"):
        read_package(package)


def test_observation_after_source_as_of_is_refused(tmp_path):
    rows = default_products()
    rows[0] = {**rows[0], "observed_on": "2026-09-01"}
    package = build_package(tmp_path, products=rows)
    with pytest.raises(PackageContractError, match="source_as_of"):
        read_package(package)


def test_coverage_start_after_end_is_refused(tmp_path):
    manifest = {**default_manifest(), "coverage_start": "2026-08-12"}
    package = build_package(tmp_path, manifest=manifest)
    with pytest.raises(PackageContractError, match="coverage_start"):
        read_package(package)


def test_impossible_date_is_refused(tmp_path):
    rows = default_daily_facts()
    rows[0] = {**rows[0], "report_date": "2026-02-31"}
    package = build_package(tmp_path, daily_facts=rows)
    with pytest.raises(PackageContractError, match="AAAA-KK-PP"):
        read_package(package)


def test_negative_value_is_refused(tmp_path):
    rows = default_daily_facts()
    rows[0] = {**rows[0], "ordered_value_net": "-1.0000"}
    package = build_package(tmp_path, daily_facts=rows)
    with pytest.raises(PackageContractError, match="negatiivne"):
        read_package(package)


def test_invalid_member_status_is_refused(tmp_path):
    rows = default_daily_facts()
    rows[0] = {**rows[0], "member_status": "probably"}
    package = build_package(tmp_path, daily_facts=rows)
    with pytest.raises(PackageContractError, match="liikmestaatus"):
        read_package(package)


def test_invalid_payment_class_is_refused(tmp_path):
    rows = default_daily_facts()
    rows[0] = {**rows[0], "payment_class": "swedbank"}
    package = build_package(tmp_path, daily_facts=rows)
    with pytest.raises(PackageContractError, match="makseviisi"):
        read_package(package)


def test_invalid_currency_is_refused(tmp_path):
    rows = default_daily_facts()
    rows[0] = {**rows[0], "currency": "USD"}
    package = build_package(tmp_path, daily_facts=rows)
    with pytest.raises(PackageContractError, match="valuuta"):
        read_package(package)


# ---------------------------------------------------------------------------
# Paths go through the one canonicaliser
# ---------------------------------------------------------------------------


def test_path_is_canonicalised_on_the_way_in(tmp_path):
    """A full URL with a query string lands on the stored GA4 join key."""
    rows = default_product_paths()
    rows[0] = {
        **rows[0],
        "canonical_path": (
            "https://www.koda.ee/et/pood/lepingute-naidised/toosuhted/naidisleping/"
            "?utm_source=uudiskiri"
        ),
    }
    package = build_package(tmp_path, product_paths=rows)
    parsed = read_package(package)
    stored = {row.path for row in parsed.product_paths}
    assert "/et/pood/lepingute-naidised/toosuhted/naidisleping" in stored


def test_path_case_is_preserved(tmp_path):
    """Koda.ee product slugs are mixed-case; lowercasing would mismatch GA4."""
    rows = default_product_paths()
    rows[0] = {**rows[0], "canonical_path": "/et/pood/lepingute-naidised/muud/Kakskeelne-Naidis"}
    package = build_package(tmp_path, product_paths=rows)
    parsed = read_package(package)
    assert any(row.path.endswith("/Kakskeelne-Naidis") for row in parsed.product_paths)


# ---------------------------------------------------------------------------
# Archive integrity
# ---------------------------------------------------------------------------


def test_corrupt_checksum_is_refused(tmp_path):
    package = build_package(tmp_path, corrupt_digest_for=PRODUCTS_NAME)
    with pytest.raises(PackageContractError, match="kontrollsumma"):
        read_package(package)


def test_missing_file_is_refused(tmp_path):
    package = build_package(tmp_path, omit={PRODUCT_PATHS_NAME})
    with pytest.raises(PackageContractError, match="kohustuslik fail"):
        read_package(package)


def test_undeclared_file_is_refused(tmp_path):
    package = build_package(tmp_path, undeclared={"orders_with_names.csv": b"nope\n"})
    with pytest.raises(PackageContractError, match="loetlemata"):
        read_package(package)


def test_missing_manifest_is_refused(tmp_path):
    package = build_package(tmp_path, omit={"manifest.json"})
    with pytest.raises(PackageContractError):
        read_package(package)


# ---------------------------------------------------------------------------
# Content checksum
# ---------------------------------------------------------------------------


def test_checksum_ignores_repackaging(tmp_path):
    """A re-export with the same facts must not look like a change.

    `exported_at` moves and the archive bytes differ; the normalised facts do
    not, so the digest must not either.
    """
    first = read_package(build_package(tmp_path, filename="a.zip"))
    manifest = {**default_manifest(), "exported_at": "2026-08-12T09:30:00+03:00"}
    second = read_package(
        build_package(tmp_path, manifest=manifest, filename="b.zip"),
    )
    assert first.package_sha256 != second.package_sha256
    assert content_checksum(first) == content_checksum(second)


def test_checksum_changes_when_a_figure_changes(tmp_path):
    first = read_package(build_package(tmp_path, filename="a.zip"))
    rows = default_daily_facts()
    rows[1] = {**rows[1], "units": "4.00"}
    second = read_package(build_package(tmp_path, daily_facts=rows, filename="b.zip"))
    assert content_checksum(first) != content_checksum(second)


def test_checksum_changes_when_verification_flag_changes(tmp_path):
    """The member gate is part of what the dashboard shows, so it is hashed."""
    first = read_package(build_package(tmp_path, filename="a.zip"))
    manifest = {**default_manifest(), "member_semantics_verified": True}
    second = read_package(build_package(tmp_path, manifest=manifest, filename="b.zip"))
    assert content_checksum(first) != content_checksum(second)
