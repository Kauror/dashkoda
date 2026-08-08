"""Synthetic Koda.ee opinion surfaces for the public-source tests.

The markup mirrors the structural handles the live site actually uses —
`meie-arvamus--teaser` cards over news nodes, `news--teaser` cards with full
dates, `news--default--date` on the detail page, `btn--file` attachment links
under ``/sites/default/files/`` — because those handles are the collector's
contract with the site. No raw crawl body enters Git; every page here is
built from these templates.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import urlparse

from apps.core.public_http import FetchFailure, PublicFetchError

MA_LISTING_PATH = "/et/meie-arvamus"
NEWS_LISTING_PATH = "/et/uudised"
DETAIL_PREFIX = "/et/uudised/"
FILE_PREFIX = "/sites/default/files/content-type/content/"


def ma_card(slug: str, title: str, *, summary: str = "") -> str:
    return f"""
    <div class="meie-arvamus--teaser node node--type-news node--view-mode-meie-arvamus-teaser">
      <div class="meie-arvamus--teaser--date">
        <span class="day">30</span><span class="month">juuli</span>
      </div>
      <div class="meie-arvamus--teaser--title">
        <a href="{DETAIL_PREFIX}{slug}">{title}</a>
      </div>
      <div class="current-draft--teaser--content"><p>{summary}</p></div>
    </div>
    """


def news_card(slug: str, title: str, date: dt.date, *, category: str = "Meie uudised") -> str:
    return f"""
    <div class="equal-height news--teaser node node--type-news node--view-mode-teaser">
      <div class="news--teaser--group-header group-header">
        <div class="news--teaser--group-header--category">{category}</div>
        <div class="news--teaser--group-header--date">{date:%d.%m.%Y}</div>
      </div>
      <div class="news--teaser--title dont-break-out">
        <a href="{DETAIL_PREFIX}{slug}">{title}</a>
      </div>
    </div>
    """


def listing(*cards: str, pager_next: int | None = None) -> str:
    pager = f'<a href="?page={pager_next}">Järgmine</a>' if pager_next is not None else ""
    return f"""<!DOCTYPE html>
    <html><head><title>Koda</title></head><body>
    <nav><a href="/et/pood">Pood</a><a href="/et/sundmused">Sündmused</a></nav>
    <main>{"".join(cards)}</main>
    {pager}
    </body></html>
    """


def attachment_link(filename: str, *, label: str | None = None, folder: str = "2026-07") -> str:
    quoted = filename.replace(" ", "%20")
    text = label if label is not None else filename.rsplit(".", 1)[0] + " (.pdf)"
    return (
        f'<div class="field field--name-ekt-content-files field--type-ds">'
        f'<a href="{FILE_PREFIX}{folder}/{quoted}" class="btn btn--file ext-pdf">{text}</a>'
        f"</div>"
    )


def detail(
    *,
    title: str,
    date: dt.date | None = dt.date(2026, 7, 30),
    body: str = "Kaubanduskoda esitas arvamuse eelnõu kohta ja teeb ettepaneku muudatusteks.",
    attachments: str = "",
    extra: str = "",
) -> str:
    date_html = f'<div class="news--default--date">{date:%d.%m.%Y}</div>' if date else ""
    return f"""<!DOCTYPE html>
    <html><head><title>{title}</title></head><body>
    <article class="node node--type-news node--view-mode-full">
      <h1 class="news--default--title">{title}</h1>
      {date_html}
      <div class="field field--name-field-paragraph">
        <p>{body}</p>
        {attachments}
      </div>
      {extra}
    </article>
    <aside class="node node--type-news node--view-mode-sideblocks">
      <h1 class="news--default--title">{title}</h1>
      <div class="news--default--date">01.01.2001</div>
      <a href="/sites/default/files/sideblock.pdf" class="btn btn--file ext-pdf">Kõrvalpaan</a>
    </aside>
    </body></html>
    """


def not_found() -> PublicFetchError:
    return PublicFetchError(
        "Allikat ei leitud (404).", failure=FetchFailure.NOT_FOUND, status_code=404
    )


def server_error() -> PublicFetchError:
    return PublicFetchError(
        "Allikas vastas koodiga 500.", failure=FetchFailure.SERVER_ERROR, status_code=500
    )


class FakePublicSite:
    """Serves synthetic HTML pages and PDF bytes by path, recording requests."""

    def __init__(
        self,
        pages: dict[str, str] | None = None,
        files: dict[str, bytes] | None = None,
        *,
        errors: dict[str, Exception] | None = None,
        content_types: dict[str, str] | None = None,
    ):
        self.pages = dict(pages or {})
        self.files = dict(files or {})
        self.errors = dict(errors or {})
        self.content_types = dict(content_types or {})
        self.requested: list[str] = []

    def __call__(self, url, **kwargs):
        from apps.core.public_http import FetchResult

        parsed = urlparse(url)
        key = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        self.requested.append(key)
        if key in self.errors:
            raise self.errors[key]
        if key in self.pages:
            return FetchResult(
                status_code=200,
                content=self.pages[key].encode("utf-8"),
                content_type=self.content_types.get(key, "text/html"),
                etag="",
                last_modified="",
                final_host="www.koda.ee",
            )
        unquoted = _unquote(key)
        if unquoted in self.files:
            return FetchResult(
                status_code=200,
                content=self.files[unquoted],
                content_type=self.content_types.get(unquoted, "application/pdf"),
                etag="",
                last_modified="",
                final_host="www.koda.ee",
            )
        raise not_found()


def _unquote(path: str) -> str:
    from urllib.parse import unquote

    return unquote(path)


def pdf_path(filename: str, *, folder: str = "2026-07") -> str:
    return f"{FILE_PREFIX}{folder}/{filename}"


def simple_public_site(
    *,
    slug: str = "koda-esitas-arvamuse",
    title: str = "Koda esitas arvamuse maksukorralduse seaduse eelnõu kohta",
    date: dt.date = dt.date(2026, 3, 9),
    pdf_name: str
    | None = "2026-03-09 - Rahandusministeerium - Arvamus maksukorralduse seaduse eelnou kohta.pdf",
    pdf_payload: bytes | None = None,
    listed_in_meie_arvamus: bool = True,
    body: str = "Kaubanduskoda esitas arvamuse eelnõu kohta ja teeb ettepaneku muudatusteks.",
) -> FakePublicSite:
    """One article, optionally with one attached opinion PDF."""
    from .opinion_factory import opinion_pdf

    attachments = ""
    files = {}
    if pdf_name is not None:
        attachments = attachment_link(pdf_name)
        files[pdf_path(pdf_name)] = (
            pdf_payload if pdf_payload is not None else opinion_pdf(our_date=f"{date:%d.%m.%Y}")
        )

    ma_cards = [ma_card(slug, title)] if listed_in_meie_arvamus else []
    site = FakePublicSite(
        pages={
            MA_LISTING_PATH: listing(*ma_cards),
            NEWS_LISTING_PATH: listing(news_card(slug, title, date)),
            f"{DETAIL_PREFIX}{slug}": detail(
                title=title, date=date, body=body, attachments=attachments
            ),
        },
        files=files,
    )
    end_listings(site)
    return site


def end_listings(site: FakePublicSite) -> None:
    """Serve empty listing pages past the end, as the live site does.

    Beyond its last page Koda.ee answers 200 with a page frame and no cards,
    not 404; the walk's stop rule depends on that shape.
    """
    for base in (MA_LISTING_PATH, NEWS_LISTING_PATH):
        page = 1
        while f"{base}?page={page}" in site.pages:
            page += 1
        site.pages[f"{base}?page={page}"] = listing()
