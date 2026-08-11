"""Reading and validating the manual E-pood commerce package.

Like `apps/membership/package.py`, this module holds no Django import: whether an
archive is the approved contract is a question about bytes, and keeping it
separate means the whole contract can be exercised on a machine with no
PostgreSQL — which is where the real export gets checked before it is trusted.

The archive is treated as hostile until every check has passed: no absolute
path, no parent-directory segment, no symlink, bounded members, bounded sizes,
bounded compression ratio, a manifest that names every file with its SHA-256,
and an exact header on every CSV.

## Why the header must match exactly

This is the privacy boundary, and it is the reason unknown columns are a hard
failure rather than something quietly ignored. The upstream source is a Drupal
Commerce order table full of names, e-mail addresses, telephone numbers, postal
addresses, participant lists, registry codes and payment-gateway transaction
IDs. An importer that skipped columns it did not recognise would accept a export
carrying any of those and simply not store them *this time* — leaving the file
on disk, in the artifact store and in whatever log recorded the run.

Refusing the file outright means a mistaken export is a failed import with a
clear message, not a quiet privacy incident.

## What the manifest must agree to

Three declarations are checked rather than trusted, because each one changes
what every stored number means:

- `money_basis` must be `net_ex_vat`. A gross figure imported as net would
  overstate every value by the VAT rate.
- `order_state_filter` must be `completed`, meaning the Drupal Commerce order
  **state**. Koda.ee also carries a custom `field_order_completed` flag whose
  value disagrees with the state on roughly four orders in five; a package built
  from that flag is refused here rather than discovered later in a chart.
- `timezone` must be `Europe/Tallinn`, the application's own zone, so a report
  date means the same day on both sides.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

from apps.visibility.ga4_paths import canonical_path

#: The importer's own contract version, combined with the package digest to make
#: the import key. Raising it makes a previously imported package importable
#: again under new parsing rules.
PACKAGE_SCHEMA_VERSION = "1.0"

#: Manifest schema versions this importer knows how to read.
SUPPORTED_MANIFEST_SCHEMA_VERSIONS = frozenset({"1.0"})

MANIFEST_NAME = "manifest.json"
PRODUCTS_NAME = "products.csv"
DAILY_FACTS_NAME = "daily_facts.csv"
PRODUCT_PATHS_NAME = "product_paths.csv"

CHUNK_SIZE = 64 * 1024
MAX_COMPRESSION_RATIO = 200

#: The only semantics schema version 1 accepts. See the module docstring.
REQUIRED_MONEY_BASIS = "net_ex_vat"
REQUIRED_ORDER_STATE = "completed"
REQUIRED_TIMEZONE = "Europe/Tallinn"

#: Product families this module covers. `membership` and `default` are refused
#: rather than skipped: a package offering them was built to a different scope,
#: and silently dropping rows would make the totals disagree with the source.
ALLOWED_PRODUCT_TYPES = frozenset({"document", "event_registration", "physical_product"})
ALLOWED_MEMBER_STATUSES = frozenset({"member", "non_member", "unknown"})
ALLOWED_PAYMENT_CLASSES = frozenset({"invoice", "bank_or_card", "unknown"})
ALLOWED_PAGE_ROLES = frozenset({"product", "information", "event"})
ALLOWED_CURRENCIES = frozenset({"EUR"})

#: A decimal the source may state. Deliberately not `float`-friendly: no
#: exponent, no `NaN`, no `Infinity`, because each of those reaches `Decimal`
#: happily and none of them is a price.
DECIMAL_PATTERN = re.compile(r"^-?\d+(\.\d+)?$")

REQUIRED_HEADERS: dict[str, tuple[str, ...]] = {
    PRODUCTS_NAME: (
        "source_product_id",
        "product_type",
        "title",
        "category_term_id",
        "category_name",
        "published",
        "publicly_listed",
        "list_price_current_net",
        "member_price_current_net",
        "members_only",
        "connected_event_node_id",
        "observed_on",
    ),
    DAILY_FACTS_NAME: (
        "report_date",
        "source_product_id",
        "commerce_state",
        "member_status",
        "payment_class",
        "order_count",
        "units",
        "ordered_value_net",
        "currency",
    ),
    PRODUCT_PATHS_NAME: (
        "source_product_id",
        "page_role",
        "canonical_path",
        "observed_on",
    ),
}

REQUIRED_PATHS: tuple[str, ...] = (MANIFEST_NAME, *REQUIRED_HEADERS)


class PackageContractError(RuntimeError):
    """The package is not the approved contract. Nothing is imported."""


@dataclass(frozen=True)
class PackageLimits:
    max_package_bytes: int = 25 * 1024 * 1024
    max_uncompressed_bytes: int = 200 * 1024 * 1024
    max_member_bytes: int = 100 * 1024 * 1024
    max_members: int = 16


@dataclass(frozen=True)
class ProductRow:
    source_product_id: int
    product_type: str
    title: str
    category_term_id: int | None
    category_name: str
    published: bool | None
    publicly_listed: bool | None
    list_price_current_net: Decimal | None
    member_price_current_net: Decimal | None
    members_only: bool | None
    connected_event_node_id: int | None
    observed_on: date


@dataclass(frozen=True)
class DailyFactRow:
    report_date: date
    source_product_id: int
    member_status: str
    payment_class: str
    order_count: int
    units: Decimal
    ordered_value_net: Decimal
    currency: str


@dataclass(frozen=True)
class ProductPathRow:
    source_product_id: int
    page_role: str
    path: str
    observed_on: date


@dataclass(frozen=True)
class PackageManifest:
    schema_version: str
    source_name: str
    source_as_of: date
    coverage_start: date
    coverage_end: date
    exported_at: str
    timezone: str
    money_basis: str
    order_state_filter: str
    member_semantics_verified: bool
    public_listing_semantics_verified: bool


@dataclass(frozen=True)
class ParsedPackage:
    package_sha256: str
    package_size_bytes: int
    manifest: PackageManifest
    products: tuple[ProductRow, ...]
    daily_facts: tuple[DailyFactRow, ...]
    product_paths: tuple[ProductPathRow, ...]

    @property
    def row_counts(self) -> dict[str, int]:
        """Aggregate counts only. Never a title, never a value, never a path."""
        return {
            "products": len(self.products),
            "daily_facts": len(self.daily_facts),
            "product_paths": len(self.product_paths),
        }


# --------------------------------------------------------------------------
# Archive safety
# --------------------------------------------------------------------------


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _safe_relative_name(raw_name: str) -> str | None:
    if raw_name.endswith("/"):
        return None
    if "\\" in raw_name or raw_name.startswith("/"):
        raise PackageContractError("Pakett sisaldab lubamatut failiteed.")
    parts = PurePosixPath(raw_name).parts
    if any(part in ("..", ".") for part in parts):
        raise PackageContractError("Pakett sisaldab lubamatut failiteed.")
    return "/".join(parts)


def _strip_root_prefix(names: list[str]) -> str:
    roots = {name.split("/", 1)[0] for name in names if "/" in name}
    top_level = {name for name in names if "/" not in name}
    if MANIFEST_NAME in top_level:
        return ""
    if len(roots) == 1:
        return f"{next(iter(roots))}/"
    raise PackageContractError("Paketi struktuur ei ole ootuspärane: manifest.json puudub juurest.")


def file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


# --------------------------------------------------------------------------
# Cell parsing
# --------------------------------------------------------------------------


def _text(value: str | None) -> str:
    return (value or "").strip()


def _required_text(value: str | None, *, column: str) -> str:
    parsed = _text(value)
    if not parsed:
        raise PackageContractError(f"Veerg {column} on kohustuslik.")
    return parsed


def _optional_int(value: str | None, *, column: str) -> int | None:
    raw = _text(value)
    if not raw:
        return None
    if not raw.lstrip("-").isdigit():
        raise PackageContractError(f"Veerg {column} peab olema täisarv.")
    return int(raw)


def _required_int(value: str | None, *, column: str) -> int:
    parsed = _optional_int(value, column=column)
    if parsed is None:
        raise PackageContractError(f"Veerg {column} on kohustuslik.")
    return parsed


def _positive_id(value: str | None, *, column: str) -> int:
    parsed = _required_int(value, column=column)
    if parsed <= 0:
        raise PackageContractError(f"Veerg {column} peab olema positiivne.")
    return parsed


def _optional_decimal(value: str | None, *, column: str) -> Decimal | None:
    """A decimal, or `None` for an empty cell.

    An empty cell is **absence** and stays `None`; an explicit `0` is a real
    value and stays zero. Conflating them would turn "we do not know this
    product's price" into "this product is free".
    """
    raw = _text(value)
    if not raw:
        return None
    if not DECIMAL_PATTERN.match(raw):
        raise PackageContractError(
            f"Veerg {column} peab olema kümnendarv ilma eksponendita: {raw[:20]!r}."
        )
    try:
        return Decimal(raw)
    except InvalidOperation as error:
        raise PackageContractError(f"Veerg {column} peab olema arv.") from error


def _required_decimal(value: str | None, *, column: str) -> Decimal:
    parsed = _optional_decimal(value, column=column)
    if parsed is None:
        raise PackageContractError(f"Veerg {column} on kohustuslik.")
    return parsed


def _not_negative(value: Decimal, *, column: str) -> Decimal:
    if value < 0:
        raise PackageContractError(f"Veerg {column} ei tohi olla negatiivne.")
    return value


def _optional_bool(value: str | None, *, column: str) -> bool | None:
    """`true`/`false`, or `None` when the source does not know.

    A tri-state on purpose. `publicly_listed` is genuinely unknown for the
    current export, and coercing an unknown to `false` would quietly shrink the
    catalogue rather than admitting the gap.
    """
    raw = _text(value).lower()
    if not raw:
        return None
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    raise PackageContractError(f"Veerg {column} peab olema true, false või tühi.")


def _required_date(value: str | None, *, column: str) -> date:
    raw = _required_text(value, column=column)
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise PackageContractError(f"Veerg {column} peab olema kujul AAAA-KK-PP.") from error


def _rows(payload: bytes, *, name: str):
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise PackageContractError(f"Fail ei ole UTF-8: {name}.") from error

    reader = csv.DictReader(io.StringIO(text, newline=""))
    expected = REQUIRED_HEADERS[name]
    actual = tuple(reader.fieldnames or ())
    if actual != expected:
        unexpected = [column for column in actual if column not in expected]
        if unexpected:
            # Named explicitly: an export that grew an `email` column is the
            # case this check exists for, and "header mismatch" alone would send
            # somebody looking for a typo.
            raise PackageContractError(
                f"Faili {name} päis sisaldab lubamatuid veerge: {', '.join(unexpected[:5])}."
            )
        raise PackageContractError(f"Faili {name} päis ei vasta kokkuleppele.")
    return reader


# --------------------------------------------------------------------------
# Package reading
# --------------------------------------------------------------------------


def _read_members(archive: zipfile.ZipFile, limits: PackageLimits) -> dict[str, bytes]:
    infos = archive.infolist()
    if len(infos) > limits.max_members:
        raise PackageContractError("Pakett sisaldab liiga palju faile.")

    named: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        if _is_symlink(info):
            raise PackageContractError("Pakett sisaldab nimeviidet.")
        name = _safe_relative_name(info.filename)
        if name is None:
            continue
        if info.file_size > limits.max_member_bytes:
            raise PackageContractError("Paketi fail on liiga suur.")
        if info.compress_size > 0 and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise PackageContractError("Paketi fail on kahtlaselt tugevalt pakitud.")
        named[name] = info

    if sum(info.file_size for info in named.values()) > limits.max_uncompressed_bytes:
        raise PackageContractError("Paketi lahtipakitud maht on liiga suur.")

    prefix = _strip_root_prefix(list(named))

    payloads: dict[str, bytes] = {}
    extracted_total = 0
    for name, info in named.items():
        if prefix and not name.startswith(prefix):
            raise PackageContractError("Pakett sisaldab mitut juurkataloogi.")
        relative = name[len(prefix) :]
        with archive.open(info, "r") as handle:
            payload = handle.read(info.file_size + 1)
        if len(payload) != info.file_size:
            raise PackageContractError("Paketi faili tegelik suurus ei vasta deklareeritule.")
        extracted_total += len(payload)
        if extracted_total > limits.max_uncompressed_bytes:
            raise PackageContractError("Paketi lahtipakitud maht on liiga suur.")
        payloads[relative] = payload
    return payloads


def _load_manifest(payloads: dict[str, bytes]) -> tuple[PackageManifest, dict[str, dict]]:
    if MANIFEST_NAME not in payloads:
        raise PackageContractError("Paketist puudub manifest.json.")
    try:
        raw = json.loads(payloads[MANIFEST_NAME].decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PackageContractError("Manifest ei ole loetav JSON.") from error

    version = str(raw.get("schema_version", "")).strip()
    if version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
        raise PackageContractError(f"Manifesti skeemi versioon ei ole toetatud: {version or '-'}.")

    money_basis = str(raw.get("money_basis", "")).strip()
    if money_basis != REQUIRED_MONEY_BASIS:
        raise PackageContractError(
            f"Manifest peab deklareerima money_basis={REQUIRED_MONEY_BASIS}, "
            f"mitte {money_basis or '-'}."
        )
    order_state = str(raw.get("order_state_filter", "")).strip()
    if order_state != REQUIRED_ORDER_STATE:
        raise PackageContractError(
            f"Manifest peab deklareerima order_state_filter={REQUIRED_ORDER_STATE} "
            "(Commerce'i tellimuse olek, mitte field_order_completed), "
            f"mitte {order_state or '-'}."
        )
    zone = str(raw.get("timezone", "")).strip()
    if zone != REQUIRED_TIMEZONE:
        raise PackageContractError(
            f"Manifest peab deklareerima timezone={REQUIRED_TIMEZONE}, mitte {zone or '-'}."
        )

    def _manifest_date(key: str) -> date:
        value = str(raw.get(key, "")).strip()
        if not value:
            raise PackageContractError(f"Manifestis puudub {key}.")
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise PackageContractError(f"Manifesti {key} peab olema kujul AAAA-KK-PP.") from error

    source_as_of = _manifest_date("source_as_of")
    coverage_start = _manifest_date("coverage_start")
    coverage_end = _manifest_date("coverage_end")
    if coverage_start > coverage_end:
        raise PackageContractError("Manifesti coverage_start on hilisem kui coverage_end.")
    if coverage_end > source_as_of:
        raise PackageContractError("Manifesti coverage_end on hilisem kui source_as_of.")

    manifest = PackageManifest(
        schema_version=version,
        source_name=str(raw.get("source_name", "")).strip(),
        source_as_of=source_as_of,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        exported_at=str(raw.get("exported_at", "")).strip(),
        timezone=zone,
        money_basis=money_basis,
        order_state_filter=order_state,
        member_semantics_verified=bool(raw.get("member_semantics_verified", False)),
        public_listing_semantics_verified=bool(raw.get("public_listing_semantics_verified", False)),
    )

    entries = raw.get("files")
    if not isinstance(entries, list) or not entries:
        raise PackageContractError("Manifest ei loetle ühtegi faili.")
    listed: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PackageContractError("Manifesti kirje ei ole objekt.")
        path = _text(entry.get("path"))
        if not path or path in listed:
            raise PackageContractError("Manifesti kirje failitee on puudu või kordub.")
        listed[path] = entry
    return manifest, listed


def _verify_manifest(payloads: dict[str, bytes], listed: dict[str, dict]) -> None:
    for path, entry in listed.items():
        payload = payloads.get(path)
        if payload is None:
            raise PackageContractError(f"Manifestis loetletud fail puudub: {path}.")
        expected_size = entry.get("size_bytes")
        if not isinstance(expected_size, int) or len(payload) != expected_size:
            raise PackageContractError(f"Faili suurus ei vasta manifestile: {path}.")
        if hashlib.sha256(payload).hexdigest() != _text(entry.get("sha256")).lower():
            raise PackageContractError(f"Faili kontrollsumma ei vasta manifestile: {path}.")

    undeclared = set(payloads) - set(listed) - {MANIFEST_NAME}
    if undeclared:
        raise PackageContractError("Pakett sisaldab manifestis loetlemata faile.")

    missing = [path for path in REQUIRED_PATHS if path not in payloads]
    if missing:
        raise PackageContractError(f"Paketist puudub kohustuslik fail: {missing[0]}.")


# --------------------------------------------------------------------------
# Table parsing
# --------------------------------------------------------------------------


def _parse_products(payload: bytes, *, manifest: PackageManifest) -> tuple[ProductRow, ...]:
    rows: list[ProductRow] = []
    seen: set[int] = set()
    for raw in _rows(payload, name=PRODUCTS_NAME):
        product_id = _positive_id(raw["source_product_id"], column="source_product_id")
        if product_id in seen:
            raise PackageContractError(f"Toote source_product_id kordub: {product_id}.")
        seen.add(product_id)

        product_type = _required_text(raw["product_type"], column="product_type")
        if product_type not in ALLOWED_PRODUCT_TYPES:
            raise PackageContractError(
                f"Tooteliik ei ole e-poe analüütika osa: {product_type}. "
                "Lubatud on document, event_registration, physical_product."
            )

        observed_on = _required_date(raw["observed_on"], column="observed_on")
        if observed_on > manifest.source_as_of:
            raise PackageContractError(
                f"Toote vaatluse kuupäev on hilisem kui source_as_of: {observed_on}."
            )

        list_price = _optional_decimal(
            raw["list_price_current_net"], column="list_price_current_net"
        )
        member_price = _optional_decimal(
            raw["member_price_current_net"], column="member_price_current_net"
        )
        if list_price is not None:
            _not_negative(list_price, column="list_price_current_net")
        if member_price is not None:
            _not_negative(member_price, column="member_price_current_net")

        rows.append(
            ProductRow(
                source_product_id=product_id,
                product_type=product_type,
                title=_required_text(raw["title"], column="title"),
                category_term_id=_optional_int(raw["category_term_id"], column="category_term_id"),
                category_name=_text(raw["category_name"]),
                published=_optional_bool(raw["published"], column="published"),
                publicly_listed=_optional_bool(raw["publicly_listed"], column="publicly_listed"),
                list_price_current_net=list_price,
                member_price_current_net=member_price,
                members_only=_optional_bool(raw["members_only"], column="members_only"),
                connected_event_node_id=_optional_int(
                    raw["connected_event_node_id"], column="connected_event_node_id"
                ),
                observed_on=observed_on,
            )
        )
    if not rows:
        raise PackageContractError("Pakett ei sisalda ühtegi toodet.")
    return tuple(rows)


def _parse_daily_facts(payload: bytes, *, manifest: PackageManifest) -> tuple[DailyFactRow, ...]:
    rows: list[DailyFactRow] = []
    seen: set[tuple[date, int, str, str, str]] = set()
    for raw in _rows(payload, name=DAILY_FACTS_NAME):
        state = _required_text(raw["commerce_state"], column="commerce_state")
        if state != REQUIRED_ORDER_STATE:
            raise PackageContractError(
                f"Ainus lubatud commerce_state on {REQUIRED_ORDER_STATE}, mitte {state}. "
                "field_order_completed ei ole müügiolek."
            )

        report_date = _required_date(raw["report_date"], column="report_date")
        if report_date > manifest.coverage_end:
            raise PackageContractError(
                f"Päevafakti kuupäev on hilisem kui coverage_end: {report_date}."
            )
        if report_date < manifest.coverage_start:
            raise PackageContractError(
                f"Päevafakti kuupäev on varasem kui coverage_start: {report_date}."
            )

        product_id = _positive_id(raw["source_product_id"], column="source_product_id")
        member_status = _required_text(raw["member_status"], column="member_status")
        if member_status not in ALLOWED_MEMBER_STATUSES:
            raise PackageContractError(f"Tundmatu liikmestaatus: {member_status}.")
        payment_class = _required_text(raw["payment_class"], column="payment_class")
        if payment_class not in ALLOWED_PAYMENT_CLASSES:
            raise PackageContractError(f"Tundmatu makseviisi klass: {payment_class}.")
        currency = _required_text(raw["currency"], column="currency").upper()
        if currency not in ALLOWED_CURRENCIES:
            raise PackageContractError(f"Tundmatu valuuta: {currency}.")

        key = (report_date, product_id, member_status, payment_class, currency)
        if key in seen:
            raise PackageContractError(
                f"Päevafakt kordub: {report_date} toode {product_id} "
                f"{member_status}/{payment_class}."
            )
        seen.add(key)

        order_count = _required_int(raw["order_count"], column="order_count")
        if order_count < 0:
            raise PackageContractError("Veerg order_count ei tohi olla negatiivne.")

        rows.append(
            DailyFactRow(
                report_date=report_date,
                source_product_id=product_id,
                member_status=member_status,
                payment_class=payment_class,
                order_count=order_count,
                units=_not_negative(
                    _required_decimal(raw["units"], column="units"), column="units"
                ),
                ordered_value_net=_not_negative(
                    _required_decimal(raw["ordered_value_net"], column="ordered_value_net"),
                    column="ordered_value_net",
                ),
                currency=currency,
            )
        )
    return tuple(rows)


def _parse_product_paths(
    payload: bytes, *, manifest: PackageManifest
) -> tuple[ProductPathRow, ...]:
    rows: list[ProductPathRow] = []
    seen: set[tuple[int, str, str]] = set()
    for raw in _rows(payload, name=PRODUCT_PATHS_NAME):
        product_id = _positive_id(raw["source_product_id"], column="source_product_id")
        page_role = _required_text(raw["page_role"], column="page_role")
        if page_role not in ALLOWED_PAGE_ROLES:
            raise PackageContractError(f"Tundmatu lehe roll: {page_role}.")

        supplied = _required_text(raw["canonical_path"], column="canonical_path")
        # The one canonicaliser, applied here so nothing downstream re-derives
        # it. A source that supplies a full URL, a trailing slash or a query
        # string lands on the same stored key GA4 is matched by.
        path = canonical_path(supplied)
        if not path or not path.startswith("/"):
            raise PackageContractError(f"Vigane tee: {supplied[:60]!r}.")

        key = (product_id, page_role, path)
        if key in seen:
            raise PackageContractError(f"Toote leht kordub: {product_id} {page_role} {path}.")
        seen.add(key)

        observed_on = _required_date(raw["observed_on"], column="observed_on")
        if observed_on > manifest.source_as_of:
            raise PackageContractError(
                f"Lehe vaatluse kuupäev on hilisem kui source_as_of: {observed_on}."
            )

        rows.append(
            ProductPathRow(
                source_product_id=product_id,
                page_role=page_role,
                path=path,
                observed_on=observed_on,
            )
        )
    return tuple(rows)


def _check_references(parsed: ParsedPackage) -> None:
    """Every fact and every path must name a product the package declares."""
    known = {product.source_product_id for product in parsed.products}
    for fact in parsed.daily_facts:
        if fact.source_product_id not in known:
            raise PackageContractError(
                f"Päevafakt viitab tundmatule tootele: {fact.source_product_id}."
            )
    for page in parsed.product_paths:
        if page.source_product_id not in known:
            raise PackageContractError(
                f"Toote leht viitab tundmatule tootele: {page.source_product_id}."
            )

    # One current page per (product, role) is a database constraint; catching it
    # here turns an IntegrityError into a sentence naming the product.
    roles: set[tuple[int, str]] = set()
    for page in parsed.product_paths:
        key = (page.source_product_id, page.page_role)
        if key in roles:
            raise PackageContractError(
                f"Tootel {page.source_product_id} on rollile {page.page_role} mitu teed."
            )
        roles.add(key)


def content_checksum(parsed: ParsedPackage) -> str:
    """A digest of the **normalised facts**, not of the archive's bytes.

    The repository rule: markup and packaging churn must not republish identical
    data. Two exports produced a week apart with the same figures compress
    differently and carry different `exported_at` values, so hashing the file
    would create a new revision of every row for no change at all.

    Everything that decides what the dashboard shows is inside; `exported_at`
    and the archive digest are deliberately outside.
    """
    payload = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "manifest": {
            "source_as_of": parsed.manifest.source_as_of.isoformat(),
            "coverage_start": parsed.manifest.coverage_start.isoformat(),
            "coverage_end": parsed.manifest.coverage_end.isoformat(),
            "money_basis": parsed.manifest.money_basis,
            "order_state_filter": parsed.manifest.order_state_filter,
            "timezone": parsed.manifest.timezone,
            "member_semantics_verified": parsed.manifest.member_semantics_verified,
            "public_listing_semantics_verified": (
                parsed.manifest.public_listing_semantics_verified
            ),
        },
        "products": sorted(
            [
                [
                    row.source_product_id,
                    row.product_type,
                    row.title,
                    row.category_term_id,
                    row.category_name,
                    row.published,
                    row.publicly_listed,
                    None if row.list_price_current_net is None else str(row.list_price_current_net),
                    None
                    if row.member_price_current_net is None
                    else str(row.member_price_current_net),
                    row.members_only,
                    row.connected_event_node_id,
                    row.observed_on.isoformat(),
                ]
                for row in parsed.products
            ],
            key=lambda item: item[0],
        ),
        "daily_facts": sorted(
            [
                [
                    row.report_date.isoformat(),
                    row.source_product_id,
                    row.member_status,
                    row.payment_class,
                    row.order_count,
                    str(row.units),
                    str(row.ordered_value_net),
                    row.currency,
                ]
                for row in parsed.daily_facts
            ],
            key=lambda item: (item[0], item[1], item[2], item[3], item[7]),
        ),
        "product_paths": sorted(
            [
                [row.source_product_id, row.page_role, row.path, row.observed_on.isoformat()]
                for row in parsed.product_paths
            ],
            key=lambda item: (item[0], item[1], item[2]),
        ),
    }
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def read_package(path: Path | str, *, limits: PackageLimits | None = None) -> ParsedPackage:
    """Validate a package and return its typed contents.

    Raises :class:`PackageContractError` and writes nothing on any failure. The
    caller treats that as "the previous dataset is still correct".
    """
    limits = limits or PackageLimits()
    package_path = Path(path).expanduser()
    if not package_path.is_file():
        raise PackageContractError("Paketifaili ei leitud.")

    package_sha256, package_size = file_digest(package_path)
    if package_size == 0:
        raise PackageContractError("Paketifail on tühi.")
    if package_size > limits.max_package_bytes:
        raise PackageContractError("Paketifail on liiga suur.")
    if not zipfile.is_zipfile(package_path):
        raise PackageContractError("Pakett ei ole ZIP-fail.")

    try:
        with zipfile.ZipFile(package_path) as archive:
            payloads = _read_members(archive, limits)
    except zipfile.BadZipFile as error:
        raise PackageContractError("Pakett on vigane ZIP-fail.") from error

    manifest, listed = _load_manifest(payloads)
    _verify_manifest(payloads, listed)

    parsed = ParsedPackage(
        package_sha256=package_sha256,
        package_size_bytes=package_size,
        manifest=manifest,
        products=_parse_products(payloads[PRODUCTS_NAME], manifest=manifest),
        daily_facts=_parse_daily_facts(payloads[DAILY_FACTS_NAME], manifest=manifest),
        product_paths=_parse_product_paths(payloads[PRODUCT_PATHS_NAME], manifest=manifest),
    )
    _check_references(parsed)
    return parsed


__all__ = [
    "ALLOWED_MEMBER_STATUSES",
    "ALLOWED_PAGE_ROLES",
    "ALLOWED_PAYMENT_CLASSES",
    "ALLOWED_PRODUCT_TYPES",
    "DAILY_FACTS_NAME",
    "MANIFEST_NAME",
    "PACKAGE_SCHEMA_VERSION",
    "PRODUCTS_NAME",
    "PRODUCT_PATHS_NAME",
    "REQUIRED_HEADERS",
    "DailyFactRow",
    "PackageContractError",
    "PackageLimits",
    "PackageManifest",
    "ParsedPackage",
    "ProductPathRow",
    "ProductRow",
    "content_checksum",
    "file_digest",
    "read_package",
]
