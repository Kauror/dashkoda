"""Canonical page-path identity.

Every figure this application shows about an article depends on one string
comparison, so what that string is has to be nailed down. No database.
"""

from __future__ import annotations

from apps.visibility.ga4_paths import (
    ROOT,
    UNKNOWN,
    canonical_path,
    canonical_paths,
    is_under,
    percent_decoded,
)

ARTICLE = "/et/uudised/example"


# -- the two shapes that have to meet ------------------------------------


def test_a_canonical_url_and_a_ga4_path_reach_the_same_identity():
    """The whole point of the module: DashKoda holds a URL, GA4 reports a path,
    and neither is a key until both go through here."""
    assert canonical_path("https://www.koda.ee/et/uudised/example") == ARTICLE
    assert canonical_path("/et/uudised/example") == ARTICLE


def test_the_host_is_not_part_of_the_identity():
    for url in (
        "https://www.koda.ee/et/uudised/example",
        "https://koda.ee/et/uudised/example",
        "http://koda.ee/et/uudised/example",
        "//www.koda.ee/et/uudised/example",
    ):
        assert canonical_path(url) == ARTICLE, url


# -- tracking parameters, the rule that matters most ---------------------


def test_tracking_parameters_do_not_split_one_article_into_several():
    """A newsletter link, a Facebook share and a bare link are one article. This
    is the difference between an article having 1 482 views and having them
    scattered over a dozen rows nobody adds up."""
    for url in (
        "https://www.koda.ee/et/uudised/example?utm_source=uudiskiri",
        "https://www.koda.ee/et/uudised/example?utm_source=fb&utm_medium=social",
        "https://www.koda.ee/et/uudised/example?fbclid=IwAR0abc",
        "/et/uudised/example?gclid=xyz",
    ):
        assert canonical_path(url) == ARTICLE, url


def test_a_fragment_is_a_position_on_a_page_and_not_a_page():
    assert canonical_path("https://www.koda.ee/et/uudised/example#kommentaarid") == ARTICLE
    assert canonical_path("/et/uudised/example?utm_source=x#top") == ARTICLE


# -- slashes -------------------------------------------------------------


def test_a_trailing_slash_does_not_make_a_second_article():
    assert canonical_path("/et/uudised/example/") == ARTICLE
    assert canonical_path("https://www.koda.ee/et/uudised/example/") == ARTICLE


def test_the_root_keeps_its_slash_because_the_slash_is_the_path():
    """Stripping it would leave the empty string, which this module reserves for
    "not matchable" — the site root is a real page with real traffic."""
    assert canonical_path("/") == ROOT
    assert canonical_path("https://www.koda.ee/") == ROOT
    assert canonical_path("https://www.koda.ee") == ROOT
    assert canonical_path("https://www.koda.ee?utm_source=x") == ROOT


def test_repeated_separators_collapse():
    """A link built by joining strings badly is still one page, and GA4 does
    report these."""
    assert canonical_path("//et//uudised//example") == ARTICLE
    assert canonical_path("https://www.koda.ee/et//uudised/example//") == ARTICLE


def test_a_doubled_slash_path_is_not_mistaken_for_a_host():
    """`//et//uudised/x` and `//www.koda.ee/et/uudised/x` both start with two
    slashes and mean opposite things. Read the first as a host and the article
    lands under `/uudised/x` — the wrong section, silently."""
    assert canonical_path("//et//uudised//example") == ARTICLE
    assert canonical_path("//www.koda.ee/et/uudised/example") == ARTICLE


def test_a_path_without_a_leading_slash_gains_one():
    assert canonical_path("et/uudised/example") == ARTICLE


# -- what is deliberately not merged -------------------------------------


def test_case_is_part_of_the_identity():
    """The server distinguishes these, so this must not merge them."""
    assert canonical_path("/et/Uudised/Example") == "/et/Uudised/Example"


def test_percent_encoding_is_left_alone_in_the_key():
    """Decoding for comparison would match two spellings the server may not
    serve interchangeably. `percent_decoded` exists for display only."""
    encoded = "/et/uudised/t%C3%B6%C3%B6turg"

    assert canonical_path(encoded) == encoded
    assert percent_decoded(encoded) == "/et/uudised/tööturg"


def test_a_unicode_path_survives_unchanged():
    assert canonical_path("https://www.koda.ee/et/uudised/tööturg") == "/et/uudised/tööturg"


def test_a_section_index_is_not_the_same_page_as_an_article_under_it():
    """GA4 reports `/et/uudised` in its own right, and it is not any article."""
    assert canonical_path("/et/uudised") != ARTICLE


# -- nothing to match ----------------------------------------------------


def test_an_empty_or_unusable_value_is_not_matchable_and_is_not_the_root():
    """`UNKNOWN` rather than `/`: an article whose URL failed to parse must not
    silently collect the whole site's front-page traffic."""
    for value in (None, "", "   ", "?utm_source=x", "#top"):
        assert canonical_path(value) == UNKNOWN, repr(value)
    assert canonical_path("") != ROOT


# -- bulk ----------------------------------------------------------------


def test_many_paths_are_deduplicated_and_keep_their_order():
    values = [
        "https://www.koda.ee/et/uudised/a",
        "https://koda.ee/et/uudised/a/?utm_source=x",
        "https://www.koda.ee/et/uudised/b",
        "",
    ]

    assert canonical_paths(values) == ("/et/uudised/a", "/et/uudised/b")


# -- section membership --------------------------------------------------


def test_a_section_prefix_matches_whole_segments_only():
    """`/et/uudiseks` starts with the same characters and is a different
    section; a plain `startswith` would file its traffic under news."""
    assert is_under("/et/uudised/example", "/et/uudised") is True
    assert is_under("/et/uudised", "/et/uudised") is True
    assert is_under("/et/uudiseks", "/et/uudised") is False
    assert is_under("/et/sundmused/example", "/et/uudised") is False
    assert is_under("", "/et/uudised") is False
