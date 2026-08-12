"""Synthetic news feed, published through the real RSS synchronisation.

`synchronize_news` catalogues everything it publishes, so these articles keep
their titles after they leave the feed — which is what lets an analytics row
resolve to a name rather than a path.
"""

from __future__ import annotations

import datetime as dt

from apps.core.e2e_seed import LONG_CATEGORY, LONG_TITLE

#: How many synthetic articles the feed publishes. More than one archive page,
#: so `/uudised/` can be tested with a pager on screen.
NEWS_ARTICLES = 40


def seed(today: dt.date) -> str:
    from apps.core.canonical import canonical_checksum
    from apps.news.collector import NewsCollection, NewsEntry
    from apps.news.sync import synchronize_news

    midnight = dt.datetime.combine(today, dt.time(9, 0), tzinfo=dt.UTC)
    entries: list[NewsEntry] = []
    # Deep enough that the news archive has more than one page of thirty, which
    # is the only way a browser test can prove the pager works at all. They are
    # dated a day apart, so every period preset selects a different slice.
    for index in range(1, NEWS_ARTICLES + 1):
        entries.append(
            NewsEntry(
                guid=f"seed-news-{index}",
                title=LONG_TITLE if index == 1 else f"Sünteetiline uudise pealkiri {index}",
                canonical_url=f"https://www.koda.ee/et/uudised/sunteetiline-{index}",
                published_at=midnight - dt.timedelta(days=index),
                category=LONG_CATEGORY if index % 4 == 0 else "Sünteetiline rubriik",
                summary=(
                    "Sünteetiline kokkuvõte, mis on piisavalt pikk, et kontrollida "
                    "teksti murdmist ja kärpimist kaardi laiuses."
                ),
                source_order=index - 1,
            )
        )
    canonical = {
        "dataset": "koda-public-news",
        "schema_version": "1.0",
        "items": [
            {
                "guid": entry.guid,
                "title": entry.title,
                "url": entry.canonical_url,
                "published_at": entry.published_at,
                "category": entry.category,
                "summary": entry.summary,
            }
            for entry in entries
        ],
    }
    checksum, size = canonical_checksum(canonical)
    collection = NewsCollection(
        entries=tuple(entries),
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        etag="",
        last_modified="",
    )
    outcome = synchronize_news(collector=lambda **_kwargs: collection)

    # Whose news each one is. Koda.ee stores this per node and nothing public
    # exposes it, so DashKoda learns it by import — which is exactly what this
    # does, through the same service the real import uses. Every third article
    # is a partner's, so the archive's category chips have both kinds to show
    # and neither is empty.
    from apps.news.catalogue import record_categories
    from apps.news.categories import NewsCategory

    record_categories(
        (
            entry.canonical_url,
            NewsCategory.PARTNER if index % 3 == 0 else NewsCategory.CHAMBER,
        )
        for index, entry in enumerate(entries)
    )
    return f"uudised: {outcome.result} ({len(entries)} uudist)"
