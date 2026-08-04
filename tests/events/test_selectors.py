"""What the public-calendar selectors will and will not answer.

This feed is a **secondary** source. Since the canonical Excel programme became
the dashboard's event source, the only thing this module speaks for is the public
calendar itself: whether it is collecting, when it last succeeded, and how many
publicly announced events are still to come.

`count_started_in_past_window` was removed with the page that used it. It
reconstructed a past-event count by scanning every archived snapshot, because the
collector drops an event once it has finished. That derived history is exactly
what the public feed must no longer produce, and the workbook — which retains
what actually happened — answers it directly instead.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.events.models import EventItem, EventSnapshot
from apps.events.selectors import count_upcoming_within, get_event_summary
from apps.events.sync import synchronize_events
from tests.koda.conftest import collector_returning, event_collection

pytestmark = pytest.mark.django_db


def test_the_near_term_count_reads_the_current_snapshot():
    synchronize_events(collector=collector_returning(event_collection(3)))

    assert count_upcoming_within() == 3


def test_a_finished_event_is_not_upcoming_however_recent_the_snapshot():
    """Filtered at read time as well as import time.

    A snapshot published before an event ended would otherwise keep counting it.
    """
    synchronize_events(collector=collector_returning(event_collection(3)))
    EventItem.objects.update(starts_on=timezone.localdate() - dt.timedelta(days=14))

    assert count_upcoming_within() == 0


def test_no_snapshot_counts_nothing():
    assert count_upcoming_within() == 0
    assert get_event_summary().has_data is False


def test_the_public_collector_still_publishes_its_own_snapshots():
    """The feed keeps running independently of the workbook programme.

    Two collections with different content publish two snapshots, the newest is
    current, and the summary describes that snapshot — none of which depends on
    the event programme having been imported at all.
    """
    synchronize_events(collector=collector_returning(event_collection(2)))
    synchronize_events(collector=collector_returning(event_collection(3)))

    assert EventSnapshot.objects.count() == 2
    assert EventSnapshot.objects.filter(is_current=True).count() == 1

    summary = get_event_summary()
    assert summary.has_data is True
    assert summary.item_count == 3
