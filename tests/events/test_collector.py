"""The events crawl: structured data first, no invented times, no category pages.

Every page here is synthetic HTML. No live event page is committed.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from apps.events.collector import EventCollectionError, collect_events

TOMORROW = dt.date.today() + dt.timedelta(days=1)
NEXT_WEEK = dt.date.today() + dt.timedelta(days=7)
YESTERDAY = dt.date.today() - dt.timedelta(days=1)
TODAY = dt.date.today()


def teaser(slug: str, title: str, category: str = "Koolitused") -> str:
    """A teaser card shaped like the real one.

    The category link comes **before** the title link and carries no
    distinguishing class, exactly as the live site publishes it. Taking "the
    first `/et/sundmused/` link in the card" therefore yields the category
    listing rather than the event — which is precisely the defect this fixture
    exists to catch.
    """
    return f"""
    <div class="event--teaser node node--type-event">
      <div class="event--teaser--group-left">
        <div class="event--teaser--date">{TOMORROW:%d.%m.%Y}</div>
      </div>
      <div class="event--teaser--group-right">
        <a href="/et/sundmused/{category.lower()}" hreflang="et">{category}</a>
        <h2 class="event--teaser--title dont-break-out">
          <a href="/et/sundmused/{slug}" hreflang="et">{title}</a>
        </h2>
        <div class="event--teaser--group-footer">
          <a href="/et/sundmused/{slug}"
             class="event--teaser--group-footer--read-more">Loe edasi</a>
        </div>
      </div>
    </div>
    """


def listing(*slugs: str, extra: str = "", next_page: bool = False) -> str:
    cards = "".join(teaser(slug, f"Sünteetiline sündmus {slug}") for slug in slugs)
    pager = '<a href="?page=1">Järgmine</a>' if next_page else ""
    return f"""<!doctype html><html><body>
      <a href="/et/sundmused/koolitused">Koolitused</a>
      <div class="event--calendar-widget">
        <a href="/et/sundmused/liikmeuritused">Liikmeüritused</a>
      </div>
      {cards}{extra}{pager}
    </body></html>"""


def detail(
    *,
    name="Sünteetiline sündmus",
    start=None,
    end=None,
    location="Sünteetiline saal",
    with_json_ld=True,
    fallback_date=None,
) -> str:
    start = start if start is not None else TOMORROW.isoformat()
    event = {
        "@type": "Event",
        "name": name,
        "startDate": start,
        "location": {"@type": "Place", "name": location},
    }
    if end is not None:
        event["endDate"] = end
    graph = (
        json.dumps({"@context": "https://schema.org", "@graph": [event]}) if with_json_ld else ""
    )
    script = f'<script type="application/ld+json">{graph}</script>' if with_json_ld else ""
    fallback = (
        f'<div class="event--default--date">{fallback_date}</div>'
        f'<div class="event--default--location">{location}</div>'
        if fallback_date
        else ""
    )
    return f"<!doctype html><html><head>{script}</head><body>{fallback}</body></html>"


def category_page() -> str:
    """A category listing: no Event JSON-LD, no fallback date field."""
    graph = json.dumps({"@context": "https://schema.org", "@graph": [{"@type": "CollectionPage"}]})
    return (
        "<!doctype html><html><head>"
        f'<script type="application/ld+json">{graph}</script>'
        "</head><body></body></html>"
    )


class FakeSite:
    """Serves synthetic pages by path and records what was requested."""

    def __init__(self, pages: dict[str, str], *, errors: dict[str, Exception] | None = None):
        self.pages = pages
        self.errors = errors or {}
        self.requested: list[str] = []

    def __call__(self, url, **kwargs):
        from urllib.parse import urlparse

        from apps.core.public_http import FetchResult

        parsed = urlparse(url)
        key = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        self.requested.append(key)
        if key in self.errors:
            raise self.errors[key]
        if key not in self.pages:
            from apps.core.public_http import PublicFetchError

            raise PublicFetchError("Allikat ei leitud (404).")
        return FetchResult(
            status_code=200,
            content=self.pages[key].encode("utf-8"),
            content_type="text/html",
            etag="",
            last_modified="",
            final_host="www.koda.ee",
        )


@pytest.fixture
def patch_fetch(monkeypatch):
    def apply(site):
        monkeypatch.setattr("apps.events.collector.fetch", site)
        return site

    return apply


# -- valid crawls -------------------------------------------------------


def test_a_valid_listing_yields_upcoming_events(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("alpha", "beta"),
                "/et/sundmused/alpha": detail(name="Alfa"),
                "/et/sundmused/beta": detail(name="Beeta", start=NEXT_WEEK.isoformat()),
            }
        )
    )

    collection = collect_events()

    assert [entry.title for entry in collection.entries] == ["Alfa", "Beeta"]
    assert collection.entries[0].starts_on == TOMORROW
    assert len(collection.sha256) == 64


def test_a_date_only_event_receives_no_invented_time(patch_fetch):
    patch_fetch(FakeSite({"/et/sundmused": listing("alpha"), "/et/sundmused/alpha": detail()}))

    entry = collect_events().entries[0]

    assert entry.starts_on == TOMORROW
    assert entry.starts_at is None
    assert entry.ends_at is None


def test_an_exact_timestamp_is_parsed_from_structured_data(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("alpha"),
                "/et/sundmused/alpha": detail(start=f"{TOMORROW.isoformat()}T14:30:00+03:00"),
            }
        )
    )

    entry = collect_events().entries[0]

    assert entry.starts_at is not None
    assert entry.starts_at.hour == 14
    assert entry.starts_on == TOMORROW


def test_the_location_is_taken_from_structured_data(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("alpha"),
                "/et/sundmused/alpha": detail(location="Sünteetiline maja"),
            }
        )
    )

    assert collect_events().entries[0].location == "Sünteetiline maja"


def test_the_class_based_date_is_the_documented_fallback(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("alpha"),
                "/et/sundmused/alpha": detail(
                    with_json_ld=False, fallback_date=f"{TOMORROW:%d.%m.%Y}"
                ),
            }
        )
    )

    entry = collect_events().entries[0]

    assert entry.starts_on == TOMORROW
    assert entry.starts_at is None


# -- the two live traps -------------------------------------------------


def test_the_event_link_is_taken_from_the_title_not_the_category(patch_fetch):
    """Every card links to its category before it links to the event.

    Regression: taking the first link in the card collected category listings,
    which then failed the Event check, and the whole calendar came back empty.
    """
    site = patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("real-event"),
                "/et/sundmused/real-event": detail(name="Päris sündmus"),
                "/et/sundmused/koolitused": category_page(),
            }
        )
    )

    collection = collect_events()

    assert [e.canonical_url for e in collection.entries] == [
        "https://www.koda.ee/et/sundmused/real-event"
    ]
    assert collection.entries[0].category == "Koolitused"
    assert "/et/sundmused/koolitused" not in site.requested


def test_category_pages_sharing_the_url_prefix_are_not_events(patch_fetch):
    """`/et/sundmused/koolitused` is a category listing, not an event."""
    site = patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("alpha"),
                "/et/sundmused/alpha": detail(),
                "/et/sundmused/koolitused": category_page(),
                "/et/sundmused/liikmeuritused": category_page(),
            }
        )
    )

    collection = collect_events()

    assert len(collection.entries) == 1
    # The sidebar and standalone category links are never even fetched.
    assert "/et/sundmused/liikmeuritused" not in site.requested


def test_a_page_without_event_markup_is_skipped(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("alpha", "notanevent"),
                "/et/sundmused/alpha": detail(),
                "/et/sundmused/notanevent": category_page(),
            }
        )
    )

    collection = collect_events()

    assert len(collection.entries) == 1
    assert collection.skipped_non_events == 1


# -- filtering and ordering --------------------------------------------


def test_past_events_are_excluded(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("past", "future"),
                "/et/sundmused/past": detail(start=YESTERDAY.isoformat()),
                "/et/sundmused/future": detail(start=TOMORROW.isoformat()),
            }
        )
    )

    collection = collect_events()

    assert len(collection.entries) == 1
    assert collection.skipped_past == 1


def test_an_event_ending_today_is_retained(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("today"),
                "/et/sundmused/today": detail(start=TODAY.isoformat()),
            }
        )
    )

    assert len(collect_events().entries) == 1


def test_a_multi_day_event_still_running_is_retained(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("running"),
                "/et/sundmused/running": detail(
                    start=YESTERDAY.isoformat(), end=TOMORROW.isoformat()
                ),
            }
        )
    )

    entries = collect_events().entries

    assert len(entries) == 1
    assert entries[0].ends_on == TOMORROW


def test_events_are_ordered_chronologically(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("later", "sooner"),
                "/et/sundmused/later": detail(name="Hiljem", start=NEXT_WEEK.isoformat()),
                "/et/sundmused/sooner": detail(name="Varem", start=TOMORROW.isoformat()),
            }
        )
    )

    assert [entry.title for entry in collect_events().entries] == ["Varem", "Hiljem"]
    assert [entry.source_order for entry in collect_events().entries] == [0, 1]


def test_an_end_before_start_is_refused(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("broken"),
                "/et/sundmused/broken": detail(
                    start=NEXT_WEEK.isoformat(), end=TOMORROW.isoformat()
                ),
            }
        )
    )

    with pytest.raises(EventCollectionError, match="lõpp on enne algust"):
        collect_events()


def test_duplicate_links_are_collected_once(patch_fetch):
    duplicated = listing("alpha") + teaser("alpha", "Sünteetiline sündmus alpha")
    patch_fetch(FakeSite({"/et/sundmused": duplicated, "/et/sundmused/alpha": detail()}))

    assert len(collect_events().entries) == 1


# -- pagination and failure policy -------------------------------------


def test_pagination_is_followed_until_the_target_is_met(patch_fetch, settings):
    settings.KODA_EVENTS_TARGET_ITEMS = 3
    site = patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("a", "b", next_page=True),
                "/et/sundmused?page=1": listing("c", "d"),
                **{f"/et/sundmused/{s}": detail(name=s) for s in "abcd"},
            }
        )
    )

    collection = collect_events()

    assert collection.pages_fetched == 2
    assert len(collection.entries) == 4
    assert "/et/sundmused?page=1" in site.requested


def test_pagination_stops_when_no_next_page_exists(patch_fetch, settings):
    settings.KODA_EVENTS_TARGET_ITEMS = 50
    patch_fetch(FakeSite({"/et/sundmused": listing("a"), "/et/sundmused/a": detail(name="a")}))

    assert collect_events().pages_fetched == 1


def test_the_page_limit_is_respected(patch_fetch, settings):
    settings.KODA_EVENTS_TARGET_ITEMS = 500
    settings.KODA_EVENTS_MAX_PAGES = 2
    pages = {
        "/et/sundmused": listing("a", next_page=True),
        "/et/sundmused?page=1": listing("b", next_page=True),
        "/et/sundmused?page=2": listing("c", next_page=True),
    }
    pages.update({f"/et/sundmused/{s}": detail(name=s) for s in "abc"})
    patch_fetch(FakeSite(pages))

    assert collect_events().pages_fetched == 2


def test_an_unreachable_detail_page_skips_only_that_event(patch_fetch):
    from apps.core.public_http import PublicFetchError

    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("good", "broken"),
                "/et/sundmused/good": detail(name="Hea"),
                "/et/sundmused/broken": detail(name="Katki"),
            },
            errors={"/et/sundmused/broken": PublicFetchError("Allikas vastas koodiga 500.")},
        )
    )

    collection = collect_events()

    assert [entry.title for entry in collection.entries] == ["Hea"]


def test_an_empty_listing_is_refused(patch_fetch):
    patch_fetch(FakeSite({"/et/sundmused": "<html><body>Tühi</body></html>"}))

    with pytest.raises(EventCollectionError, match="sündmuskaarti"):
        collect_events()


def test_a_listing_with_only_past_events_is_refused(patch_fetch):
    patch_fetch(
        FakeSite(
            {
                "/et/sundmused": listing("past"),
                "/et/sundmused/past": detail(start=YESTERDAY.isoformat()),
            }
        )
    )

    with pytest.raises(EventCollectionError, match="tulevast sündmust"):
        collect_events()


def test_the_published_item_limit_is_applied(patch_fetch, settings):
    settings.KODA_EVENTS_MAX_ITEMS = 2
    settings.KODA_EVENTS_TARGET_ITEMS = 50
    slugs = ["a", "b", "c", "d"]
    pages = {"/et/sundmused": listing(*slugs)}
    pages.update({f"/et/sundmused/{s}": detail(name=s) for s in slugs})
    patch_fetch(FakeSite(pages))

    assert len(collect_events().entries) == 2


# -- checksum -----------------------------------------------------------


def test_identical_calendars_produce_the_same_checksum(patch_fetch):
    pages = {"/et/sundmused": listing("alpha"), "/et/sundmused/alpha": detail()}
    patch_fetch(FakeSite(pages))
    first = collect_events()
    patch_fetch(FakeSite(dict(pages)))
    second = collect_events()

    assert first.sha256 == second.sha256


def test_a_changed_calendar_changes_the_checksum(patch_fetch):
    patch_fetch(
        FakeSite({"/et/sundmused": listing("alpha"), "/et/sundmused/alpha": detail(name="Üks")})
    )
    first = collect_events()
    patch_fetch(
        FakeSite({"/et/sundmused": listing("alpha"), "/et/sundmused/alpha": detail(name="Kaks")})
    )
    second = collect_events()

    assert first.sha256 != second.sha256


def test_markup_churn_alone_does_not_change_the_checksum(patch_fetch):
    """A re-render with different whitespace must report unchanged."""
    patch_fetch(FakeSite({"/et/sundmused": listing("alpha"), "/et/sundmused/alpha": detail()}))
    first = collect_events()

    noisy = listing("alpha", extra="<!-- build 12345 --><div>   </div>")
    patch_fetch(FakeSite({"/et/sundmused": noisy, "/et/sundmused/alpha": detail()}))
    second = collect_events()

    assert first.sha256 == second.sha256
