from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import close_old_connections
from django.utils import timezone

from apps.access.models import ViewerRateLimitBucket
from apps.access.rate_limit import check_pin

pytestmark = pytest.mark.django_db


def test_fifth_failure_locks_client_for_fifteen_minutes(client):
    for _attempt in range(4):
        response = client.post("/sisene/", {"pin": "1111"})
        assert response.status_code == 200

    response = client.post("/sisene/", {"pin": "1111"})

    assert response.status_code == 429
    assert 899 <= int(response.headers["Retry-After"]) <= 900
    bucket = ViewerRateLimitBucket.objects.get()
    assert bucket.failure_count == 5
    assert 899 <= (bucket.locked_until - timezone.now()).total_seconds() <= 900


def test_locked_client_is_rejected_even_with_correct_pin(client, viewer_pin):
    for _attempt in range(5):
        client.post("/sisene/", {"pin": "1111"})

    response = client.post("/sisene/", {"pin": viewer_pin})

    assert response.status_code == 429
    assert ViewerRateLimitBucket.objects.get().failure_count == 5


def test_success_removes_existing_failure_bucket(client, viewer_pin):
    client.post("/sisene/", {"pin": "1111"})
    assert ViewerRateLimitBucket.objects.count() == 1

    response = client.post("/sisene/", {"pin": viewer_pin})

    assert response.status_code == 302
    assert ViewerRateLimitBucket.objects.count() == 0


def test_different_client_addresses_have_separate_buckets(client):
    client.post("/sisene/", {"pin": "1111"}, REMOTE_ADDR="192.0.2.10")
    client.post("/sisene/", {"pin": "1111"}, REMOTE_ADDR="192.0.2.11")

    buckets = list(ViewerRateLimitBucket.objects.order_by("client_key"))
    assert len(buckets) == 2
    assert buckets[0].client_key != buckets[1].client_key
    assert all(bucket.failure_count == 1 for bucket in buckets)


def test_expired_failure_window_starts_again_at_one(client):
    now = timezone.now()
    client.post("/sisene/", {"pin": "1111"})
    bucket = ViewerRateLimitBucket.objects.get()
    ViewerRateLimitBucket.objects.filter(pk=bucket.pk).update(
        window_started_at=now - timedelta(minutes=16),
        failure_count=4,
    )

    response = client.post("/sisene/", {"pin": "1111"})

    assert response.status_code == 200
    bucket.refresh_from_db()
    assert bucket.failure_count == 1
    assert bucket.locked_until is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_failures_are_serialized():
    def fail_once():
        close_old_connections()
        try:
            return check_pin("a" * 64, "1111")
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _attempt: fail_once(), range(5)))

    bucket = ViewerRateLimitBucket.objects.get(client_key="a" * 64)
    assert bucket.failure_count == 5
    assert sum(result.locked for result in results) == 1
