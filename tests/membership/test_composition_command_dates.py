"""The composition import validates dates against the application's day.

The container clock runs UTC while the application's day is Europe/Tallinn.
For the first hours of every Tallinn day the two disagree, and a future-date
check built on the wall clock would reject that day's own snapshot as
"future". The command therefore consults `timezone.localdate()`, and this
test pins it by placing the clock at half past midnight Tallinn time.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture
def half_past_midnight_tallinn(monkeypatch):
    """21:30 UTC on the 14th — already 00:30 on the 15th in Tallinn."""
    moment = dt.datetime(2026, 8, 14, 21, 30, tzinfo=dt.UTC)
    monkeypatch.setattr(timezone, "now", lambda: moment)
    return timezone.localdate()


def test_todays_snapshot_is_accepted_after_tallinn_midnight(half_past_midnight_tallinn, tmp_path):
    """A snapshot dated the application's today must pass the future check.

    The roster path does not exist, so the command still fails — but on the
    file, after the date validation. Failing on `tulevikus` would mean the
    check consulted the wall clock rather than the application day.
    """
    assert half_past_midnight_tallinn == dt.date(2026, 8, 15)

    with pytest.raises(CommandError) as failure:
        call_command(
            "import_membership_composition",
            "--roster",
            str(tmp_path / "puudub.xlsx"),
            "--snapshot-date",
            "2026-08-15",
        )

    assert "tulevikus" not in str(failure.value)


def test_a_genuinely_future_snapshot_is_still_refused(half_past_midnight_tallinn, tmp_path):
    with pytest.raises(CommandError) as failure:
        call_command(
            "import_membership_composition",
            "--roster",
            str(tmp_path / "puudub.xlsx"),
            "--snapshot-date",
            "2026-08-16",
        )

    assert "tulevikus" in str(failure.value)
