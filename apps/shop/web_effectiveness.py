"""Which public page a product's acquisition rate may be divided by.

**One module owns this decision.** Before it existed, every surface that wanted
a rate reached for `PageRole.PRODUCT` because contract templates are the bulk of
the catalogue — which silently gave event registrations no rate at all, since an
event product's only public address is its event page. A policy duplicated
across selectors and templates is a policy that drifts, so role selection
happens here and nowhere else.

## The policy

| Product family | Acquisition page | Why |
| --- | --- | --- |
| `document` | `PageRole.PRODUCT` | `/et/pood/` carries the buy action, `/et/tooriistad/` only explains |
| `event_registration` | `PageRole.EVENT` | the public event page *is* the registration page |
| `physical_product` | `PageRole.PRODUCT` | the shop product page carries the buy action |

## No substitution

A product whose denominator role is absent has **no rate**, and this module
returns an empty path rather than falling back to another role. Dividing
acquisitions by an information page would answer a different question and read
as a much worse rate than the truth — the information page for a template
carries roughly a hundred times less traffic than its product page on the real
dataset, which would invert the ranking rather than merely blur it.

## Counting a shared page once

`ShopProductPage` does not guarantee that a canonical path belongs to exactly
one product. Two event registrations — an early-bird and a full-price row for
the same seminar — legitimately map to one event page. Summing each product's
own view figure would then count that page's traffic twice in any aggregate, and
the error grows with exactly the products most likely to share.

So aggregates here resolve **unique denominator paths first** and add each
path's views once. The numerator is still every product's acquisitions, which is
the intended reading: the page sells both products, so both products' units
belong over its views.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from .models import PageRole, ProductType

#: Which page role carries the acquisition action, per product family. Read
#: through :func:`denominator_role` rather than directly, so an unknown type
#: resolves rather than raising on a page render.
DENOMINATOR_ROLES: dict[str, str] = {
    ProductType.DOCUMENT: PageRole.PRODUCT,
    ProductType.EVENT_REGISTRATION: PageRole.EVENT,
    ProductType.PHYSICAL_PRODUCT: PageRole.PRODUCT,
}

#: What an unrecognised product type divides by. A type this module has not been
#: taught about is far more likely to be sold from a shop product page than from
#: anything else, and the alternative — no rate at all for a whole family — is a
#: worse failure than a defensible default.
DEFAULT_DENOMINATOR_ROLE = PageRole.PRODUCT


def denominator_role(product_type: str) -> str:
    """The page role whose views may divide this family's acquisitions."""
    return DENOMINATOR_ROLES.get(product_type, DEFAULT_DENOMINATOR_ROLE)


def denominator_path(product_type: str, roles: Mapping[str, str]) -> str:
    """This product's acquisition page, or `""` — **never** a substitute.

    `roles` is the mapping `selectors.paths_by_product` returns for one product.
    An absent role yields an empty path, which every caller must read as "no
    rate available" rather than as zero views.
    """
    return roles.get(denominator_role(product_type), "")


@dataclass(frozen=True)
class WebCoverage:
    """How much of a selected population the web figures actually cover.

    Three states, kept apart because they call for different answers. A product
    with no acquisition page cannot ever have a rate; a product whose page
    exists but was never measured might have one next month; a measured product
    has one now. Collapsing the first two into "no data" would hide which of
    them a reader could do something about.
    """

    measured: int = 0
    without_path: int = 0
    without_measurement: int = 0

    @property
    def total(self) -> int:
        return self.measured + self.without_path + self.without_measurement

    @property
    def has_population(self) -> bool:
        return self.total > 0

    @property
    def is_complete(self) -> bool:
        return self.has_population and self.measured == self.total


@dataclass(frozen=True)
class WebAggregate:
    """Views and acquisitions for a group of products, counted honestly.

    `views` is `None` rather than `0` when nothing in the group was measured:
    an unmeasured population has no traffic figure, and a zero would read as a
    group nobody visited.
    """

    views: int | None = None
    units: Decimal = Decimal("0")
    coverage: WebCoverage = WebCoverage()

    @property
    def has_views(self) -> bool:
        return self.views is not None

    @property
    def acquisitions_per_hundred(self) -> Decimal | None:
        if self.views is None or self.views <= 0:
            return None
        return (self.units * 100 / Decimal(self.views)).quantize(Decimal("0.1"))


def aggregate_web(rows: Sequence) -> WebAggregate:
    """Views over **unique** acquisition paths, with acquisitions over all rows.

    Each row must expose `denominator_path`, `denominator_page_views` and
    `conversion_units` — that is `selectors.ProductRow`, but the signature is
    kept structural so a test can pass a stub without a database.

    The deduplication is the point. Two products sharing one event page
    contribute their views once and their acquisitions twice, which is what the
    page actually did.
    """
    seen_paths: dict[str, int] = {}
    units = Decimal("0")
    measured = 0
    without_path = 0
    without_measurement = 0

    for row in rows:
        units += Decimal(row.conversion_units or 0)
        path = row.denominator_path
        if not path:
            without_path += 1
            continue
        figure = row.denominator_page_views
        if figure is None or figure.views is None:
            without_measurement += 1
            continue
        measured += 1
        # One entry per path, whatever how many products point at it.
        seen_paths.setdefault(path, figure.views)

    views = sum(seen_paths.values()) if seen_paths else None
    return WebAggregate(
        views=views,
        units=units,
        coverage=WebCoverage(
            measured=measured,
            without_path=without_path,
            without_measurement=without_measurement,
        ),
    )


__all__ = [
    "DEFAULT_DENOMINATOR_ROLE",
    "DENOMINATOR_ROLES",
    "WebAggregate",
    "WebCoverage",
    "aggregate_web",
    "denominator_path",
    "denominator_role",
]
