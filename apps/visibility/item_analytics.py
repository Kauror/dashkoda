"""Hanging total measured page views on whatever a page is about to render.

The shared bridge between a list of DashKoda objects — news items, programme
events, anything with a public URL — and the one selector that totals views.

Two rules the callers rely on:

- **one query for the whole list.** The attribute is filled for every item from
  a single grouped total, so a five-row preview and a fifty-row page cost the
  same number of queries;
- **an item with no measurement gets `None`, not zero.** A template asking
  `{% if item.page_views %}` then renders nothing, which is the honest output
  for a page nobody measured. `0 vaatamist` would be a claim.

The attribute is set on the instance and never saved. These are immutable
imported rows; a view count written onto one would be a mutable figure inside a
frozen record, and would have to be rewritten on every sync.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from .ga4_paths import canonical_path
from .ga4_selectors import Coverage, PageViews, get_coverage, get_page_view_totals

#: What the number is called beside an item. Page views, not people: one reader
#: opening an article twice is two of these.
VIEWS_UNIT = "vaatamist"
VIEWS_UNIT_LONG = "lehevaatamist"


def news_url(item) -> str:
    return getattr(item, "canonical_url", "") or ""


def event_url(item) -> str:
    """An event's effective public page.

    Straight off `item.public_link`, which `attach_public_links` has already
    resolved through the workbook-then-match precedence. This must never redo
    that resolution: two answers to "which page is this event on" is exactly the
    kind of disagreement that shows up as traffic on the wrong row.
    """
    link = getattr(item, "public_link", None)
    return getattr(link, "url", "") or ""


def attach_page_views(
    items: Iterable,
    *,
    url_of: Callable[[object], str] = news_url,
    coverage: Coverage | None = None,
    attribute: str = "page_views",
) -> Sequence:
    """Give every item a `.page_views` — a `PageViews` or `None` — in one query.

    Returns a list rather than a generator: a template iterates rows more than
    once, and a generator would silently render an empty second pass.
    """
    rows = list(items)
    if not rows:
        return rows

    coverage = coverage if coverage is not None else get_coverage()
    if not coverage.has_data:
        for item in rows:
            setattr(item, attribute, None)
            setattr(item, f"{attribute}_label", "")
            setattr(item, f"{attribute}_label_long", "")
        return rows

    totals = get_page_view_totals((url_of(item) for item in rows), coverage=coverage)
    for item in rows:
        path = canonical_path(url_of(item))
        views = totals.get(path) if path else None
        setattr(item, attribute, views)
        # Pre-formatted, because a template cannot call a function with
        # arguments and the alternative is a filter that duplicates the rule
        # about what an absent measurement renders as.
        setattr(item, f"{attribute}_label", views_label(views))
        setattr(item, f"{attribute}_label_long", views_label(views, long=True))
    return rows


def views_label(views: PageViews | None, *, long: bool = False) -> str:
    """`1 842 vaatamist`, or nothing at all.

    Nothing rather than `0 vaatamist` when the page was never measured — the
    two look alike on a screen and mean opposite things.
    """
    if views is None:
        return ""
    from apps.core.formatting import group_thousands

    unit = VIEWS_UNIT_LONG if long else VIEWS_UNIT
    return f"{group_thousands(views.total)} {unit}"


__all__ = [
    "VIEWS_UNIT",
    "VIEWS_UNIT_LONG",
    "attach_page_views",
    "event_url",
    "news_url",
    "views_label",
]
