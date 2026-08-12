"""Publishing the manual E-pood package into immutable domain rows.

The whole import is one transaction and one decision: either every product,
path, daily fact and the source state land together, or nothing does and the
previous dataset stays exactly as it was. A half-imported catalogue — new
products against last month's facts — would be worse than a stale one, because
nothing on screen would say it had happened.

## Three ways an import can end

- **unchanged** — the normalised content digest matches what is already
  published. Nothing is written and no revision is created, because a
  re-packaged identical export is not new information. This is checked against
  the *facts*, not the archive bytes; see `package.content_checksum`.
- **imported** — something differs. Every changed natural key gets a **new
  current row** naming the one it supersedes, and the replaced row keeps its
  figures.
- **failed** — validation refused the package. The previous dataset is
  untouched and the failure is recorded with a sanitized reason.

## What a "correction" looks like here

Nothing is updated in place. A product observed again on the same day with a
different price is a new `ShopProductSnapshot` superseding the old one; a
corrected day of sales is a new `ShopDailyFact`. That is the repository rule and
it is also the only way a chart can be re-drawn later showing what the board was
actually shown last month.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.sources.models import ImportRun, ImportStatus, SourceArtifact
from apps.sources.services import (
    build_import_run,
    calculate_import_key,
    complete_import_run,
    fail_import_run,
    register_external_reference,
    start_import_run,
)

from .bootstrap import ensure_shop_source
from .models import (
    MemberStatus,
    PageRole,
    PaymentClass,
    ProductType,
    ShopDailyFact,
    ShopDailySummary,
    ShopProduct,
    ShopProductPage,
    ShopProductSnapshot,
    ShopSourceState,
)
from .package import (
    PACKAGE_SCHEMA_VERSION,
    PackageContractError,
    PackageLimits,
    ParsedPackage,
    content_checksum,
    read_package,
)

IMPORTER_NAME = "shop_commerce_snapshot"

#: A fixed, non-secret provenance label. The package itself is never stored: the
#: artifact carries only the server-computed digest and size, exactly like the
#: public-feed collectors.
ARTIFACT_REFERENCE_PREFIX = "manual:shop-commerce-snapshot"
ARTIFACT_NAME = "shop_commerce_snapshot.zip"
ARTIFACT_MIME_TYPE = "application/zip"


class ShopImportError(RuntimeError):
    """The package was refused. Nothing was written."""


@dataclass(frozen=True)
class ShopImportResult:
    import_run: ImportRun
    dry_run: bool
    unchanged: bool
    package_sha256: str
    content_checksum: str
    counts: dict[str, int]
    source_as_of: date | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None

    def as_json(self) -> dict:
        """Aggregate counts and dates only — never a title, price or path."""
        return {
            "status": "unchanged"
            if self.unchanged
            else ("dry_run" if self.dry_run else "imported"),
            "import_run": self.import_run.pk,
            "package_sha256": self.package_sha256,
            "content_checksum": self.content_checksum,
            "counts": self.counts,
            "source_as_of": self.source_as_of.isoformat() if self.source_as_of else None,
            "coverage_start": self.coverage_start.isoformat() if self.coverage_start else None,
            "coverage_end": self.coverage_end.isoformat() if self.coverage_end else None,
        }


def _sanitized(error: Exception) -> dict:
    message = str(error).strip().replace("\n", " ")
    return {"type": type(error).__name__, "message": message[:300]}


def _existing_successful_run(import_key: str) -> ImportRun | None:
    return ImportRun.objects.filter(
        import_key=import_key, dry_run=False, status=ImportStatus.SUCCEEDED
    ).first()


def _ensure_artifact(source, parsed: ParsedPackage, *, actor, correlation_id) -> SourceArtifact:
    """Register the package's content identity once, and reuse it thereafter."""
    existing = SourceArtifact.objects.filter(source=source, sha256=parsed.package_sha256).first()
    if existing is not None:
        return existing
    return register_external_reference(
        source=source,
        external_reference=f"{ARTIFACT_REFERENCE_PREFIX}:{parsed.package_sha256}",
        original_name=ARTIFACT_NAME,
        mime_type=ARTIFACT_MIME_TYPE,
        sha256=parsed.package_sha256,
        size_bytes=parsed.package_size_bytes,
        uploaded_by=actor,
        actor=actor,
        correlation_id=correlation_id,
    )


def _current_state() -> ShopSourceState | None:
    return ShopSourceState.objects.filter(is_current=True).first()


# --------------------------------------------------------------------------
# Writers. Each returns how many current rows it wrote.
# --------------------------------------------------------------------------


def _write_products(parsed: ParsedPackage, *, run: ImportRun) -> dict[int, ShopProduct]:
    """Identities, created once and thereafter only re-observed.

    A product's type is part of its identity here. A source that changes it is
    describing a different product under a reused ID, and that is refused rather
    than silently reclassifying six years of history.
    """
    existing = {
        product.source_product_id: product
        for product in ShopProduct.objects.filter(
            source_product_id__in=[row.source_product_id for row in parsed.products]
        )
    }
    products: dict[int, ShopProduct] = {}
    to_create: list[ShopProduct] = []

    for row in parsed.products:
        found = existing.get(row.source_product_id)
        if found is None:
            product = ShopProduct(
                source_product_id=row.source_product_id,
                product_type=row.product_type,
                first_seen_on=row.observed_on,
                last_seen_on=row.observed_on,
            )
            to_create.append(product)
            products[row.source_product_id] = product
            continue
        if found.product_type != row.product_type:
            raise ShopImportError(
                f"Toote {row.source_product_id} liik muutus "
                f"{found.product_type} → {row.product_type}. Sama ID, teine toode."
            )
        if row.observed_on > found.last_seen_on:
            found.last_seen_on = row.observed_on
            found.save(update_fields=["last_seen_on"])
        products[row.source_product_id] = found

    if to_create:
        ShopProduct.objects.bulk_create(to_create)
    return products


#: The snapshot fields whose equality decides "nothing changed on this day".
_SNAPSHOT_FIELDS = (
    "title",
    "category_term_id",
    "category_name",
    "published",
    "publicly_listed",
    "members_only",
    "list_price_net",
    "member_price_net",
    "currency",
    "connected_event_node_id",
)


def _write_snapshots(parsed: ParsedPackage, *, products, run: ImportRun) -> int:
    current = {
        (snapshot.product_id, snapshot.observed_on): snapshot
        for snapshot in ShopProductSnapshot.objects.filter(is_current=True)
    }
    superseded: list[int] = []
    to_create: list[ShopProductSnapshot] = []

    for row in parsed.products:
        product = products[row.source_product_id]
        candidate = ShopProductSnapshot(
            product=product,
            observed_on=row.observed_on,
            title=row.title,
            category_term_id=row.category_term_id,
            category_name=row.category_name,
            published=row.published,
            publicly_listed=row.publicly_listed,
            members_only=row.members_only,
            list_price_net=row.list_price_current_net,
            member_price_net=row.member_price_current_net,
            connected_event_node_id=row.connected_event_node_id,
            import_run=run,
        )
        found = current.get((product.pk, row.observed_on))
        if found is not None:
            if all(getattr(found, name) == getattr(candidate, name) for name in _SNAPSHOT_FIELDS):
                continue
            superseded.append(found.pk)
            candidate.supersedes = found
        to_create.append(candidate)

    if superseded:
        ShopProductSnapshot.objects.filter(pk__in=superseded).update(is_current=False)
    if to_create:
        ShopProductSnapshot.objects.bulk_create(to_create)
    return len(to_create)


def _write_paths(parsed: ParsedPackage, *, products, run: ImportRun) -> int:
    """Path mappings, keeping every address a product has ever had.

    A changed path retires the old row rather than deleting it: GA4 still holds
    traffic filed under the old address, and a page that existed is provenance.
    """
    wanted = {
        (products[row.source_product_id].pk, row.page_role): row for row in parsed.product_paths
    }
    existing = {
        (page.product_id, page.page_role, page.path): page
        for page in ShopProductPage.objects.filter(
            product_id__in={product.pk for product in products.values()}
        )
    }
    written = 0
    to_create: list[ShopProductPage] = []
    retire: list[int] = []

    for (product_pk, role), row in wanted.items():
        found = existing.get((product_pk, role, row.path))
        if found is not None:
            if not found.is_current or found.last_seen_on < row.observed_on:
                found.is_current = True
                found.last_seen_on = max(found.last_seen_on, row.observed_on)
                found.save(update_fields=["is_current", "last_seen_on"])
            written += 1
            continue
        # A different path for this role: retire whatever currently holds it.
        retire.extend(
            page.pk
            for (other_pk, other_role, _), page in existing.items()
            if other_pk == product_pk and other_role == role and page.is_current
        )
        to_create.append(
            ShopProductPage(
                product_id=product_pk,
                page_role=role,
                path=row.path,
                first_seen_on=row.observed_on,
                last_seen_on=row.observed_on,
                import_run=run,
            )
        )

    if retire:
        ShopProductPage.objects.filter(pk__in=retire).update(is_current=False)
    if to_create:
        ShopProductPage.objects.bulk_create(to_create)
    return written + len(to_create)


#: The measures whose equality decides "this day is unchanged".
_FACT_FIELDS = (
    "order_count",
    "units",
    "ordered_value_net",
    "free_units",
    "paid_units",
    "unknown_units",
)


def _write_facts(parsed: ParsedPackage, *, products, run: ImportRun) -> int:
    dates = {row.report_date for row in parsed.daily_facts}
    current = {
        (
            fact.product_id,
            fact.report_date,
            fact.member_status,
            fact.payment_class,
            fact.currency,
        ): fact
        for fact in ShopDailyFact.objects.filter(is_current=True, report_date__in=dates)
    }
    superseded: list[int] = []
    to_create: list[ShopDailyFact] = []

    for row in parsed.daily_facts:
        product = products[row.source_product_id]
        candidate = ShopDailyFact(
            report_date=row.report_date,
            product=product,
            member_status=row.member_status,
            payment_class=row.payment_class,
            currency=row.currency,
            order_count=row.order_count,
            units=row.units,
            ordered_value_net=row.ordered_value_net,
            free_units=row.free_units,
            paid_units=row.paid_units,
            unknown_units=row.unknown_units,
            import_run=run,
        )
        key = (
            product.pk,
            row.report_date,
            row.member_status,
            row.payment_class,
            row.currency,
        )
        found = current.get(key)
        if found is not None:
            if all(getattr(found, name) == getattr(candidate, name) for name in _FACT_FIELDS):
                continue
            superseded.append(found.pk)
            candidate.supersedes = found
        to_create.append(candidate)

    if superseded:
        ShopDailyFact.objects.filter(pk__in=superseded).update(is_current=False)
    if to_create:
        ShopDailyFact.objects.bulk_create(to_create)
    return len(to_create)


def _write_summaries(parsed: ParsedPackage, *, run: ImportRun) -> int:
    """Distinct order counts per day. Nothing identifying reaches this table.

    The counts arrive already aggregated: the package producer read the order
    identifiers, counted the distinct ones and wrote only the total. No order
    number ever enters this application, and no field here could hold one.
    """
    if not parsed.daily_orders:
        return 0

    dates = {row.report_date for row in parsed.daily_orders}
    current = {
        (summary.report_date, summary.product_type): summary
        for summary in ShopDailySummary.objects.filter(is_current=True, report_date__in=dates)
    }
    superseded: list[int] = []
    to_create: list[ShopDailySummary] = []

    for row in parsed.daily_orders:
        candidate = ShopDailySummary(
            report_date=row.report_date,
            product_type=row.product_type,
            distinct_order_count=row.distinct_order_count,
            import_run=run,
        )
        found = current.get((row.report_date, row.product_type))
        if found is not None:
            if found.distinct_order_count == candidate.distinct_order_count:
                continue
            superseded.append(found.pk)
            candidate.supersedes = found
        to_create.append(candidate)

    if superseded:
        ShopDailySummary.objects.filter(pk__in=superseded).update(is_current=False)
    if to_create:
        ShopDailySummary.objects.bulk_create(to_create)
    return len(to_create)


def _write_state(
    parsed: ParsedPackage, *, source, run: ImportRun, digest: str, counts: dict[str, int]
) -> ShopSourceState:
    ShopSourceState.objects.filter(source=source, is_current=True).update(is_current=False)
    return ShopSourceState.objects.create(
        source=source,
        import_run=run,
        schema_version=PACKAGE_SCHEMA_VERSION,
        source_as_of=parsed.manifest.source_as_of,
        coverage_start=parsed.manifest.coverage_start,
        coverage_end=parsed.manifest.coverage_end,
        member_semantics_verified=parsed.manifest.member_semantics_verified,
        public_listing_semantics_verified=parsed.manifest.public_listing_semantics_verified,
        content_checksum=digest,
        product_count=counts.get("products", 0),
        fact_count=counts.get("daily_facts", 0),
        page_count=counts.get("product_paths", 0),
        observed_at=timezone.now(),
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def import_shop_package(
    path: Path | str,
    *,
    dry_run: bool = True,
    actor=None,
    correlation_id: uuid.UUID | None = None,
    limits: PackageLimits | None = None,
) -> ShopImportResult:
    """Validate and, unless this is a dry run, publish the package.

    A dry run validates the whole contract, records the attempt and writes no
    domain row — and never blocks a later live import of the same package.
    """
    try:
        parsed = read_package(path, limits=limits)
    except PackageContractError as error:
        raise ShopImportError(str(error)) from error

    digest = content_checksum(parsed)
    source = ensure_shop_source(actor=actor, correlation_id=correlation_id)
    import_key = calculate_import_key(IMPORTER_NAME, PACKAGE_SCHEMA_VERSION, parsed.package_sha256)

    if not dry_run:
        state = _current_state()
        already = _existing_successful_run(import_key)
        # Two ways to be unchanged: the same archive, or a different archive
        # carrying identical facts. Both must publish nothing, because a
        # re-exported file is not new information about the shop.
        if already is not None or (state is not None and state.content_checksum == digest):
            run = already or (state.import_run if state else None)
            if run is not None:
                record_event(
                    action=AuditAction.SHOP_SNAPSHOT_UNCHANGED,
                    obj=run,
                    actor=actor,
                    correlation_id=run.correlation_id,
                    change_summary={
                        "source": source.slug,
                        "content_checksum": digest,
                        "import_key": import_key,
                    },
                )
                return ShopImportResult(
                    import_run=run,
                    dry_run=False,
                    unchanged=True,
                    package_sha256=parsed.package_sha256,
                    content_checksum=digest,
                    counts=parsed.row_counts,
                    source_as_of=parsed.manifest.source_as_of,
                    coverage_start=parsed.manifest.coverage_start,
                    coverage_end=parsed.manifest.coverage_end,
                )

    artifact = _ensure_artifact(source, parsed, actor=actor, correlation_id=correlation_id)
    run = build_import_run(
        artifact=artifact,
        importer_name=IMPORTER_NAME,
        schema_version=PACKAGE_SCHEMA_VERSION,
        dry_run=dry_run,
        initiated_by=actor,
        actor=actor,
        correlation_id=correlation_id,
    )
    start_import_run(run)

    try:
        if dry_run:
            complete_import_run(
                run,
                rows_added=0,
                rows_skipped=sum(parsed.row_counts.values()),
                actor=actor,
            )
            return ShopImportResult(
                import_run=run,
                dry_run=True,
                unchanged=False,
                package_sha256=parsed.package_sha256,
                content_checksum=digest,
                counts=parsed.row_counts,
                source_as_of=parsed.manifest.source_as_of,
                coverage_start=parsed.manifest.coverage_start,
                coverage_end=parsed.manifest.coverage_end,
            )

        with transaction.atomic():
            products = _write_products(parsed, run=run)
            counts = {
                "products": _write_snapshots(parsed, products=products, run=run),
                "product_paths": _write_paths(parsed, products=products, run=run),
                "daily_facts": _write_facts(parsed, products=products, run=run),
                "daily_orders": _write_summaries(parsed, run=run),
            }
            state = _write_state(
                parsed, source=source, run=run, digest=digest, counts=parsed.row_counts
            )
            complete_import_run(run, rows_added=sum(counts.values()), actor=actor)
            record_event(
                action=AuditAction.SHOP_SNAPSHOT_IMPORTED,
                obj=state,
                actor=actor,
                correlation_id=run.correlation_id,
                change_summary={
                    "source": source.slug,
                    "content_checksum": digest,
                    "source_as_of": parsed.manifest.source_as_of.isoformat(),
                    "coverage_start": parsed.manifest.coverage_start.isoformat(),
                    "coverage_end": parsed.manifest.coverage_end.isoformat(),
                    "counts": counts,
                },
            )
    except Exception as error:  # noqa: BLE001 - recorded, re-raised below
        fail_import_run(run, errors=[_sanitized(error)], actor=actor)
        record_event(
            action=AuditAction.SHOP_SNAPSHOT_FAILED,
            obj=run,
            actor=actor,
            correlation_id=run.correlation_id,
            change_summary={"source": source.slug, "error": _sanitized(error)},
        )
        raise ShopImportError(str(error)) from error

    return ShopImportResult(
        import_run=run,
        dry_run=False,
        unchanged=False,
        package_sha256=parsed.package_sha256,
        content_checksum=digest,
        counts=counts,
        source_as_of=parsed.manifest.source_as_of,
        coverage_start=parsed.manifest.coverage_start,
        coverage_end=parsed.manifest.coverage_end,
    )


__all__ = [
    "ARTIFACT_REFERENCE_PREFIX",
    "IMPORTER_NAME",
    "MemberStatus",
    "PageRole",
    "PaymentClass",
    "ProductType",
    "ShopImportError",
    "ShopImportResult",
    "import_shop_package",
]
