"""Synthetic archive listing markup and a fake site to serve it.

The archive publishes the same card markup as the current listing, so these
fixtures reuse `current_topic_factory`'s card and detail builders and add the
two things that are archive-specific: a Drupal pager that advertises its own
last page, and a listing root at `/arhiiv`.

No live Koda.ee page is committed, and no test in this suite performs a network
request.
"""

from __future__ import annotations

from .current_topic_factory import FakeSite, card, detail

ARCHIVE_PATH = "/et/meie-moju/hetkel-kasil/arhiiv"
DETAIL_PREFIX = "/et/meie-moju/hetkel-kasil/"
CURRENT_PATH = "/et/meie-moju/hetkel-kasil"


def pager(current: int, last: int) -> str:
    """A pager shaped like Drupal's, advertising its own last page.

    `pager__item--last` is what lets the collector read the end of the archive
    in one request instead of probing until a page comes back empty, so it is
    reproduced faithfully rather than approximated.
    """
    if last <= 0:
        return ""
    items = [
        f'<li class="pager__item"><a href="?page={index}">{index + 1}</a></li>'
        for index in range(min(last + 1, 5))
    ]
    if current < last:
        items.append(f'<li class="pager__item--next"><a href="?page={current + 1}">Next ›</a></li>')
    items.append(f'<li class="pager__item--last"><a href="?page={last}">Last »</a></li>')
    return f'<nav class="pager"><ul class="pagination">{"".join(items)}</ul></nav>'


def archive_listing(*cards: str, current: int = 0, last: int = 0, extra: str = "") -> str:
    """One archive listing page, with the site chrome a naive scraper collects."""
    return f"""<!doctype html><html><body>
      <nav class="menu"><a href="/et/uudised">Uudised</a></nav>
      <a href="{CURRENT_PATH}">Hetkel käsil</a>
      <a href="{ARCHIVE_PATH}">Arhiiv</a>
      <script>var t = {{"p": "/et/meie-moju/hetkel-kasil/kummitus"}};</script>
      <div class="view view-current-drafts"><div class="view-content">
        {"".join(cards)}{extra}
      </div></div>
      {pager(current, last)}
    </body></html>"""


def archive_card(
    slug: str,
    title: str,
    *,
    summary: str = "",
    day: str = "27",
    month: str = "dets",
    href: str | None = None,
) -> str:
    """An archive card.

    The default day and month are the ones the 2016 page prints, as a standing
    reminder that the card carries **no year** and that a date read here would
    be a guess across a decade.
    """
    return card(
        slug,
        title,
        summary=summary or f"Sünteetiline ministeerium on koostanud {slug} eelnõu.",
        day=day,
        month=month,
        href=href,
    )


def archive_site(
    pages: dict[int, list[tuple[str, str]]],
    *,
    details: dict | None = None,
    errors: dict | None = None,
) -> FakeSite:
    """A paginated archive plus a detail page for every slug it lists.

    ``pages`` maps a page number to its (slug, title) pairs. ``details`` may
    override the generated detail page for a slug; ``errors`` may make one fail.
    """
    details = details or {}
    last = max(pages) if pages else 0
    served: dict[str, str] = {}
    for page, entries in pages.items():
        cards = [archive_card(slug, title) for slug, title in entries]
        key = ARCHIVE_PATH if page == 0 else f"{ARCHIVE_PATH}?page={page}"
        served[key] = archive_listing(*cards, current=page, last=last)
        for slug, title in entries:
            served.setdefault(
                f"{DETAIL_PREFIX}{slug}",
                details.get(slug, detail(title=title)),
            )
    return FakeSite(served, errors=errors or {})


def simple_archive(*slugs: str) -> FakeSite:
    """A single-page archive holding one entry per slug."""
    return archive_site({0: [(slug, f"Arhiveeritud {slug}") for slug in slugs]})
