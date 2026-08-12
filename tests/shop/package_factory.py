"""Builds synthetic E-pood import packages for the tests.

**Everything here is invented.** No real Koda.ee product, price, order count or
value is reproduced. The 2026-08-11 backend audit produced aggregate discovery
figures, not a row-level dataset, and seeding those totals as if they were
records would put fabricated numbers into a database that is supposed to hold
only what a source actually said.

The builder produces a package that passes every check by default and exposes
enough seams to break exactly one thing at a time: an unknown column, a wrong
digest, a bad Commerce state, a dangling product reference, a duplicate key, a
date outside coverage, or contradictory manifest semantics.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from apps.shop.package import (
    DAILY_FACTS_NAME,
    DAILY_ORDERS_NAME,
    MANIFEST_NAME,
    PRODUCT_PATHS_NAME,
    PRODUCTS_NAME,
    headers_for,
)

PACKAGE_ROOT = "dashkoda-epood-package"

# Synthetic product IDs, deliberately in a range no real Koda.ee product uses.
DOCUMENT_WITH_BOTH_PAGES = 900001
DOCUMENT_PRODUCT_PAGE_ONLY = 900002
EVENT_PRODUCT = 900003
PHYSICAL_PRODUCT = 900004


def _row(header: tuple[str, ...], values: dict) -> dict:
    return {name: values.get(name, "") for name in header}


def _csv_bytes(
    name: str,
    rows: list[dict],
    *,
    header: tuple[str, ...] | None = None,
    version: str = "2.0",
) -> bytes:
    header = header or headers_for(name, version)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(header), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(_row(header, row))
    return buffer.getvalue().encode("utf-8")


#: The as-of date every default row is observed on. A package is only coherent
#: when its observations do not postdate its own export, so this is also the
#: manifest's `source_as_of` — and a builder given a manifest that moves that
#: date backwards moves the observations with it. See `PackageBuilder`.
DEFAULT_AS_OF = "2026-08-11"


def default_products(observed_on: str = DEFAULT_AS_OF) -> list[dict]:
    return [
        {
            "source_product_id": str(DOCUMENT_WITH_BOTH_PAGES),
            "product_type": "document",
            "title": "Näidisleping ühe lehega",
            "category_term_id": "159",
            "category_name": "Töösuhted",
            "published": "true",
            "publicly_listed": "",  # genuinely unknown, must stay null
            "list_price_current_net": "30.0000",
            "member_price_current_net": "15.0000",
            "members_only": "false",
            "connected_event_node_id": "",
            "observed_on": observed_on,
        },
        {
            "source_product_id": str(DOCUMENT_PRODUCT_PAGE_ONLY),
            "product_type": "document",
            "title": (
                "Väga pikk näidislepingu pealkiri mis peab tabelis reavahetusega "
                "murduma ja mitte lehte laiemaks venitama"
            ),
            "category_term_id": "187",
            "category_name": "Lepingute komplektid",
            "published": "true",
            "publicly_listed": "true",
            "list_price_current_net": "0.0000",  # explicit free, not absent
            "member_price_current_net": "0.0000",
            "members_only": "false",
            "connected_event_node_id": "",
            "observed_on": observed_on,
        },
        {
            "source_product_id": str(EVENT_PRODUCT),
            "product_type": "event_registration",
            "title": "Näidiskoolitus",
            "category_term_id": "160",
            "category_name": "Koolitused",
            "published": "true",
            "publicly_listed": "true",
            "list_price_current_net": "78.0000",
            "member_price_current_net": "39.0000",
            "members_only": "false",
            "connected_event_node_id": "777001",
            "observed_on": observed_on,
        },
        {
            "source_product_id": str(PHYSICAL_PRODUCT),
            "product_type": "physical_product",
            "title": "Näidistoode",
            "category_term_id": "",
            "category_name": "",
            "published": "true",
            "publicly_listed": "true",
            "list_price_current_net": "",  # price genuinely unknown, stays null
            "member_price_current_net": "",
            "members_only": "",
            "connected_event_node_id": "",
            "observed_on": observed_on,
        },
    ]


def default_product_paths(observed_on: str = DEFAULT_AS_OF) -> list[dict]:
    return [
        {
            "source_product_id": str(DOCUMENT_WITH_BOTH_PAGES),
            "page_role": "product",
            "canonical_path": "/et/pood/lepingute-naidised/toosuhted/naidisleping",
            "observed_on": observed_on,
        },
        {
            "source_product_id": str(DOCUMENT_WITH_BOTH_PAGES),
            "page_role": "information",
            "canonical_path": "/et/tooriistad/naidisleping",
            "observed_on": observed_on,
        },
        {
            "source_product_id": str(DOCUMENT_PRODUCT_PAGE_ONLY),
            "page_role": "product",
            "canonical_path": "/et/pood/lepingute-naidised/komplektid/naidiskomplekt",
            "observed_on": observed_on,
        },
        {
            "source_product_id": str(EVENT_PRODUCT),
            "page_role": "event",
            "canonical_path": "/et/sundmused/naidiskoolitus",
            "observed_on": observed_on,
        },
        # PHYSICAL_PRODUCT deliberately has no path: a product with no mapping
        # must still appear in Commerce totals and must yield no conversion.
    ]


def default_daily_facts() -> list[dict]:
    """Fact cells, with the free/paid split schema 2.0 requires.

    The split is stated per row rather than derived from the cell value on the
    way out, because that is the whole point of classifying at line level: a
    cell mixing a free acquisition with a paid one has a positive total and
    would otherwise read as entirely paid.
    """
    rows = [
        # Before GA4 coverage begins (2023-06-16 on the real property): must
        # never enter a conversion numerator.
        {
            "report_date": "2021-03-04",
            "source_product_id": str(DOCUMENT_WITH_BOTH_PAGES),
            "commerce_state": "completed",
            "member_status": "member",
            "payment_class": "invoice",
            "order_count": "2",
            "units": "2.00",
            "ordered_value_net": "30.0000",
            "currency": "EUR",
        },
        {
            "report_date": "2026-03-10",
            "source_product_id": str(DOCUMENT_WITH_BOTH_PAGES),
            "commerce_state": "completed",
            "member_status": "member",
            "payment_class": "bank_or_card",
            "order_count": "3",
            "units": "3.00",
            "ordered_value_net": "45.0000",
            "currency": "EUR",
        },
        {
            "report_date": "2026-03-10",
            "source_product_id": str(DOCUMENT_WITH_BOTH_PAGES),
            "commerce_state": "completed",
            "member_status": "non_member",
            "payment_class": "invoice",
            "order_count": "1",
            "units": "1.00",
            "ordered_value_net": "30.0000",
            "currency": "EUR",
        },
        {
            "report_date": "2026-04-02",
            "source_product_id": str(DOCUMENT_WITH_BOTH_PAGES),
            "commerce_state": "completed",
            "member_status": "unknown",
            "payment_class": "unknown",
            "order_count": "1",
            "units": "1.00",
            "ordered_value_net": "30.0000",
            "currency": "EUR",
        },
        # A free acquisition with an explicit zero value.
        {
            "report_date": "2026-04-02",
            "source_product_id": str(DOCUMENT_PRODUCT_PAGE_ONLY),
            "commerce_state": "completed",
            "member_status": "member",
            "payment_class": "bank_or_card",
            "order_count": "4",
            "units": "4.00",
            "ordered_value_net": "0.0000",
            "currency": "EUR",
        },
        {
            "report_date": "2026-05-20",
            "source_product_id": str(EVENT_PRODUCT),
            "commerce_state": "completed",
            "member_status": "member",
            "payment_class": "bank_or_card",
            "order_count": "6",
            "units": "9.00",  # one order, several participants
            "ordered_value_net": "351.0000",
            "currency": "EUR",
        },
        {
            "report_date": "2026-06-01",
            "source_product_id": str(PHYSICAL_PRODUCT),
            "commerce_state": "completed",
            "member_status": "non_member",
            "payment_class": "bank_or_card",
            "order_count": "1",
            "units": "1.00",
            "ordered_value_net": "25.0000",
            "currency": "EUR",
        },
    ]
    for row in rows:
        units = Decimal(row["units"])
        if Decimal(row["ordered_value_net"]) == 0:
            row["free_units"], row["paid_units"] = f"{units:.2f}", "0.00"
        else:
            row["free_units"], row["paid_units"] = "0.00", f"{units:.2f}"
    # One cell whose classification the source could not complete: two units,
    # one of them paid, the remainder genuinely unknown rather than free.
    rows[3]["free_units"], rows[3]["paid_units"] = "0.00", "0.00"
    return rows


def default_daily_orders() -> list[dict]:
    """Distinct order counts, including the required all-types row per day.

    2026-03-10 deliberately carries two product cells from **one** order, so a
    test can show that adding the fact grain would count it twice.
    """
    return [
        {"report_date": "2021-03-04", "product_type": "", "distinct_order_count": "2"},
        {"report_date": "2021-03-04", "product_type": "document", "distinct_order_count": "2"},
        {"report_date": "2026-03-10", "product_type": "", "distinct_order_count": "3"},
        {"report_date": "2026-03-10", "product_type": "document", "distinct_order_count": "3"},
        {"report_date": "2026-04-02", "product_type": "", "distinct_order_count": "5"},
        {"report_date": "2026-04-02", "product_type": "document", "distinct_order_count": "5"},
        {"report_date": "2026-05-20", "product_type": "", "distinct_order_count": "6"},
        {
            "report_date": "2026-05-20",
            "product_type": "event_registration",
            "distinct_order_count": "6",
        },
        {"report_date": "2026-06-01", "product_type": "", "distinct_order_count": "1"},
        {
            "report_date": "2026-06-01",
            "product_type": "physical_product",
            "distinct_order_count": "1",
        },
    ]


def default_manifest() -> dict:
    return {
        "schema_version": "2.0",
        "source_name": "Koda.ee Commerce (sünteetiline testväljavõte)",
        "source_as_of": DEFAULT_AS_OF,
        "coverage_start": "2020-10-22",
        "coverage_end": "2026-08-11",
        "exported_at": "2026-08-11T12:00:00+03:00",
        "timezone": "Europe/Tallinn",
        "money_basis": "net_ex_vat",
        "order_state_filter": "completed",
        "member_semantics_verified": False,
        "public_listing_semantics_verified": False,
    }


@dataclass
class PackageBuilder:
    """A valid package by default, with one seam per failure mode.

    `products` and `product_paths` default to `None` rather than to the lists
    themselves so that the default rows can be observed on whatever day the
    manifest says the export was taken. They used to be built independently of
    it, which made the builder produce an *invalid* package the moment a test
    moved `source_as_of` backwards to describe a stale export — the observations
    stayed in August and the contract rejected them for postdating the export.
    Two of the stale-Commerce tests were failing on exactly that.

    Passing either list explicitly overrides this entirely, which is what the
    contract tests that deliberately postdate an observation rely on.
    """

    products: list[dict] | None = None
    product_paths: list[dict] | None = None
    daily_facts: list[dict] = field(default_factory=default_daily_facts)
    #: `None` builds the default set; an explicit empty list writes the file
    #: with no rows, which is how a 2.0 package with no orders is simulated.
    daily_orders: list[dict] | None = None
    manifest: dict = field(default_factory=default_manifest)

    #: Override a file's header, to simulate an export that grew a column.
    headers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Extra rows keyed by file, written with the overridden header.
    extra_values: dict[str, list[dict]] = field(default_factory=dict)
    #: Files to omit, extra undeclared members, and digest corruption.
    omit: set[str] = field(default_factory=set)
    undeclared: dict[str, bytes] = field(default_factory=dict)
    corrupt_digest_for: str | None = None
    wrap_in_root: bool = True

    def __post_init__(self) -> None:
        as_of = self.manifest.get("source_as_of", DEFAULT_AS_OF)
        if self.products is None:
            self.products = default_products(as_of)
        if self.product_paths is None:
            self.product_paths = default_product_paths(as_of)
        if self.daily_orders is None:
            self.daily_orders = default_daily_orders()
        if self.version < "2.0":
            # A 1.0 package cannot carry either addition. Dropping the columns
            # here is what lets a test build a genuine 1.0 export rather than a
            # 2.0 one with holes in it.
            self.daily_orders = []
            self.daily_facts = [
                {k: v for k, v in row.items() if k not in ("free_units", "paid_units")}
                for row in self.daily_facts
            ]

    @property
    def version(self) -> str:
        return str(self.manifest.get("schema_version", "2.0"))

    def _payloads(self) -> dict[str, bytes]:
        payloads: dict[str, bytes] = {}
        tables = {
            PRODUCTS_NAME: self.products,
            PRODUCT_PATHS_NAME: self.product_paths,
            DAILY_FACTS_NAME: self.daily_facts,
        }
        if self.version >= "2.0":
            tables[DAILY_ORDERS_NAME] = self.daily_orders
        for name, rows in tables.items():
            if name in self.omit:
                continue
            header = self.headers.get(name)
            values = self.extra_values.get(name, rows)
            payloads[name] = _csv_bytes(name, values, header=header, version=self.version)
        return payloads

    def build(self, directory: Path, *, filename: str = "epood.zip") -> Path:
        payloads = self._payloads()
        files = []
        for name, payload in sorted(payloads.items()):
            digest = hashlib.sha256(payload).hexdigest()
            if self.corrupt_digest_for == name:
                digest = "0" * 64
            files.append({"path": name, "sha256": digest, "size_bytes": len(payload)})

        manifest = dict(self.manifest)
        manifest["files"] = files
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

        target = directory / filename
        prefix = f"{PACKAGE_ROOT}/" if self.wrap_in_root else ""
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            if MANIFEST_NAME not in self.omit:
                archive.writestr(f"{prefix}{MANIFEST_NAME}", manifest_bytes)
            for name, payload in sorted(payloads.items()):
                archive.writestr(f"{prefix}{name}", payload)
            for name, payload in sorted(self.undeclared.items()):
                archive.writestr(f"{prefix}{name}", payload)
        return target


def build_package(directory: Path, *, filename: str = "epood.zip", **overrides) -> Path:
    """A valid synthetic package, unless an override breaks one thing.

    `filename` is separate from the builder's fields so a test can write two
    packages into one `tmp_path` without the second overwriting the first.
    """
    return PackageBuilder(**overrides).build(directory, filename=filename)
