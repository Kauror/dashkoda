"""Which measured paths may appear in a ranking of *content*.

Every path GA4 reports is real traffic and stays in the stored history, in the
site totals and in the traffic chart. This module answers a narrower question:
which of them is a piece of content a board member could meaningfully see ranked
against another.

`/et` received 133 588 views. That is the busiest address the Chamber has and it
belongs in the site's totals — but "the Estonian homepage" is not an article, a
service or an event, and ranked beside them it simply wins forever and tells
nobody anything. The same is true of internal search, the cart, error pages and
Drupal's numeric node aliases.

**Nothing here deletes or hides traffic.** `get_traffic_series`,
`get_channel_totals` and every site-wide figure read the same rows they always
did. The exclusions apply to the content ranking and to content search, and to
nothing else.

## Where the list came from

Read from the production GA4 history on 2026-08-11 — 40 998 distinct paths,
1 429 700 page views — rather than guessed:

| family | paths | views | example |
| --- | --- | --- | --- |
| language roots | 4 | 172 919 | `/et` |
| Drupal node aliases | 10 784 | 121 667 | `/et/node/1173` |
| cart and checkout | 17 756 | 38 928 | `/et/cart` |
| error pages | 1 097 | 16 374 | `/403.html` |
| internal search | 7 | 13 723 | `/et/search/node` |
| user and authentication | 50 | 1 994 | `/et/user/login` |
| taxonomy listings | 79 | 1 124 | `/et/taxonomy/term/47` |
| system routes | 75 | 371 | `/et/system/404` |

The node aliases are worth a sentence of their own. They are Drupal's internal
address for a page that also has a readable one, so including them would rank
the same article twice — once under a name and once as `/et/node/9294`, which
nobody can identify.

## Why segments, never substrings

`/en/services/search-cooperation-partner` has 972 views and is a service the
Chamber sells. A rule that excluded any path *containing* "search" would delete
it from the rankings, and nothing on the page would look wrong afterwards. So
every rule here matches whole path segments through
:func:`apps.visibility.ga4_paths.is_under`, and the only substring rule is the
one for error pages, which is anchored at the start.

## What is deliberately **not** excluded

`/et/pood`, `/et/astu-liikmeks`, `/et/liikmed/liikmemaks`, `/et/parkimine`,
`/et/andmekaitsetingimused`, `/et/contact/ask_more`, `/et/form/…` and everything
else a visitor might actually be looking for. "How many people read this page?"
is a fair question about all of them, and they are not News, Events or Services
only in the sense that the section registry does not name them.

Under-excluding is the safer error. A utility page in a ranking is untidy; a
service page missing from one is a wrong answer.
"""

from __future__ import annotations

from .ga4_paths import canonical_path, is_under

#: The site's language roots, plus the bare root. Navigation, not content.
LANGUAGE_ROOTS: tuple[str, ...] = ("/", "/et", "/en", "/ru")

#: Path segments that make everything beneath them a utility route, in each of
#: the three languages. Matched whole-segment: `/et/search` and anything under
#: it, never a path that merely contains the word.
UTILITY_SEGMENTS: tuple[str, ...] = (
    "search",  # internal site search
    "user",  # login, password, profile
    "cart",  # basket
    "checkout",  # one path per order
    "node",  # Drupal's numeric alias for a page that also has a readable one
    "taxonomy",  # Drupal term listings
    "system",  # /et/system/404 and friends
)

#: Languages the site publishes in. Used only to build the utility prefixes.
LANGUAGES: tuple[str, ...] = ("et", "en", "ru")

#: Utility prefixes that carry no language segment.
BARE_UTILITY_PREFIXES: tuple[str, ...] = (
    "/search",
    "/sites/default/files",  # uploaded assets, not pages
)

#: Error documents. Anchored at the start rather than segment-matched, because
#: GA4 records them with the failed address appended:
#: `/403.html%3Fpage=/et/checkout/9305/payment/return&from=…`.
ERROR_DOCUMENT_PREFIXES: tuple[str, ...] = ("/403.html", "/404.html")


def _utility_prefixes() -> tuple[str, ...]:
    return (
        tuple(f"/{language}/{segment}" for language in LANGUAGES for segment in UTILITY_SEGMENTS)
        + BARE_UTILITY_PREFIXES
    )


#: Every prefix beneath which nothing is content, built once at import.
CONTENT_RANKING_PREFIX_EXCLUSIONS: tuple[str, ...] = _utility_prefixes()

#: Every path that is not content in itself.
CONTENT_RANKING_EXACT_EXCLUSIONS: tuple[str, ...] = LANGUAGE_ROOTS


def is_rankable_content(path: str) -> bool:
    """Whether a measured path may appear in a ranking of content.

    False for language roots, internal search, the cart and checkout, Drupal
    node aliases, taxonomy listings, system routes, uploaded assets and error
    documents. True for everything else, including the many perfectly ordinary
    pages that belong to none of the three registered sections.
    """
    canonical = canonical_path(path)
    if not canonical:
        return False
    if canonical in CONTENT_RANKING_EXACT_EXCLUSIONS:
        return False
    if any(canonical.startswith(prefix) for prefix in ERROR_DOCUMENT_PREFIXES):
        return False
    return not any(is_under(canonical, prefix) for prefix in CONTENT_RANKING_PREFIX_EXCLUSIONS)


def rankable_paths(paths) -> tuple[str, ...]:
    """Those of `paths` that may be ranked, in the order given."""
    return tuple(path for path in paths if is_rankable_content(path))


__all__ = [
    "BARE_UTILITY_PREFIXES",
    "CONTENT_RANKING_EXACT_EXCLUSIONS",
    "CONTENT_RANKING_PREFIX_EXCLUSIONS",
    "ERROR_DOCUMENT_PREFIXES",
    "LANGUAGES",
    "LANGUAGE_ROOTS",
    "UTILITY_SEGMENTS",
    "is_rankable_content",
    "rankable_paths",
]
