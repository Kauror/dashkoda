"""Synthetic `Hetkel käsil` markup and a fake site to serve it.

Every page here is written by hand and is deliberately minimal: enough of the
real structure to exercise the parser, and nothing else. No live Koda.ee page is
committed to this repository, and no test in this suite performs a network
request.

The fixtures reproduce three properties of the live markup that the collector
has to survive, because getting any of them wrong is silent rather than loud:

- the listing card prints a **day and an abbreviated month with no year**, so
  anything reading a publication date from the listing would have to invent one;
- ``field--name-body`` also appears on the site's language-switcher block, so a
  parser scoped to that class alone swallows "Eesti keel English Русский";
- the page repeats its own node in a sideblock further down, so a parser that
  keeps every match doubles the stored text.
"""

from __future__ import annotations

from urllib.parse import urlparse

LISTING_PATH = "/et/meie-moju/hetkel-kasil"
DETAIL_PREFIX = "/et/meie-moju/hetkel-kasil/"
ARCHIVE_PATH = "/et/meie-moju/hetkel-kasil/arhiiv"


def card(
    slug: str,
    title: str,
    *,
    summary: str = "Sünteetiline ministeerium on koostanud eelnõu.",
    day: str = "05",
    month: str = "aug",
    href: str | None = None,
) -> str:
    """One listing teaser, shaped like the live one — year and all missing."""
    link = href if href is not None else f"{DETAIL_PREFIX}{slug}"
    return f"""
    <div class="current-draft--teaser node node--type-current-draft
                node--view-mode-teaser">
      <div class="inner">
        <div class="current-draft--teaser--group-left">
          <div class="current-draft--teaser--date">
            <span class="day"> {day} </span>
            <span class="month"> {month} </span>
          </div>
        </div>
        <div class="current-draft--teaser--group-right">
          <h2 class="current-draft--teaser--title">
            <a href="{link}" hreflang="et">{title}</a>
          </h2>
        </div>
        <div class="current-draft--teaser--group-footer">
          <div class="current-draft--teaser--content">{summary}</div>
          <div class="current-draft--teaser--group-footer--read-more">
            <a href="{link}" hreflang="et">Vaata</a>
          </div>
        </div>
      </div>
    </div>
    """


def listing(*cards: str, next_page: bool = False, extra: str = "") -> str:
    """A listing page carrying the site chrome a naive scraper would collect."""
    pager = (
        '<nav class="pager"><ul class="pagination">'
        '<li><a href="?page=0">1</a></li>'
        '<li><a href="?page=1" rel="next">Next ›</a></li>'
        "</ul></nav>"
        if next_page
        else ""
    )
    return f"""<!doctype html><html><body>
      <nav class="menu"><a href="/et/uudised">Uudised</a></nav>
      <a href="{ARCHIVE_PATH}">Arhiiv</a>
      <script>var tracking = {{"path": "/et/meie-moju/hetkel-kasil/kummitus"}};</script>
      <style>.current-draft--teaser {{ color: red; }}</style>
      <div class="view view-current-drafts">
        <div class="view-content">{"".join(cards)}{extra}</div>
      </div>
      {pager}
      <footer><a href="{ARCHIVE_PATH}">Arhiiv</a></footer>
    </body></html>"""


def detail(
    *,
    title: str = "Sünteetiline pealkiri",
    date: str | None = "05.08.2026",
    intro: str = (
        "Sünteetiline ministeerium on koostanud eelnõu. "
        "Anna hiljemalt 18. augustiks teada, mida arvad."
    ),
    body: str = "Sünteetiline sisu eelnõu kohta.",
    with_sideblock: bool = True,
) -> str:
    """One detail page, including the traps the live template contains."""
    date_block = f'<div class="current-draft--default--date"> {date} </div>' if date else ""
    heading = f"<h1>{title}</h1>" if title else ""
    sideblock = (
        """
        <div class="node node--type-current-draft node--view-mode-sideblocks">
          <div class="current-draft--default--date"> 01.01.2000 </div>
          <div class="field--intro">Kordus, mida ei tohi salvestada.</div>
        </div>
        """
        if with_sideblock
        else ""
    )
    return f"""<!doctype html><html><head>
      <script type="application/ld+json">{{"@type": "Organization"}}</script>
      </head><body>
      <nav class="menu"><a href="/et/uudised">Uudised</a></nav>
      <div class="block block-language clearfix field--name-body">
        Language switcher Eesti keel English Русский
      </div>
      <div class="node node--type-current-draft node--view-mode-full">
        <div class="current-draft--default--group--date-and-header">
          {date_block}
          {heading}
        </div>
        <div class="field--intro events--default--intro"><p>{intro}</p></div>
        <div class="field field--name-body field--type-text-with-summary">
          <p>{body}</p>
        </div>
      </div>
      {sideblock}
      <footer>Sünteetiline jalus</footer>
    </body></html>"""


def not_found(message: str = "Allikat ei leitud (404).") -> Exception:
    """A 404 exactly as the transport layer raises one.

    Callers classify a collection failure from `PublicFetchError.failure`, not
    from the message, so a fake that omitted the classification would make a
    missing page look merely `unavailable` and quietly weaken the tests that
    depend on telling the two apart.
    """
    from apps.core.public_http import FetchFailure, PublicFetchError

    return PublicFetchError(message, failure=FetchFailure.NOT_FOUND, status_code=404)


def refused(status: int = 403) -> Exception:
    """An access refusal, classified the way `_require_success` classifies one."""
    from apps.core.public_http import FetchFailure, PublicFetchError

    return PublicFetchError(
        f"Allikas keeldus ligipääsust ({status}).",
        failure=FetchFailure.REFUSED,
        status_code=status,
    )


class FakeSite:
    """Serves synthetic pages by path and records what was requested."""

    def __init__(self, pages: dict[str, str], *, errors: dict[str, Exception] | None = None):
        self.pages = pages
        self.errors = errors or {}
        self.requested: list[str] = []

    def __call__(self, url, **kwargs):
        from apps.core.public_http import FetchResult

        parsed = urlparse(url)
        key = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        self.requested.append(key)
        if key in self.errors:
            raise self.errors[key]
        if key not in self.pages:
            raise not_found()
        return FetchResult(
            status_code=200,
            content=self.pages[key].encode("utf-8"),
            content_type="text/html",
            etag="",
            last_modified="",
            final_host="www.koda.ee",
        )


def simple_site(**slugs: str) -> FakeSite:
    """A one-page listing plus one detail page per slug.

    ``slugs`` maps a slug to the page title; the detail pages carry the default
    intro and body unless a test builds its own.
    """
    pages = {LISTING_PATH: listing(*(card(slug, title) for slug, title in slugs.items()))}
    for slug, title in slugs.items():
        pages[f"{DETAIL_PREFIX}{slug}"] = detail(title=title)
    return FakeSite(pages)
