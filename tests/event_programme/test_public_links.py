"""Which link an event actually shows, and what the page says about it.

Two rules carry this file. The workbook always wins over the matcher, and the
counts must answer the same question the table answers — a page reading
"0 linked" above a column full of links would be worse than no counts at all.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.event_programme.event_match_models import EventPublicMatch, EventPublicMatchSnapshot
from apps.event_programme.event_matching import MatchDecision
from apps.event_programme.models import EventProgrammeItem, EventProgrammeSnapshot
from apps.event_programme.public_links import (
    MATCHED,
    WORKBOOK,
    attach_public_links,
    matched_event_ids,
    matched_urls_by_event,
)
from apps.event_programme.selectors import count_linked_events, count_workbook_linked_events
from apps.events.public_models import DiscoveryOrigin, PublicEventResource

from .workbook_factory import synthetic_row

DAY = dt.date(2099, 6, 1)
WORKBOOK_URL = "https://www.koda.ee/et/sundmused/kasitsi-sisestatud"
MATCHED_URL = "https://www.koda.ee/et/sundmused/sobitatud"


@pytest.fixture
def resource(db):
    def build(url: str = MATCHED_URL, title: str = "Sobitatud leht") -> PublicEventResource:
        return PublicEventResource.objects.create(
            canonical_url=url,
            stable_key=url.rsplit("/", 1)[-1],
            title=title,
            starts_on=DAY,
            discovered_from=DiscoveryOrigin.SITEMAP,
            content_checksum="0" * 64,
            last_seen_at=timezone.now(),
        )

    return build


@pytest.fixture
def programme(publish_programme):
    def build(*rows):
        publish_programme(rows=list(rows))
        return EventProgrammeSnapshot.objects.get(is_current=True)

    return build


def row(index: int, *, public_url: str | None = None):
    return synthetic_row(
        event_id=f"EVENT-{9000 + index}",
        service_code=str(9000 + index),
        event_name=f"Sünteetiline sündmus {index}",
        start_date=dt.datetime.combine(DAY, dt.time(9, 0)),
        end_date=dt.datetime.combine(DAY, dt.time(17, 0)),
        source_row=2 + index,
        public_url=public_url,
        public_link_status="linked_embedded_latest" if public_url else "not_linked",
    )


def publish_match(snapshot, *, event_id: str, resource=None, decision=MatchDecision.MATCHED):
    match_snapshot = EventPublicMatchSnapshot.objects.filter(is_current=True).first()
    if match_snapshot is None:
        match_snapshot = EventPublicMatchSnapshot.objects.create(
            programme_snapshot=snapshot,
            resource_high_water=PublicEventResource.objects.count(),
            matcher_version="test-1.0",
            considered_count=99,
            is_current=True,
        )
    EventPublicMatch.objects.create(
        snapshot=match_snapshot, event_id=event_id, resource=resource, decision=decision
    )
    return match_snapshot


def linked(items):
    return {item.event_id: item.public_link for item in items}


# -- precedence ----------------------------------------------------------


def test_the_workbook_wins_over_a_match(db, programme, resource):
    """A person naming a page beats a score, even a perfect one."""
    snapshot = programme(row(0, public_url=WORKBOOK_URL))
    publish_match(snapshot, event_id="EVENT-9000", resource=resource())

    links = linked(attach_public_links(EventProgrammeItem.objects.filter(snapshot=snapshot)))

    assert links["EVENT-9000"].url == WORKBOOK_URL
    assert links["EVENT-9000"].source == WORKBOOK
    assert links["EVENT-9000"].is_matched is False


def test_a_match_fills_a_gap_the_workbook_left(db, programme, resource):
    snapshot = programme(row(0))
    publish_match(snapshot, event_id="EVENT-9000", resource=resource())

    links = linked(attach_public_links(EventProgrammeItem.objects.filter(snapshot=snapshot)))

    assert links["EVENT-9000"].url == MATCHED_URL
    assert links["EVENT-9000"].source == MATCHED
    assert links["EVENT-9000"].is_matched is True


def test_an_ambiguous_decision_shows_nothing(db, programme):
    """Declining is the point. A coin-toss link is worse than no link."""
    snapshot = programme(row(0))
    publish_match(snapshot, event_id="EVENT-9000", decision=MatchDecision.AMBIGUOUS)

    links = linked(attach_public_links(EventProgrammeItem.objects.filter(snapshot=snapshot)))

    assert not links["EVENT-9000"]
    assert links["EVENT-9000"].url == ""


def test_an_event_with_neither_shows_nothing(db, programme):
    snapshot = programme(row(0))

    links = linked(attach_public_links(EventProgrammeItem.objects.filter(snapshot=snapshot)))

    assert not links["EVENT-9000"]


def test_with_no_match_snapshot_the_workbook_links_still_work(db, programme):
    """The ordinary state before the matcher has ever run. Not an error."""
    snapshot = programme(row(0, public_url=WORKBOOK_URL), row(1))

    links = linked(attach_public_links(EventProgrammeItem.objects.filter(snapshot=snapshot)))

    assert links["EVENT-9000"].url == WORKBOOK_URL
    assert not links["EVENT-9001"]
    assert matched_urls_by_event() == {}
    assert matched_event_ids() == frozenset()


def test_only_the_current_match_snapshot_is_read(db, programme, resource):
    snapshot = programme(row(0))
    superseded = publish_match(snapshot, event_id="EVENT-9000", resource=resource())
    superseded.is_current = False
    superseded.save(update_fields=["is_current"])

    links = linked(attach_public_links(EventProgrammeItem.objects.filter(snapshot=snapshot)))

    assert not links["EVENT-9000"]


# -- the counts must agree with the table --------------------------------


def test_the_linked_count_includes_matched_events(db, programme, resource):
    snapshot = programme(row(0, public_url=WORKBOOK_URL), row(1), row(2))
    publish_match(snapshot, event_id="EVENT-9001", resource=resource())

    assert count_linked_events(snapshot) == 2
    assert count_workbook_linked_events(snapshot) == 1


def test_an_ambiguous_decision_is_not_counted_as_linked(db, programme):
    snapshot = programme(row(0), row(1))
    publish_match(snapshot, event_id="EVENT-9000", decision=MatchDecision.AMBIGUOUS)

    assert count_linked_events(snapshot) == 0


def test_the_workbook_count_is_unaffected_by_matching(db, programme, resource):
    """The Chamber's own coverage figure must not move because a matcher ran."""
    snapshot = programme(row(0, public_url=WORKBOOK_URL), row(1))
    before = count_workbook_linked_events(snapshot)

    publish_match(snapshot, event_id="EVENT-9001", resource=resource())

    assert count_workbook_linked_events(snapshot) == before


# -- the filter must select what the table shows -------------------------


def test_the_linked_filter_finds_a_matched_event(db, programme, resource):
    from apps.event_programme.page import parse_filters
    from apps.event_programme.selectors import (
        get_event_programme_filter_options,
        get_filtered_event_programme_items,
    )

    snapshot = programme(row(0, public_url=WORKBOOK_URL), row(1), row(2))
    publish_match(snapshot, event_id="EVENT-9001", resource=resource())
    options = get_event_programme_filter_options(snapshot)

    filters = parse_filters({"public_link": "linked"}, options)
    found = {
        item.event_id for item in get_filtered_event_programme_items(snapshot, filters=filters)
    }

    assert found == {"EVENT-9000", "EVENT-9001"}


def test_the_unlinked_filter_excludes_a_matched_event(db, programme, resource):
    from apps.event_programme.page import parse_filters
    from apps.event_programme.selectors import (
        get_event_programme_filter_options,
        get_filtered_event_programme_items,
    )

    snapshot = programme(row(0, public_url=WORKBOOK_URL), row(1), row(2))
    publish_match(snapshot, event_id="EVENT-9001", resource=resource())
    options = get_event_programme_filter_options(snapshot)

    filters = parse_filters({"public_link": "unlinked"}, options)
    found = {
        item.event_id for item in get_filtered_event_programme_items(snapshot, filters=filters)
    }

    assert found == {"EVENT-9002"}


# -- cost ----------------------------------------------------------------


def test_resolving_a_whole_page_costs_one_extra_query(
    db, programme, resource, django_assert_num_queries
):
    snapshot = programme(*[row(index) for index in range(6)])
    for index in range(6):
        publish_match(
            snapshot,
            event_id=f"EVENT-{9000 + index}",
            resource=resource(url=f"https://www.koda.ee/et/sundmused/leht-{index}"),
        )
    items = list(EventProgrammeItem.objects.filter(snapshot=snapshot))

    with django_assert_num_queries(2):
        attach_public_links(items)

    assert all(item.public_link for item in items)


def test_an_empty_page_costs_nothing(db, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert attach_public_links([]) == []
