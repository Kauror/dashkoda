"""What a public event resource and a discovery run will not let you do.

A resource is mutable where a snapshot row is not — a public page can be
corrected upstream — so the guard here is narrower and worth stating precisely:
the *description* may change, the *identity* may not.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.events.models import EventImmutable
from apps.events.public_bootstrap import ensure_event_pages_source
from apps.events.public_models import (
    DiscoveryMode,
    DiscoveryOrigin,
    PublicEventDiscoverySnapshot,
    PublicEventResource,
)


def make_resource(**overrides) -> PublicEventResource:
    fields = {
        "canonical_url": "https://www.koda.ee/et/sundmused/alfa",
        "stable_key": "alfa",
        "title": "Alfa",
        "starts_on": dt.date(2019, 5, 6),
        "location": "Toom-Kooli 17",
        "discovered_from": DiscoveryOrigin.SITEMAP,
        "content_checksum": "0" * 64,
        "last_seen_at": timezone.now(),
    }
    return PublicEventResource.objects.create(**(fields | overrides))


def test_a_page_may_be_corrected(db):
    resource = make_resource()

    resource.title = "Alfa (parandatud)"
    resource.content_checksum = "1" * 64
    resource.save(update_fields=["title", "content_checksum"])

    resource.refresh_from_db()
    assert resource.title == "Alfa (parandatud)"


def test_its_address_may_not_change(db):
    resource = make_resource()

    resource.canonical_url = "https://www.koda.ee/et/sundmused/hoopis-teine"
    with pytest.raises(EventImmutable):
        resource.save(update_fields=["canonical_url"])


def test_a_blanket_save_is_refused(db):
    """`update_fields` is the whole guard, so omitting it cannot be allowed."""
    resource = make_resource()
    resource.title = "Alfa (parandatud)"

    with pytest.raises(EventImmutable):
        resource.save()


def test_two_rows_cannot_claim_one_address(db):
    make_resource()

    with pytest.raises(IntegrityError):
        make_resource(stable_key="teine")


def test_an_event_cannot_end_before_it_starts(db):
    with pytest.raises(IntegrityError):
        make_resource(starts_on=dt.date(2019, 5, 6), ends_on=dt.date(2019, 5, 5))


def test_a_page_without_a_name_is_refused(db):
    with pytest.raises(IntegrityError):
        make_resource(title="")


# -- discovery runs ------------------------------------------------------


def make_snapshot(source, **overrides) -> PublicEventDiscoverySnapshot:
    fields = {
        "source": source,
        "mode": DiscoveryMode.FULL,
        "observed_at": timezone.now(),
        "is_current": True,
    }
    return PublicEventDiscoverySnapshot.objects.create(**(fields | overrides))


def test_a_run_records_what_happened_and_is_then_frozen(db):
    source = ensure_event_pages_source()
    snapshot = make_snapshot(source, urls_seen=1516, resources_created=1516)

    snapshot.urls_seen = 0
    with pytest.raises(EventImmutable):
        snapshot.save(update_fields=["urls_seen"])


def test_only_one_run_per_source_is_current(db):
    source = ensure_event_pages_source()
    make_snapshot(source)

    with pytest.raises(IntegrityError):
        make_snapshot(source)


def test_a_superseded_run_may_be_stood_down(db):
    source = ensure_event_pages_source()
    snapshot = make_snapshot(source)

    snapshot.is_current = False
    snapshot.save(update_fields=["is_current"])

    snapshot.refresh_from_db()
    assert snapshot.is_current is False


def test_resources_do_not_belong_to_a_run(db):
    """The point of the split: deleting run history cannot delete the catalogue.

    A resource has no foreign key to a snapshot, so there is nothing to cascade.
    """
    source = ensure_event_pages_source()
    snapshot = make_snapshot(source)
    make_resource()

    PublicEventDiscoverySnapshot.objects.filter(pk=snapshot.pk).delete()

    assert PublicEventResource.objects.count() == 1
    assert not any(
        field.related_model is PublicEventDiscoverySnapshot
        for field in PublicEventResource._meta.get_fields()
    )
