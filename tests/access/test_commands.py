from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import check_password
from django.core.management import call_command
from django.utils import timezone

from apps.access.models import ViewerRateLimitBucket

pytestmark = pytest.mark.django_db


def test_generate_pin_hash_reads_hidden_input_and_outputs_only_hash():
    output = StringIO()

    with patch("getpass.getpass", return_value="8642"):
        call_command("generate_viewer_pin_hash", stdout=output)

    generated_hash = output.getvalue().strip()
    assert check_password("8642", generated_hash)
    assert "8642" not in generated_hash
    assert len(output.getvalue().splitlines()) == 1


def test_purge_removes_only_old_inactive_buckets():
    now = timezone.now()
    old_inactive = ViewerRateLimitBucket.objects.create(
        client_key="a" * 64,
        window_started_at=now - timedelta(days=40),
    )
    old_locked = ViewerRateLimitBucket.objects.create(
        client_key="b" * 64,
        window_started_at=now - timedelta(days=40),
        locked_until=now + timedelta(days=1),
    )
    recent = ViewerRateLimitBucket.objects.create(
        client_key="c" * 64,
        window_started_at=now,
    )
    ViewerRateLimitBucket.objects.filter(pk__in=[old_inactive.pk, old_locked.pk]).update(
        updated_at=now - timedelta(days=31)
    )

    output = StringIO()
    call_command("purge_viewer_rate_limits", stdout=output)

    assert output.getvalue().strip() == "1"
    assert not ViewerRateLimitBucket.objects.filter(pk=old_inactive.pk).exists()
    assert ViewerRateLimitBucket.objects.filter(pk=old_locked.pk).exists()
    assert ViewerRateLimitBucket.objects.filter(pk=recent.pk).exists()
