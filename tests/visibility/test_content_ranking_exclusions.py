"""Which measured paths may be ranked as content, and which may not.

The screenshot that started this: `/et`, `/en`, `/ru` and `/et/search/node`
occupying content-ranking positions. They are real traffic — `/et` alone is
133 588 measured views — and they stay in every site-wide figure. What they may
not do is compete with articles in a list of content.

The other half of these tests is the more important half: **the exclusions must
not reach a real page.** `/en/services/search-cooperation-partner` is a service
the Chamber sells, and a rule matching the substring "search" would delete it
from every ranking with nothing looking wrong afterwards.
"""

from __future__ import annotations

import pytest

from apps.visibility.content_ranking import is_rankable_content

# -- excluded: navigation, search, technical ---------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # Language roots. The busiest addresses on the site and not content.
        "/",
        "/et",
        "/en",
        "/ru",
        # Internal search.
        "/et/search/node",
        "/en/search/node",
        "/ru/search/node",
        "/et/search/node/help",
        "/search",
        # Error documents, including the form GA4 records with the failed
        # address appended.
        "/403.html",
        "/404.html",
        "/403.html%3Fpage=/et/checkout/9305/payment/return&from=https:/www.koda.ee",
        # Drupal's numeric alias for a page that also has a readable address.
        "/et/node/1173",
        "/en/node/9535",
        # Authentication and profiles.
        "/et/user/login",
        "/et/user/password",
        "/et/user/463",
        # Basket and per-order checkout paths.
        "/et/cart",
        "/en/cart",
        "/et/checkout/93629/order_information",
        # Drupal taxonomy listings and system routes.
        "/et/taxonomy/term/47",
        "/ru/system/404",
        # Uploaded assets.
        "/sites/default/files/content-type/content/2024-12/mingi.pdf",
    ],
)
def test_a_utility_path_is_not_content(path):
    assert is_rankable_content(path) is False


# -- kept: everything a visitor might actually be looking for -----------------


@pytest.mark.parametrize(
    "path",
    [
        # Named in the brief as pages that must not be lost.
        "/et/pood",
        "/et/astu-liikmeks",
        "/et/liikmed/liikmemaks",
        "/et/liikmed/miks-olla-meie-liige",
        # Sections the registry knows.
        "/et/teenused/paritolusertifikaadid",
        "/et/uudised/kasunduslepinguga-tootaja-versus-toolepinguga-tootaja",
        "/et/sundmused/arihooaja-avamine-20262027",
        # Ordinary pages belonging to no registered section.
        "/et/parkimine",
        "/et/andmekaitsetingimused",
        "/et/tooriistad/emtaki-koodid",
        "/et/meist/kontakt/tallinna",
        "/et/contact/ask_more",
        "/et/form/soovin-astuda-koja-liikmeks",
    ],
)
def test_a_real_page_stays_rankable(path):
    assert is_rankable_content(path) is True


def test_a_service_whose_name_contains_search_is_not_excluded():
    """The over-exclusion trap, with 972 measured views behind it.

    A substring rule on "search" would take this page out of every ranking and
    nothing on the page would look wrong.
    """
    assert is_rankable_content("/en/services/search-cooperation-partner") is True


def test_a_path_that_merely_starts_like_a_utility_segment_is_kept():
    """Segment matching, not `startswith`.

    `/et/nodes-and-networks` is not `/et/node/…`, and `/et/username` is not
    `/et/user/…`.
    """
    assert is_rankable_content("/et/nodeksi-teemal") is True
    assert is_rankable_content("/et/kasutajad") is True
    assert is_rankable_content("/et/cartoons") is True


def test_an_absolute_url_is_canonicalised_before_it_is_judged():
    assert is_rankable_content("https://www.koda.ee/et") is False
    assert is_rankable_content("https://www.koda.ee/et/pood") is True
