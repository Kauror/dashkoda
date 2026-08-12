"""Synthetic public event calendar, published through its own synchronisation.

Supplementary to the event programme by design: this feed never supplies a
figure the programme owns.
"""

from __future__ import annotations

import datetime as dt

from apps.core.e2e_seed import LONG_CATEGORY, LONG_LOCATION, LONG_TITLE


def seed(today: dt.date) -> str:
    from apps.core.canonical import canonical_checksum
    from apps.events.collector import EventCollection, EventEntry
    from apps.events.sync import synchronize_events

    entries: list[EventEntry] = []

    def add(index: int, *, title: str, starts_on: dt.date, ends_on: dt.date | None = None) -> None:
        entries.append(
            EventEntry(
                stable_key=f"seed-event-{index}",
                title=title,
                canonical_url=f"https://www.koda.ee/et/sundmused/sunteetiline-{index}",
                category=LONG_CATEGORY if index % 3 == 0 else "Sünteetiline koolitus",
                summary="",
                starts_on=starts_on,
                ends_on=ends_on,
                starts_at=None,
                ends_at=None,
                location=LONG_LOCATION if index % 4 == 0 else "Sünteetiline saal",
                source_order=index,
            )
        )

    # The overflow candidate: a very long linked title carrying the visually
    # hidden "(koda.ee, avaneb uuel vahelehel)" suffix.
    add(1, title=LONG_TITLE, starts_on=today + dt.timedelta(days=2))
    # A multi-day range, so the date column renders two dates.
    add(
        2,
        title="Sünteetiline mitmepäevane sündmus",
        starts_on=today + dt.timedelta(days=5),
        ends_on=today + dt.timedelta(days=7),
    )
    # Month and year boundaries, where date formatting most often breaks.
    first_next_month = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    add(3, title="Sünteetiline kuupiiri sündmus", starts_on=first_next_month - dt.timedelta(days=1))
    add(4, title="Sünteetiline uue kuu sündmus", starts_on=first_next_month)
    add(5, title="Sünteetiline aastavahetuse sündmus", starts_on=dt.date(today.year, 12, 31))
    add(6, title="Sünteetiline uue aasta sündmus", starts_on=dt.date(today.year + 1, 1, 2))
    # Enough further events that the list scrolls.
    for index in range(7, 19):
        add(
            index, title=f"Sünteetiline sündmus {index}", starts_on=today + dt.timedelta(days=index)
        )

    entries.sort(key=lambda item: (item.starts_on, item.title, item.stable_key))
    entries = [
        EventEntry(**{**vars(entry), "source_order": position})
        for position, entry in enumerate(entries)
    ]
    canonical = {
        "dataset": "koda-public-events",
        "schema_version": "1.0",
        "items": [
            {
                "key": entry.stable_key,
                "title": entry.title,
                "url": entry.canonical_url,
                "category": entry.category,
                "starts_on": entry.starts_on,
                "ends_on": entry.ends_on,
                "starts_at": entry.starts_at,
                "ends_at": entry.ends_at,
                "location": entry.location,
            }
            for entry in entries
        ],
    }
    checksum, size = canonical_checksum(canonical)
    collection = EventCollection(
        entries=tuple(entries),
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        pages_fetched=1,
        details_fetched=len(entries),
        skipped_non_events=0,
        skipped_past=0,
    )
    outcome = synchronize_events(collector=lambda **_kwargs: collection)
    return f"sündmused: {outcome.result} ({len(entries)} sündmust)"
