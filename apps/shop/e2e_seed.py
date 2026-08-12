"""Synthetic E-pood catalogue and order history, through the package importer.

The scale constants are declared here rather than beside the analytics seed that
also reads them: what the synthetic shop contains is the shop's own fact, and the
traffic seeded for it has to follow that rather than the other way round.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from apps.core.e2e_seed import LONG_TITLE

#: Synthetic E-pood scale. Thirty document products is deliberately more than
#: the ranking's page of twenty-five, so a product outside the first page exists
#: for a search test to find. Twenty of them are measured; the last ten are sold
#: and never measured, which is the only way an honest `—` reaches the column.
SHOP_DOCUMENT_PRODUCTS = 30
SHOP_MEASURED_PRODUCTS = 20
SHOP_INFORMATION_PRODUCTS = 5
SHOP_EVENT_INDEX = 3

#: Commerce IDs, in a range no real Koda.ee product occupies.
SHOP_FIRST_PRODUCT_ID = 900001
SHOP_EVENT_PRODUCT_ID = 909001
SHOP_PHYSICAL_PRODUCT_ID = 909002

#: The one product a search test looks up by name. Sits beyond the first page of
#: the ranking on purpose: searching the visible rows would find it anyway, and
#: prove nothing.
SHOP_DEEP_SEARCH_TERM = "sunteetiline-28"

#: How far the Commerce export lags GA4. The seeded export stops ten days before
#: the analytics do, which is the whole point: those ten days of traffic must
#: never be divided by "no orders". Nothing in the interface may show a rate for
#: them.
SHOP_STALE_DAYS = 10

#: How far back the seeded order history runs. Comfortably before GA4's 45 days,
#: so acquisitions exist that no conversion may ever count.
SHOP_HISTORY_DAYS = 400


def _shop_products(observed_on: dt.date) -> list[dict]:
    """The synthetic catalogue.

    Every figure is invented. The 2026-08-11 backend audit produced aggregate
    discovery totals, not a row-level dataset, and seeding those totals as
    records would put fabricated numbers where only source data belongs.
    """
    rows: list[dict] = []
    for index in range(1, SHOP_DOCUMENT_PRODUCTS + 1):
        # One deliberately enormous title, at a rank the layout suite measures.
        title = LONG_TITLE if index == 2 else f"Sünteetiline lepingu näidis {index}"
        # A free template with an explicit zero, beside one whose price is
        # genuinely unknown. The two must never render alike.
        if index == 4:
            list_price, member_price = "0.0000", "0.0000"
        elif index == 5:
            list_price, member_price = "", ""
        else:
            list_price = f"{20 + index}.0000"
            member_price = f"{(20 + index) / 2:.4f}"
        rows.append(
            {
                "source_product_id": str(SHOP_FIRST_PRODUCT_ID + index - 1),
                "product_type": "document",
                "title": title,
                "category_term_id": "159" if index % 2 else "187",
                "category_name": "Töösuhted" if index % 2 else "Lepingute komplektid",
                "published": "true",
                # Left unknown on purpose: the public-listing semantics are not
                # verified, and the interface must show that rather than guess.
                "publicly_listed": "",
                "list_price_current_net": list_price,
                "member_price_current_net": member_price,
                "members_only": "false",
                "connected_event_node_id": "",
                "observed_on": observed_on.isoformat(),
            }
        )
    rows.append(
        {
            "source_product_id": str(SHOP_EVENT_PRODUCT_ID),
            "product_type": "event_registration",
            "title": "Sünteetiline koolitus",
            "category_term_id": "160",
            "category_name": "Koolitused",
            "published": "true",
            "publicly_listed": "true",
            "list_price_current_net": "78.0000",
            "member_price_current_net": "39.0000",
            "members_only": "false",
            "connected_event_node_id": "777001",
            "observed_on": observed_on.isoformat(),
        }
    )
    rows.append(
        {
            "source_product_id": str(SHOP_PHYSICAL_PRODUCT_ID),
            "product_type": "physical_product",
            "title": "Sünteetiline füüsiline toode",
            "category_term_id": "",
            "category_name": "",
            "published": "true",
            "publicly_listed": "true",
            "list_price_current_net": "25.0000",
            "member_price_current_net": "20.0000",
            "members_only": "",
            "connected_event_node_id": "",
            "observed_on": observed_on.isoformat(),
        }
    )
    return rows


def _shop_paths(observed_on: dt.date) -> list[dict]:
    rows: list[dict] = []
    for index in range(1, SHOP_DOCUMENT_PRODUCTS + 1):
        rows.append(
            {
                "source_product_id": str(SHOP_FIRST_PRODUCT_ID + index - 1),
                "page_role": "product",
                "canonical_path": f"/et/pood/lepingute-naidised/sunteetiline-{index}",
                "observed_on": observed_on.isoformat(),
            }
        )
        if index <= SHOP_INFORMATION_PRODUCTS:
            rows.append(
                {
                    "source_product_id": str(SHOP_FIRST_PRODUCT_ID + index - 1),
                    "page_role": "information",
                    "canonical_path": f"/et/tooriistad/sunteetiline-{index}",
                    "observed_on": observed_on.isoformat(),
                }
            )
    rows.append(
        {
            "source_product_id": str(SHOP_EVENT_PRODUCT_ID),
            "page_role": "event",
            "canonical_path": f"/et/sundmused/sunteetiline-{SHOP_EVENT_INDEX}",
            "observed_on": observed_on.isoformat(),
        }
    )
    rows.append(
        {
            "source_product_id": str(SHOP_PHYSICAL_PRODUCT_ID),
            "page_role": "product",
            "canonical_path": "/et/pood/tooted/sunteetiline-fyysiline",
            "observed_on": observed_on.isoformat(),
        }
    )
    return rows


def _shop_facts(coverage_start: dt.date, coverage_end: dt.date) -> list[dict]:
    """Daily cells across the whole span, including before GA4 existed.

    Deterministic: re-running the seed on the same day produces an identical
    normalised payload, so the second import reports `unchanged` rather than
    filling the history with revisions of itself.
    """
    rows: list[dict] = []

    def add(day, product_id, member, payment, orders, units, value):
        rows.append(
            {
                "report_date": day.isoformat(),
                "source_product_id": str(product_id),
                "commerce_state": "completed",
                "member_status": member,
                "payment_class": payment,
                "order_count": str(orders),
                "units": f"{units}.00",
                "ordered_value_net": f"{value}.0000",
                "currency": "EUR",
            }
        )

    for index in range(1, SHOP_DOCUMENT_PRODUCTS + 1):
        product_id = SHOP_FIRST_PRODUCT_ID + index - 1
        # One cell well before GA4 coverage began, so acquisitions exist that no
        # conversion rate may ever take into its numerator.
        add(coverage_start + dt.timedelta(days=index), product_id, "member", "invoice", 2, 2, 40)
        # One cell inside the measured window.
        recent = coverage_end - dt.timedelta(days=index % 20 + 1)
        add(recent, product_id, "non_member", "bank_or_card", 1, 1, 30)
        if index % 3 == 0:
            # An unknown member status, which must stay distinct from both.
            add(recent, product_id, "unknown", "unknown", 1, 1, 0)
        if index == 4:
            # The free template: an explicit zero value, not an absence.
            add(recent, product_id, "member", "bank_or_card", 3, 3, 0)

    add(
        coverage_end - dt.timedelta(days=5),
        SHOP_EVENT_PRODUCT_ID,
        "member",
        "bank_or_card",
        4,
        9,  # one order, several participants: units and orders differ on purpose
        351,
    )
    add(
        coverage_end - dt.timedelta(days=7),
        SHOP_PHYSICAL_PRODUCT_ID,
        "non_member",
        "bank_or_card",
        1,
        1,
        25,
    )
    return rows


def _write_shop_package(path: Path, today: dt.date) -> tuple[Path, dt.date, dt.date]:
    import csv as _csv
    import io as _io
    import json as _json
    import zipfile as _zipfile

    from apps.shop.package import (
        DAILY_FACTS_NAME,
        MANIFEST_NAME,
        PRODUCT_PATHS_NAME,
        PRODUCTS_NAME,
        REQUIRED_HEADERS,
    )

    # The export stops before the analytics do. That gap is the point.
    coverage_end = today - dt.timedelta(days=SHOP_STALE_DAYS)
    coverage_start = coverage_end - dt.timedelta(days=SHOP_HISTORY_DAYS)

    tables = {
        PRODUCTS_NAME: _shop_products(coverage_end),
        PRODUCT_PATHS_NAME: _shop_paths(coverage_end),
        DAILY_FACTS_NAME: _shop_facts(coverage_start, coverage_end),
    }

    payloads: dict[str, bytes] = {}
    for name, rows in tables.items():
        header = REQUIRED_HEADERS[name]
        buffer = _io.StringIO(newline="")
        writer = _csv.DictWriter(buffer, fieldnames=list(header), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in header})
        payloads[name] = buffer.getvalue().encode("utf-8")

    manifest = {
        "schema_version": "1.0",
        "source_name": "Sünteetiline e-poe väljavõte",
        "source_as_of": coverage_end.isoformat(),
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "exported_at": f"{coverage_end.isoformat()}T12:00:00+03:00",
        "timezone": "Europe/Tallinn",
        "money_basis": "net_ex_vat",
        "order_state_filter": "completed",
        # Both gates stay shut: the seed must exercise the withheld state,
        # because that is the state production is in.
        "member_semantics_verified": False,
        "public_listing_semantics_verified": False,
        "files": [
            {
                "path": name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
            for name, payload in sorted(payloads.items())
        ],
    }

    target = path / "epood.zip"
    with _zipfile.ZipFile(target, "w", _zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            MANIFEST_NAME, _json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        )
        for name, payload in sorted(payloads.items()):
            archive.writestr(name, payload)
    return target, coverage_start, coverage_end


def seed(today: dt.date) -> str:
    """Publish the synthetic E-pood dataset through the real importer.

    Nothing is written directly: the package goes through the same validation,
    artifact registration, import run and atomic publication a real export
    would, so the seeded state is one the application could actually reach.
    """
    import tempfile

    from apps.shop.importing import import_shop_package

    with tempfile.TemporaryDirectory() as directory:
        package, start, end = _write_shop_package(Path(directory), today)
        result = import_shop_package(package, dry_run=False)

    status = "unchanged" if result.unchanged else "imported"
    return (
        f"e-pood: {status} ({result.counts.get('products', 0)} toodet, "
        f"{result.counts.get('daily_facts', 0)} päevafakti, "
        f"{start:%d.%m.%Y}–{end:%d.%m.%Y}, veebistatistika ulatub kaugemale)"
    )
