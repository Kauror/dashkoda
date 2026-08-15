"""The programme ↔ Commerce join, and the words that may not be used for it.

The cardinality cases here are the ones that silently corrupt a total if the
join is written casually: several products on one event page, one page shared by
two programme events, a Commerce page with no programme event behind it, and a
programme event the shop never sold anything for.

The gate matters as much as the arithmetic. On the Chamber's current export
there are **no** event-registration products at all, so the whole surface has to
disappear rather than render zeros.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.event_programme import commerce
from apps.event_programme.selectors import get_current_event_programme_snapshot
from apps.shop.models import (
    MemberStatus,
    PageRole,
    PaymentClass,
    ProductType,
    ShopDailyFact,
    ShopProduct,
    ShopProductPage,
    ShopSourceState,
)

from .workbook_factory import synthetic_row

pytestmark = pytest.mark.django_db

EVENT_PAGE = "https://www.koda.ee/et/sundmused/sunteetiline-1"
EVENT_PATH = "/et/sundmused/sunteetiline-1"
OTHER_PAGE = "https://www.koda.ee/et/sundmused/sunteetiline-2"
OTHER_PATH = "/et/sundmused/sunteetiline-2"
DAY = dt.date(2026, 5, 1)


def _at(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time())


@pytest.fixture
def shop_state():
    """A current shop dataset with membership semantics **unverified**.

    Unverified is the real state of the Chamber's export, so it is the default
    here too: a test that had to opt out of the gate would be asserting against
    a world that does not exist.
    """
    from apps.shop.bootstrap import ensure_shop_source

    source = ensure_shop_source()
    return ShopSourceState.objects.create(
        source=source,
        schema_version="1.0",
        source_as_of=dt.date(2026, 8, 11),
        coverage_start=dt.date(2020, 10, 22),
        coverage_end=dt.date(2026, 8, 11),
        member_semantics_verified=False,
        content_checksum="c" * 64,
        observed_at=timezone.now(),
        is_current=True,
    )


@pytest.fixture
def make_product():
    def build(product_id: int, path: str, *, product_type=ProductType.EVENT_REGISTRATION):
        product = ShopProduct.objects.create(
            source_product_id=product_id,
            product_type=product_type,
            first_seen_on=DAY,
            last_seen_on=DAY,
        )
        ShopProductPage.objects.create(
            product=product,
            page_role=PageRole.EVENT,
            path=path,
            first_seen_on=DAY,
            last_seen_on=DAY,
            is_current=True,
        )
        return product

    return build


@pytest.fixture
def add_fact():
    def build(product, *, day=DAY, units=1, value="50.0000", orders=1, member=MemberStatus.UNKNOWN):
        return ShopDailyFact.objects.create(
            report_date=day,
            product=product,
            member_status=member,
            payment_class=PaymentClass.BANK_OR_CARD,
            order_count=orders,
            units=Decimal(units),
            ordered_value_net=Decimal(value),
            is_current=True,
        )

    return build


def _events(publish_programme, rows):
    publish_programme(rows=rows)
    from apps.event_programme import analytics

    return list(analytics.items_for(get_current_event_programme_snapshot()))


def _linked_row(index: int, page: str, **kwargs):
    kwargs.setdefault("start_date", _at(dt.date(2026, 6, 1)))
    return synthetic_row(
        event_id=f"E-{index}",
        service_code=str(index),
        public_url=page,
        public_link_status="linked_embedded_latest",
        source_row=index + 1,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_without_registration_products_nothing_is_produced(publish_programme, shop_state):
    """The Chamber's current export: documents and physical goods only."""
    ShopProduct.objects.create(
        source_product_id=1,
        product_type=ProductType.DOCUMENT,
        first_seen_on=DAY,
        last_seen_on=DAY,
    )
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE)])

    assert commerce.registration_pages() == {}
    assert commerce.attach_registrations(events) == {}
    report = commerce.join_report(events)
    assert report.has_data is False
    assert report.registration_products == 0
    assert report.matched_events == 0


def test_only_event_registration_products_are_read(publish_programme, shop_state, make_product):
    """A document product reachable at an event path is still not an event product."""
    make_product(1, EVENT_PATH, product_type=ProductType.DOCUMENT)
    _events(publish_programme, [_linked_row(1, EVENT_PAGE)])
    assert commerce.registration_pages() == {}


# ---------------------------------------------------------------------------
# Cardinality
# ---------------------------------------------------------------------------


def test_one_event_one_product(publish_programme, shop_state, make_product, add_fact):
    product = make_product(1, EVENT_PATH)
    add_fact(product, units=3, value="150.0000")
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE)])

    rows = commerce.attach_registrations(events)
    assert rows["E-1"].units == Decimal(3)
    assert rows["E-1"].ordered_value_net == Decimal("150.0000")
    assert rows["E-1"].product_count == 1
    assert rows["E-1"].shares_page is False

    report = commerce.join_report(events)
    assert report.matched_events == 1
    assert report.paths_with_one_product == 1
    assert report.paths_with_many_products == 0


def test_two_products_on_one_page_are_summed(publish_programme, shop_state, make_product, add_fact):
    """Two ticket types for one event. Picking one would drop real registrations."""
    first = make_product(1, EVENT_PATH)
    second = make_product(2, EVENT_PATH)
    add_fact(first, units=3, value="150.0000")
    add_fact(second, units=2, value="80.0000")
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE)])

    row = commerce.attach_registrations(events)["E-1"]
    assert row.units == Decimal(5)
    assert row.ordered_value_net == Decimal("230.0000")
    assert row.product_count == 2
    assert commerce.join_report(events).paths_with_many_products == 1


def test_two_events_on_one_page_are_both_disclosed(
    publish_programme, shop_state, make_product, add_fact
):
    """Neither is dropped, and both are marked as sharing the page.

    The page's total belongs to both as context and to neither exclusively, so
    the flag is what stops a cohort total adding it twice without saying so.
    """
    product = make_product(1, EVENT_PATH)
    add_fact(product, units=4)
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE), _linked_row(2, EVENT_PAGE)])

    rows = commerce.attach_registrations(events)
    assert set(rows) == {"E-1", "E-2"}
    assert all(row.shares_page for row in rows.values())
    assert all(row.units == Decimal(4) for row in rows.values())

    report = commerce.join_report(events)
    assert report.events_sharing_a_path == 2
    assert report.matched_paths == 1
    assert report.matched_events == 2


def test_a_commerce_page_with_no_programme_event_is_counted(
    publish_programme, shop_state, make_product
):
    make_product(1, OTHER_PATH)
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE)])
    report = commerce.join_report(events)
    assert report.commerce_paths_without_event == 1
    assert report.matched_events == 0


def test_an_event_with_no_product_is_absent_not_zero(
    publish_programme, shop_state, make_product, add_fact
):
    product = make_product(1, EVENT_PATH)
    add_fact(product, units=2)
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE), _linked_row(2, OTHER_PAGE)])
    rows = commerce.attach_registrations(events)
    assert set(rows) == {"E-1"}


def test_a_product_with_no_facts_is_absent(publish_programme, shop_state, make_product):
    make_product(1, EVENT_PATH)
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE)])
    assert commerce.attach_registrations(events) == {}


# ---------------------------------------------------------------------------
# Cohort semantics
# ---------------------------------------------------------------------------


def test_a_registration_bought_the_year_before_still_belongs_to_the_event(
    publish_programme, shop_state, make_product, add_fact
):
    """A January 2026 event sold in December 2025 is a 2026 event's registration.

    The **event** defines the cohort; the transaction date defines the activity
    timeline. Filtering registrations to the event's calendar year would drop
    this one and quietly understate the event.
    """
    product = make_product(1, EVENT_PATH)
    add_fact(product, day=dt.date(2025, 12, 10), units=7)
    events = _events(
        publish_programme, [_linked_row(1, EVENT_PAGE, start_date=_at(dt.date(2026, 1, 15)))]
    )

    row = commerce.attach_registrations(events)["E-1"]
    assert row.units == Decimal(7)
    assert row.first_fact == dt.date(2025, 12, 10)


def test_registration_lead_bands_use_relative_time(
    publish_programme, shop_state, make_product, add_fact
):
    product = make_product(1, EVENT_PATH)
    start = dt.date(2026, 6, 1)
    add_fact(product, day=start - dt.timedelta(days=2), units=3)
    add_fact(product, day=start - dt.timedelta(days=40), units=5)
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE, start_date=_at(start))])

    bands = dict(commerce.registration_lead_bands(events))
    assert bands["0–7 päeva enne"] == 3
    assert bands["30+ päeva enne"] == 5


def test_a_pre_commerce_event_contributes_no_bands(
    publish_programme, shop_state, make_product, add_fact
):
    """Missing history is not zero registrations."""
    product = make_product(1, EVENT_PATH)
    add_fact(product, day=dt.date(2026, 5, 1), units=2)
    events = _events(
        publish_programme,
        [_linked_row(1, EVENT_PAGE, start_date=_at(dt.date(2019, 6, 1)))],
    )
    assert commerce.registration_lead_bands(events) == ()


# ---------------------------------------------------------------------------
# Gates on dimensions
# ---------------------------------------------------------------------------


def test_member_split_is_withheld_until_semantics_are_verified(
    publish_programme, shop_state, make_product, add_fact
):
    product = make_product(1, EVENT_PATH)
    add_fact(product, units=2, member=MemberStatus.MEMBER)
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE)])

    assert shop_state.member_semantics_verified is False
    assert commerce.member_split(events) is None
    assert commerce.join_report(events).member_semantics_verified is False


def test_member_split_keeps_unknown_separate_when_verified(
    publish_programme, shop_state, make_product, add_fact
):
    ShopSourceState.objects.filter(pk=shop_state.pk).update(member_semantics_verified=True)
    product = make_product(1, EVENT_PATH)
    add_fact(product, units=2, member=MemberStatus.MEMBER)
    add_fact(product, day=DAY + dt.timedelta(days=1), units=3, member=MemberStatus.UNKNOWN)
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE)])

    split = commerce.member_split(events)
    assert split[MemberStatus.MEMBER] == Decimal(2)
    assert split[MemberStatus.UNKNOWN] == Decimal(3)
    assert split[MemberStatus.NON_MEMBER] == Decimal(0)


def test_free_paid_split_is_none_when_the_source_did_not_classify(
    publish_programme, shop_state, make_product, add_fact
):
    """Schema 1.0 packages state nothing. Three zeros would be a claim."""
    product = make_product(1, EVENT_PATH)
    add_fact(product, units=2)
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE)])
    assert commerce.free_paid_split(events) is None


def test_units_label_reads_as_a_whole_number(publish_programme, shop_state, make_product, add_fact):
    product = make_product(1, EVENT_PATH)
    add_fact(product, units=12)
    events = _events(publish_programme, [_linked_row(1, EVENT_PAGE)])
    assert commerce.attach_registrations(events)["E-1"].units_label == "12"
