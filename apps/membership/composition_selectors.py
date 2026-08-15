"""Read paths for aggregate membership composition.

Separate from `internal_selectors.py` and `selectors.py`, and for the same
reason those two are separate from each other: these describe *what kinds of
organisations* the membership is made of, and neither of the others does. A
composition total is not a third membership count and is never charted beside
one — it is the size of one dated roster export, which the board-report total
and the public directory count both measure differently.

Every selector here reads PostgreSQL only, reads exactly one snapshot, and
returns `None` rather than a zero when there is nothing to read.

## One query, not one per category

The whole snapshot is fetched in a single query and grouped in memory. Seven
dimensions across two populations is a couple of hundred rows; asking for them
separately would be fourteen round trips to draw one page, and the count of
queries would then grow every time a dimension was added.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings

from .composition import (
    DIMENSION_LABELS,
    LONG_TENURE_YEARS,
    TENURE_11_20,
    TENURE_20_PLUS,
    UNKNOWN,
    Dimension,
    GrowthIndexRow,
    Population,
    growth_index,
    ordered_keys,
)
from .models import MembershipCompositionSnapshot, MembershipCompositionValue

#: How many categories a ranked chart draws before the rest become `Muu`.
#:
#: Ten bars is about as many as stay individually readable at page width with
#: Estonian sector names beside them. The remainder is a real aggregate of real
#: categories, and the exact figures stay available in the chart's data table.
TOP_CATEGORY_LIMIT = 10

DAYS_PER_YEAR = Decimal("365.25")


@dataclass(frozen=True)
class CompositionCategory:
    key: str
    label: str
    count: int
    share_pct: Decimal


@dataclass(frozen=True)
class CompositionDimensionResult:
    """One dimension's categories for one population, with its denominator.

    `total` is the population, not the sum of the drawn categories: a ranked
    chart that has folded a tail into `Muu` still divides by everyone, and a
    reader checking a share against the table gets the same number.
    """

    dimension: str
    label: str
    population: str
    total: int
    categories: tuple[CompositionCategory, ...]

    @property
    def has_data(self) -> bool:
        return bool(self.categories)

    @property
    def unknown_count(self) -> int:
        return next((c.count for c in self.categories if c.key == UNKNOWN), 0)

    @property
    def largest(self) -> CompositionCategory | None:
        """The biggest category, ignoring `unknown`.

        "Most members are unclassified" is a fact about the import, not about
        the membership, and it does not belong in a headline that reads as a
        statement about the Chamber.
        """
        known = [c for c in self.categories if c.key != UNKNOWN]
        return max(known, key=lambda c: c.count) if known else None

    def ranked(self, limit: int = TOP_CATEGORY_LIMIT) -> tuple[CompositionCategory, ...]:
        """Largest first, with everything past `limit` folded into one `Muu`.

        `unknown` is never folded into `Muu`. They mean different things — one
        is "several small categories" and the other is "the source did not say"
        — and a reader cannot tell them apart once they are added together.
        """
        known = sorted(
            (c for c in self.categories if c.key != UNKNOWN),
            key=lambda c: (-c.count, c.label),
        )
        unknown = [c for c in self.categories if c.key == UNKNOWN]
        if len(known) <= limit:
            return tuple(known + unknown)

        head, tail = known[:limit], known[limit:]
        folded = CompositionCategory(
            key="other",
            label=f"Muu ({len(tail)} tegevusala)" if self.dimension == Dimension.SECTOR else "Muu",
            count=sum(c.count for c in tail),
            share_pct=_share(sum(c.count for c in tail), self.total),
        )
        return tuple(head + [folded] + unknown)


@dataclass(frozen=True)
class CompositionSnapshot:
    """The dated roster reading, and everything derived from it."""

    id: int
    snapshot_date: date
    row_count: int
    median_tenure_days: int | None
    coverage_pct: dict
    mapping_version: str
    sector_mapping_version: str
    dimensions: dict[tuple[str, str], CompositionDimensionResult]

    @property
    def median_tenure_years(self) -> Decimal | None:
        if self.median_tenure_days is None:
            return None
        return (Decimal(self.median_tenure_days) / DAYS_PER_YEAR).quantize(Decimal("0.1"))

    @property
    def recent_joiner_count(self) -> int:
        result = self.dimension(Dimension.STATUS, Population.RECENT_JOINERS)
        return result.total if result else 0

    def dimension(
        self, dimension: str, population: str = Population.ALL_CURRENT
    ) -> CompositionDimensionResult | None:
        return self.dimensions.get((population, dimension))

    @property
    def long_tenure_share_pct(self) -> Decimal | None:
        """The share of members who have been in the Chamber 11 years or more.

        Read off the two top bands rather than recomputed from tenures, which
        are deliberately not stored. `None` when the tenure dimension is absent
        — never a zero, which would claim the Chamber has no long-standing
        members.
        """
        result = self.dimension(Dimension.TENURE_BAND)
        if result is None or not result.total:
            return None
        counts = {c.key: c.count for c in result.categories}
        long_standing = counts.get(TENURE_11_20, 0) + counts.get(TENURE_20_PLUS, 0)
        return _share(long_standing, result.total)


def _share(count: int, total: int) -> Decimal:
    if not total:
        return Decimal("0.0")
    return (Decimal(count) / Decimal(total) * 100).quantize(Decimal("0.1"))


def _source_slug() -> str:
    return settings.MEMBERSHIP_COMPOSITION_SOURCE_SLUG


def get_current_composition_snapshot() -> CompositionSnapshot | None:
    """The composition in force, or nothing when no roster has been imported.

    Two queries: one for the snapshot, one for all of its values. Returns `None`
    rather than an empty snapshot, so a caller cannot accidentally draw a page
    of zeroes for a roster that was never loaded.
    """
    snapshot = (
        MembershipCompositionSnapshot.objects.filter(source__slug=_source_slug(), is_current=True)
        .order_by("-snapshot_date", "-id")
        .first()
    )
    if snapshot is None:
        return None

    rows = MembershipCompositionValue.objects.filter(snapshot=snapshot).only(
        "population", "dimension", "category_key", "category_label", "member_count"
    )

    grouped: dict[tuple[str, str], list[MembershipCompositionValue]] = {}
    for row in rows:
        grouped.setdefault((row.population, row.dimension), []).append(row)

    dimensions: dict[tuple[str, str], CompositionDimensionResult] = {}
    for (population, dimension), values in grouped.items():
        total = sum(value.member_count for value in values)
        counts = {value.category_key: value for value in values}
        dimensions[(population, dimension)] = CompositionDimensionResult(
            dimension=dimension,
            label=DIMENSION_LABELS.get(dimension, dimension),
            population=population,
            total=total,
            categories=tuple(
                CompositionCategory(
                    key=key,
                    label=counts[key].category_label,
                    count=counts[key].member_count,
                    share_pct=_share(counts[key].member_count, total),
                )
                for key in ordered_keys(dimension, counts)
            ),
        )

    return CompositionSnapshot(
        id=snapshot.pk,
        snapshot_date=snapshot.snapshot_date,
        row_count=snapshot.source_row_count,
        median_tenure_days=snapshot.median_tenure_days,
        coverage_pct=snapshot.coverage_pct or {},
        mapping_version=snapshot.mapping_version,
        sector_mapping_version=snapshot.sector_mapping_version,
        dimensions=dimensions,
    )


def get_composition_growth(
    snapshot: CompositionSnapshot, dimension: str
) -> tuple[tuple[GrowthIndexRow, ...], tuple[str, ...]]:
    """Which categories are over-represented among recent joiners.

    Both populations come off the snapshot already in memory, so this adds no
    query. Categories below the sample floors are returned as suppressed rather
    than as an index of 100 — "not measured reliably" and "exactly average" are
    different statements and the page says which one it means.
    """
    overall = snapshot.dimension(dimension, Population.ALL_CURRENT)
    recent = snapshot.dimension(dimension, Population.RECENT_JOINERS)
    if overall is None or recent is None:
        return (), ()

    return growth_index(
        overall={c.key: c.count for c in overall.categories if c.key != UNKNOWN},
        recent={c.key: c.count for c in recent.categories if c.key != UNKNOWN},
        dimension=dimension,
    )


LONG_TENURE_LABEL = f"{LONG_TENURE_YEARS}+ aastat liikmed"
