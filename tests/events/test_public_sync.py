"""Recording a discovery run, and the command that drives it.

The crawl itself is covered in `test_public_discovery`. What matters here is
what happens around it: the snapshot written, the audit entry, the lock, and
what the command prints to a scheduler that only reads one line.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from django.core.management import call_command

from apps.audit.models import AuditEvent
from apps.core.feeds import FeedLocked
from apps.events.audit_actions import EventsAudit
from apps.events.management.commands import discover_koda_event_pages as command_module
from apps.events.public_discovery import WARN_DETAIL_CAP, DiscoveryTally
from apps.events.public_models import DiscoveryMode, PublicEventDiscoverySnapshot
from apps.events.public_sync import LOCKED_MESSAGE, discover_event_pages

COMMAND = "discover_koda_event_pages"


def fake_discovery(**tally_fields):
    """A stand-in crawl that records how it was called."""
    calls = []

    def run(*, mode, max_detail_pages, dry_run):
        calls.append({"mode": mode, "max_detail_pages": max_detail_pages, "dry_run": dry_run})
        return DiscoveryTally(mode=mode, **tally_fields)

    run.calls = calls
    return run


def test_a_run_is_recorded_as_the_current_snapshot(db):
    run = fake_discovery(urls_seen=1516, pages_fetched=40, created=38, unchanged=2)

    discover_event_pages(mode=DiscoveryMode.FULL, discover=run)

    snapshot = PublicEventDiscoverySnapshot.objects.get()
    assert snapshot.is_current is True
    assert snapshot.mode == DiscoveryMode.FULL
    assert snapshot.urls_seen == 1516
    assert snapshot.resources_created == 38
    assert snapshot.resources_unchanged == 2
    assert snapshot.is_complete is True


def test_a_later_run_takes_over_as_current(db):
    run = fake_discovery()

    discover_event_pages(discover=run)
    discover_event_pages(discover=run)

    assert PublicEventDiscoverySnapshot.objects.count() == 2
    assert PublicEventDiscoverySnapshot.objects.filter(is_current=True).count() == 1


def test_an_incomplete_run_is_recorded_as_incomplete(db):
    run = fake_discovery(created=150, is_complete=False, warnings=[WARN_DETAIL_CAP])

    discover_event_pages(discover=run)

    snapshot = PublicEventDiscoverySnapshot.objects.get()
    assert snapshot.is_complete is False
    assert snapshot.warning_codes == [WARN_DETAIL_CAP]


def test_the_audit_entry_carries_counts_and_no_addresses(db):
    run = fake_discovery(urls_seen=1516, created=38)

    discover_event_pages(discover=run)

    event = AuditEvent.objects.get(action=EventsAudit.EVENT_PAGES_DISCOVERED)
    assert event.change_summary["urls_seen"] == 1516
    assert event.change_summary["created"] == 38
    assert "koda.ee" not in json.dumps(event.change_summary)


def test_a_dry_run_records_nothing(db):
    run = fake_discovery(created=5)

    tally = discover_event_pages(dry_run=True, discover=run)

    assert tally.created == 5
    assert run.calls[0]["dry_run"] is True
    assert not PublicEventDiscoverySnapshot.objects.exists()
    assert not AuditEvent.objects.filter(action=EventsAudit.EVENT_PAGES_DISCOVERED).exists()


# -- the command ---------------------------------------------------------


@pytest.fixture
def patch_discovery(monkeypatch):
    def apply(run):
        monkeypatch.setattr("apps.events.public_sync.discover_public_events", run)
        return run

    return apply


def test_the_command_defaults_to_an_incremental_run(db, patch_discovery, capsys):
    run = patch_discovery(fake_discovery(urls_seen=1516, created=1))

    call_command(COMMAND)

    assert run.calls[0]["mode"] == DiscoveryMode.INCREMENTAL
    assert "Täielik: jah" in capsys.readouterr().out


def test_full_asks_for_a_full_run(db, patch_discovery):
    run = patch_discovery(fake_discovery())

    call_command(COMMAND, "--full")

    assert run.calls[0]["mode"] == DiscoveryMode.FULL


def test_the_budget_reaches_the_crawl(db, patch_discovery):
    run = patch_discovery(fake_discovery())

    call_command(COMMAND, "--max-detail-pages", "25")

    assert run.calls[0]["max_detail_pages"] == 25


def test_the_json_line_is_counts_only(db, patch_discovery, capsys):
    patch_discovery(fake_discovery(urls_seen=1516, pages_fetched=40, created=38))

    call_command(COMMAND, "--json")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["urls_seen"] == 1516
    assert payload["created"] == 38
    assert payload["is_complete"] is True
    assert set(payload) == {
        "mode",
        "pages_fetched",
        "urls_seen",
        "created",
        "updated",
        "unchanged",
        "errors",
        "is_complete",
        "warning_codes",
    }


def test_a_held_lock_stops_the_crawl_before_it_starts(db, patch_discovery, monkeypatch):
    """A contended run must not reach koda.ee at all.

    The lock is replaced rather than actually taken: `pg_try_advisory_lock` is
    session-level and re-entrant, so a test holding it on the same connection
    the command uses would be granted it a second time and prove nothing.

    The exit code and the JSON shape are the shared feed-command contract,
    covered for this command in `tests/core/test_feed_command_mechanics.py`.
    """
    run = patch_discovery(fake_discovery())

    @contextmanager
    def held(*args, **kwargs):
        raise FeedLocked(LOCKED_MESSAGE)
        yield  # pragma: no cover

    monkeypatch.setattr(command_module, "advisory_lock", held)

    with pytest.raises(SystemExit) as exit_info:
        call_command(COMMAND)

    assert exit_info.value.code == 3
    assert run.calls == []
    assert not PublicEventDiscoverySnapshot.objects.exists()


def test_an_unreadable_sitemap_fails_the_run_without_a_snapshot(db, patch_discovery):
    from apps.events.collector import EventCollectionError

    def broken(**kwargs):
        raise EventCollectionError("Saidikaarti ei õnnestunud lugeda.")

    patch_discovery(broken)

    with pytest.raises(SystemExit) as exit_info:
        call_command(COMMAND)

    assert exit_info.value.code == 1
    assert not PublicEventDiscoverySnapshot.objects.exists()
