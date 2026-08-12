"""Publishing what the matcher decided.

The decisions themselves are covered in `test_event_matching`. What matters here
is everything around them: the snapshot's inputs, all-or-nothing publication,
what the command prints, and the two rules the models exist to enforce — one
decision per event per run, and a page that may serve several events.
"""

from __future__ import annotations

import datetime as dt
import json
from contextlib import contextmanager

import pytest
from django.core.management import call_command
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.core.feeds import FeedLocked
from apps.event_programme.audit_actions import EventProgrammeAudit
from apps.event_programme.event_match_models import (
    EventPublicMatch,
    EventPublicMatchSnapshot,
)
from apps.event_programme.event_match_sync import EventMatchError, run_event_matching
from apps.event_programme.event_matching import MATCHER_VERSION, MatchDecision
from apps.event_programme.management.commands import (
    match_public_event_links as command_module,
)
from apps.event_programme.models import (
    EventProgrammeItem,
    EventProgrammeSnapshot,
    SnapshotImmutable,
)
from apps.events.public_models import DiscoveryOrigin, PublicEventResource

from .workbook_factory import synthetic_row

COMMAND = "match_public_event_links"


@pytest.fixture
def page(db):
    """Create a public event page whose date can be aimed at a programme item."""
    counter = {"n": 0}

    def build(title: str, starts_on: dt.date, **overrides) -> PublicEventResource:
        counter["n"] += 1
        slug = f"synteetiline-{counter['n']}"
        return PublicEventResource.objects.create(
            canonical_url=f"https://www.koda.ee/et/sundmused/{slug}",
            stable_key=slug,
            title=title,
            starts_on=starts_on,
            discovered_from=DiscoveryOrigin.SITEMAP,
            content_checksum=f"{counter['n']:064d}",
            last_seen_at=timezone.now(),
            **overrides,
        )

    return build


@pytest.fixture
def programme(publish_programme):
    """One published programme snapshot with two dated, named events."""

    def build(*names_and_dates):
        rows = [
            synthetic_row(
                event_id=f"EVENT-{9000 + index}",
                service_code=str(9000 + index),
                event_name=name,
                start_date=dt.datetime.combine(day, dt.time(9, 0)),
                end_date=dt.datetime.combine(day, dt.time(17, 0)),
                source_row=2 + index,
            )
            for index, (name, day) in enumerate(names_and_dates)
        ]
        publish_programme(rows=rows)
        return EventProgrammeSnapshot.objects.get(is_current=True)

    return build


DAY = dt.date(2099, 6, 1)


# -- what a run records --------------------------------------------------


def test_a_run_pins_its_programme_snapshot_and_its_page_high_water(db, programme, page):
    page("Arbitraažikohtu seminar", DAY)
    snapshot = programme(("Arbitraažikohtu seminar", DAY))
    high_water = PublicEventResource.objects.latest("id").id

    report = run_event_matching()

    published = EventPublicMatchSnapshot.objects.get()
    assert published.programme_snapshot_id == snapshot.pk
    assert published.resource_high_water == high_water
    assert published.matcher_version == MATCHER_VERSION
    assert published.is_current is True
    assert report.matched == 1


def test_a_page_added_after_the_mark_is_not_in_this_run(db, programme, page):
    """The high-water mark is what makes a run reconstructible."""
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))
    run_event_matching()

    first = EventPublicMatchSnapshot.objects.get()
    later = page("Arbitraažikohtu seminar", DAY)

    assert later.id > first.resource_high_water


def test_every_event_gets_exactly_one_decision(db, programme, page):
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY), ("Midagi hoopis muud", DAY))

    run_event_matching()

    snapshot = EventPublicMatchSnapshot.objects.get()
    decisions = EventPublicMatch.objects.filter(snapshot=snapshot)
    assert (
        decisions.count()
        == EventProgrammeItem.objects.filter(snapshot=snapshot.programme_snapshot).count()
    )
    assert len({row.event_id for row in decisions}) == decisions.count()


def test_the_counts_agree_with_the_rows(db, programme, page):
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY), ("Midagi hoopis muud", DAY))

    run_event_matching()

    snapshot = EventPublicMatchSnapshot.objects.get()
    rows = EventPublicMatch.objects.filter(snapshot=snapshot)
    assert snapshot.considered_count == rows.count()
    assert snapshot.matched_count == rows.filter(decision=MatchDecision.MATCHED).count()
    assert snapshot.ambiguous_count == rows.filter(decision=MatchDecision.AMBIGUOUS).count()
    assert snapshot.unmatched_count == rows.filter(decision=MatchDecision.UNMATCHED).count()


def test_a_later_run_takes_over_as_current(db, programme, page):
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))

    run_event_matching()
    page("Teine leht hoopis", DAY)
    run_event_matching()

    assert EventPublicMatchSnapshot.objects.count() == 2
    assert EventPublicMatchSnapshot.objects.filter(is_current=True).count() == 1


def test_the_programme_is_never_written_to(db, programme, page):
    """The whole product rule: Koda.ee supplies an address and nothing else."""
    page("Hoopis teine pealkiri", DAY)
    programme(("Arbitraažikohtu seminar", DAY))
    before = list(
        EventProgrammeItem.objects.values_list(
            "event_name", "start_date", "public_url", "event_type_label"
        )
    )

    run_event_matching()

    after = list(
        EventProgrammeItem.objects.values_list(
            "event_name", "start_date", "public_url", "event_type_label"
        )
    )
    assert after == before


def test_a_dry_run_publishes_nothing(db, programme, page):
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))

    report = run_event_matching(dry_run=True)

    assert report.matched == 1
    assert not EventPublicMatchSnapshot.objects.exists()
    assert not EventPublicMatch.objects.exists()


def test_matching_without_a_programme_is_refused(db, page):
    page("Arbitraažikohtu seminar", DAY)

    with pytest.raises(EventMatchError):
        run_event_matching()


def test_the_audit_entry_carries_counts_and_no_addresses(db, programme, page):
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))

    run_event_matching()

    event = AuditEvent.objects.get(action=EventProgrammeAudit.EVENT_PUBLIC_LINKS_MATCHED)
    assert event.change_summary["matched"] == 1
    assert "koda.ee" not in json.dumps(event.change_summary)


# -- the model rules -----------------------------------------------------


def test_one_page_may_serve_several_events(db, programme, page):
    """Recurring trainings share a page. The workbook already does this."""
    shared = page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))
    run_event_matching()
    snapshot = EventPublicMatchSnapshot.objects.get()

    EventPublicMatch.objects.create(
        snapshot=snapshot,
        event_id="EVENT-OTHER",
        resource=shared,
        decision=MatchDecision.MATCHED,
    )

    assert EventPublicMatch.objects.filter(resource=shared).count() == 2


def test_one_event_may_not_get_two_decisions_in_one_run(db, programme, page):
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))
    run_event_matching()
    snapshot = EventPublicMatchSnapshot.objects.get()
    existing = EventPublicMatch.objects.filter(snapshot=snapshot).first()

    with pytest.raises(IntegrityError):
        EventPublicMatch.objects.create(
            snapshot=snapshot, event_id=existing.event_id, decision=MatchDecision.UNMATCHED
        )


def test_an_unmatched_decision_cannot_name_a_page(db, programme, page):
    resource = page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))
    run_event_matching()
    snapshot = EventPublicMatchSnapshot.objects.get()

    with pytest.raises(IntegrityError):
        EventPublicMatch.objects.create(
            snapshot=snapshot,
            event_id="EVENT-INVENTED",
            resource=resource,
            decision=MatchDecision.UNMATCHED,
        )


def test_a_published_decision_cannot_be_edited(db, programme, page):
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))
    run_event_matching()
    row = EventPublicMatch.objects.first()

    row.decision = MatchDecision.UNMATCHED
    with pytest.raises(SnapshotImmutable):
        row.save()


def test_a_published_snapshot_only_stands_down(db, programme, page):
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))
    run_event_matching()
    snapshot = EventPublicMatchSnapshot.objects.get()

    snapshot.matched_count = 999
    with pytest.raises(SnapshotImmutable):
        snapshot.save(update_fields=["matched_count"])

    snapshot.refresh_from_db()
    snapshot.is_current = False
    snapshot.save(update_fields=["is_current"])


# -- the command ---------------------------------------------------------


def test_the_command_publishes_and_reports(db, programme, page, capsys):
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))

    call_command(COMMAND)

    assert EventPublicMatchSnapshot.objects.filter(is_current=True).exists()
    assert "Seotud: 1" in capsys.readouterr().out


def test_the_json_line_is_counts_only(db, programme, page, capsys):
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))

    call_command(COMMAND, "--json")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["matched"] == 1
    assert payload["matcher_version"] == MATCHER_VERSION
    assert "koda.ee" not in json.dumps(payload)


def test_a_held_lock_stops_the_run(db, programme, page, monkeypatch):
    page("Arbitraažikohtu seminar", DAY)
    programme(("Arbitraažikohtu seminar", DAY))

    @contextmanager
    def held(*args, **kwargs):
        raise FeedLocked("Sobitamine juba käib.")
        yield  # pragma: no cover

    monkeypatch.setattr(command_module, "advisory_lock", held)

    with pytest.raises(SystemExit) as exit_info:
        call_command(COMMAND)

    assert exit_info.value.code == 3
    assert not EventPublicMatchSnapshot.objects.exists()


def test_no_programme_fails_the_command(db, page):
    page("Arbitraažikohtu seminar", DAY)

    with pytest.raises(SystemExit) as exit_info:
        call_command(COMMAND)

    assert exit_info.value.code == 1
