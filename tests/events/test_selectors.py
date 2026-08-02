"""What the events selectors will and will not count.

The one that needs guarding is `count_started_in_past_window`. Everything else
the module reads lives in the current snapshot; last month's events do not,
because the collector drops an event once it has finished. The count therefore
reads the archive, and the rule that matters is when it refuses to answer.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.events.models import EventItem, EventSnapshot
from apps.events.selectors import count_started_in_past_window, count_upcoming_within
from apps.events.sync import synchronize_events
from tests.koda.conftest import collector_returning, event_collection

pytestmark = pytest.mark.django_db


def age_the_archive(*, days: int) -> None:
    """Move the snapshot back in time, past events and all.

    Both models refuse an update through `save()`, deliberately. A test that
    needs an archive reaching back past the window has no other way to build
    one, and `QuerySet.update()` writes the columns directly.
    """
    EventSnapshot.objects.update(observed_at=timezone.now() - dt.timedelta(days=days))


def test_events_that_have_since_finished_are_counted_from_the_archive():
    synchronize_events(collector=collector_returning(event_collection(3)))
    age_the_archive(days=45)
    # The three synthetic events start in the future; moved back a fortnight
    # they sit inside the window that has just closed.
    EventItem.objects.update(starts_on=timezone.localdate() - dt.timedelta(days=14))

    assert count_started_in_past_window() == 3
    # The same rows are finished, so nothing is upcoming any more. The two
    # counts describe different windows and must not agree here.
    assert count_upcoming_within() == 0


def test_an_archive_that_does_not_reach_back_states_nothing():
    """A fortnight-old install never saw last month, and says so with `None`.

    A zero here would report that no event took place, which is a measurement
    nobody made.
    """
    synchronize_events(collector=collector_returning(event_collection(3)))

    assert count_started_in_past_window() is None


def test_no_snapshot_at_all_states_nothing():
    assert count_started_in_past_window() is None


def test_an_event_is_counted_once_however_many_snapshots_hold_it():
    synchronize_events(collector=collector_returning(event_collection(2)))
    synchronize_events(collector=collector_returning(event_collection(3)))
    age_the_archive(days=45)
    EventItem.objects.update(starts_on=timezone.localdate() - dt.timedelta(days=10))

    # Two snapshots, five rows, three distinct events.
    assert EventItem.objects.count() == 5
    assert count_started_in_past_window() == 3
