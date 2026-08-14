"""Synthetic news feed, published through the real RSS synchronisation.

`synchronize_news` catalogues everything it publishes, so these articles keep
their titles after they leave the feed — which is what lets an analytics row
resolve to a name rather than a path.

## Why there are articles the feed never carried

The feed is a rolling window and everything in it is recent, so a seed built
only from it can exercise none of the states the intelligence dashboard exists
to distinguish: an article older than GA4's coverage, one still being read years
after publication, or a publication history longer than one month.

`ARCHIVE_ARTICLES` are therefore catalogued directly, exactly as
`apps/news/discovery.py` records a page found on the public site rather than in
the feed. They are the states, one article each:

- **outside coverage** — published before measurement began, so it has a
  measured total and no first-week or first-month figure at all;
- **evergreen** — published over a year ago and still read this month, which is
  the whole point of `Jätkuvalt loetav` and is invisible to any ranking by date;
- **unclassified** — catalogued with no category, because nothing public on
  Koda.ee exposes one until the article has been read from the site. It must
  appear under `Kõik` and in neither category's figures;
- **older months** — so the publication cadence chart has more than one bar and
  the monthly grain has something to draw.
"""

from __future__ import annotations

import datetime as dt

from apps.core.e2e_seed import LONG_CATEGORY, LONG_TITLE

#: How many synthetic articles the feed publishes. More than one archive page,
#: so `/uudised/` can be tested with a pager on screen.
NEWS_ARTICLES = 40

#: Articles the feed no longer carries, as `(slug, days_before_today, category)`.
#: `None` is a real category state and not a placeholder — see the module note.
ARCHIVE_ARTICLES: tuple[tuple[str, int, str | None], ...] = (
    ("arhiiv-enne-mootmist", 400, "meie_uudised"),
    ("arhiiv-igihaljas", 380, "meie_uudised"),
    ("arhiiv-igihaljas-sopradelt", 300, "soprade_uudised"),
    ("arhiiv-liigita", 240, None),
    ("arhiiv-mullune", 180, "meie_uudised"),
    ("arhiiv-poolaasta", 120, "soprade_uudised"),
    ("arhiiv-kvartal", 95, "meie_uudised"),
)


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

    archived = _seed_archive(today)
    return f"uudised: {outcome.result} ({len(entries) + archived} uudist)"


def _seed_archive(today: dt.date) -> int:
    """Catalogue the articles the rolling feed no longer carries.

    Written straight to `NewsResource`, which is what `apps/news/discovery.py`
    does for a page found on the public site: the feed is ten items deep and
    these are the states a ten-item window cannot contain.

    The category is applied through `record_categories`, the same service the
    real classification uses, and the unclassified one is simply left out of it
    — an article DashKoda has not been told about is not a third category.
    """
    from django.utils import timezone

    from apps.news.catalogue import record_categories
    from apps.news.public_models import NewsResource, TitleOrigin

    now = timezone.now()
    created = 0
    for slug, days_ago, category in ARCHIVE_ARTICLES:
        url = f"https://www.koda.ee/et/uudised/{slug}"
        path = f"/et/uudised/{slug}"
        if NewsResource.objects.filter(path=path).exists():
            continue
        NewsResource.objects.create(
            canonical_url=url,
            path=path,
            title=f"Arhiivi uudis {slug.removeprefix('arhiiv-').replace('-', ' ')}",
            published_at=dt.datetime.combine(
                today - dt.timedelta(days=days_ago), dt.time(9, 0), tzinfo=dt.UTC
            ),
            # Read from the public site rather than from the feed, which is
            # exactly how these would really have been found.
            title_origin=TitleOrigin.PAGE,
            last_seen_at=now,
        )
        created += 1

    record_categories(
        (f"https://www.koda.ee/et/uudised/{slug}", category)
        for slug, _days, category in ARCHIVE_ARTICLES
        if category
    )
    return created
