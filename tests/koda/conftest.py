"""Synthetic collectors shared by the public-feed synchronisation tests.

No test in this package performs any HTTP. Each fake stands in for a domain
collector and returns the same dataclass the real one does.
"""

from __future__ import annotations

import datetime as dt
import hashlib

import pytest

from apps.core.canonical import canonical_checksum
from apps.events.collector import EventCollection, EventEntry
from apps.membership.collector import MembershipCollection
from apps.membership.directory_collector import DirectoryCollection, DirectoryRow
from apps.news.collector import NewsCollection, NewsEntry

TOMORROW = dt.date.today() + dt.timedelta(days=1)
NEXT_WEEK = dt.date.today() + dt.timedelta(days=7)


def membership_collection(total: int = 3000) -> MembershipCollection:
    canonical = {"dataset": "koda-public-members", "schema_version": "1.0", "total_members": total}
    checksum, size = canonical_checksum(canonical)
    return MembershipCollection(
        total_members=total,
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        etag=f'"synthetic-{total}"',
        last_modified="Thu, 30 Jul 2026 03:00:00 GMT",
        duplicate_identities=0,
        rejected_rows=0,
    )


def directory_collection(count: int = 3) -> DirectoryCollection:
    """The row-level half of the same company list.

    A separate source from the count above, deliberately: they read the same
    endpoint and neither may be able to fail the other.
    """
    rows = tuple(
        DirectoryRow(registry_code=f"9990000{index}", profile_path=f"/et/liige/synthetic-{index}")
        for index in range(count)
    )
    canonical = {
        "dataset": "koda-member-directory",
        "schema_version": "1.0",
        "entries": [[row.registry_code, row.profile_path] for row in rows],
    }
    checksum, size = canonical_checksum(canonical)
    return DirectoryCollection(
        rows=rows,
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        etag=f'"synthetic-directory-{count}"',
        last_modified="Thu, 30 Jul 2026 03:00:00 GMT",
        duplicate_identities=0,
        rejected_rows=0,
    )


def news_entry(index: int) -> NewsEntry:
    return NewsEntry(
        guid=f"synthetic-news-{index}",
        title=f"Sünteetiline uudis {index}",
        canonical_url=f"https://www.koda.ee/et/uudised/synthetic-{index}",
        published_at=dt.datetime(2026, 7, 20, 9, 0, tzinfo=dt.UTC) + dt.timedelta(days=index),
        category="",
        summary=f"Sünteetiline kokkuvõte {index}.",
        source_order=index,
    )


def news_collection(count: int = 3) -> NewsCollection:
    entries = tuple(news_entry(i) for i in range(count))
    canonical = {
        "dataset": "koda-public-news",
        "schema_version": "1.0",
        "items": [
            {
                "guid": e.guid,
                "title": e.title,
                "url": e.canonical_url,
                "published_at": e.published_at,
                "category": e.category,
                "summary": e.summary,
            }
            for e in entries
        ],
    }
    checksum, size = canonical_checksum(canonical)
    return NewsCollection(
        entries=entries,
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        etag=f'"synthetic-news-{count}"',
        last_modified="Thu, 30 Jul 2026 09:00:00 GMT",
    )


def event_entry(index: int, *, starts_on=None, exact=False) -> EventEntry:
    starts_on = starts_on or (TOMORROW + dt.timedelta(days=index))
    starts_at = (
        dt.datetime.combine(starts_on, dt.time(14, 30)).replace(
            tzinfo=dt.timezone(dt.timedelta(hours=3))
        )
        if exact
        else None
    )
    return EventEntry(
        stable_key=f"synthetic-event-{index}",
        title=f"Sünteetiline sündmus {index}",
        canonical_url=f"https://www.koda.ee/et/sundmused/synthetic-{index}",
        category="Koolitused",
        summary="",
        starts_on=starts_on,
        ends_on=None,
        starts_at=starts_at,
        ends_at=None,
        location="Sünteetiline saal",
        source_order=index,
    )


def event_collection(count: int = 3, *, exact=False) -> EventCollection:
    entries = tuple(event_entry(i, exact=exact) for i in range(count))
    canonical = {
        "dataset": "koda-public-events",
        "schema_version": "1.0",
        "items": [
            {
                "key": e.stable_key,
                "title": e.title,
                "url": e.canonical_url,
                "category": e.category,
                "starts_on": e.starts_on,
                "ends_on": e.ends_on,
                "starts_at": e.starts_at,
                "ends_at": e.ends_at,
                "location": e.location,
            }
            for e in entries
        ],
    }
    checksum, size = canonical_checksum(canonical)
    return EventCollection(
        entries=entries,
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        pages_fetched=1,
        details_fetched=count,
        skipped_non_events=0,
        skipped_past=0,
    )


def collector_returning(value):
    """A collector that returns a prepared collection and records its calls."""

    def collect(**kwargs):
        collect.calls += 1
        collect.kwargs.append(kwargs)
        return value

    collect.calls = 0
    collect.kwargs = []
    return collect


def collector_raising(error):
    def collect(**kwargs):
        raise error

    return collect


@pytest.fixture
def membership_collector():
    return lambda total=3000: collector_returning(membership_collection(total))


@pytest.fixture
def news_collector():
    return lambda count=3: collector_returning(news_collection(count))


@pytest.fixture
def events_collector():
    return lambda count=3, exact=False: collector_returning(event_collection(count, exact=exact))


def checksum_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
