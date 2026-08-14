"""Whose news an article is, and what the archive does with that.

Koda.ee stores `field_category` on every news node, and nothing public exposes
it — no marker on the article page, no `<category>` in the RSS, no JSON:API, no
category filter on the archive view. All four were checked before this was
built. So the value arrives by import, and the tests below are mostly about what
happens to an article DashKoda has *not* been told about: it must stay visible
under `Kõik` and must never be guessed into one of the two categories.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.news.archive import build_news_archive
from apps.news.catalogue import record_categories
from apps.news.categories import NewsCategory, parse_category
from apps.news.periods import SORT_VIEWS
from apps.news.public_models import NewsResource, TitleOrigin

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 8, 11)


def article(slug: str, *, category: str = "", days_ago: int = 1) -> NewsResource:
    return NewsResource.objects.create(
        canonical_url=f"https://www.koda.ee/et/uudised/{slug}",
        path=f"/et/uudised/{slug}",
        title=f"Uudis {slug}",
        published_at=timezone.make_aware(
            dt.datetime.combine(TODAY - dt.timedelta(days=days_ago), dt.time(9, 0))
        ),
        title_origin=TitleOrigin.FEED,
        category=category,
        last_seen_at=timezone.now(),
    )


def build(**kwargs):
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("period_key", "koik")
    return build_news_archive(**kwargs)


def paths(archive):
    return [row.url.replace("https://www.koda.ee", "") for row in archive.rows]


# -- the registry ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("meie_uudised", NewsCategory.CHAMBER),
        ("soprade_uudised", NewsCategory.PARTNER),
        ("  meie_uudised  ", NewsCategory.CHAMBER),
        # The listing names. `field_category` can hold them, but no article was
        # found stored under one, and they are views rather than categories.
        ("arhiiv", ""),
        ("artiklid", ""),
        ("ajakiri_teataja", ""),
        ("rss_voog", ""),
        ("", ""),
        (None, ""),
        ("something else", ""),
    ],
)
def test_only_the_two_real_categories_are_accepted(raw, expected):
    assert parse_category(raw) == expected


# -- filtering ---------------------------------------------------------------


def test_a_category_filter_selects_only_that_category():
    article("koja", category=NewsCategory.CHAMBER)
    article("sopra", category=NewsCategory.PARTNER)

    assert paths(build(category=NewsCategory.CHAMBER)) == ["/et/uudised/koja"]
    assert paths(build(category=NewsCategory.PARTNER)) == ["/et/uudised/sopra"]


def test_an_unclassified_article_is_in_neither_category_but_stays_in_everything():
    """The state that matters most. Nothing public exposes the category, so an
    article DashKoda has not been told about must not be guessed into one — and
    must not disappear from the archive either."""
    article("koja", category=NewsCategory.CHAMBER)
    article("teadmata")

    assert "/et/uudised/teadmata" not in paths(build(category=NewsCategory.CHAMBER))
    assert "/et/uudised/teadmata" not in paths(build(category=NewsCategory.PARTNER))
    assert "/et/uudised/teadmata" in paths(build())


def test_an_unreadable_category_shows_everything(viewer_client):
    article("koja", category=NewsCategory.CHAMBER)
    article("teadmata")

    response = viewer_client.get(
        reverse("news"), {"periood": "koik", "kategooria": "arhiiv", "fookus": "arhiiv"}
    )

    assert response.status_code == 200
    assert response.context["archive"].total == 2


def test_the_filter_composes_with_the_period_and_the_search():
    article("koja-hiljutine", category=NewsCategory.CHAMBER, days_ago=3)
    article("koja-vana", category=NewsCategory.CHAMBER, days_ago=300)
    article("sopra-hiljutine", category=NewsCategory.PARTNER, days_ago=3)

    narrowed = build(period_key="30", category=NewsCategory.CHAMBER)
    assert paths(narrowed) == ["/et/uudised/koja-hiljutine"]

    searched = build(category=NewsCategory.CHAMBER, search="vana")
    assert paths(searched) == ["/et/uudised/koja-vana"]


# -- the controls keep each other's state ------------------------------------


def test_every_control_carries_the_category():
    for index in range(35):
        article(f"lugu-{index:02d}", category=NewsCategory.PARTNER, days_ago=index)

    archive = build(category=NewsCategory.PARTNER, sort=SORT_VIEWS, search="lugu")

    assert "kategooria=soprade_uudised" in archive.next_query
    for option in archive.periods:
        assert "kategooria=soprade_uudised" in option.query
    for option in archive.sorts:
        assert "kategooria=soprade_uudised" in option.query


def test_changing_the_category_keeps_the_period_and_the_ordering():
    article("koja", category=NewsCategory.CHAMBER)

    archive = build(period_key="90", sort=SORT_VIEWS, category=NewsCategory.CHAMBER)
    other = next(o for o in archive.categories if o.key == NewsCategory.PARTNER)

    assert "periood=90" in other.query
    assert "sort=vaadatud" in other.query


def test_the_everything_chip_clears_the_filter():
    article("koja", category=NewsCategory.CHAMBER)

    everything = next(o for o in build(category=NewsCategory.CHAMBER).categories if o.key == "")

    assert "kategooria=" not in everything.query


# -- the import --------------------------------------------------------------


def test_the_import_stores_the_category_by_canonical_path():
    article("koja")
    article("sopra")

    updated, unchanged, unknown = record_categories(
        [
            ("https://www.koda.ee/et/uudised/koja", "meie_uudised"),
            ("/et/uudised/sopra", "soprade_uudised"),
        ]
    )

    assert (updated, unchanged, unknown) == (2, 0, 0)
    assert NewsResource.objects.get(path="/et/uudised/koja").category == NewsCategory.CHAMBER
    assert NewsResource.objects.get(path="/et/uudised/sopra").category == NewsCategory.PARTNER


def test_a_second_import_of_the_same_file_writes_nothing():
    article("koja")
    rows = [("/et/uudised/koja", "meie_uudised")]

    record_categories(rows)
    updated, unchanged, unknown = record_categories(rows)

    assert (updated, unchanged) == (0, 1)


def test_a_row_for_an_article_the_catalogue_does_not_hold_is_skipped():
    """A stale export must not be able to resurrect a removed article."""
    updated, unchanged, unknown = record_categories([("/et/uudised/ei-ole-olemas", "meie_uudised")])

    assert (updated, unchanged, unknown) == (0, 0, 1)
    assert not NewsResource.objects.exists()


def test_a_listing_name_is_not_stored_as_a_category():
    article("koja")

    updated, unchanged, unknown = record_categories([("/et/uudised/koja", "arhiiv")])

    assert (updated, unknown) == (0, 1)
    assert NewsResource.objects.get().category == ""


def test_a_dry_run_counts_without_writing():
    article("koja")

    updated, _unchanged, _unknown = record_categories(
        [("/et/uudised/koja", "meie_uudised")], dry_run=True
    )

    assert updated == 1
    assert NewsResource.objects.get().category == ""


def test_the_import_does_not_touch_identity():
    resource = article("koja")

    record_categories([("/et/uudised/koja", "meie_uudised")])

    resource.refresh_from_db()
    assert resource.path == "/et/uudised/koja"
    assert resource.canonical_url == "https://www.koda.ee/et/uudised/koja"
    assert resource.title == "Uudis koja"


# -- what the page says ------------------------------------------------------


def test_the_page_offers_all_three_chips(viewer_client):
    article("koja", category=NewsCategory.CHAMBER)

    page = viewer_client.get(
        reverse("news"), {"periood": "koik", "fookus": "arhiiv"}
    ).content.decode()

    assert "Koja uudised" in page
    assert "Sõprade uudised" in page
    assert "Kõik" in page


def test_the_page_says_how_many_are_unclassified(viewer_client):
    """Two chips that between them show less than the archive would otherwise
    read as a filter that loses articles."""
    article("koja", category=NewsCategory.CHAMBER)
    article("teadmata-1")
    article("teadmata-2")

    archive = viewer_client.get(reverse("news"), {"periood": "koik", "fookus": "arhiiv"}).context[
        "archive"
    ]

    assert archive.unclassified_count == 2


# -- the category and the live search, together ------------------------------
#
# The category chips shipped after live search did, and the fragment was not
# taught about them: it filtered on `Kõik` whatever the reader had chosen, while
# the chip above the results went on reading `Koja uudised`, and the URL it
# pushed dropped the filter so a reload lost it for good. Every other category
# test calls `build_news_archive` directly, so the view was the one layer none
# of them touched — the same shape of gap as the page search in #99.


def searched(viewer_client, **params):
    """The live-search fragment, answering what the form actually submits.

    The category travels as a hidden field in the search form, so it reaches the
    fragment exactly as `periood` and `sort` do.
    """
    return viewer_client.get(reverse("news-search"), {"periood": "koik", **params})


def test_live_search_filters_inside_the_chosen_category(viewer_client):
    article("koja-eelnou", category=NewsCategory.CHAMBER)
    article("sopra-eelnou", category=NewsCategory.PARTNER)
    article("teadmata-eelnou")

    response = searched(viewer_client, otsing="eelnou", kategooria=NewsCategory.CHAMBER)

    assert paths(response.context["archive"]) == ["/et/uudised/koja-eelnou"]


def test_live_search_under_koik_still_finds_the_unclassified(viewer_client):
    """The other half of the rule: `Kõik` is the absence of the filter, so a
    search under it must still reach an article DashKoda cannot place."""
    article("koja-eelnou", category=NewsCategory.CHAMBER)
    article("teadmata-eelnou")

    response = searched(viewer_client, otsing="eelnou")

    assert sorted(paths(response.context["archive"])) == [
        "/et/uudised/koja-eelnou",
        "/et/uudised/teadmata-eelnou",
    ]


def test_the_pushed_url_keeps_the_category(viewer_client):
    """Without this the address bar loses the chip on the first keystroke, and
    the next reload silently widens the archive to everything."""
    article("koja-eelnou", category=NewsCategory.CHAMBER)

    response = searched(viewer_client, otsing="eelnou", kategooria=NewsCategory.CHAMBER)

    assert f"kategooria={NewsCategory.CHAMBER}" in response.headers["HX-Push-Url"]


def test_the_pushed_url_leaves_koik_out_of_the_address_bar(viewer_client):
    """`Kõik` is the unfiltered page, and an unfiltered page keeps a clean URL."""
    article("koja-eelnou", category=NewsCategory.CHAMBER)

    response = searched(viewer_client, otsing="eelnou")

    assert "kategooria=" not in response.headers["HX-Push-Url"]


def test_an_unreadable_category_does_not_reach_the_address_bar(viewer_client):
    """The pushed value is the validated one, not the raw parameter: whatever a
    hand-typed URL carried, what comes back is a category or nothing."""
    article("koja-eelnou", category=NewsCategory.CHAMBER)

    response = searched(viewer_client, otsing="eelnou", kategooria="arhiiv")

    assert "kategooria=" not in response.headers["HX-Push-Url"]
    assert response.context["archive"].total == 1
