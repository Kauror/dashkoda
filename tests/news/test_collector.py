"""The news RSS contract: validation, sanitisation and deterministic order.

Every feed here is synthetic. No live RSS document is committed.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.core.public_http import PublicFetchError
from apps.news.collector import NewsCollectionError, collect_news, parse_feed, to_plain_text

ITEM_TEMPLATE = """
  <item>
    <title>{title}</title>
    <link>{link}</link>
    <description>{description}</description>
    <pubDate>{published}</pubDate>
    <guid isPermaLink="false">{guid}</guid>
    {extra}
  </item>
"""


def feed(items: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <title>Sünteetiline uudisvoog</title>
  <link>https://www.koda.ee/et</link>
  {items}
</channel></rss>""".encode()


def item(
    *,
    guid="synthetic-1",
    title="Sünteetiline uudis",
    link="https://www.koda.ee/et/uudised/synthetic-1",
    description="Sünteetiline kokkuvõte.",
    published="Wed, 29 Jul 2026 09:00:00 +0000",
    extra="",
) -> str:
    return ITEM_TEMPLATE.format(
        title=title, link=link, description=description, published=published, guid=guid, extra=extra
    )


class FakeFetch:
    def __init__(self, content=b"", *, status=200, content_type="application/rss+xml", error=None):
        self.content = content
        self.status = status
        self.content_type = content_type
        self.error = error

    def __call__(self, url, **kwargs):
        if self.error is not None:
            raise self.error
        from apps.core.public_http import FetchResult

        return FetchResult(
            status_code=self.status,
            content=self.content,
            content_type=self.content_type,
            etag='"synthetic"',
            last_modified="Thu, 30 Jul 2026 09:00:00 GMT",
            final_host="www.koda.ee",
        )


@pytest.fixture
def patch_fetch(monkeypatch):
    def apply(fake):
        monkeypatch.setattr("apps.news.collector.fetch", fake)
        return fake

    return apply


# -- valid feeds --------------------------------------------------------


def test_a_valid_feed_is_parsed(patch_fetch):
    patch_fetch(FakeFetch(feed(item())))

    collection = collect_news()

    assert len(collection.entries) == 1
    entry = collection.entries[0]
    assert entry.guid == "synthetic-1"
    assert entry.title == "Sünteetiline uudis"
    assert entry.canonical_url.startswith("https://www.koda.ee/")
    assert entry.published_at.tzinfo is not None
    assert len(collection.sha256) == 64


def test_items_are_ordered_newest_first(patch_fetch):
    items = (
        item(
            guid="a",
            link="https://www.koda.ee/et/uudised/a",
            published="Mon, 27 Jul 2026 09:00:00 +0000",
        )
        + item(
            guid="c",
            link="https://www.koda.ee/et/uudised/c",
            published="Wed, 29 Jul 2026 09:00:00 +0000",
        )
        + item(
            guid="b",
            link="https://www.koda.ee/et/uudised/b",
            published="Tue, 28 Jul 2026 09:00:00 +0000",
        )
    )
    patch_fetch(FakeFetch(feed(items)))

    guids = [entry.guid for entry in collect_news().entries]

    assert guids == ["c", "b", "a"]


def test_a_category_is_preserved_when_the_feed_supplies_one(patch_fetch):
    patch_fetch(FakeFetch(feed(item(extra="<category>Meie uudised</category>"))))

    assert collect_news().entries[0].category == "Meie uudised"


def test_a_missing_category_stays_blank_and_is_never_invented(patch_fetch):
    patch_fetch(FakeFetch(feed(item())))

    assert collect_news().entries[0].category == ""


def test_the_checksum_is_stable_across_identical_feeds(patch_fetch):
    patch_fetch(FakeFetch(feed(item())))
    first = collect_news()
    patch_fetch(FakeFetch(feed(item())))
    second = collect_news()

    assert first.sha256 == second.sha256


def test_a_changed_feed_changes_the_checksum(patch_fetch):
    patch_fetch(FakeFetch(feed(item())))
    first = collect_news()
    patch_fetch(FakeFetch(feed(item(title="Teine pealkiri"))))
    second = collect_news()

    assert first.sha256 != second.sha256


# -- rejected feeds -----------------------------------------------------


def test_a_wrong_content_type_is_refused(patch_fetch):
    patch_fetch(FakeFetch(error=PublicFetchError("Ootamatu sisutüüp: text/html.")))

    with pytest.raises(NewsCollectionError, match="sisutüüp"):
        collect_news()


def test_malformed_xml_is_refused(patch_fetch):
    patch_fetch(FakeFetch(b"<rss><channel><item>"))

    with pytest.raises(NewsCollectionError, match="XML"):
        collect_news()


def test_an_empty_feed_is_refused(patch_fetch):
    patch_fetch(FakeFetch(feed("")))

    with pytest.raises(NewsCollectionError, match="ühtegi kirjet"):
        collect_news()


def test_a_missing_title_is_refused():
    with pytest.raises(NewsCollectionError, match="pealkiri"):
        parse_feed(feed(item(title="")))


def test_an_item_with_neither_guid_nor_link_is_refused():
    """The link is the documented fallback identity, so losing both is fatal."""
    with pytest.raises(NewsCollectionError, match="GUID"):
        parse_feed(feed(item(guid="", link="")))


def test_a_missing_guid_falls_back_to_the_link():
    entries = parse_feed(feed(item(guid="", link="https://www.koda.ee/et/uudised/only-link")))

    assert entries[0].guid == "https://www.koda.ee/et/uudised/only-link"


def test_a_duplicate_guid_is_refused():
    items = item(guid="same", link="https://www.koda.ee/et/uudised/a") + item(
        guid="same", link="https://www.koda.ee/et/uudised/b"
    )

    with pytest.raises(NewsCollectionError, match="kordub GUID"):
        parse_feed(feed(items))


def test_a_duplicate_canonical_url_is_refused():
    items = item(guid="a", link="https://www.koda.ee/et/uudised/same") + item(
        guid="b", link="https://www.koda.ee/et/uudised/same"
    )

    with pytest.raises(NewsCollectionError, match="kordub viide"):
        parse_feed(feed(items))


@pytest.mark.parametrize(
    "link",
    [
        "https://example.invalid/et/uudised/a",
        "http://www.koda.ee/et/uudised/a",
        "https://koda.ee.attacker.invalid/a",
    ],
)
def test_an_off_domain_or_insecure_link_is_refused(link):
    with pytest.raises(NewsCollectionError, match="koda.ee"):
        parse_feed(feed(item(link=link)))


def test_a_missing_publication_time_is_refused():
    with pytest.raises(NewsCollectionError, match="avaldamise aeg"):
        parse_feed(feed(item(published="")))


def test_an_unparsable_publication_time_is_refused():
    with pytest.raises(NewsCollectionError, match="avaldamise aeg"):
        parse_feed(feed(item(published="eile hommikul")))


def test_a_far_future_publication_time_is_refused():
    far_future = dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
    stamp = far_future.strftime("%a, %d %b %Y %H:%M:%S +0000")

    with pytest.raises(NewsCollectionError, match="tulevikus"):
        parse_feed(feed(item(published=stamp)))


def test_a_slightly_future_publication_time_is_tolerated():
    """Clock skew between the site and this server is not an error."""
    soon = dt.datetime.now(dt.UTC) + dt.timedelta(hours=6)
    stamp = soon.strftime("%a, %d %b %Y %H:%M:%S +0000")

    assert len(parse_feed(feed(item(published=stamp)))) == 1


# -- summary sanitisation ----------------------------------------------


def test_markup_is_reduced_to_plain_text():
    result = to_plain_text("<p>Esimene <strong>lõik</strong>.</p><p>Teine.</p>")

    assert result == "Esimene lõik. Teine."
    assert "<" not in result


def test_scripts_and_styles_are_removed_entirely():
    dangerous = "<script>alert('x')</script><style>p{color:red}</style><p>Ohutu tekst.</p>"

    result = to_plain_text(dangerous)

    assert result == "Ohutu tekst."
    assert "alert" not in result
    assert "color" not in result


def test_escaped_markup_does_not_survive_unescaping():
    result = to_plain_text("&lt;script&gt;alert(1)&lt;/script&gt; Tekst.")

    assert "<script>" not in result
    assert "Tekst." in result


def test_a_long_summary_is_truncated(settings):
    settings.KODA_SUMMARY_MAX_LENGTH = 20

    result = to_plain_text("a" * 100)

    assert len(result) <= 21
    assert result.endswith("…")


def test_no_article_html_is_stored(patch_fetch):
    """Escaped markup, which is how the real feed publishes descriptions."""
    escaped = "&lt;div class='body'&gt;&lt;p&gt;Sisu&lt;/p&gt;&lt;/div&gt;"
    patch_fetch(FakeFetch(feed(item(description=escaped))))

    summary = collect_news().entries[0].summary

    assert summary == "Sisu"
    assert "div" not in summary


def test_a_description_parsed_into_child_elements_still_yields_text(patch_fetch):
    """If a feed ever emits real child elements, the summary must not vanish."""
    patch_fetch(FakeFetch(feed(item(description="<p>Sisu</p>"))))

    assert collect_news().entries[0].summary == "Sisu"


def test_the_item_limit_is_applied(patch_fetch, settings):
    settings.KODA_NEWS_MAX_ITEMS = 2
    items = "".join(
        item(guid=f"g{i}", link=f"https://www.koda.ee/et/uudised/{i}") for i in range(5)
    )
    patch_fetch(FakeFetch(feed(items)))

    assert len(collect_news().entries) == 2


def test_a_not_modified_response_returns_none(patch_fetch):
    patch_fetch(FakeFetch(b"", status=304))

    assert collect_news(etag='"synthetic"') is None
