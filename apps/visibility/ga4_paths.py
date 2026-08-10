"""One canonical page path, used by everything that has to match GA4 to Koda.ee.

GA4 reports a `pagePath` — host stripped, query string usually attached. DashKoda
holds `NewsItem.canonical_url`, a full URL. Neither is a key until both are put
through the same function, which is what this module is: **the only** place a
page path is made canonical, so an article and its analytics cannot disagree
about their own identity.

What identity means here:

- **the host is not part of it.** `koda.ee` and `www.koda.ee` serve the same
  article. GA4 already drops the host from `pagePath`; a canonical URL carries
  one, and comparing them without stripping it matches nothing;
- **the query string is not part of it.** A newsletter link arrives as
  `?utm_source=…`, a share link as `?fbclid=…`, and treating those as separate
  pages splits one article's readership across a dozen rows. This is the single
  most consequential rule in the module;
- **the fragment is not part of it.** `#kommentaarid` is a position on a page,
  not a page;
- **a trailing slash is not part of it.** `/et/uudised/x` and `/et/uudised/x/`
  are one article, so the slash is dropped — except at the root, where `/` is
  the whole path and dropping it would leave nothing;
- **case is part of it.** Path segments on this site are case-sensitive, and
  lowercasing would silently merge two paths the server treats as different.

What is deliberately *not* done: no unicode normalisation, no percent-decoding,
no stripping of trailing `index.html`, no language-prefix rewriting. Every one
of those merges paths that a server may distinguish, and a wrong merge shows a
figure for an article that is partly another article's. `percent_decoded` exists
for display, and is never the key.
"""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

#: What an unusable input canonicalises to. Not `"/"` — the site root is a real
#: page with real traffic, and an empty string must never be mistaken for it.
UNKNOWN = ""

#: The site root, the one path whose trailing slash is the path.
ROOT = "/"


def _is_scheme_relative(raw: str) -> bool:
    """Whether `//…` names a host or is just a path with a doubled slash.

    Both occur. `//www.koda.ee/et/uudised/x` is a scheme-relative URL whose host
    must be dropped; `//et//uudised/x` is a path GA4 genuinely reports, produced
    by a link built by joining strings badly. Handing either to `urlsplit`
    unqualified reads the first segment as the host, which silently turns the
    second one into `/uudised/x` — an article filed under the wrong section, and
    exactly the kind of quiet wrong match this module exists to prevent.

    A dot in the first segment is what separates them. `www.koda.ee` has one and
    no path segment on this site does, so the ambiguity is decided by the only
    thing in the string that distinguishes a hostname from a directory.
    """
    if not raw.startswith("//"):
        return False
    first = raw[2:].split("/", 1)[0]
    return "." in first


def canonical_path(value: str | None) -> str:
    """The canonical page path of a URL or a GA4 `pagePath`.

    Accepts either — a full URL, a scheme-relative URL, or a bare path — because
    the two sides of every comparison in this application arrive in different
    shapes and the caller should not have to know which it holds.

        >>> canonical_path("https://www.koda.ee/et/uudised/example/?utm_source=x")
        '/et/uudised/example'
        >>> canonical_path("/et/uudised/example")
        '/et/uudised/example'
        >>> canonical_path("https://koda.ee")
        '/'

    Returns :data:`UNKNOWN` for anything with no path to speak of, which callers
    treat as "not matchable" rather than as the root.
    """
    if not value:
        return UNKNOWN

    raw = value.strip()
    if not raw:
        return UNKNOWN

    # `urlsplit` reads a bare path as a path and a full URL as a URL, so both
    # shapes go through the same parser rather than through a heuristic here.
    #
    # The one thing it cannot be handed as-is is a leading `//`, which it always
    # reads as a host. When that really is a host the string needs a scheme so
    # the host lands in `netloc`; when it is a doubled separator the slashes are
    # collapsed first, because otherwise `//et//uudised/x` parses with `et` as
    # the host and the article silently loses its section.
    if _is_scheme_relative(raw):
        raw = f"https:{raw}"
    elif raw.startswith("//"):
        raw = "/" + raw.lstrip("/")
    parts = urlsplit(raw)
    path = parts.path

    if not path:
        # `https://koda.ee` and `https://koda.ee?x=1` both name the root. A bare
        # `?x=1` names nothing, and that is a different answer.
        return ROOT if parts.netloc else UNKNOWN

    if not path.startswith("/"):
        path = "/" + path

    # Collapse repeated separators. `//et//uudised` is one path served once, and
    # GA4 does report it when a link is built by joining strings badly.
    while "//" in path:
        path = path.replace("//", "/")

    if len(path) > 1:
        path = path.rstrip("/") or ROOT

    return path


def canonical_paths(values) -> tuple[str, ...]:
    """Canonicalise many, dropping what cannot be matched, keeping order.

    Deduplicated: two URLs that differ only by tracking parameters are one page,
    and a caller building a lookup key set wants it to say so once.
    """
    seen: dict[str, None] = {}
    for value in values:
        path = canonical_path(value)
        if path != UNKNOWN:
            seen.setdefault(path, None)
    return tuple(seen)


def percent_decoded(path: str) -> str:
    """A path made readable for a person. **Never** used as a key.

    `/et/uudised/t%C3%B6%C3%B6turg` is one article and reads as noise. Decoding
    it for display is worth doing; decoding it for comparison is not, because
    two different encodings of the same characters would then match while the
    server may serve only one of them.
    """
    return unquote(path)


def is_under(path: str, prefix: str) -> bool:
    """Whether a canonical path sits under a canonical section prefix.

    Segment-aware on purpose: `/et/uudiseks` is not under `/et/uudised`, though
    it does start with those characters, and a plain `startswith` would file one
    section's traffic under another.
    """
    if not path or not prefix:
        return False
    prefix = canonical_path(prefix)
    return path == prefix or path.startswith(prefix + "/")


__all__ = ["ROOT", "UNKNOWN", "canonical_path", "canonical_paths", "is_under", "percent_decoded"]
