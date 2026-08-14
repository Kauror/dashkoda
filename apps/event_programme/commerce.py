"""Event-registration Commerce facts, joined to programme events by page path.

A **bounded, read-only window** onto `apps.shop`. It reads that app's models and
nothing else — no shop selector that shapes a shop page, no shop presenter, no
shop template — so the Sündmused dashboard can answer one question about an
event without acquiring an opinion about the store. E-pood remains the
authoritative dashboard for shop analytics, and nothing here recreates it:
there is no payment-method breakdown, no order-line analysis and no catalogue
explorer.

The one question is:

    For this event's public page, how many Commerce registration units were
    acquired, and what ordered value did they represent?

What a registration unit is **not**
===================================

It is not a participant, an attendee or a person. It is a **quantity on a
completed Commerce order line** for a product sold through that event's page.
One order may carry several units; a unit may be cancelled without Commerce
recording it here; somebody may attend without ever appearing in Commerce at
all. DashKoda has no attendance source, so no figure below is ever labelled
`Osalejad`, and no rate is ever computed against attendance.

`ordered_value_net` is **ordered value excluding VAT**, never revenue: Koda.ee
records no payment receipt and no refund. That is E-pood's rule and it is
reused here rather than restated with different words.

The join
========

The key is the **canonical event-page path** — `ShopProductPage` with
`page_role = event`, whose `path` the shop importer has already canonicalised
with the same `apps.visibility.ga4_paths.canonical_path` this app uses for GA4.
One join key across all three domains is what keeps an event's traffic and its
registrations on the same row.

Cardinality is not assumed. `join_report` states it, including the cases that
must never be silently collapsed:

- one page carrying **several** registration products — multiple ticket types
  for one event. All of them are summed, because picking one would drop
  registrations that really happened;
- one page carrying **no** product — the ordinary case for an event that was
  never sold through the shop;
- one page shared by **two programme events** — its Commerce total belongs to
  both as context and to neither as an exclusive fact, so it is disclosed and
  never added into a cohort total twice.

Coverage gate
=============

On the Chamber's current Commerce export there are **zero** `event_registration`
products: the dataset holds contract templates and physical goods only. Every
consumer therefore has to check `has_data` before drawing anything. A registration
block that renders "0" against a programme of 1 190 events would be a claim that
nobody registered for anything, which is not what an unimported product type
means.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Max, Min, Sum

from apps.shop.models import (
    MemberStatus,
    PageRole,
    ProductType,
    ShopDailyFact,
    ShopProductPage,
    ShopSourceState,
)
from apps.visibility.ga4_paths import canonical_path

from .public_links import attach_public_links

#: What one row of `ShopDailyFact.units` is called on screen. Deliberately long:
#: `Registreerimised` invites the reader to hear "people", and until the exact
#: transaction semantics are established the extra word is the honest cost.
REGISTRATION_UNIT_LABEL = "E-poe registreerimisühikud"

#: Relative-time bands for when registrations happen before an event.
LEAD_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("0–7 päeva enne", 0, 7),
    ("8–14 päeva enne", 8, 14),
    ("15–30 päeva enne", 15, 30),
    ("30+ päeva enne", 31, None),
)


@dataclass(frozen=True)
class EventRegistrations:
    """One event's Commerce registration facts, summed over every product on its page."""

    event_id: str
    path: str
    units: Decimal = Decimal("0")
    ordered_value_net: Decimal = Decimal("0")
    order_lines: int = 0
    product_count: int = 0
    first_fact: date | None = None
    last_fact: date | None = None
    #: True when this page is also another programme event's page. The figures
    #: are still correct for the page; what they are not is exclusively this
    #: event's.
    shares_page: bool = False

    @property
    def has_data(self) -> bool:
        return self.order_lines > 0

    @property
    def units_label(self) -> str:
        """The quantity as a reader should see it, grouped and in Estonian.

        `units` is a `Decimal` because a Commerce line can carry a fractional
        quantity, but almost every registration is a whole one and `12,00`
        beside `3,00` is harder to scan than `12` beside `3`. A genuinely
        fractional value keeps its fraction, with a decimal comma.
        """
        from apps.core.formatting import group_thousands

        if self.units == self.units.to_integral_value():
            return group_thousands(int(self.units))
        return f"{self.units.normalize():f}".replace(".", ",")


@dataclass(frozen=True)
class JoinReport:
    """The cardinality of the programme ↔ Commerce join, stated in full.

    Every one of these is a denominator something on the page divides by, so
    they are computed once and shown rather than implied.
    """

    programme_events: int = 0
    events_with_page: int = 0
    event_paths: int = 0
    registration_products: int = 0
    commerce_event_paths: int = 0
    matched_paths: int = 0
    matched_events: int = 0
    paths_with_one_product: int = 0
    paths_with_many_products: int = 0
    commerce_paths_without_event: int = 0
    events_sharing_a_path: int = 0
    coverage_start: date | None = None
    coverage_end: date | None = None
    member_semantics_verified: bool = False

    @property
    def has_data(self) -> bool:
        """Whether anything at all can be drawn from the Commerce side."""
        return self.matched_events > 0

    @property
    def match_rate(self) -> float:
        if not self.programme_events:
            return 0.0
        return self.matched_events / self.programme_events * 100


def _current_state() -> ShopSourceState | None:
    return ShopSourceState.objects.filter(is_current=True).order_by("-observed_at").first()


def registration_pages() -> dict[str, set[int]]:
    """`canonical path -> {product id}` for every current event-registration page.

    Only `event_registration` products, and only the `event` page role. A
    document template's product page is not an event page even when somebody
    linked to it from an event, and pulling other product types in would put
    E-pood's subject matter inside this dashboard.
    """
    pages: dict[str, set[int]] = {}
    rows = ShopProductPage.objects.filter(
        is_current=True,
        page_role=PageRole.EVENT,
        product__product_type=ProductType.EVENT_REGISTRATION,
    ).values_list("path", "product_id")
    for path, product_id in rows:
        key = canonical_path(path)
        if key:
            pages.setdefault(key, set()).add(product_id)
    return pages


def attach_registrations(
    items, *, pages: dict[str, set[int]] | None = None
) -> dict[str, EventRegistrations]:
    """Registration facts for many events, keyed by `event_id`, in two queries.

    Events whose page carries no registration product are **absent** from the
    result rather than present with zero. Zero registrations and "this event was
    never sold through the shop" are different facts, and only the first is a
    measurement.
    """
    rows = list(items)
    if not rows:
        return {}
    pages = registration_pages() if pages is None else pages
    if not pages:
        return {}

    by_event: dict[str, str] = {}
    users_of_path: dict[str, int] = {}
    for item in attach_public_links(rows):
        url = getattr(getattr(item, "public_link", None), "url", "") or ""
        path = canonical_path(url)
        if path and path in pages:
            by_event[item.event_id] = path
            users_of_path[path] = users_of_path.get(path, 0) + 1
    if not by_event:
        return {}

    product_ids = {pid for path in set(by_event.values()) for pid in pages[path]}
    per_product = {
        row["product_id"]: row
        for row in ShopDailyFact.objects.filter(is_current=True, product_id__in=product_ids)
        .values("product_id")
        .annotate(
            units=Sum("units"),
            value=Sum("ordered_value_net"),
            lines=Sum("order_count"),
            first=Min("report_date"),
            last=Max("report_date"),
        )
    }

    result: dict[str, EventRegistrations] = {}
    for event_id, path in by_event.items():
        ids = pages[path]
        present = [per_product[pid] for pid in ids if pid in per_product]
        if not present:
            continue
        days = [row["first"] for row in present] + [row["last"] for row in present]
        result[event_id] = EventRegistrations(
            event_id=event_id,
            path=path,
            # Every product on the page, because one event page can sell several
            # ticket types and choosing one of them would drop registrations
            # that really happened.
            units=sum((row["units"] or Decimal("0")) for row in present),
            ordered_value_net=sum((row["value"] or Decimal("0")) for row in present),
            order_lines=sum((row["lines"] or 0) for row in present),
            product_count=len(ids),
            first_fact=min(days),
            last_fact=max(days),
            shares_page=users_of_path.get(path, 0) > 1,
        )
    return result


def registration_lead_bands(
    items, *, pages: dict[str, set[int]] | None = None
) -> tuple[tuple[str, int], ...]:
    """When registrations happen, relative to each event's own start date.

    Relative time rather than calendar time, which is what lets a February event
    and a September one be read on one chart. Only events whose whole Commerce
    coverage contains the window contribute: a pre-Commerce event has not had
    zero registrations, it has had unmeasured ones.

    Units are counted, so a day carrying three units contributes three. That is
    a weighted distribution of units, not of orders, and the label says so.
    """
    pages = registration_pages() if pages is None else pages
    if not pages:
        return ()
    state = _current_state()
    if state is None:
        return ()

    windows: list[tuple[set[int], date]] = []
    for item in attach_public_links(list(items)):
        if item.start_date is None:
            continue
        url = getattr(getattr(item, "public_link", None), "url", "") or ""
        path = canonical_path(url)
        if not path or path not in pages:
            continue
        # The whole 30-day-plus run-up has to sit inside Commerce coverage, or a
        # short measurement reads as late registration.
        if item.start_date > state.coverage_end or state.coverage_start > item.start_date:
            continue
        windows.append((pages[path], item.start_date))
    if not windows:
        return ()

    product_ids = {pid for ids, _start in windows for pid in ids}
    rows = list(
        ShopDailyFact.objects.filter(is_current=True, product_id__in=product_ids).values(
            "product_id", "report_date", "units"
        )
    )
    by_product: dict[int, list[tuple[date, Decimal]]] = {}
    for row in rows:
        by_product.setdefault(row["product_id"], []).append((row["report_date"], row["units"]))

    banded: dict[str, int] = {label: 0 for label, _low, _high in LEAD_BANDS}
    for ids, start in windows:
        for pid in ids:
            for day, units in by_product.get(pid, ()):
                if day > start:
                    continue
                ahead = (start - day).days
                for label, low, high in LEAD_BANDS:
                    if ahead >= low and (high is None or ahead <= high):
                        banded[label] += int(units)
                        break
    return tuple((label, banded[label]) for label, _low, _high in LEAD_BANDS if banded[label])


def member_split(items, *, pages: dict[str, set[int]] | None = None) -> dict[str, Decimal] | None:
    """Units by member status, or `None` while the source's semantics are unverified.

    The gate is `ShopSourceState.member_semantics_verified`, and it is checked
    here rather than in a template because the whole dimension has to be absent,
    not merely hidden. It is false today: nobody has confirmed that Koda.ee's
    Commerce orders snapshot the buyer's membership **at the moment of the
    transaction** rather than reflecting what it is now. If profiles are reused
    and updated, every historical split would be wrong in a way no reader could
    detect.

    `unknown` is never folded into either side.
    """
    state = _current_state()
    if state is None or not state.member_semantics_verified:
        return None
    pages = registration_pages() if pages is None else pages
    if not pages:
        return None

    product_ids: set[int] = set()
    for item in attach_public_links(list(items)):
        url = getattr(getattr(item, "public_link", None), "url", "") or ""
        path = canonical_path(url)
        if path and path in pages:
            product_ids |= pages[path]
    if not product_ids:
        return None

    split = {status.value: Decimal("0") for status in MemberStatus}
    for row in (
        ShopDailyFact.objects.filter(is_current=True, product_id__in=product_ids)
        .values("member_status")
        .annotate(units=Sum("units"))
    ):
        split[row["member_status"]] = row["units"] or Decimal("0")
    return split


def free_paid_split(
    items, *, pages: dict[str, set[int]] | None = None
) -> dict[str, Decimal] | None:
    """Units classified free / paid / unstated at import, or `None` when unavailable.

    This is **transaction behaviour**, and it is a different question from the
    programme's own price metadata: a current list price of zero does not prove
    that every historical registration was free, and a paid list price does not
    prove nobody was let in for nothing. The two never substitute for each other.

    `ShopDailyFact` classifies all three or none, so a partial split cannot
    occur; when the source did not classify at all the answer is `None` rather
    than three zeros.
    """
    pages = registration_pages() if pages is None else pages
    if not pages:
        return None
    product_ids: set[int] = set()
    for item in attach_public_links(list(items)):
        url = getattr(getattr(item, "public_link", None), "url", "") or ""
        path = canonical_path(url)
        if path and path in pages:
            product_ids |= pages[path]
    if not product_ids:
        return None

    totals = ShopDailyFact.objects.filter(
        is_current=True, product_id__in=product_ids, free_units__isnull=False
    ).aggregate(free=Sum("free_units"), paid=Sum("paid_units"), unknown=Sum("unknown_units"))
    if totals["free"] is None:
        return None
    return {
        "free": totals["free"] or Decimal("0"),
        "paid": totals["paid"] or Decimal("0"),
        "unknown": totals["unknown"] or Decimal("0"),
    }


def join_report(items) -> JoinReport:
    """The full cardinality of the join, computed once for the data-quality block."""
    rows = list(items)
    state = _current_state()
    pages = registration_pages()

    programme_paths: dict[str, int] = {}
    for item in attach_public_links(rows):
        url = getattr(getattr(item, "public_link", None), "url", "") or ""
        path = canonical_path(url)
        if path:
            programme_paths[path] = programme_paths.get(path, 0) + 1

    matched = set(programme_paths) & set(pages)
    # `order_by()` with no arguments is load-bearing rather than tidiness.
    # `ShopProductPage` orders by product, role and path; a `DISTINCT` query
    # inheriting that ordering has those columns appended to its select list —
    # Django does it silently — and would count distinct *(product, role, path)*
    # triples instead of distinct products. The unique constraint happens to
    # make the two agree today, which is exactly why the mistake would survive.
    products = (
        ShopProductPage.objects.filter(
            is_current=True,
            page_role=PageRole.EVENT,
            product__product_type=ProductType.EVENT_REGISTRATION,
        )
        .order_by()
        .values("product_id")
        .distinct()
        .count()
    )

    return JoinReport(
        programme_events=len(rows),
        events_with_page=sum(programme_paths.values()),
        event_paths=len(programme_paths),
        registration_products=products,
        commerce_event_paths=len(pages),
        matched_paths=len(matched),
        matched_events=sum(programme_paths[path] for path in matched),
        paths_with_one_product=sum(1 for path in matched if len(pages[path]) == 1),
        paths_with_many_products=sum(1 for path in matched if len(pages[path]) > 1),
        commerce_paths_without_event=len(set(pages) - set(programme_paths)),
        events_sharing_a_path=sum(count for path, count in programme_paths.items() if count > 1),
        coverage_start=state.coverage_start if state else None,
        coverage_end=state.coverage_end if state else None,
        member_semantics_verified=bool(state and state.member_semantics_verified),
    )


__all__ = [
    "LEAD_BANDS",
    "REGISTRATION_UNIT_LABEL",
    "EventRegistrations",
    "JoinReport",
    "attach_registrations",
    "free_paid_split",
    "join_report",
    "member_split",
    "registration_lead_bands",
    "registration_pages",
]
