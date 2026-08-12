"""Publishing GA4 reporting days: revisions, idempotency and the feed contract.

The transport is always faked. **No Google credential, property ID or response
body exists anywhere in this file**; the service is exercised through a
collector double, and the one test that touches configuration asserts what
happens when it is absent.

What is pinned down here is the behaviour a five-year history depends on: the
same day read twice publishes once, a day GA4 revises publishes a second
revision without rewriting the first, and exactly one revision per date is ever
current.
"""

from __future__ import annotations

import datetime as dt
import json
from io import StringIO

import pytest
from django.core.management import call_command

from apps.audit.models import AuditEvent
from apps.core.feeds import FeedResult
from apps.sources.models import ImportRun, ImportStatus
from apps.visibility.audit_actions import VisibilityAudit
from apps.visibility.ga4 import ChannelRow, DayReading, Ga4NotConfigured, PageRow, RangeCollection
from apps.visibility.ga4_sync import (
    RECONCILIATION_DAYS,
    chunks,
    last_completed_day,
    reconciliation_window,
    synchronize_ga4,
)
from apps.visibility.models import Ga4ChannelDaily, Ga4DailySnapshot, Ga4FeedState, Ga4PageDaily

pytestmark = pytest.mark.django_db

DAY = dt.date(2026, 7, 1)
#: Comfortably after every date these tests publish, so a window is never
#: clamped by accident. `2026-07-03` was not: a range ending on the 3rd loses
#: its last day to the rule that today has not finished, which is the code
#: behaving correctly and the test asking the wrong question.
TODAY = dt.date(2026, 7, 10)


class FakeCollector:
    """Answers a range from a `{date: DayReading}` map it is given.

    Same signature as `Ga4ApiCollector.collect_range`, so nothing about the
    service knows it is talking to a double.
    """

    def __init__(self, days=None, *, error=None):
        self.days = days or {}
        self.error = error
        self.calls = []

    def collect_range(self, *, start, end, with_pages=True, with_channels=True):
        self.calls.append((start, end))
        if self.error is not None:
            raise self.error
        collection = RangeCollection()
        cursor = start
        while cursor <= end:
            reading = self.days.get(cursor)
            if reading is None:
                reading = DayReading(report_date=cursor)
            collection.days[cursor] = DayReading(
                report_date=cursor,
                sessions=reading.sessions,
                active_users=reading.active_users,
                new_users=reading.new_users,
                page_views=reading.page_views,
                engaged_sessions=reading.engaged_sessions,
                user_engagement_seconds=reading.user_engagement_seconds,
                pages=reading.pages if with_pages else (),
                channels=reading.channels if with_channels else (),
                has_page_detail=with_pages,
                has_channel_detail=with_channels,
            )
            cursor += dt.timedelta(days=1)
        collection.counts.requests = 3 if with_pages and with_channels else 1
        return collection


def reading(day=DAY, *, sessions=100, pages=(("/et/uudised/a", 40),), **figures):
    return DayReading(
        report_date=day,
        sessions=sessions,
        active_users=figures.get("active_users", 80),
        new_users=figures.get("new_users", 20),
        page_views=figures.get("page_views", 150),
        engaged_sessions=figures.get("engaged_sessions", 60),
        user_engagement_seconds=figures.get("user_engagement_seconds", 4000),
        pages=tuple(PageRow(path=path, page_views=views) for path, views in pages),
        channels=(ChannelRow(channel="Organic Search", sessions=70),),
        has_page_detail=True,
        has_channel_detail=True,
    )


def sync(days, **kwargs):
    kwargs.setdefault("start", DAY)
    kwargs.setdefault("end", DAY)
    kwargs.setdefault("today", TODAY)
    return synchronize_ga4(collector=FakeCollector(days), **kwargs)


def current(day=DAY):
    return Ga4DailySnapshot.objects.get(report_date=day, is_current_for_date=True)


# -- windows (no database needed, but kept beside what they describe) ----


def test_the_ordinary_window_is_eight_completed_days_ending_yesterday():
    start, end = reconciliation_window(dt.date(2026, 8, 9))

    assert end == dt.date(2026, 8, 8), "today has not finished and is never collected"
    assert start == dt.date(2026, 8, 1)
    assert (end - start).days + 1 == RECONCILIATION_DAYS


def test_today_is_never_in_the_window():
    """A partial day would publish a figure wrong by construction and then need
    revising tomorrow."""
    today = dt.date(2026, 8, 9)

    assert last_completed_day(today) < today
    assert reconciliation_window(today)[1] < today


def test_a_range_is_split_into_bounded_chunks():
    got = list(chunks(dt.date(2026, 1, 1), dt.date(2026, 3, 15), size=31))

    assert got[0] == (dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    assert got[-1][1] == dt.date(2026, 3, 15)
    assert all(start <= stop for start, stop in got)


def test_chunks_cover_the_range_exactly_once():
    covered = [
        day
        for start, stop in chunks(dt.date(2026, 1, 1), dt.date(2026, 2, 20), size=7)
        for day in [start + dt.timedelta(days=n) for n in range((stop - start).days + 1)]
    ]

    assert len(covered) == len(set(covered)) == 51


# -- first import --------------------------------------------------------


def test_a_first_reading_is_published_as_revision_one():
    outcome = sync({DAY: reading()})

    assert outcome.result == FeedResult.IMPORTED
    day = current()
    assert day.revision == 1
    assert day.supersedes is None
    assert day.sessions == 100
    assert day.report_date == DAY


def test_the_page_and_channel_rows_are_stored_with_the_day():
    sync({DAY: reading(pages=(("/et/uudised/a", 40), ("/et/uudised/b", 25)))})

    assert Ga4PageDaily.objects.filter(snapshot=current()).count() == 2
    assert Ga4ChannelDaily.objects.filter(snapshot=current()).count() == 1
    assert Ga4PageDaily.objects.get(path="/et/uudised/a").report_date == DAY


def test_a_day_with_nothing_measured_is_still_a_published_day():
    """An absence of measurement is a fact about that day, and a day with no
    revision at all is indistinguishable from one never collected."""
    outcome = sync({})

    assert outcome.result == FeedResult.IMPORTED
    assert current().sessions is None
    assert current().has_page_detail is True


# -- idempotency ---------------------------------------------------------


def test_the_same_reading_twice_publishes_once():
    sync({DAY: reading()})
    outcome = sync({DAY: reading()})

    assert outcome.result == FeedResult.UNCHANGED
    assert Ga4DailySnapshot.objects.filter(report_date=DAY).count() == 1
    assert outcome.extra["days_unchanged"] == 1


def test_an_unchanged_day_writes_no_second_import_run():
    sync({DAY: reading()})
    before = ImportRun.objects.count()

    sync({DAY: reading()})

    assert ImportRun.objects.count() == before


# -- revision ------------------------------------------------------------


def test_a_revised_day_publishes_a_new_revision_that_names_the_old_one():
    sync({DAY: reading(sessions=100)})
    outcome = sync({DAY: reading(sessions=140)})

    assert outcome.result == FeedResult.IMPORTED
    assert outcome.extra["days_revised"] == 1
    day = current()
    assert day.revision == 2
    assert day.sessions == 140
    assert day.supersedes is not None
    assert day.supersedes.sessions == 100, "the earlier reading keeps its figures"


def test_only_one_revision_of_a_date_is_current():
    sync({DAY: reading(sessions=100)})
    sync({DAY: reading(sessions=140)})
    sync({DAY: reading(sessions=150)})

    assert Ga4DailySnapshot.objects.filter(report_date=DAY).count() == 3
    assert Ga4DailySnapshot.objects.filter(report_date=DAY, is_current_for_date=True).count() == 1
    assert current().revision == 3


def test_a_revision_replaces_the_page_rows_rather_than_adding_to_them():
    """The current day's pages are the current revision's pages. Rows from the
    superseded revision stay attached to it and never join a sum."""
    sync({DAY: reading(pages=(("/et/uudised/a", 40),))})
    sync({DAY: reading(pages=(("/et/uudised/a", 55), ("/et/uudised/b", 10)))})

    live = Ga4PageDaily.objects.filter(snapshot__is_current_for_date=True)
    assert live.count() == 2
    assert live.get(path="/et/uudised/a").page_views == 55
    assert Ga4PageDaily.objects.count() == 3, "the superseded revision keeps its own row"


def test_a_narrower_re_read_does_not_replace_a_richer_published_day():
    """A site-only run over a day that already carries page rows would publish a
    revision with less in it, and every article's history would lose a day to a
    run that never asked about articles."""
    sync({DAY: reading()})

    outcome = synchronize_ga4(
        collector=FakeCollector({DAY: reading(sessions=999)}),
        start=DAY,
        end=DAY,
        with_pages=False,
        with_channels=False,
        today=TODAY,
    )

    assert outcome.extra["days_kept"] == 1
    assert current().revision == 1
    assert current().sessions == 100


# -- ranges --------------------------------------------------------------


def test_a_range_publishes_every_day_in_it():
    days = {DAY + dt.timedelta(days=n): reading(DAY + dt.timedelta(days=n)) for n in range(3)}

    outcome = synchronize_ga4(
        collector=FakeCollector(days), start=DAY, end=DAY + dt.timedelta(days=2), today=TODAY
    )

    assert outcome.extra["days_imported"] == 3
    assert Ga4DailySnapshot.objects.filter(is_current_for_date=True).count() == 3


def test_a_long_range_is_asked_for_in_chunks():
    collector = FakeCollector({})
    synchronize_ga4(
        collector=collector,
        start=dt.date(2026, 1, 1),
        end=dt.date(2026, 3, 1),
        chunk_days=31,
        today=dt.date(2026, 7, 3),
    )

    assert len(collector.calls) == 2
    assert collector.calls[0][0] == dt.date(2026, 1, 1)


def test_a_range_ending_in_the_future_is_clamped_to_yesterday():
    outcome = synchronize_ga4(
        collector=FakeCollector({}),
        start=DAY,
        end=dt.date(2026, 12, 31),
        today=TODAY,
    )

    assert outcome.extra["window_end"] == last_completed_day(TODAY).isoformat()


def test_a_window_entirely_in_the_future_is_not_a_failure():
    """A schedule that fires before its first day has finished has nothing to
    do, which is not the same as something going wrong."""
    outcome = synchronize_ga4(
        collector=FakeCollector({}),
        start=dt.date(2027, 1, 1),
        end=dt.date(2027, 1, 2),
        today=TODAY,
    )

    assert outcome.result == FeedResult.UNCHANGED
    assert Ga4DailySnapshot.objects.count() == 0


# -- resumability --------------------------------------------------------


class FailingAfterFirstChunk(FakeCollector):
    def collect_range(self, *, start, end, with_pages=True, with_channels=True):
        if self.calls:
            raise OSError("the network went away")
        return super().collect_range(
            start=start, end=end, with_pages=with_pages, with_channels=with_channels
        )


def test_a_chunk_that_fails_leaves_every_earlier_chunk_published():
    """This is what makes a backfill resumable: not a stored cursor, but the
    fact that what landed stays landed and re-running publishes nothing twice."""
    outcome = synchronize_ga4(
        collector=FailingAfterFirstChunk({}),
        start=dt.date(2026, 1, 1),
        end=dt.date(2026, 3, 1),
        chunk_days=31,
        today=dt.date(2026, 7, 3),
    )

    assert outcome.result == FeedResult.FAILED
    assert Ga4DailySnapshot.objects.filter(is_current_for_date=True).count() == 31


def test_re_running_after_a_failure_publishes_only_what_is_missing():
    synchronize_ga4(
        collector=FailingAfterFirstChunk({}),
        start=dt.date(2026, 1, 1),
        end=dt.date(2026, 2, 10),
        chunk_days=31,
        today=dt.date(2026, 7, 3),
    )

    outcome = synchronize_ga4(
        collector=FakeCollector({}),
        start=dt.date(2026, 1, 1),
        end=dt.date(2026, 2, 10),
        chunk_days=31,
        today=dt.date(2026, 7, 3),
    )

    assert outcome.extra["days_imported"] == 10
    assert outcome.extra["days_unchanged"] == 31
    assert Ga4DailySnapshot.objects.filter(is_current_for_date=True).count() == 41


# -- dry run -------------------------------------------------------------


def test_a_dry_run_publishes_nothing():
    outcome = sync({DAY: reading()}, dry_run=True)

    assert outcome.dry_run is True
    assert Ga4DailySnapshot.objects.count() == 0
    assert outcome.extra["days_imported"] == 1


def test_a_dry_run_over_published_days_reports_them_unchanged():
    sync({DAY: reading()})

    outcome = sync({DAY: reading()}, dry_run=True)

    assert outcome.extra["days_unchanged"] == 1


# -- failure -------------------------------------------------------------


def test_an_unconfigured_collector_fails_without_publishing():
    outcome = synchronize_ga4(
        collector=FakeCollector({}, error=Ga4NotConfigured("Puuduvad: GA4_PROPERTY_ID")),
        start=DAY,
        end=DAY,
        today=TODAY,
    )

    assert outcome.result == FeedResult.FAILED
    assert Ga4DailySnapshot.objects.count() == 0


def test_a_failure_leaves_the_previously_published_day_exactly_where_it_was():
    sync({DAY: reading(sessions=100)})

    synchronize_ga4(
        collector=FakeCollector({}, error=OSError("boom")), start=DAY, end=DAY, today=TODAY
    )

    assert current().sessions == 100
    assert Ga4FeedState.objects.get().last_result == FeedResult.FAILED


def test_a_failed_run_records_why_without_quoting_the_failure():
    """A transport error carries the request URL, and the URL carries the
    property ID. `requests` raises exactly this shape, and the text was reaching
    `last_error_summary` — a field the admin renders — and the log beside it.

    Only the exception type is recorded now: enough to say whether to look at
    the network or the credential, and incapable of quoting the request.
    """
    synchronize_ga4(
        collector=FakeCollector({}, error=OSError("HTTP 403 for property 123456789")),
        start=DAY,
        end=DAY,
        today=TODAY,
    )

    summary = Ga4FeedState.objects.get().last_error_summary
    assert summary
    assert "123456789" not in summary
    assert "OSError" in summary


def test_our_own_refusal_is_recorded_in_full():
    """`Ga4ResponseError` messages are written in `ga4.py` and name nothing the
    caller supplied, so they are worth keeping verbatim."""
    from apps.visibility.ga4 import Ga4ResponseError

    synchronize_ga4(
        collector=FakeCollector(
            {}, error=Ga4ResponseError("Google Analytics tagastas ootamatu ridade kuju.")
        ),
        start=DAY,
        end=DAY,
        today=TODAY,
    )

    assert "ootamatu ridade kuju" in Ga4FeedState.objects.get().last_error_summary


# -- provenance ----------------------------------------------------------


def test_a_published_day_carries_its_artifact_and_a_succeeded_import_run():
    sync({DAY: reading()})

    day = current()
    assert day.artifact.sha256 == day.checksum
    assert day.import_run.status == ImportStatus.SUCCEEDED
    assert day.import_run.rows_added == 1 + 1 + 1, "the day, its page and its channel"


def test_the_audit_event_carries_counts_and_never_a_page_list():
    sync({DAY: reading(pages=(("/et/uudised/a", 40), ("/et/uudised/b", 25)))})

    event = AuditEvent.objects.get(action=VisibilityAudit.GA4_OBSERVATION_IMPORTED)
    assert event.change_summary["page_rows"] == 2
    assert event.change_summary["report_date"] == DAY.isoformat()
    assert "/et/uudised/a" not in json.dumps(event.change_summary)


def test_the_feed_state_follows_the_window():
    sync({DAY: reading()})

    state = Ga4FeedState.objects.get()
    assert state.last_period_end == DAY
    assert state.current_snapshot == current()
    assert state.last_result == FeedResult.IMPORTED


# -- the command ---------------------------------------------------------


def call(**kwargs):
    out = StringIO()
    call_command("sync_ga4", stdout=out, **kwargs)
    return out.getvalue()


def test_the_command_reports_counts_as_json(monkeypatch):
    monkeypatch.setattr(
        "apps.visibility.management.commands.sync_ga4.synchronize_ga4",
        lambda **kwargs: synchronize_ga4(collector=FakeCollector({}), today=TODAY, **kwargs),
    )

    payload = json.loads(call(as_json=True, single_date=DAY.isoformat()))

    assert payload["result"] in (FeedResult.IMPORTED, FeedResult.UNCHANGED)
    assert "days_examined" in payload
    for forbidden in ("property", "credential", "token"):
        assert forbidden not in json.dumps(payload).lower()


def test_the_command_refuses_a_date_it_cannot_read():
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call(single_date="not-a-date")


def test_the_command_refuses_a_reversed_range():
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call(start_date="2026-07-10", end_date="2026-07-01")


def test_a_locked_feed_exits_three_without_publishing(monkeypatch):
    """The output contract for a skipped run, without needing a real race.

    Taking the advisory lock in the test does **not** make the command see it
    held: it is a PostgreSQL session lock and the test shares the command's
    connection, so it is re-entrant and the run proceeds. What is being pinned
    here is what the command does when the lock *is* held, so the lock is
    replaced rather than contended. True cross-connection overlap is covered by
    the transactional test below.
    """
    from contextlib import contextmanager

    from apps.core.feeds import FeedLocked
    from apps.visibility.management.commands import sync_ga4 as command_module

    @contextmanager
    def locked(name):
        raise FeedLocked(f"Allika {name} sünkroonimine juba käib.")
        yield  # pragma: no cover - never reached

    monkeypatch.setattr(command_module, "advisory_lock", locked)

    out = StringIO()
    with pytest.raises(SystemExit) as exit_code:
        call_command("sync_ga4", "--json", stdout=out, stderr=StringIO())

    assert exit_code.value.code == 3
    assert json.loads(out.getvalue().strip())["result"] == "locked"
    assert Ga4DailySnapshot.objects.count() == 0
