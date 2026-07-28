import math
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.utils import timezone

from .models import ViewerRateLimitBucket

FAILURE_LIMIT = 5
WINDOW_DURATION = timedelta(minutes=15)
LOCK_DURATION = timedelta(minutes=15)


@dataclass(frozen=True)
class PinCheckResult:
    authenticated: bool
    locked: bool
    retry_after: int | None = None


def check_pin(client_key: str, pin: str) -> PinCheckResult:
    now = timezone.now()

    with transaction.atomic():
        # One locked fetch-or-create: a separate unlocked get_or_create followed
        # by a locking re-fetch leaves a window in which a concurrent successful
        # login can delete the bucket and make the re-fetch crash.
        bucket, _created = ViewerRateLimitBucket.objects.select_for_update().get_or_create(
            client_key=client_key,
            defaults={
                "window_started_at": now,
                "failure_count": 0,
            },
        )

        if bucket.locked_until and bucket.locked_until > now:
            retry_after = max(1, math.ceil((bucket.locked_until - now).total_seconds()))
            return PinCheckResult(False, True, retry_after)

        if check_password(pin, settings.VIEWER_PIN_HASH):
            bucket.delete()
            return PinCheckResult(True, False)

        if now - bucket.window_started_at >= WINDOW_DURATION:
            bucket.window_started_at = now
            bucket.failure_count = 0
            bucket.locked_until = None

        bucket.failure_count += 1
        if bucket.failure_count >= FAILURE_LIMIT:
            bucket.locked_until = now + LOCK_DURATION

        bucket.save()

        if bucket.locked_until:
            return PinCheckResult(False, True, math.ceil(LOCK_DURATION.total_seconds()))
        return PinCheckResult(False, False)
